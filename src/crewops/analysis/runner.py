"""Load the operation, run ``analyse`` on stored disruptions and store each result once.

Used by the stream Lambda for new reports and by the ``run`` command for older ones or ones
whose analysis failed.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from crewops.analysis.disruption import analyse
from crewops.domain import DataError, GateSlot, Report, ReportType
from crewops.domain.desks import assign_desk
from crewops.domain.reports import ABSENCE_TYPES, crew_out_of
from crewops.engine import build_world
from crewops.engine.world import World
from crewops.ingestion.seed import SeedData
from crewops.store.items import from_dynamo
from crewops.store.ops_store import OpsStore
from crewops.store.plans import PLAN_COMMITTED, plans_with_status
from crewops.store.reports import (
    STATUS_OPTIONS_READY,
    STATUS_REPORTED,
    AnalysisOutcome,
    disruptions_with_status,
    get_disruption,
    store_analysis,
)

log = logging.getLogger(__name__)

OPEN_STATUSES = (STATUS_REPORTED, STATUS_OPTIONS_READY, "SUBMITTED", "RESOLVING")
"""Still being worked; these count against a controller's desk."""
UNAVAILABLE_STATUSES = (*OPEN_STATUSES, "COMMITTED", "CLOSED")
"""Disruptions whose crew are out on their dates. Committed and closed ones count too, since
the person is still sick (or still lacks the certificate) whatever was done about the seats."""


def absences_from(items: list[dict[str, Any]]) -> dict[tuple[str, date], datetime]:
    """(crew id, date) -> start time of a part-day absence. A whole-day absence for the same
    person and date wins, including any day of a longer absence, not only its first."""
    since: dict[tuple[str, date], datetime | None] = {}
    for item in items:
        try:
            report = Report.from_dict(from_dynamo(item["report"]))
        except (DataError, KeyError, TypeError):
            continue
        if report.report_type not in ABSENCE_TYPES:
            continue
        d = report.details
        first, last = date.fromisoformat(d["from_date"]), date.fromisoformat(d["to_date"])
        at = (
            datetime.fromisoformat(d["from_utc"].replace("Z", "+00:00"))
            if d.get("from_utc")
            else None
        )
        days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
        for crew_id in crew_out_of(report.report_type, d):
            for day in days:
                key = (crew_id, day)
                part = at if day == first else None
                if part is None or key in since and since[key] is None:
                    since[key] = None
                else:
                    since[key] = min(part, since.get(key) or part)
    return {k: v for k, v in since.items() if v is not None}


def cancelled_flights(table: Any) -> frozenset[str]:
    """Flights on trips an approved plan cancelled."""
    return frozenset(
        f
        for plan in plans_with_status(table, PLAN_COMMITTED)
        for a in plan.get("assignments", [])
        if a.get("strategy") == "cancel"
        for f in a.get("flights", [])
    )


def unavailable_crew(
    world: World, items: list[dict[str, Any]]
) -> dict[str, tuple[tuple[date, str], ...]]:
    """Crew id -> (date, report id) for every open report that takes someone out."""
    duty_dates = sorted({d.date for days in world.duty_days.values() for d in days})
    out: dict[str, list[tuple[date, str]]] = {}
    for item in items:
        try:
            report = Report.from_dict(from_dynamo(item["report"]))
        except (DataError, KeyError, TypeError):
            log.warning("skipping unreadable disruption %s", item.get("disruption_id"))
            continue
        d = report.details
        if report.report_type in ABSENCE_TYPES:
            start, end = date.fromisoformat(d["from_date"]), date.fromisoformat(d["to_date"])
            dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        elif report.report_type is ReportType.CERTIFICATION_LAPSE:
            valid_to = date.fromisoformat(d["valid_to"])
            dates = [day for day in duty_dates if day > valid_to]
        else:
            continue
        for crew_id in crew_out_of(report.report_type, d):
            out.setdefault(crew_id, []).extend((day, report.report_id) for day in dates)
    return {crew: tuple(sorted(set(days))) for crew, days in out.items()}


ALL_STATUSES = UNAVAILABLE_STATUSES


def next_desk(table: Any) -> str:
    """Desk for the next disruption. Fewest open first, then fewest overall so ties take
    turns, then desk order."""
    open_counts: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    for status in ALL_STATUSES:
        for item in disruptions_with_status(table, status):
            desk_id = (from_dynamo(item.get("analysis") or {})).get("desk")
            if desk_id:
                totals[desk_id] += 1
                if status in OPEN_STATUSES:
                    open_counts[desk_id] += 1
    return assign_desk(open_counts, totals).desk_id


def seat_keys(analysis: dict[str, Any]) -> set[tuple[str, str, str]]:
    """(trip, role, crew member out) for each seat the analysis needs covered."""
    return {
        (s["pairing_id"], s["role"], s["crew_out"])
        for s in analysis.get("seats") or []
        if s.get("crew_out") and s.get("pairing_id") and s.get("role")
    }


