"""The operation in memory, shaped for the rules.

Built once from typed records, then read-only. The operation is small enough that holding
it in memory beats a DynamoDB round trip per question. The store loads the records; this
module just shapes them for the engine.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from crewops.domain import CostTable, Flight, Pairing, ReserveDuty
from crewops.engine.duty import DutyDay, hours_between
from crewops.engine.rules import CrewState, Duty, RuleBook
from crewops.ingestion.seed import SeedData

POSITIONING_REPORT_BUFFER = timedelta(minutes=15)
"""A positioned crew member reports this long after their positioning flight lands."""


class UnknownEntity(LookupError):
    """Unknown id. Callers suggest near matches; the engine doesn't guess."""

    def __init__(self, kind: str, value: str) -> None:
        super().__init__(f"no {kind} {value!r}")
        self.kind = kind
        self.value = value


@dataclass(frozen=True, slots=True)
class Positioning:
    """How a crew member from another base gets to the first departure."""

    flight_id: str
    from_station: str
    to_station: str
    arrives_utc: datetime
    delay_hours: float
    """Slip needed so they can report after landing."""


@dataclass(frozen=True, slots=True)
class World:
    crew: Mapping[str, CrewState]
    pairings: Mapping[str, Pairing]
    duty_days: Mapping[str, tuple[DutyDay, ...]]
    flights: Mapping[str, Flight]
    reserves: Mapping[str, tuple[ReserveDuty, ...]]
    costs: CostTable
    book: RuleBook
    snapshot_utc: datetime
    reachability: Mapping[str, int]
    """Minutes for each crew member to get in. Paid as travel allowance; a callout too close
    to report time slips the departure."""
    seniority: Mapping[str, int]
    """Years of service. Only affects rank through the callout allowance in the cost."""
    risk: Mapping[str, float]
    unavailable: Mapping[str, tuple[tuple[date, str], ...]] = field(default_factory=dict)
    """Crew id -> (date, report id) from open disruptions. Empty for the published roster."""
    unavailable_from: Mapping[tuple[str, date], datetime] = field(default_factory=dict)
    """(crew id, date) -> start of a part-day absence. Legs before it are still theirs."""
    cancelled: frozenset[str] = frozenset()
    """Cancelled by an approved plan; nobody flies or positions on them."""
    airline_of_aircraft: Mapping[str, str] = field(default_factory=dict)
    airline_of_crew: Mapping[str, str] = field(default_factory=dict)
    """Crew id -> employing airline. Cover only comes from the same airline."""
    airline_names: Mapping[str, str] = field(default_factory=dict)
    """Airline code -> display name."""
    command_qualified: frozenset[str] = frozenset()
    """First officers cleared to command: may fill a captain's seat."""
    right_seat_qualified: frozenset[str] = frozenset()
    """Captains current in the right seat: may fill a first officer's seat."""
    experience: Mapping[str, int] = field(default_factory=dict)
    """Years flying, which orders a command upgrade."""
    emergency_gates: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    """Station -> gates held back for disruptions."""

    def airline_of_pairing(self, pairing_id: str) -> str | None:
        return self.airline_of_aircraft.get(self.pairing(pairing_id).aircraft)

    def state(self, crew_id: str) -> CrewState:
        try:
            return self.crew[crew_id]
        except KeyError:
            raise UnknownEntity("crew", crew_id) from None

    def pairing(self, pairing_id: str) -> Pairing:
        try:
            return self.pairings[pairing_id]
        except KeyError:
            raise UnknownEntity("pairing", pairing_id) from None

    def days_of(self, pairing_id: str) -> tuple[DutyDay, ...]:
        self.pairing(pairing_id)
        return self.duty_days[pairing_id]

    def positioning(self, crew: CrewState, day: DutyDay) -> Positioning | None:
        """Same-day flight from their base that delays the day least, among flights not yet
        departed. None means they can't get there that day, which fails the base rule."""
        best: Positioning | None = None
        for f in self.flights.values():
            if f.date != day.date or f.dep_station != crew.base:
                continue
            if f.dep_utc < self.snapshot_utc:
                continue
            if f.flight_id in self.cancelled:
                continue
            if f.arr_station != day.dep_station:
                continue
            new_report = f.arr_utc + POSITIONING_REPORT_BUFFER
            slip = max(0.0, hours_between(day.report_utc, new_report))
            if best is None or (slip, f.arr_utc) < (best.delay_hours, best.arrives_utc):
                best = Positioning(f.flight_id, f.dep_station, f.arr_station, f.arr_utc, slip)
        return best


