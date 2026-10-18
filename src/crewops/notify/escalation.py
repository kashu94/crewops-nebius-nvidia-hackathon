"""Raises a disruption with the duty supervisor when nobody touches it on a desk.

When a disruption lands on a desk (analysed, or its plan sent back) the notifier creates a
one-time EventBridge schedule ``CREWOPS_ESCALATE_AFTER_MINUTES`` out. The schedule deletes
itself after it runs. When it fires, this Lambda checks again. If the disruption is still
sitting there with nothing done since, it emails the "duty_supervisor" audience and writes
an ESCALATED audit event, which the desk and room speakers announce. Otherwise it was
handled and we do nothing.

There is no polling. A schedule only exists between a disruption reaching a desk and its
check running.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from crewops.notify.changes import Alert, Change

log = logging.getLogger()
log.setLevel(logging.INFO)

AUDIENCE = "duty_supervisor"
ACTOR = "system:escalation"
STARTS_CLOCK = (("REPORTED", "OPTIONS_READY"), ("SUBMITTED", "OPTIONS_READY"))
"""Changes that leave a disruption waiting on a desk."""


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def schedule_name(prefix: str, disruption_id: str, at: datetime) -> str:
    """EventBridge caps names at 64 chars with a restricted charset. The timestamp and id
    hash always fit, so the readable id is truncated rather than the unique part."""
    digest = hashlib.sha256(disruption_id.encode()).hexdigest()[:10]
    fixed = f"{prefix}-esc-{int(at.timestamp())}-{digest}"
    room = 64 - len(fixed) - 1
    safe = re.sub(r"[^0-9A-Za-z_.-]", "-", disruption_id)[: max(room, 0)]
    return f"{fixed}-{safe}" if safe else fixed


def schedule_check(
    scheduler: Any,
    change: Change,
    *,
    prefix: str,
    function_arn: str,
    role_arn: str,
    minutes: int,
    now: datetime,
) -> str | None:
    if (change.before, change.after) not in STARTS_CLOCK:
        return None
    if change.report_type == "planned_leave":
        return None  # re-crewed weeks ahead, a short clock makes no sense
    name = schedule_name(prefix, change.disruption_id, now)
    when = now + timedelta(minutes=minutes)
    scheduler.create_schedule(
        Name=name,
        ScheduleExpression=f"at({when.astimezone(UTC):%Y-%m-%dT%H:%M:%S})",
        ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow={"Mode": "OFF"},
        ActionAfterCompletion="DELETE",
        Target={
            "Arn": function_arn,
            "RoleArn": role_arn,
            "Input": json.dumps({"disruption_id": change.disruption_id, "since": _iso(now)}),
        },
    )
    return name


def check(
    table: Any,
    disruption_id: str,
    since: str,
    minutes: int,
    publish: Any,
    now: datetime,
    describe: Any,
    aircraft: Any = lambda _p: None,
) -> str:
    """Returns "escalated", "handled" or "missing"."""
    from crewops.alerts import STAGE
    from crewops.domain import Report
    from crewops.domain.desks import owning_desks
    from crewops.store import plans as plan_store
    from crewops.store.items import from_dynamo
    from crewops.store.reports import get_disruption

    item = get_disruption(table, disruption_id)
    if item is None:
        return "missing"
    if item.get("status") != "OPTIONS_READY":
        return "handled"  # a plan was submitted, or it was resolved
    trail = plan_store.audit_trail(table, disruption_id)
    # Any staged action after `since` (submit then send-back, an earlier escalation) means
    # someone acted, or a newer check is already queued.
    if any(
        str(e.get("at") or "") > since and STAGE.get(str(e.get("action")), 0) >= 1 for e in trail
    ):
        return "handled"
    analysis = from_dynamo(item.get("analysis") or {})
    desks = owning_desks(analysis, aircraft) if analysis else []
    on_desk = f"{desks[0].controller}'s desk ({desks[0].desk_id})" if desks else "a desk"
    what = describe(Report.from_dict(from_dynamo(item["report"])))
    # Write the audit event before emailing. If SNS fails and Lambda retries, the retry sees
    # ESCALATED and stops, so the supervisor never gets two emails.
    at = _iso(now)
    table.put_item(
        Item={
            "pk": f"DISRUPTION#{disruption_id}",
            "sk": f"AUDIT#{at}#ESCALATED#",
            "entity": "audit_event",
            "disruption_id": disruption_id,
            "action": "ESCALATED",
            "actor": ACTOR,
            "at": at,
            "reason": f"untouched for {minutes} minutes on {on_desk}",
        }
    )
    publish(
        Alert(
            AUDIENCE,
            f"CrewOps: escalated, {disruption_id} untouched for {minutes} minutes"[:100],
            f"Disruption {disruption_id} ({what}) has waited {minutes} minutes on {on_desk} "
            "with no plan submitted. Check the desk is staffed, or plan it from another desk."
            "\n\nYou receive this because a CrewOps administrator subscribed this address to "
            "duty supervisor escalations.",
        )
    )
    return "escalated"


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    import boto3

    from crewops.config import Settings
    from crewops.notify.channels import AlertPublisher
    from crewops.store.ops_store import table_for
    from crewops.tools.port import describe_report

    settings = Settings.from_env()
    table = table_for(settings)
    sns = boto3.Session(region_name=settings.aws_region).client("sns")
    publisher = AlertPublisher(sns, settings.alerts_topic_arn or "")
    outcome = check(
        table,
        str(event["disruption_id"]),
        str(event["since"]),
        settings.escalate_after_minutes,
        publisher.publish,
        datetime.now(UTC),
        describe_report,
    )
    log.info("escalation check for %s: %s", event["disruption_id"], outcome)
    return {"outcome": outcome}