def same_seat_owner(table: Any, analysis: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Open disruptions already needing one of these seats, and their desk.

    One person out on two days of the same trip, reported twice, is still one seat (the cover
    flies the whole trip), so the second report goes to the desk that has the first, the way
    crew control would add it to the open case."""
    mine = seat_keys(analysis)
    if not mine:
        return None, []
    desk, ids = None, []
    for status in OPEN_STATUSES:
        for item in disruptions_with_status(table, status):
            theirs = from_dynamo(item.get("analysis") or {})
            if mine & seat_keys(theirs):
                ids.append(str(item["disruption_id"]))
                desk = desk or theirs.get("desk")
    return desk, sorted(ids)


def unavailable_items(table: Any) -> list[dict[str, Any]]:
    return [
        item for status in UNAVAILABLE_STATUSES for item in disruptions_with_status(table, status)
    ]


def open_disruptions(table: Any) -> list[dict[str, Any]]:
    """Every disruption still being worked, oldest status first."""
    return [item for status in OPEN_STATUSES for item in disruptions_with_status(table, status)]


def operation_now() -> datetime | None:
    """Wall-clock now when deployed (the stacks set ``CREWOPS_REAL_TIME=1``), so earlier
    flights count as flown. Otherwise None, meaning the seed's own fixed moment, which keeps
    tests, the evaluation and local runs repeatable."""
    return datetime.now(UTC) if os.environ.get("CREWOPS_REAL_TIME") == "1" else None


@dataclass(frozen=True, slots=True)
class Operation:
    """The operation loaded once and reused for every disruption in a batch."""

    world: World
    gates: tuple[GateSlot, ...]
    seed: SeedData | None = None
    """The records the world was built from, for tools that show them as they are."""

    @classmethod
    def load(cls, table: Any, now: datetime | None = None) -> Operation:
        """The roster from the table, minus everyone open disruptions have taken out.

        Someone reported sick in one disruption must not be offered as cover in another, so
        crew named in absence and certification-lapse reports are marked unavailable on
        those dates. Flights an approved plan cancelled are marked cancelled so nobody is
        rostered or positioned on them.
        """
        seed = OpsStore(table).load_seed()
        world = build_world(seed, now or operation_now())
        items = unavailable_items(table)
        return cls(
            world=replace(
                world,
                unavailable=unavailable_crew(world, items),
                unavailable_from=absences_from(items),
                cancelled=cancelled_flights(table),
            ),
            gates=seed.gates,
            seed=seed,
        )


@dataclass(frozen=True, slots=True)
class RunResult:
    disruption_id: str
    outcome: str
    """stored, already_analysed, not_reported (status moved on) or missing."""
    summary: str = ""


def _on_call_then(world: World, report: Report) -> bool:
    """Is anyone this report takes out on reserve in its period? Crew control needs to know,
    the pool at that base is one smaller."""
    d = report.details
    start, end = date.fromisoformat(d["from_date"]), date.fromisoformat(d["to_date"])
    return any(
        start <= day <= end
        for crew_id in crew_out_of(report.report_type, d)
        for duty in world.reserves.get(crew_id, ())
        for day in duty.dates
    )


def analyse_item(table: Any, item: dict[str, Any], operation: Operation) -> RunResult:
    """Analyse one disruption item (as stored, or as a stream image) and store the result."""
    disruption_id = item["disruption_id"]
    if item.get("status") != STATUS_REPORTED:
        return RunResult(disruption_id, "not_reported")
    report = Report.from_dict(from_dynamo(item["report"]))
    contacts = operation.seed.contacts if operation.seed else ()
    analysis = analyse(operation.world, report, operation.gates, contacts)
    doc = analysis.to_dict()
    record_only = None
    if (
        report.report_type in ABSENCE_TYPES
        and not (doc.get("seats") or doc.get("delays"))
        and not _on_call_then(operation.world, report)
    ):
        # off on days with no flying: keep the record so they aren't called out, but there's
        # nothing to plan so no desk gets it
        record_only = "Recorded: no flights to cover on these dates."
        doc["handling"] = {
            "kind": "recorded",
            "text": "Recorded: no flights to cover. Not called out on these days.",
        }
    else:
        owner, same = same_seat_owner(table, doc)
        doc["desk"] = owner or next_desk(table)
        if same:
            doc["notes"] = [
                *doc.get("notes", []),
                f"The same seat is already open in {', '.join(same)}: one cover fills both, "
                "so this goes to the same desk.",
            ]
    outcome = store_analysis(table, disruption_id, doc, datetime.now(UTC), record_only)
    if outcome is AnalysisOutcome.STORED:
        log.info("analysed %s: %s", disruption_id, analysis.summary)
    return RunResult(disruption_id, str(outcome), analysis.summary)


def analyse_by_id(table: Any, disruption_id: str, operation: Operation) -> RunResult:
    item = get_disruption(table, disruption_id)
    if item is None:
        return RunResult(disruption_id, "missing")
    return analyse_item(table, item, operation)
