"""Chimes the Echo of whoever a disruption is waiting on.

Runs inside the notifier Lambda after console and email alerts, rather than as its own
stream consumer. DynamoDB streams want at most two readers and we already have the analysis
and the notifier.

Who gets chimed:

* analysed (REPORTED -> OPTIONS_READY) or sent back (SUBMITTED -> OPTIONS_READY) goes to the
  controller of the assigned desk
* plan submitted (OPTIONS_READY -> SUBMITTED) goes to every approver

Along with the chime we message the skill, which sets a reminder a few seconds out so the
Echo actually reads the alert aloud (see ``crewops.alexa.proactive``).

Only linked accounts with notifications allowed are sent anything. Failures are logged and
not retried. Console and email carry the same news, and a double chime is worse than a
missed one.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from crewops.notify.changes import change_of

log = logging.getLogger()
log.setLevel(logging.INFO)


def desk_of(record: dict[str, Any]) -> str | None:
    from boto3.dynamodb.types import TypeDeserializer

    image = (record.get("dynamodb") or {}).get("NewImage") or {}
    analysis = image.get("analysis")
    if not analysis:
        return None
    value = TypeDeserializer().deserialize(analysis)
    return value.get("desk") if isinstance(value, dict) else None


def recipients(record: dict[str, Any], links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    change = change_of(record)
    if change is None or change.report_type == "planned_leave":
        return []  # leave is re-crewed weeks ahead, nothing urgent
    allowed = [link for link in links if link.get("notifications") and link.get("username")]
    if change.after == "SUBMITTED":
        return [link for link in allowed if "approver" in (link.get("roles") or [])]
    if change.after == "OPTIONS_READY" and change.before in ("REPORTED", "SUBMITTED"):
        desk = desk_of(record)
        return [
            link
            for link in allowed
            if desk and "controller" in (link.get("roles") or []) and link.get("desk") == desk
        ]
    return []


def rated(record: dict[str, Any]) -> str | None:
    """Severity level (high/medium/low), or None if the record has no usable analysis."""
    from boto3.dynamodb.types import TypeDeserializer

    from crewops.analysis.severity import severity

    image = (record.get("dynamodb") or {}).get("NewImage") or {}
    try:
        analysis = TypeDeserializer().deserialize(image["analysis"])
        report = TypeDeserializer().deserialize(image["report"])
        today = datetime.now(UTC).date()
        return severity(analysis, str(report.get("reported_utc") or ""), today)["level"]
    except Exception:
        return None


def announcement(record: dict[str, Any]) -> str | None:
    """Spoken alert text. High/medium severity is said up front."""
    from boto3.dynamodb.types import TypeDeserializer

    from crewops.alerts import spoken
    from crewops.analysis.severity import spoken_level
    from crewops.domain import Report
    from crewops.tools.port import describe_report

    change = change_of(record)
    if change is None:
        return None
    image = (record.get("dynamodb") or {}).get("NewImage") or {}
    try:
        report = TypeDeserializer().deserialize(image["report"])
        what = describe_report(Report.from_dict(report))
    except Exception:
        what = f"{change.report_type.replace('_', ' ')}, {change.disruption_id}"
    if change.after == "SUBMITTED":
        text = f"CrewOps: a plan for {what} is waiting for your approval."
    elif change.before == "SUBMITTED":
        text = f"CrewOps: the plan for {what} was sent back to your desk."
    else:
        text = f"CrewOps: new on your desk. {what}. Open CrewOps to plan the cover."
    level = rated(record)
    if level in ("high", "medium") and change.after == "OPTIONS_READY":
        text = text.replace("CrewOps:", f"CrewOps, {spoken_level(level).lower()}:", 1)
    return spoken(text)


def current(links: list[dict[str, Any]], profile: Any) -> list[dict[str, Any]]:
    """Refresh roles and desk from the profile, since the link only has what they were at
    the person's last request. Links for people who left the team are dropped."""
    out = []
    for link in links:
        found = profile(link.get("username") or "")
        if not found:
            continue
        out.append(link | {"roles": list(found.get("roles") or []), "desk": found.get("desk")})
    return out


def process(
    records: list[dict[str, Any]],
    links: list[dict[str, Any]],
    send: Any,
    now: datetime,
) -> int:
    """``send(link, now, text)`` notifies one account. Returns the number sent."""
    sent = 0
    for record in records:
        text = announcement(record)
        for link in recipients(record, links):
            try:
                send(link, now, text)
                sent += 1
            except Exception:
                log.exception("Alexa notification to %s failed", link.get("username"))
    return sent


def notify(records: list[dict[str, Any]], settings: Any) -> int:
    import boto3

    from crewops.alexa.proactive import MESSAGING_SCOPE, Tokens, message_alert, message_skill
    from crewops.alexa.proactive import send as send_event
    from crewops.store.alexa import links
    from crewops.store.ops_store import table_for
    from crewops.store.users import get_profile

    table = table_for(settings)
    found = current(links(table), lambda u: get_profile(table, u))
    if not any(recipients(r, found) for r in records):
        return 0  # don't hit SSM when nobody is linked
    ssm = boto3.Session(region_name=settings.aws_region).client("ssm")
    prefix = settings.alexa_parameter_prefix
    client_id = ssm.get_parameter(Name=f"{prefix}/client-id")["Parameter"]["Value"]
    secret = ssm.get_parameter(Name=f"{prefix}/client-secret", WithDecryption=True)
    tokens = Tokens(client_id, secret["Parameter"]["Value"])
    messaging = Tokens(client_id, secret["Parameter"]["Value"], scope=MESSAGING_SCOPE)

    def send(link: dict[str, Any], now: datetime, text: str | None) -> None:
        send_event(
            tokens,
            link["api_endpoint"],
            message_alert(link["alexa_user_id"], now),
            settings.alexa_events_stage,
        )
        log.info("Alexa notification sent to %s", link["username"])
        if text:
            # proactive events can't carry speech, so the skill turns this into a reminder
            try:
                message_skill(
                    messaging,
                    link["api_endpoint"],
                    link["alexa_user_id"],
                    {"announce": text, "locale": link.get("locale") or "en-IN"},
                )
            except Exception:
                log.exception("Alexa announcement to %s failed", link["username"])

    return process(records, found, send, datetime.now(UTC))
