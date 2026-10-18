"""Turns a stream record into what people see when a disruption changes status.

Only disruption status changes matter here. Each one becomes a console event, and some
also become an alert for a role.

=========================  ===================  ============================================
From -> to                 Alert to             Why
=========================  ===================  ============================================
REPORTED -> OPTIONS_READY  controller           a disruption is analysed and needs a plan
OPTIONS_READY -> SUBMITTED approver             a plan is waiting for approval
SUBMITTED -> OPTIONS_READY controller           the plan was rejected, withdrawn or went
                                                stale; the disruption needs another plan
=========================  ===================  ============================================

When a disruption is analysed (REPORTED -> OPTIONS_READY), each operational team its
analysis names is also alerted (see ``crewops.analysis.notices``), e.g. flight information
to update the screens, gate control to move gates. One email per team with all its notices.

Events and alerts carry the disruption id, report type and statuses. Team alerts also carry
the flights, gates, stations and times the team acts on. Crew names and ids are left out on
purpose; people read those in CrewOps after signing in, where role checks apply.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crewops.store.plans import STATUS_SUBMITTED
from crewops.store.reports import STATUS_OPTIONS_READY, STATUS_REPORTED

CONSOLE_CHANNEL = "/ops/disruptions"


@dataclass(frozen=True, slots=True)
class Change:
    disruption_id: str
    before: str | None
    """None when the disruption was just reported."""
    after: str
    report_type: str


@dataclass(frozen=True, slots=True)
class Alert:
    role: str
    subject: str
    message: str


def _s(image: dict[str, Any], key: str) -> str | None:
    value = image.get(key) or {}
    return value.get("S") if isinstance(value, dict) else None


def change_of(record: dict[str, Any]) -> Change | None:
    """None unless the record is a disruption whose status actually changed."""
    if record.get("eventName") not in ("INSERT", "MODIFY"):
        return None
    data = record.get("dynamodb") or {}
    new, old = data.get("NewImage") or {}, data.get("OldImage") or {}
    if _s(new, "entity") != "disruption":
        return None
    after, before = _s(new, "status"), _s(old, "status")
    disruption_id = _s(new, "disruption_id")
    if not after or not disruption_id or after == before:
        return None
    report = (new.get("report") or {}).get("M") or {}
    return Change(disruption_id, before, after, _s(report, "type") or "unknown")


def console_event(change: Change) -> dict[str, Any]:
    return {
        "kind": "disruption_status",
        "disruption_id": change.disruption_id,
        "status": change.after,
        "previous": change.before,
        "report_type": change.report_type,
    }


def team_alerts(record: dict[str, Any], change: Change, stage: str) -> list[Alert]:
    """One alert per team the analysis names. Only fires on REPORTED -> OPTIONS_READY."""
    if not (change.before == STATUS_REPORTED and change.after == STATUS_OPTIONS_READY):
        return []
    if change.report_type == "planned_leave":
        return []  # its trips are re-crewed in advance; no team needs telling yet
    from boto3.dynamodb.types import TypeDeserializer

    image = (record.get("dynamodb") or {}).get("NewImage") or {}
    raw = image.get("analysis")
    if not raw:
        return []
    analysis = TypeDeserializer().deserialize(raw)
    by_team: dict[str, list[dict[str, Any]]] = {}
    for n in analysis.get("notices") or []:
        if n.get("channel") == "email" and n.get("team"):
            by_team.setdefault(str(n["team"]), []).append(n)
    what = change.report_type.replace("_", " ")
    out = []
    for team, notices in by_team.items():
        title = str(notices[0].get("team_title") or team)
        lines = [
            f"- {n.get('scope')}: {n.get('message')} (why you: {n.get('why')})" for n in notices
        ]
        body = (
            f"Disruption {change.disruption_id} ({what}) needs {title.lower()}:\n\n"
            + "\n".join(lines)
            + "\n\nSign in to CrewOps for the full record. You receive this because a CrewOps "
            f"administrator subscribed this address to {title.lower()} alerts."
        )
        subject = f"CrewOps {stage}: {title}, {what} {change.disruption_id}"
        out.append(Alert(team, subject[:100], body))
    return out


def alert_for(change: Change, stage: str) -> Alert | None:
    what = change.report_type.replace("_", " ")
    ref = f"Disruption {change.disruption_id} ({what})"
    footer = (
        "\n\nSign in to CrewOps to see the details. You receive this because a CrewOps "
        "administrator subscribed this address to {role} alerts."
    )
    if change.before == STATUS_REPORTED and change.after == STATUS_OPTIONS_READY:
        role, subject = "controller", f"options ready for {change.disruption_id}"
        body = f"{ref} has been analysed and its cover options are ready for a plan."
    elif change.before == STATUS_OPTIONS_READY and change.after == STATUS_SUBMITTED:
        role, subject = "approver", f"plan waiting for approval on {change.disruption_id}"
        body = f"{ref} has a plan waiting for approval by someone other than its submitter."
    elif change.before == STATUS_SUBMITTED and change.after == STATUS_OPTIONS_READY:
        role, subject = "controller", f"plan sent back on {change.disruption_id}"
        body = f"{ref} is back with controllers: its plan was rejected, withdrawn or went stale."
    else:
        return None
    return Alert(role, f"CrewOps {stage}: {subject}"[:100], body + footer.format(role=role))
