"""Turn a report into an analysis a controller can decide from.

``analyse`` takes a validated report and the current operation. The ``Analysis`` lists the
seats that lost their crew, each with its blast radius and ranked legal options, plus a joint
plan when several seats need cover at once so one person is never offered twice. For delays
it says what happens to the duty day and to each rostered crew member, including the trailing
legs that must come off when the day is too long for anyone. Notes cover the rest (nothing to
cover, already started, gate already taken).

No AWS and no model in here. The disruption Lambda stores the result and alerts the
controller; the agent reads it back later to answer questions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from crewops.analysis.gates import (
    REMOTE_STAND,
    GateBoard,
    NextAtGate,
    StandMove,
    close_gate,
    late_departures,
    station_of,
)
from crewops.analysis.notices import LateLeg, notices_for
from crewops.domain import GateSlot, Report, ReportType
from crewops.domain.contacts import Contact
from crewops.engine.impact import (
    DelayEffect,
    JointPlan,
    PairingImpact,
    Seat,
    delay_effect,
    joint_plan,
    pairing_impact,
)
from crewops.engine.options import (
    AlreadyStarted,
    Option,
    OptionsResult,
    TripCancelled,
    find_options,
)
from crewops.engine.world import World

TOP_OPTIONS = 5
"""Ranked options kept per seat; counts of the rest are kept too."""

MAX_SEATS, MAX_DELAYS, MAX_FLIGHTS, MAX_NOTES = 40, 60, 300, 50
"""Caps on what one analysis stores, so it always fits a DynamoDB item (400 KB) however
long a closure or absence runs. Anything beyond a cap is counted, never silently lost."""

STATION_REOPEN_TURNAROUND = timedelta(minutes=30)
"""After a station reopens, the first movements need this long: a flight due to depart or
land in the closure is delayed until reopening plus this turnaround."""


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _inr(amount: int) -> str:
    return f"₹{amount:,}"


def option_dict(o: Option) -> dict[str, Any]:
    return {
        "rank": o.rank,
        "action": o.action,
        "strategy": o.strategy.value,
        "crew_id": o.crew_id,
        "name": o.name,
        "crew_rank": o.crew_rank.value if o.crew_rank else None,
        "cost_inr": o.cost_inr,
        "cost_breakdown": dict(o.cost_breakdown),
        "delay_hours": o.delay_hours,
        "positioning_flight": o.positioning_flight,
    }


@dataclass(frozen=True, slots=True)
class SeatAnalysis:
    seat: Seat
    crew_out: str | None
    """The rostered crew member who can no longer fly it, when there is one."""
    impact: PairingImpact
    options: OptionsResult | None
    """None when the pairing has already started, or was cancelled, and so can no longer
    be re-crewed."""
    note: str = ""
    delay_hours: float = 0.0
    """The schedule the options were judged against: a delayed day needs cover legal for
    the day as it will now run, and approval re-checks against the same."""
    delay_date: date | None = None
    cancelled: bool = False
    """An approved plan cancelled the trip: nothing to re-crew."""

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "pairing_id": self.seat.pairing_id,
            "role": self.seat.role.value,
            "crew_out": self.crew_out,
            "delay_hours": self.delay_hours,
            "delay_date": self.delay_date.isoformat() if self.delay_date else None,
            "impact": {
                "affected_dates": [d.isoformat() for d in self.impact.affected_dates],
                "uncovered_flights": list(self.impact.uncovered_flights),
                "at_risk_flights": list(self.impact.at_risk_flights),
                "passengers": self.impact.passengers,
                "flown_before": list(self.impact.flown_before),
            },
            "note": self.note,
        }
        if self.options is None:
            doc["status"] = "cancelled" if self.cancelled else "already_started"
            return doc
        r = self.options
        doc.update(
            status="options" if r.crew_options else "no_legal_cover",
            recommended=option_dict(r.recommended) if r.recommended else None,
            options=[option_dict(o) for o in r.crew_options[:TOP_OPTIONS]],
            legal_options=len(r.crew_options),
            excluded=len(r.excluded),
            cancellation_cost_inr=r.cancellation.cost_inr,
            cancellation_multiple=r.cancellation_multiple,
            next_tier_premium_inr=r.next_tier_premium_inr,
        )
        return doc


@dataclass(frozen=True, slots=True)
class Analysis:
    disruption_id: str
    report_type: ReportType
    summary: str
    seats: tuple[SeatAnalysis, ...] = ()
    joint: JointPlan | None = None
    delays: tuple[DelayEffect, ...] = ()
    affected_flights: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    late: tuple[LateLeg, ...] = ()
    """Departures that now leave late, each by how much."""
    stands: tuple[StandMove, ...] = ()
    """Departures moved to another gate or a remote stand."""
    next_at_gate: tuple[NextAtGate, ...] = ()
    """The departure due next at each gate a late flight still holds."""
    notices: tuple[dict[str, Any], ...] = ()
    """Who is told, besides crew control, and what (``crewops.analysis.notices``)."""
    handling: dict[str, str] = field(default_factory=dict)
    """How it gets resolved: ``kind`` (recrew, decision or internal) and one sentence."""
    aircraft: tuple[str, ...] = ()
    """Every aircraft it touches."""
    airlines: tuple[str, ...] = ()
    """The airlines those aircraft fly for."""

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "disruption_id": self.disruption_id,
            "report_type": self.report_type.value,
            "summary": self.summary,
            "seats": [s.to_dict() for s in self.seats[:MAX_SEATS]],
            "delays": [_delay_dict(d) for d in self.delays[:MAX_DELAYS]],
            "affected_flights": list(self.affected_flights[:MAX_FLIGHTS]),
            "notes": list(self.notes[:MAX_NOTES]),
            "late_departures": [
                {"flight_id": leg.flight_id, "minutes": leg.minutes}
                for leg in self.late[:MAX_FLIGHTS]
            ],
            "stands": [m.to_dict() for m in self.stands[:MAX_FLIGHTS]],
            "next_at_gate": [n.to_dict() for n in self.next_at_gate[:MAX_FLIGHTS]],
            "notices": list(self.notices[:MAX_NOTES]),
            "handling": dict(self.handling),
            "aircraft": list(self.aircraft),
            "airlines": list(self.airlines),
            "joint_plan": None,
            "omitted": {
                name: n - cap
                for name, n, cap in (
                    ("seats", len(self.seats), MAX_SEATS),
                    ("delays", len(self.delays), MAX_DELAYS),
                    ("affected_flights", len(self.affected_flights), MAX_FLIGHTS),
                    ("notes", len(self.notes), MAX_NOTES),
                    ("late_departures", len(self.late), MAX_FLIGHTS),
                    ("stands", len(self.stands), MAX_FLIGHTS),
                    ("next_at_gate", len(self.next_at_gate), MAX_FLIGHTS),
                    ("notices", len(self.notices), MAX_NOTES),
                )
                if n > cap
            },
        }
        if self.joint is not None:
            doc["joint_plan"] = {
                "assignments": [
                    {"pairing_id": s.pairing_id, "role": s.role.value, **option_dict(o)}
                    for s, o in self.joint.assignments
                ],
                "unfilled": [
                    {"pairing_id": s.pairing_id, "role": s.role.value} for s in self.joint.unfilled
                ],
                "total_cost_inr": self.joint.total_cost_inr,
                "equal_cost_alternatives": self.joint.equal_cost_alternatives,
                "exhaustive": self.joint.exhaustive,
            }
        return doc


def _delay_dict(d: DelayEffect) -> dict[str, Any]:
    return {
        "pairing_id": d.pairing_id,
        "date": d.date.isoformat(),
        "delay_hours": d.delay_hours,
        "fdp_hours": d.fdp_hours,
        "fdp_limit": d.fdp_limit,
        "duty_breaches_limit": d.duty_breaches_limit,
        "breaches": [
            {"crew_id": b.crew_id, "role": b.role.value, "reasons": list(b.reasons)}
            for b in d.breaches
        ],
        "legs_to_drop": list(d.legs_to_drop),
        "cancellation_cost_inr": d.cancellation_cost_inr,
    }


def _hours_up(seconds: float) -> float:
    """Seconds as hours, rounded up to the whole minute, so a delay is never rounded away."""
    return math.ceil(seconds / 60) / 60


def _flight_index(world: World) -> dict[str, tuple[str, date]]:
    """Flight id -> (pairing id, duty date)."""
    out = {}
    for pid, pairing in world.pairings.items():
        for day in pairing.days:
            for fid in day.flight_ids:
                out[fid] = (pid, day.date)
    return out


def _seat_for(
    world: World,
    seat: Seat,
    crew_out: str | None,
    dates: set[date] | None,
    delay_hours: float = 0.0,
    delay_date: date | None = None,
    from_utc: datetime | None = None,
) -> SeatAnalysis:
    impact = pairing_impact(world, seat.pairing_id, dates, from_utc)
    try:
        options = find_options(world, seat.pairing_id, seat.role, delay_hours, delay_date)
    except AlreadyStarted as exc:
        return SeatAnalysis(
            seat,
            crew_out,
            impact,
            None,
            str(exc),
            delay_hours,
            delay_date,
            cancelled=isinstance(exc, TripCancelled),
        )
    return SeatAnalysis(seat, crew_out, impact, options, "", delay_hours, delay_date)


def _crew_label(world: World, crew_id: str) -> str:
    crew = world.crew.get(crew_id)
    return f"{crew.name} ({crew_id})" if crew and crew.name else crew_id


def _seat_summary(seats: tuple[SeatAnalysis, ...]) -> str:
    parts = []
    for s in seats:
        head = f"{s.seat.pairing_id} {s.seat.role.value}"
        if s.options is None:
            parts.append(f"{head}: {'cancelled' if s.cancelled else 'already started'}")
        elif s.options.recommended:
            rec = s.options.recommended
            parts.append(f"{head}: {rec.action}, {_inr(rec.cost_inr)}")
        else:
            parts.append(f"{head}: no legal cover")
    cancellations = {
        s.seat.pairing_id: s.options.cancellation
        for s in seats
        if s.options is not None and not s.options.recommended
    }
    for pid, cancel in sorted(cancellations.items()):
        parts.append(f"cancelling {pid} ({cancel.name.lower()}) costs {_inr(cancel.cost_inr)}")
    return "; ".join(parts)


def _delay_outcome(
    world: World, effect: DelayEffect, flight_label: str
) -> tuple[list[SeatAnalysis], str]:
    """Seats to re-crew and a one-line summary for one delayed duty day."""
    head = (
        f"{flight_label} on {effect.date}: duty becomes {effect.fdp_hours}h "
        f"against a {effect.fdp_limit}h limit"
    )
    if effect.duty_breaches_limit:
        legs = ", ".join(f.split("-")[0] for f in effect.legs_to_drop)
        return [], (
            f"{head}. No crew can legally fly the whole delayed day: {legs} must come off "
            f"this crew (cancelling costs {_inr(effect.cancellation_cost_inr)}); the remaining "
            "legs still need the usual legality checks"
        )
    seats = [
        _seat_for(
            world,
            Seat(effect.pairing_id, b.role),
            b.crew_id,
            {effect.date},
            effect.delay_hours,
            effect.date,
        )
        for b in effect.breaches
    ]
    if not seats:
        return [], f"{head}. All rostered crew stay legal"
    causes = sorted({b.reasons[0] for b in effect.breaches if b.reasons})
    cause = f" ({'; '.join(causes[:2])})" if causes else ""
    return seats, (
        f"{head}. {len(seats)} crew member(s) become illegal{cause}: " + _seat_summary(tuple(seats))
    )


# One builder per report type.


def _crew_out_seats(world: World, crew_id: str, dates: set[date]) -> list[tuple[Seat, set[date]]]:
    """Seats this crew member holds on pairings with a duty day in ``dates``."""
    seats = []
    for pid, pairing in sorted(world.pairings.items()):
        for seat in pairing.crew:
            if seat.crew_id != crew_id:
                continue
            hit = {d.date for d in pairing.days} & dates
            if hit:
                seats.append((Seat(pid, seat.role), hit))
    return seats


def off_duty_notes(world: World, crew_id: str, start: date, end: date) -> tuple[str, ...]:
    """What still matters when someone is out on days they fly nothing: on-call days
    in the period (one reserve fewer), and the next duty after it (are they fit for it?)."""
    who = _crew_label(world, crew_id)
    notes = []
    for duty in world.reserves.get(crew_id, ()):
        on_call = [d for d in duty.dates if start <= d <= end]
        if on_call:
            days = ", ".join(d.isoformat() for d in on_call)
            notes.append(f"{who} was on call at {duty.base} on {days}: one reserve fewer there.")
    later = [
        day
        for pairing in world.pairings.values()
        if any(seat.crew_id == crew_id for seat in pairing.crew)
        for day in pairing.days
        if day.date > end
    ]
    if later:
        nxt = min(later, key=lambda d: d.report_utc)
        notes.append(
            f"{who}'s next duty is {nxt.date}, reporting {_iso(nxt.report_utc)}: if still "
            "unwell, they report again before then."
        )
    return tuple(notes)


def _with_joint(seats: list[SeatAnalysis], world: World) -> JointPlan | None:
    """Joint plan over the seats that can still be re-crewed.

    Reuses each seat's own options so the plan agrees with them (delayed schedule included)
    and nothing is searched twice."""
    open_seats = [s for s in seats if s.options is not None]
    if len(open_seats) < 2:
        return None
    return joint_plan(world, [s.seat for s in open_seats], [s.options for s in open_seats])


def _crew_unavailable(world: World, report: Report) -> Analysis:
    d = report.details
    start, end = date.fromisoformat(d["from_date"]), date.fromisoformat(d["to_date"])
    dates = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    who = _crew_label(world, d["crew_id"])
    pairs = _crew_out_seats(world, d["crew_id"], dates)
    if not pairs:
        return Analysis(
            report.report_id,
            report.report_type,
            f"{who} is unavailable {start} to {end} but has no rostered duty then: "
            "nothing to cover. Recorded, so they are not called out on those days.",
            notes=off_duty_notes(world, d["crew_id"], start, end),
        )
    part = (
        datetime.fromisoformat(d["from_utc"].replace("Z", "+00:00")) if d.get("from_utc") else None
    )
    seats, done = _still_to_fly(
        [_seat_for(world, s, d["crew_id"], hit, from_utc=part) for s, hit in pairs]
    )
    when = f" from {_iso(part)}" if part else ""
    if not seats:
        return Analysis(
            report.report_id,
            report.report_type,
            f"{who} unavailable{when} ({d['reason']}), after flying every leg of their "
            "trips: nothing to cover.",
            notes=done,
        )
    notes = done + tuple(n for s in seats if (n := _joins_mid_day(world, s, part)))
    if report.report_type is ReportType.PLANNED_LEAVE:
        reason = d["reason"].replace("_", " ")
        notes += (
            f"Planned leave ({reason}) {start} to {end}: re-crew these trips in advance; "
            "nothing here is urgent.",
        )
        lead = f"{who} on planned leave {start} to {end} ({reason}): "
    else:
        lead = f"{who} unavailable{when} ({d['reason']}): "
    return Analysis(
        report.report_id,
        report.report_type,
        lead + _seat_summary(seats),
        seats=seats,
        joint=_with_joint(list(seats), world),
        notes=notes,
    )


def _still_to_fly(
    seats: list[SeatAnalysis],
) -> tuple[tuple[SeatAnalysis, ...], tuple[str, ...]]:
    """The seats that still have a flight to crew, and a note for each whose trip was
    already flown in full before the crew member fell ill (an absence from a time)."""
    keep, done = [], []
    for s in seats:
        i = s.impact
        if i.flown_before and not i.uncovered_flights and not i.at_risk_flights:
            done.append(
                f"{s.crew_out} had already flown every leg of {s.seat.pairing_id} "
                "before falling ill: nothing to cover there."
            )
        else:
            keep.append(s)
    return tuple(keep), tuple(done)


def _joins_mid_day(world: World, seat: SeatAnalysis, part: datetime | None) -> str:
    """When someone falls ill part-way through a day, the cover joins where they stop."""
    if part is None or not seat.impact.flown_before or not seat.impact.uncovered_flights:
        return ""
    first = world.flights[seat.impact.uncovered_flights[0]]
    flown = ", ".join(f.split("-")[0] for f in seat.impact.flown_before)
    return (
        f"{seat.crew_out} still flies {flown}; the cover joins at {first.dep_station} for "
        f"{first.flight_no} at {_iso(first.dep_utc)[11:16]}Z, positioned there if based "
        "elsewhere (that fare is not in the costs shown). Cover is checked against the whole "
        "duty day, which is the stricter test."
    )


def _multiple_crew_unavailable(world: World, report: Report) -> Analysis:
    """Several crew out together. The open report already marks each of them unavailable
    in the world, so none is offered to cover another, and one joint plan gives every seat a
    different person."""
    d = report.details
    start, end = date.fromisoformat(d["from_date"]), date.fromisoformat(d["to_date"])
    dates = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    part = (
        datetime.fromisoformat(d["from_utc"].replace("Z", "+00:00")) if d.get("from_utc") else None
    )
    seats: list[SeatAnalysis] = []
    idle = []
    for crew_id in d["crew_ids"]:
        pairs = _crew_out_seats(world, crew_id, dates)
        if not pairs:
            idle.append(crew_id)
        seats += [_seat_for(world, s, crew_id, hit, from_utc=part) for s, hit in pairs]
    kept, done = _still_to_fly(seats)
    seats = list(kept)
    who = f"{len(d['crew_ids'])} crew unavailable {start} to {end} ({d['reason']})"
    if not seats:
        return Analysis(
            report.report_id,
            report.report_type,
            f"{who}, but none has a flight left to crew then: nothing to cover. Recorded, "
            "so none of them is called out on those days.",
            notes=done
            + tuple(n for c in d["crew_ids"] for n in off_duty_notes(world, c, start, end)),
        )
    trips = sorted({s.seat.pairing_id for s in seats})
    shared = [p for p in trips if sum(s.seat.pairing_id == p for s in seats) > 1]
    lead = f"{who}: {len(seats)} seat(s) on {len(trips)} trip(s)"
    if shared:
        lead += f", {', '.join(shared)} losing more than one crew member"
    notes = (
        tuple(f"{_crew_label(world, c)} has no rostered duty then" for c in idle)
        + done
        + tuple(n for s in seats if (n := _joins_mid_day(world, s, part)))
    )
    return Analysis(
        report.report_id,
        report.report_type,
        f"{lead}. " + _seat_summary(tuple(seats)),
        seats=tuple(seats),
        joint=_with_joint(seats, world),
        notes=notes,
    )


def _certification_lapse(world: World, report: Report) -> Analysis:
    d = report.details
    valid_to = date.fromisoformat(d["valid_to"])
    who = _crew_label(world, d["crew_id"])
    after = {day.date for p in world.pairings.values() for day in p.days if day.date > valid_to}
    pairs = _crew_out_seats(world, d["crew_id"], after)
    if not pairs:
        return Analysis(
            report.report_id,
            report.report_type,
            f"{who}'s {d['cert_type']} expires {valid_to}: no rostered duty after that date.",
        )
    seats = tuple(_seat_for(world, s, d["crew_id"], hit) for s, hit in pairs)
    return Analysis(
        report.report_id,
        report.report_type,
        f"{who}'s {d['cert_type']} expires {valid_to}: " + _seat_summary(seats),
        seats=seats,
        joint=_with_joint(list(seats), world),
    )


def _delays(
    world: World,
    report: Report,
    delays: dict[tuple[str, date], tuple[float, str, str]],
    affected: list[str],
    lead: str,
) -> Analysis:
    if not delays:
        return Analysis(
            report.report_id,
            report.report_type,
            f"{lead}: no rostered flights affected.",
            affected_flights=tuple(affected),
        )
    effects, seats, lines, late = [], [], [], []
    for (pid, day), (hours, label, flight_id) in sorted(delays.items()):
        if hours <= 0:
            continue
        legs = next((d.flight_ids for d in world.pairings[pid].days if d.date == day), (flight_id,))
        start = legs.index(flight_id) if flight_id in legs else 0
        late += [LateLeg(f, round(hours * 60)) for f in legs[start:]]
        try:
            effect = delay_effect(world, pid, day, hours, from_flight=flight_id)
        except ValueError as exc:
            lines.append(f"{label} on {day}: not assessed ({exc})")
            continue
        effects.append(effect)
        new_seats, line = _delay_outcome(world, effect, label)
        seats.extend(new_seats)
        lines.append(line)
    return Analysis(
        report.report_id,
        report.report_type,
        f"{lead}. " + ". ".join(lines) + ".",
        seats=tuple(seats),
        joint=_with_joint(seats, world),
        delays=tuple(effects),
        affected_flights=tuple(affected),
        late=tuple(late),
    )


def _flight_delay(world: World, report: Report) -> Analysis:
    d = report.details
    index = _flight_index(world)
    fid = d["flight_id"]
    hours = d["delay_minutes"] / 60
    lead = f"{fid.split('-')[0]} delayed {d['delay_minutes']} min"
    if fid not in index:
        return Analysis(
            report.report_id,
            report.report_type,
            f"{lead}: the flight is not on any pairing.",
            affected_flights=(fid,),
        )
    pid, day = index[fid]
    return _delays(world, report, {(pid, day): (hours, fid.split("-")[0], fid)}, [fid], lead)


def _aircraft_technical(world: World, report: Report) -> Analysis:
    d = report.details
    start, end = (
        datetime.fromisoformat(d[k].replace("Z", "+00:00")) for k in ("from_utc", "to_utc")
    )
    index = _flight_index(world)
    hit = sorted(
        (
            f
            for f in world.flights.values()
            if f.aircraft == d["aircraft"] and start <= f.dep_utc < end
        ),
        key=lambda f: f.dep_utc,
    )
    delays: dict[tuple[str, date], tuple[float, str, str]] = {}
    for f in hit:
        if f.flight_id not in index:
            continue
        key = index[f.flight_id]
        if key not in delays:  # the first affected departure sets the day's delay
            hours = _hours_up((end - f.dep_utc).total_seconds())
            delays[key] = (hours, f.flight_no, f.flight_id)
    lead = f"{d['aircraft']} unavailable {_iso(start)} to {_iso(end)}"
    return _delays(world, report, delays, [f.flight_id for f in hit], lead)


def _station_disruption(world: World, report: Report) -> Analysis:
    d = report.details
    start, end = (
        datetime.fromisoformat(d[k].replace("Z", "+00:00")) for k in ("from_utc", "to_utc")
    )
    station = d["station"]
    index = _flight_index(world)
    worst: dict[tuple[str, date], tuple[float, str, str]] = {}
    affected = []
    for f in sorted(world.flights.values(), key=lambda f: f.dep_utc):
        moment = None
        if f.dep_station == station and start <= f.dep_utc < end:
            moment = f.dep_utc
        elif f.arr_station == station and start <= f.arr_utc < end:
            moment = f.arr_utc
        if moment is None:
            continue
        affected.append(f.flight_id)
        if f.flight_id in index:
            ready = end + STATION_REOPEN_TURNAROUND
            hours = _hours_up((ready - moment).total_seconds())
            key = index[f.flight_id]
            if key not in worst:
                # departure order, so this is the day's earliest affected leg and every
                # later leg slips with it
                worst[key] = (hours, f.flight_no, f.flight_id)
            elif hours > worst[key][0]:
                worst[key] = (hours, worst[key][1], worst[key][2])
    lead = (
        f"{station} closed {_iso(start)} to {_iso(end)} ({d['reason']}), "
        f"{len(affected)} flights affected"
    )
    return _delays(world, report, dict(worst), affected, lead)


def _gate_change(world: World, report: Report, gates: tuple[GateSlot, ...]) -> Analysis:
    d = report.details
    fid, new_gate = d["flight_id"], d["new_gate"]
    mine = next((g for g in gates if g.flight_id == fid), None)
    label = fid.split("-")[0]
    if mine is None:
        return Analysis(
            report.report_id,
            report.report_type,
            f"{label} moved to {new_gate}: no boarding slot on record for this flight.",
            affected_flights=(fid,),
        )
    clashes = [
        g
        for g in gates
        if g.gate == new_gate
        and g.flight_id != fid
        and g.start_utc < mine.end_utc
        and mine.start_utc < g.end_utc
    ]
    if not clashes:
        summary = (
            f"{label} moved from {mine.gate} to {new_gate}: the gate is free for its boarding "
            "window. Crew legality is unaffected."
        )
        return Analysis(report.report_id, report.report_type, summary, affected_flights=(fid,))
    taken = ", ".join(c.flight_id.split("-")[0] for c in clashes)
    return Analysis(
        report.report_id,
        report.report_type,
        f"{label} moved from {mine.gate} to {new_gate}, but {new_gate} is held by {taken} "
        "during its boarding window. Choose another gate, or the departure slips.",
        affected_flights=(fid, *[c.flight_id for c in clashes]),
        notes=tuple(
            f"{new_gate} held by {c.flight_id} {_iso(c.start_utc)} to {_iso(c.end_utc)}"
            for c in clashes
        ),
    )


def _gate_closure(world: World, report: Report, gates: tuple[GateSlot, ...]) -> Analysis:
    d = report.details
    start, end = (
        datetime.fromisoformat(d[k].replace("Z", "+00:00")) for k in ("from_utc", "to_utc")
    )
    gate = d["gate"]
    board = GateBoard(gates, world.flights, world.emergency_gates, world.cancelled)
    moves = close_gate(board, gate, start, end, d["reason"])
    lead = f"{gate} closed {_iso(start)} to {_iso(end)} ({d['reason']})"
    if not moves:
        return Analysis(
            report.report_id,
            report.report_type,
            f"{lead}: no departure holds the gate then, so nothing moves.",
        )
    regated = [m for m in moves if m.to_gate != REMOTE_STAND]
    bussed = [m for m in moves if m.to_gate == REMOTE_STAND]
    parts = []
    if regated:
        parts.append(
            f"{len(regated)} departure(s) re-gated ("
            + ", ".join(
                f"{m.flight_no} to {m.to_gate}" + (" (emergency gate)" if m.emergency else "")
                for m in regated
            )
            + ")"
        )
    if bussed:
        parts.append(
            f"{len(bussed)} board(s) from a remote stand by bus, "
            f"{bussed[0].delay_minutes} min later ("
            + ", ".join(m.flight_no for m in bussed)
            + f"): no other {station_of(gate)} gate is free for the whole slot"
        )
    index = _flight_index(world)
    # earliest bused departure of each duty day sets the slip; it and every later leg leave
    # late, which is what the duty check assumes
    slips: dict[tuple[str, date], tuple[float, str, str]] = {}
    for m in sorted(bussed, key=lambda m: m.end_utc):
        if m.flight_id in index and index[m.flight_id] not in slips:
            slips[index[m.flight_id]] = (m.delay_minutes / 60, m.flight_no, m.flight_id)
    affected = [m.flight_id for m in moves]
    if slips:
        crew = _delays(world, report, slips, affected, f"{lead}: " + "; ".join(parts))
        return replace(crew, stands=tuple(moves))
    return Analysis(
        report.report_id,
        report.report_type,
        f"{lead}: " + "; ".join(parts) + ". Crew legality is unaffected.",
        affected_flights=tuple(affected),
        stands=tuple(moves),
    )


def _gate_alternative(world: World, gates: tuple[GateSlot, ...], analysis: Analysis) -> Analysis:
    """A gate change onto a gate already held: suggest the first gate free for the slot."""
    if not analysis.notes:
        return analysis
    fid = analysis.affected_flights[0]
    board = GateBoard(gates, world.flights, world.emergency_gates, world.cancelled)
    mine = board.slot_of(fid)
    if mine is None:
        return analysis
    # Its own gate first: it already holds it, so staying put is the least disruptive.
    others = [f for f in board.holding(mine.gate, mine.start_utc, mine.end_utc) if f != fid]
    if not others:
        return replace(
            analysis,
            summary=analysis.summary + f" Keep it at {mine.gate}: that gate stays free for it.",
        )
    taken = {g.gate for g in gates if g.flight_id in analysis.affected_flights[1:]}
    move = board.move(mine, "requested gate already held", taken)
    tail = (
        f" {move.to_gate} is free for its whole boarding window."
        if move.to_gate != REMOTE_STAND
        else " No gate at the station is free for its whole slot: board from a remote stand."
    )
    return replace(analysis, summary=analysis.summary + tail, stands=(move,))


def _late_gates(world: World, gates: tuple[GateSlot, ...], analysis: Analysis) -> Analysis:
    """A late departure keeps its gate longer: name the next departure at each such gate
    and move it when the gate is still taken."""
    if not analysis.late or not gates:
        return analysis
    board = GateBoard(gates, world.flights, world.emergency_gates, world.cancelled)
    late = sorted(
        analysis.late,
        key=lambda leg: (
            world.flights[leg.flight_id].dep_utc if leg.flight_id in world.flights else 0
        ),
    )
    moves, nexts = late_departures(
        board, [(leg.flight_id, timedelta(minutes=leg.minutes)) for leg in late]
    )
    return replace(analysis, stands=tuple(moves), next_at_gate=tuple(nexts))


def _kept_gate(gates: tuple[GateSlot, ...], report: Report, a: Analysis) -> str | None:
    """A refused gate change (the gate is taken) that keeps the flight where it was."""
    if report.report_type is not ReportType.GATE_CHANGE or not a.notes or a.stands:
        return None
    return next((g.gate for g in gates if g.flight_id == report.details["flight_id"]), None)


def _uncovered(analysis: Analysis) -> list[str]:
    seats = [s for s in analysis.seats if s.options is None or not s.options.recommended]
    return sorted({f for s in seats for f in s.impact.uncovered_flights})


def _crew_line(analysis: Analysis) -> str:
    if any(d.duty_breaches_limit for d in analysis.delays):
        return "The crew's duty day becomes too long: crew control decides which legs come off."
    if analysis.seats:
        n = len(analysis.seats)
        return f"{n} crew member(s) go over a legal limit; crew control is re-crewing."
    return "Every rostered crew member stays legal."


def _handling(world: World, report: Report, analysis: Analysis) -> dict[str, str]:
    """How this disruption gets resolved, in one sentence for the record."""
    coverable = [s for s in analysis.seats if s.options is not None and s.options.recommended]
    if coverable:
        return {
            "kind": "recrew",
            "text": f"Crew control re-crews {len(analysis.seats)} seat(s): choose cover below, "
            "then a different approver approves it.",
        }
    breaches = [d for d in analysis.delays if d.duty_breaches_limit]
    if analysis.seats or breaches:
        what = (
            "no legal cover exists for "
            + ", ".join(f"{s.seat.pairing_id} {s.seat.role.value}" for s in analysis.seats)
            if analysis.seats
            else "the delayed duty day is too long for any crew: "
            + ", ".join(f.split("-")[0] for d in breaches for f in d.legs_to_drop)
            + " must come off"
        )
        return {
            "kind": "decision",
            "text": f"Not solved by re-crewing: {what}. Crew control decides with operations "
            "(delay, swap or cancel) and closes it with what was done.",
        }
    kind = report.report_type
    told = sorted({n["team_title"] for n in analysis.notices if n["channel"] == "email"})
    informed = f" Informed: {', '.join(told)}." if told else ""
    if kind is ReportType.GATE_CLOSURE:
        moved = len(analysis.stands)
        text = (
            f"Handled at {station_of(report.details['gate'])} by gate and stand control: "
            f"{moved} departure(s) moved off {report.details['gate']}. No re-crew required."
            if moved
            else f"No departure holds {report.details['gate']} during the closure. "
            "No re-crew required."
        )
    elif kind is ReportType.STATION_DISRUPTION:
        text = (
            f"Handled by operations: {len(analysis.late)} departure(s) wait until "
            f"{report.details['station']} reopens, and every rostered crew member stays "
            "legal. No re-crew required."
        )
    elif kind in (ReportType.FLIGHT_DELAY, ReportType.AIRCRAFT_TECHNICAL):
        nxt = "; ".join(
            f"next at {n.gate} is {n.flight_no}"
            + (
                f", moved to {m.to_gate}"
                if (m := next((m for m in analysis.stands if m.flight_id == n.flight_id), None))
                else f", gate free {n.buffer_minutes} min before it boards"
            )
            for n in analysis.next_at_gate
        )
        text = (
            f"Handled by operations: {len(analysis.late)} departure(s) leave late and every "
            "rostered crew member stays legal. No re-crew required."
            + (f" Gates: {nxt}." if nxt else "")
        )
    elif kind is ReportType.GATE_CHANGE:
        text = "Handled by gate control: crew legality is unaffected. No re-crew required."
    else:
        text = "Nothing to cover: no rostered duty is affected."
    return {"kind": "internal", "text": text + informed}


def _aircraft_of(world: World, analysis: Analysis) -> tuple[str, ...]:
    touched = {
        world.pairings[s.seat.pairing_id].aircraft
        for s in analysis.seats
        if s.seat.pairing_id in world.pairings
    }
    # Only the flights the disruption is about. A flight merely re-gated because of it stays
    # with its own desk (for a gate closure the moved flights are the affected ones).
    touched |= {
        world.flights[f].aircraft
        for f in (*analysis.affected_flights, *(leg.flight_id for leg in analysis.late))
        if f in world.flights
    }
    return tuple(sorted(touched))


def analyse(
    world: World,
    report: Report,
    gates: tuple[GateSlot, ...] = (),
    contacts: tuple[Contact, ...] = (),
) -> Analysis:
    """The analysis of one report against the current operation: what broke, who must be
    replaced and how, which gates move, who is told, and how it gets resolved."""
    kind = report.report_type
    if kind in (ReportType.CREW_UNAVAILABLE, ReportType.PLANNED_LEAVE):
        a = _crew_unavailable(world, report)
    elif kind is ReportType.MULTIPLE_CREW_UNAVAILABLE:
        a = _multiple_crew_unavailable(world, report)
    elif kind is ReportType.CERTIFICATION_LAPSE:
        a = _certification_lapse(world, report)
    elif kind is ReportType.FLIGHT_DELAY:
        a = _late_gates(world, gates, _flight_delay(world, report))
    elif kind is ReportType.AIRCRAFT_TECHNICAL:
        a = _late_gates(world, gates, _aircraft_technical(world, report))
    elif kind is ReportType.STATION_DISRUPTION:
        a = _late_gates(world, gates, _station_disruption(world, report))
    elif kind is ReportType.GATE_CLOSURE:
        a = _gate_closure(world, report, gates)
    else:
        a = _gate_alternative(world, gates, _gate_change(world, report, gates))
    notices = notices_for(
        world,
        report,
        contacts,
        late=a.late,
        moves=a.stands,
        nexts=a.next_at_gate,
        crew_line=_crew_line(a),
        uncovered_flights=_uncovered(a),
        kept_gate=_kept_gate(gates, report, a),
    )
    aircraft = _aircraft_of(world, a)
    airlines = tuple(
        sorted({world.airline_of_aircraft[t] for t in aircraft if t in world.airline_of_aircraft})
    )
    a = replace(a, notices=tuple(notices), aircraft=aircraft, airlines=airlines)
    return replace(a, handling=_handling(world, report, a))


__all__ = ["Analysis", "SeatAnalysis", "analyse", "option_dict"]
