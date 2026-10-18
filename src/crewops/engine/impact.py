"""What a disruption breaks, what a delay does to legality, and how to cover several gaps.

``pairing_impact`` is the blast radius of losing crew on some days of a pairing (uncovered
flights, later flights of the trip put at risk, passengers). ``delay_effect`` says who stops
being legal after a delay and whether the day is now too long for anyone, in which case
re-crewing can't help and trailing legs have to come off. ``joint_plan`` finds the cheapest
legal cover for several seats at once without giving one person two seats.

Same rules as single-seat legality throughout.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from crewops.domain import Flight, Rank
from crewops.engine.duty import hours_between
from crewops.engine.legality import assess
from crewops.engine.options import AlreadyStarted, Option, OptionsResult, find_options
from crewops.engine.world import World

RELEASE_AFTER_LAST_ARRIVAL = timedelta(minutes=30)
JOINT_OPTIONS_PER_SEAT = 20
JOINT_EXHAUSTIVE_LIMIT = 200_000
"""Above this many combinations the joint plan is built greedily instead of exhaustively."""


@dataclass(frozen=True, slots=True)
class PairingImpact:
    pairing_id: str
    affected_dates: tuple[date, ...]
    uncovered_flights: tuple[str, ...]
    """Flights on the affected days, now with nobody rostered."""
    at_risk_flights: tuple[str, ...]
    """Later flights of the same trip. It overnights away, so covering only the affected days
    would strand the aircraft or the crew."""
    passengers: int
    """Passengers booked on the uncovered flights (seats, for a flight with no bookings)."""
    flown_before: tuple[str, ...] = ()
    """Legs of the first affected day they still fly before going unavailable mid-day."""


def pairing_impact(
    world: World,
    pairing_id: str,
    dates: set[date] | None = None,
    from_utc: datetime | None = None,
) -> PairingImpact:
    """Blast radius of losing crew on ``dates`` of a pairing (all its days if None).

    With ``from_utc`` they go unavailable part-way through the first affected day, and legs
    leaving before then are still theirs."""
    pairing = world.pairing(pairing_id)
    days = [d.date for d in pairing.days]
    affected = sorted(set(days) if dates is None else (set(days) & dates))
    if not affected:
        return PairingImpact(pairing_id, (), (), (), 0)
    first = affected[0]
    uncovered, at_risk, flown = [], [], []
    for day in pairing.days:
        if day.date in affected:
            for f in day.flight_ids:
                if from_utc and day.date == first and world.flights[f].dep_utc < from_utc:
                    flown.append(f)
                else:
                    uncovered.append(f)
        elif day.date > first:
            at_risk.extend(day.flight_ids)
    passengers = sum(_booked(world.flights[f]) for f in uncovered)
    return PairingImpact(
        pairing_id, tuple(affected), tuple(uncovered), tuple(at_risk), passengers, tuple(flown)
    )


def _booked(flight: Flight) -> int:
    return flight.booked if flight.booked is not None else flight.seats


@dataclass(frozen=True, slots=True)
class CrewBreach:
    crew_id: str
    role: Rank
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DelayEffect:
    pairing_id: str
    date: date
    delay_hours: float
    fdp_hours: float
    """The delayed duty day's flight-duty period."""
    fdp_limit: float
    duty_breaches_limit: bool
    """Delayed day is too long for anyone, so re-crewing can't fix it."""
    breaches: tuple[CrewBreach, ...]
    """Rostered crew who were legal before the delay and are not after it."""
    legs_to_drop: tuple[str, ...]
    """Fewest trailing legs whose removal brings the day back within its limit."""
    cancellation_cost_inr: int
    """Cost of cancelling ``legs_to_drop``."""