def build_world(seed: SeedData, now: datetime | None = None) -> World:
    """The operation as the engine sees it at ``now``.

    ``now`` is the real time when deployed; tests and the evaluation leave it out and get the
    seed's own moment (the duty clocks' time). Never earlier than the duty clocks, whose
    hours are already counted. Duties rostered before ``now`` count as flown so the hour
    limits see them."""
    flights = {f.flight_id: f for f in seed.flights}

    duty_days: dict[str, tuple[DutyDay, ...]] = {}
    duties: dict[str, list[Duty]] = defaultdict(list)
    for p in seed.pairings:
        days = []
        for day in p.days:
            legs = [flights[fid] for fid in day.flight_ids]
            days.append(
                DutyDay(
                    date=day.date,
                    report_utc=day.report_utc,
                    release_utc=day.release_utc,
                    n_sectors=len(legs),
                    block_hours=round(sum(leg.block_hours for leg in legs), 2),
                    aircraft_type=legs[0].aircraft_type,
                    dep_station=legs[0].dep_station,
                )
            )
        duty_days[p.pairing_id] = tuple(days)
        for seat in p.crew:
            for d in days:
                duties[seat.crew_id].append(
                    Duty(p.pairing_id, d.date, d.report_utc, d.release_utc, d.block_hours)
                )

    clocks = {c.crew_id: c for c in seed.duty_clocks}
    certs: dict[str, list] = defaultdict(list)
    for c in seed.certifications:
        certs[c.crew_id].append((c.cert_type, c.valid_from, c.valid_to))

    crew: dict[str, CrewState] = {}
    for c in seed.crew:
        clock = clocks.get(c.crew_id)
        history = clock.daily_history if clock else ()
        crew[c.crew_id] = CrewState(
            crew_id=c.crew_id,
            name=c.name,
            rank=c.rank,
            base=c.base,
            ratings=c.ratings,
            status=c.status,
            last_rest_ended=clock.last_rest_ended if clock else None,
            daily_duty={h.date: h.duty_hours for h in history},
            daily_flight={h.date: h.flight_hours for h in history},
            certs=tuple(certs.get(c.crew_id, ())),
            duties=tuple(sorted(duties.get(c.crew_id, ()), key=lambda d: d.report_utc)),
        )

    reserves: dict[str, list[ReserveDuty]] = defaultdict(list)
    for r in seed.reserves:
        reserves[r.crew_id].append(r)

    return World(
        crew=crew,
        pairings={p.pairing_id: p for p in seed.pairings},
        duty_days=duty_days,
        flights=flights,
        reserves={k: tuple(v) for k, v in reserves.items()},
        costs=seed.costs,
        book=RuleBook.from_rules(seed.rules),
        snapshot_utc=max(seed.snapshot_utc, now) if now else seed.snapshot_utc,
        reachability={c.crew_id: c.reachability_minutes for c in seed.crew},
        seniority={c.crew_id: c.seniority for c in seed.crew},
        risk={r.crew_id: r.score for r in seed.risk_signals},
        airline_of_aircraft={tail: a.code for a in seed.airlines for tail in a.aircraft},
        airline_of_crew={c.crew_id: c.airline for c in seed.crew if c.airline},
        airline_names={a.code: a.name for a in seed.airlines},
        command_qualified=frozenset(c.crew_id for c in seed.crew if c.command_qualified),
        right_seat_qualified=frozenset(c.crew_id for c in seed.crew if c.right_seat_qualified),
        experience={c.crew_id: c.experience_years for c in seed.crew if c.experience_years},
        emergency_gates={
            station: tuple(sorted(g.gate for g in seed.emergency_gates if g.station == station))
            for station in {g.station for g in seed.emergency_gates}
        },
    )
