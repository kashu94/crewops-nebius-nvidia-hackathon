"""Lambda handler for disruption status changes on the table's stream.

The event source mapping already filters to disruption items. Here we keep the records
whose status actually changed and publish each to the console channel. Changes that need
someone also go out as a role alert, and an analysed disruption alerts every team it names.

Console updates are best effort. If one fails the console catches up on its next read. A
failed alert is reported as a batch item failure so Lambda retries from that record. That
can send a console event twice, which is harmless since it only makes the console re-read,
and an alert that went out before the failure may also be sent twice.
"""

from __future__ import annotations

import logging
from typing import Any

from crewops.notify.changes import (
    CONSOLE_CHANNEL,
    alert_for,
    change_of,
    console_event,
    team_alerts,
)

log = logging.getLogger()
log.setLevel(logging.INFO)


def process(
    records: list[dict[str, Any]],
    events: Any,
    alerts: Any,
    stage: str,
    escalate: Any = None,
) -> dict[str, Any]:
    """Any of ``events``, ``alerts`` and ``escalate`` may be None when that channel is off."""
    changes = [(r, change_of(r)) for r in records]
    if escalate is not None:
        for _, change in changes:
            if change is None:
                continue
            try:
                escalate(change)
            except Exception:
                # the desk still gets its alerts, we only lose the escalation
                log.exception("escalation check for %s not scheduled", change.disruption_id)
    if events is not None:
        payload = [console_event(c) for _, c in changes if c is not None]
        if payload:
            try:
                events.publish(CONSOLE_CHANNEL, payload)
            except Exception:
                log.exception("console update failed for %d change(s)", len(payload))
    for record, change in changes:
        if change is None or alerts is None:
            continue
        role_alert = alert_for(change, stage)
        try:
            outgoing = [*([role_alert] if role_alert else []), *team_alerts(record, change, stage)]
            for alert in outgoing:
                alerts.publish(alert)
                log.info("alert %s sent to %s", change.disruption_id, alert.role)
        except Exception:
            sequence = record["dynamodb"]["SequenceNumber"]
            log.exception("alert for %s failed; it will be retried", change.disruption_id)
            return {"batchItemFailures": [{"itemIdentifier": sequence}]}
    return {"batchItemFailures": []}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    import boto3

    from crewops.config import Settings
    from crewops.notify.channels import AlertPublisher, EventPublisher

    settings = Settings.from_env()
    session = boto3.Session(region_name=settings.aws_region)
    events = (
        EventPublisher(settings.events_http_domain, settings.aws_region, session.get_credentials())
        if settings.events_http_domain
        else None
    )
    alerts = (
        AlertPublisher(session.client("sns"), settings.alerts_topic_arn)
        if settings.alerts_topic_arn
        else None
    )
    escalate = None
    if settings.escalation_function_arn and settings.escalation_role_arn:
        from datetime import UTC, datetime

        from crewops.notify.escalation import schedule_check

        scheduler = session.client("scheduler")

        def escalate(change: Any) -> None:
            schedule_check(
                scheduler,
                change,
                prefix=settings.prefix,
                function_arn=settings.escalation_function_arn,
                role_arn=settings.escalation_role_arn,
                minutes=settings.escalate_after_minutes,
                now=datetime.now(UTC),
            )

    records = event.get("Records", [])
    result = process(records, events, alerts, settings.stage, escalate)
    # A failed record is retried with everything after it. Chimes and pushes go only for the
    # records before it now, or the retry would announce those twice.
    failed = {f["itemIdentifier"] for f in result["batchItemFailures"]}
    if failed:
        cut = next(i for i, r in enumerate(records) if r["dynamodb"]["SequenceNumber"] in failed)
        records = records[:cut]
    if settings.alexa_skill_id:
        # never fails the batch, see crewops.alexa.notifier
        try:
            from crewops.alexa.notifier import notify

            notify(records, settings)
        except Exception:
            log.exception("Alexa notifications failed")
    if settings.push_enabled:
        try:
            from crewops.notify.push_alerts import notify as push_notify

            push_notify(records, settings)
        except Exception:
            log.exception("desktop alerts failed")
    return result