def delay_effect(
    world: World,
    pairing_id: str,
    on_date: date,
    delay_hours: float,
    from_flight: str | None = None,
) -> DelayEffect:
    """What a delay of ``delay_hours`` on ``on_date`` does to the pairing and its crew.

    ``from_flight`` is the first late leg (the day's first leg if None). Earlier legs fly on
    time; it and every later leg slip, which pushes the day's release out.
    """
    if delay_hours <= 0:
        raise ValueError("delay_hours must be positive")
    pairing = world.pairing(pairing_id)
    days = world.days_of(pairing_id)
    try:
        index = next(i for i, d in enumerate(days) if d.date == on_date)
    except StopIteration:
        raise ValueError(f"{pairing_id} has no duty day on {on_date}") from None
    day = days[index]
    flight_ids = pairing.days[index].flight_ids
    first_late = flight_ids.index(from_flight) if from_flight in flight_ids else 0

    delayed = day.delayed(delay_hours)
    limit = world.book.fdp_limit(day.n_sectors)
    over = delayed.fdp_hours > limit

    breaches = []
    for seat in pairing.crew:
        before = assess(world, seat.crew_id, pairing_id)
        after = assess(world, seat.crew_id, pairing_id, delay_hours, on_date)
        if before.legal and not after.legal:
            reasons = tuple(v.detail for v in after.blocking)
            breaches.append(CrewBreach(seat.crew_id, seat.role, reasons))

    legs: tuple[str, ...] = ()
    if over:
        # keep as many legs as possible; only legs from first_late onward slip
        for keep in range(len(flight_ids) - 1, 0, -1):
            last_index = keep - 1
            last_kept = world.flights[flight_ids[last_index]]
            slip = timedelta(hours=delay_hours) if last_index >= first_late else timedelta(0)
            release = last_kept.arr_utc + RELEASE_AFTER_LAST_ARRIVAL + slip
            if hours_between(day.report_utc, release) <= world.book.fdp_limit(keep):
                legs = tuple(flight_ids[keep:])
                break
        else:
            legs = tuple(flight_ids)

    return DelayEffect(
        pairing_id=pairing_id,
        date=on_date,
        delay_hours=round(delay_hours, 2),
        fdp_hours=delayed.fdp_hours,
        fdp_limit=limit,
        duty_breaches_limit=over,
        breaches=tuple(breaches),
        legs_to_drop=legs,
        cancellation_cost_inr=len(legs) * world.costs.cancellation_per_flight,
    )


@dataclass(frozen=True, slots=True)
class Seat:
    pairing_id: str
    role: Rank


@dataclass(frozen=True, slots=True)
class JointPlan:
    assignments: tuple[tuple[Seat, Option], ...]
    unfilled: tuple[Seat, ...]
    """No legal cover in this plan; cancelling is what's left."""
    total_cost_inr: int
    """Cost of the assignments only; unfilled seats are listed, not priced in."""
    equal_cost_alternatives: int
    """Other full plans with the same total among each seat's top options. Ties are normal
    and any of them is correct."""
    exhaustive: bool
    """False when the plan was built greedily (too many combinations, or no plan fills
    every seat)."""


def joint_plan(
    world: World,
    seats: list[Seat],
    precomputed: list[OptionsResult | None] | None = None,
) -> JointPlan:
    """Cheapest cover for every seat at once, one seat per crew member.

    ``seats`` is a list, not a set, because a pairing can need several people in one role.
    ``precomputed`` reuses each seat's options (e.g. worked out under a delayed schedule) so
    the plan agrees with the per-seat answers; without it options are found for the planned
    schedule.
    """
    slots: list[tuple[Seat, list[Option]]] = []
    unfilled: list[Seat] = []
    for i, seat in enumerate(seats):
        if precomputed is not None:
            result = precomputed[i]
        else:
            try:
                result = find_options(world, seat.pairing_id, seat.role)
            except AlreadyStarted:
                result = None
        crew = list(result.crew_options[:JOINT_OPTIONS_PER_SEAT]) if result else []
        if crew:
            slots.append((seat, crew))
        else:
            unfilled.append(seat)

    combinations = math.prod(len(opts) for _, opts in slots) if slots else 0
    if slots and combinations <= JOINT_EXHAUSTIVE_LIMIT:
        best: tuple[int, tuple[Option, ...]] | None = None
        ties = 0
        for combo in itertools.product(*(opts for _, opts in slots)):
            ids = [o.crew_id for o in combo]
            if len(set(ids)) != len(ids):
                continue
            total = sum(o.cost_inr for o in combo)
            if best is None or total < best[0]:
                best, ties = (total, combo), 0
            elif total == best[0]:
                ties += 1
        if best is not None:
            assignments = tuple((seat, o) for (seat, _), o in zip(slots, best[1], strict=True))
            return JointPlan(assignments, tuple(unfilled), best[0], ties, True)
        # no plan fills every seat with different people, fall back to greedy

    return _greedy(slots, unfilled)


def _greedy(slots: list[tuple[Seat, list[Option]]], unfilled: list[Seat]) -> JointPlan:
    """Seats with the fewest choices first, each taking its cheapest crew not yet used."""
    order = sorted(
        range(len(slots)),
        key=lambda i: (len(slots[i][1]), slots[i][0].pairing_id, slots[i][0].role.value, i),
    )
    taken: set[str] = set()
    chosen: dict[int, Option] = {}
    missed: list[Seat] = list(unfilled)
    for i in order:
        seat, opts = slots[i]
        choice = next((o for o in opts if o.crew_id not in taken), None)
        if choice is None:
            missed.append(seat)
            continue
        taken.add(choice.crew_id)
        chosen[i] = choice
    assignments = tuple((slots[i][0], chosen[i]) for i in sorted(chosen))
    total = sum(o.cost_inr for _, o in assignments)
    return JointPlan(assignments, tuple(missed), total, 0, False)
