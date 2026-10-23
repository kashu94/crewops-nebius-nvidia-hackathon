"""Spoken alerts, one or two sentences on what just happened to each disruption.

The desk speaker, the "welcome back" summary and Alexa notifications all use what's built
here. It comes from the stored analysis and the audit trail, not a model, so every figure
in an alert is one the engine produced.

An alert is a disruption's latest event (reported, analysed, plan submitted, sent back,
approved, closed). The alert id includes the event, so the next event on the same
disruption counts as new for a listener that already heard the last one.

Scope is who it's for: a controller desk (``desk-1``...), ``approvals``, or the whole
``room``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Any

from crewops.analysis.severity import severity, spoken_level
from crewops.domain import DataError, Report, ReportType
from crewops.domain.desks import DESKS, owning_desks

ROOM = "room"
APPROVALS = "approvals"
SCOPES = (ROOM, APPROVALS, *(d.desk_id for d in DESKS))

SENT_BACK = ("REJECTED", "WITHDRAWN", "STALE")
"""Audit actions that return a plan to its desk."""
FOR_DESK = (
    "OPTIONS_READY",
    "ESCALATED",
    *SENT_BACK,
    "RESOLUTION_RETURNED",
    "COMMITTED",
    "CLOSED",
    "REVOKED",
)
"""Events a desk speaker announces. ANALYSIS_FAILED isn't here because such a report has no
desk yet; only the room speaker hears about it."""
FOR_APPROVALS = ("SUBMITTED", "RESOLUTION_SUBMITTED")

STRATEGY = {
    "reserve_callout": "a reserve callout",
    "day_off_callout": "a day-off callout",
    "positioning": "positioned from another base",
    "command_upgrade": "a command-qualified first officer",
    "right_seat": "a captain in the right seat",
}

_ID = re.compile(r"\b([CP])-(\d{4})\b")
_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_RUPEES = re.compile(r"₹([\d,]+)")


def spoken(text: str) -> str:
    """Rewrite for TTS: "C-1042" -> "C 1042", ISO dates -> "13 October", "₹23,090" ->
    "23,090 rupees"."""
    text = _ID.sub(r"\1 \2", text)
    text = _DATE.sub(_spoken_date, text)
    return _RUPEES.sub(r"\1 rupees", text)


def _spoken_date(match: re.Match[str]) -> str:
    """Free text (a reject reason, a model's answer) can hold something date-shaped that is
    no date, like "2026-02-30": leave it as written."""
    try:
        return _day(date(*map(int, match.groups())))
    except ValueError:
        return match.group(0)


def _day(d: date | datetime) -> str:
    return f"{d.day} {d:%B}"  # %-d isn't portable


def _rupees(value: Any) -> str:
    return f"₹{int(value):,}"


def _cover(analysis: Mapping[str, Any]) -> str:
    """Recommendation in a sentence, or what happens instead."""
    handling = analysis.get("handling") or {}
    seats = analysis.get("seats") or []
    picks = [s for s in seats if s.get("status") == "options" and s.get("recommended")]
    if picks:
        s, rec = picks[0], picks[0]["recommended"]
        how = STRATEGY.get(rec.get("strategy", ""), "")
        name = f"{rec.get('name') or rec.get('crew_id')} ({rec.get('crew_id')})"
        text = (
            f"Recommended cover for {s['pairing_id']} {s['role']}: {name}"
            f"{', ' + how if how else ''}, {_rupees(rec.get('cost_inr', 0))}."
        )
        if len(seats) > 1:
            text += f" {len(seats)} seats in all."
        return text
    if handling.get("kind") == "internal":
        return "Nothing to re-crew; mark it resolved once handled."
    if seats:
        return "No legal cover: crew control decides with operations."
    return ""


def brief(
    item: Mapping[str, Any],
    analysis: Mapping[str, Any],
    report: Report,
    latest: Mapping[str, Any],
    desks: list[Any],
    what: Callable[[Report], str],
) -> dict[str, Any]:
    action = str(latest.get("action") or item.get("status") or "")
    happened = what(report)
    desk = desks[0] if desks else None
    on_desk = f" On {desk.controller}'s desk." if desk else ""
    reason = str(latest.get("reason") or "").strip().rstrip(".")
    if action == "REPORTED":
        title, text = "New disruption", f"New disruption: {happened}. It is being analysed."
    elif action == "ANALYSIS_FAILED":
        title, text = "Needs a look", f"{happened} could not be analysed; check it in the console."
    elif action == "OPTIONS_READY":
        title = f"New on {desk.desk_id.replace('-', ' ').title()}" if desk else "Ready"
        text = f"{happened}. {_cover(analysis)}".strip() + on_desk
    elif action == "ESCALATED":
        title = "Escalated"
        text = (
            f"Still waiting: {happened}. {_cover(analysis)}".strip()
            + f"{on_desk} The duty supervisor has been told."
        )
    elif action in SENT_BACK:
        title = "Plan sent back"
        text = f"The plan for {happened} was sent back" + (f": {reason}." if reason else ".")
        text += on_desk
    elif action == "RESOLUTION_SUBMITTED":
        title = "Waiting for sign-off"
        text = f"{happened} was handled by {latest.get('actor')}" + (
            f": {reason}." if reason else "."
        )
        text += " It needs an approver's sign-off."
    elif action == "RESOLUTION_RETURNED":
        title = "Sent back"
        text = f"The resolution of {happened} was sent back" + (f": {reason}." if reason else ".")
        text += on_desk
    elif action == "SUBMITTED":
        title = "Waiting for approval"
        text = f"A plan for {happened} is waiting for approval, from {latest.get('actor')}."
    elif action in ("APPROVED", "COMMITTED"):
        title, text = "Approved", f"The plan for {happened} was approved; the roster is updated."
    elif action == "CLOSED":
        title = "Closed"
        text = f"{happened} was closed" + (f": {reason}." if reason else ".")
    elif action == "REVOKED":
        title = "Revoked"
        text = f"{happened} was revoked by an admin" + (f": {reason}." if reason else ".")
    else:
        title, text = action.replace("_", " ").title(), f"{happened}: {action.lower()}."
    rated = (
        severity(analysis, report.reported_utc.date().isoformat(), datetime.now(UTC).date())
        if analysis
        else None
    )
    if rated and action in ("OPTIONS_READY", "ESCALATED") and rated["level"] != "low":
        # lead with severity so the listener knows whether to drop everything
        text = f"{spoken_level(rated['level'])}. {text}"
    at = str(latest.get("at") or "")
    return {
        "id": f"{item['disruption_id']}:{action}:{at}",
        "disruption_id": item["disruption_id"],
        "event": action,
        "status": item.get("status"),
        "at": at,
        "title": title,
        "text": text,
        "speech": spoken(text),
        "desk": {"desk_id": desk.desk_id, "controller": desk.controller} if desk else None,
        # older records can touch two desks' aircraft
        "desks": [d.desk_id for d in desks],
        "severity": rated["level"] if rated else None,
    }


STAGE = {
    "REPORTED": 0,
    "ANALYSIS_FAILED": 1,
    "OPTIONS_READY": 1,
    "ESCALATED": 1,
    "SUBMITTED": 2,
    "RESOLUTION_SUBMITTED": 2,
    "RESOLUTION_RETURNED": 3,
    **dict.fromkeys(SENT_BACK, 3),
    "APPROVED": 3,
    "COMMITTED": 4,
    "CLOSED": 4,
    "REVOKED": 4,
}
"""Lifecycle order of audit actions. Breaks ties when two events share a timestamp (a plan
submitted and rejected in the same second)."""


def _order(event: Mapping[str, Any]) -> tuple[str, int]:
    return str(event.get("at") or ""), STAGE.get(str(event.get("action")), 0)


def for_scope(alert: Mapping[str, Any], scope: str) -> bool:
    if scope == ROOM:
        return True
    if scope == APPROVALS:
        return alert["event"] in FOR_APPROVALS
    return scope in (alert.get("desks") or []) and alert["event"] in FOR_DESK


def alerts(
    items: Iterable[Mapping[str, Any]],
    audit: Callable[[str], list[Mapping[str, Any]]],
    aircraft_of_pairing: Callable[[str], str | None],
    what: Callable[[Report], str],
    scope: str = ROOM,
    since: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Latest event per disruption for ``scope``, newer than ``since`` (ISO time), newest
    first."""
    from crewops.store.items import from_dynamo

    if scope not in SCOPES:
        raise ValueError(f"scope is one of {', '.join(SCOPES)}")
    out = []
    for item in items:
        try:
            report = Report.from_dict(from_dynamo(item["report"]))
        except (DataError, KeyError, TypeError):
            continue  # the console still shows it
        if report.report_type is ReportType.PLANNED_LEAVE:
            continue  # re-crewed weeks ahead, nothing to announce
        trail = audit(str(item["disruption_id"]))
        latest = max(trail, key=_order) if trail else {}
        analysis = from_dynamo(item.get("analysis") or {})
        desks = owning_desks(analysis, aircraft_of_pairing) if analysis else []
        alert = brief(item, analysis, report, latest, desks, what)
        if for_scope(alert, scope) and (since is None or alert["at"] > since):
            out.append(alert)
    out.sort(key=lambda a: a["at"], reverse=True)
    return out[:limit]


def welcome_speech(name: str, alerts_since: list[Mapping[str, Any]], since: datetime) -> str:
    """Short spoken summary of what arrived while someone was away."""
    if not alerts_since:
        return f"Welcome back, {name}. Nothing new came to you since {_day(since)} {since:%H:%M}Z."
    first = alerts_since[-1]  # oldest first reads best
    more = len(alerts_since) - 1
    text = (
        f"Welcome back, {name}. While you were away, {len(alerts_since)} "
        f"update{'s' if len(alerts_since) != 1 else ''} came to you. {first['speech']}"
    )
    if more:
        text += f" And {more} more on screen."
    return text
