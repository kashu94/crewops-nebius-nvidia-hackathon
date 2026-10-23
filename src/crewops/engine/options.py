"""Who could cover this seat, what each choice costs, and why everyone else is out.

``find_options`` assesses every crew member of the needed rank against the pairing and
returns the legal ones ranked by cost, with cancellation always last as the baseline. Any
figure a controller might want read out (next-tier premium, how many times cheaper covering
is than cancelling, how many options tie) is computed here so a model never has to.

Ranked by cost, then departure delay, then crew id so the order is stable. Seniority and
travel allowances are in the cost, so they only affect rank through what they pay. The risk
score is shown but never changes the rank.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from enum import StrEnum

from crewops.domain import Rank
from crewops.engine.legality import Assessment, assess
from crewops.engine.rules import BASE, CERT, DUTY, FDP, FLIGHT, QUAL, REST
from crewops.engine.world import World


class AlreadyStarted(ValueError):
    """Pairing reported before the snapshot, too late to re-crew."""


class TripCancelled(AlreadyStarted):
    """An approved plan cancelled the pairing's flights, nothing to re-crew."""


class Strategy(StrEnum):
    RESERVE_CALLOUT = "reserve_callout"
    DAY_OFF_CALLOUT = "day_off_callout"
    POSITIONING = "positioning"
    COMMAND_UPGRADE = "command_upgrade"
    RIGHT_SEAT = "right_seat"
    SHORT_CREW = "short_crew"
    CANCEL = "cancel"

    @property
    def label(self) -> str:
        return {
            Strategy.RESERVE_CALLOUT: "Reserve callout",
            Strategy.DAY_OFF_CALLOUT: "Day-off callout",
            Strategy.POSITIONING: "Position from another base",
            Strategy.COMMAND_UPGRADE: "Command-qualified first officer",
            Strategy.RIGHT_SEAT: "Captain in the right seat",
            Strategy.SHORT_CREW: "Operate one cabin crew short",
            Strategy.CANCEL: "Cancel",
        }[self]


SUBSTITUTES = frozenset({Strategy.COMMAND_UPGRADE, Strategy.RIGHT_SEAT})
"""Another rank in the seat, which regulation allows only when own-rank cover is short."""


SUBSTITUTE_BELOW = 3
"""With fewer legal crew of the seat's own rank than this, the qualified other rank is offered
as well (command-qualified FOs for a captain's seat, right-seat-current captains for an FO's),
listed after every own-rank option."""

SEATS_PER_CABIN_CREW = 50
"""Minimum cabin crew is one per 50 passenger seats or part of 50. A trip that still meets it
without the missing member may operate one short."""

REACH_ROUNDING_MINUTES = 5


FUNNEL = (
    ("qualified", (QUAL,), "not rated on the aircraft or not active"),
    ("certified", (CERT,), "a certification is invalid on a duty date"),
    ("in position", (BASE,), "cannot reach the departure, or on-call window misses the report"),
    ("rested", (REST,), "rest conflict or already rostered"),
    ("within limits", (FDP, DUTY, FLIGHT), "would breach a duty or flight-hour limit"),
)
"""The order a controller thinks in: rated on the type, current, can get there, free, has the
hours. A candidate drops out at the first stage they fail."""


@dataclass(frozen=True, slots=True)
class Option:
    rank: int
    strategy: Strategy
    crew_id: str | None
    name: str
    crew_rank: Rank | None
    base: str | None
    cost_inr: int
    cost_breakdown: dict[str, int]
    delay_hours: float
    """How late the first departure leaves with this choice."""
    positioning_flight: str | None = None
    reachability_minutes: int | None = None
    seniority: int | None = None
    risk_score: float | None = None
    resilience: float | None = None
    """0-100, how much of today's reserve cover for this rank and base survives the choice."""
    experience_years: int | None = None

    @property
    def action(self) -> str:
        if self.crew_id is None:
            return self.name
        who = f"{self.crew_rank} {self.name} ({self.crew_id})"
        if self.strategy is Strategy.COMMAND_UPGRADE:
            return f"Assign {who} in command (command-qualified, {self.experience_years} years)"
        if self.strategy is Strategy.RIGHT_SEAT:
            return f"Assign {who} in the right seat"
        if self.strategy is Strategy.POSITIONING:
            return (
                f"Assign {who}, positioning from {self.base} "
                f"(first departure ~{self.delay_hours:g}h late)"
            )
        return f"Assign {who} ({self.strategy.label.lower()})"


@dataclass(frozen=True, slots=True)
class Exclusion:
    crew_id: str
    name: str
    stage: str
    rule_ids: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FunnelStage:
    stage: str
    remaining: int
    dropped: int
    reason: str


@dataclass(frozen=True, slots=True)
class OptionsResult:
    pairing_id: str
    role: Rank
    options: tuple[Option, ...]
    """Legal crew options ranked by cost, then cancellation last."""
    strategies: tuple[Option, ...]
    """The cheapest legal option of each strategy, then cancellation."""
    excluded: tuple[Exclusion, ...]
    funnel: tuple[FunnelStage, ...]
    policies: dict[str, Option] = field(default_factory=dict)

    @property
    def crew_options(self) -> tuple[Option, ...]:
        return tuple(o for o in self.options if o.crew_id is not None)

    @property
    def short_crew(self) -> Option | None:
        """Operating without the missing cabin crew member, when the minimum allows it."""
        return next((o for o in self.options if o.strategy is Strategy.SHORT_CREW), None)

    @property
    def recommended(self) -> Option | None:
        return self.crew_options[0] if self.crew_options else None

    @property
    def cancellation(self) -> Option:
        return self.options[-1]

    @property
    def next_tier_premium_inr(self) -> int:
        """Extra cost of the next-cheapest price tier over the recommendation."""
        if not self.recommended:
            return 0
        # Substitutes are listed after every own-rank option, so one can cost less than the
        # recommendation; only dearer tiers count.
        cheapest = self.recommended.cost_inr
        dearer = [o.cost_inr for o in self.crew_options if o.cost_inr > cheapest]
        return min(dearer) - cheapest if dearer else 0

    @property
    def equal_cost_alternatives(self) -> int:
        """Other legal options exactly as cheap as the recommendation."""
        if not self.recommended:
            return 0
        cheapest = self.recommended.cost_inr
        return sum(o.cost_inr == cheapest for o in self.crew_options) - 1

    @property
    def cancellation_multiple(self) -> int:
        """How many times the recommendation's cost cancelling would cost."""
        if not self.recommended or not self.recommended.cost_inr:
            return 0
        return round(self.cancellation.cost_inr / self.recommended.cost_inr)


def _strategy(a: Assessment) -> Strategy:
    if a.positioning is not None:
        return Strategy.POSITIONING
    return Strategy.RESERVE_CALLOUT if a.on_reserve else Strategy.DAY_OFF_CALLOUT


def _stage(a: Assessment) -> str:
    failed = {v.rule_id for v in a.verdicts if v.failed}
    for stage, rule_ids, _ in FUNNEL:
        if failed & set(rule_ids):
            return stage
    return "within limits"


def _resilience(world: World, a: Assessment, role: Rank, base: str) -> float:
    """Score for using one of today's on-call crew of this rank at this base.

    Taking the only reserve captain at a station hurts more than taking one of twelve, hence
    100 * (n - 1) / n. A day-off callout leaves the pool alone and scores 100. The pool is the
    crew member's own airline's, since another airline's reserves can't cover its trips.
    """
    if not a.on_reserve:
        return 100.0
    day = a.days[0].date
    airline = world.airline_of_crew.get(a.crew_id)
    pool = sum(
        1
        for crew_id, duties in world.reserves.items()
        if any(day in r.dates and r.base == base for r in duties)
        and world.crew[crew_id].rank is role
        and world.airline_of_crew.get(crew_id) == airline
    )
    return 100.0 if pool <= 0 else round(100.0 * (pool - 1) / pool, 1)


def _assess(
    world: World, crew_id: str, pairing_id: str, delay_hours: float, delay_date: date | None
) -> Assessment:
    """``assess``, plus a departure slip when someone called now can't reach the airport by
    report time. A delay to the first day already gives them that much longer; only the
    part of their lateness beyond it slips the departure further."""
    a = assess(world, crew_id, pairing_id, delay_hours, delay_date)
    if not a.legal or a.positioning is not None:
        return a
    first = world.days_of(pairing_id)[0]
    reach = world.reachability.get(crew_id) or 0
    ready = world.snapshot_utc + timedelta(minutes=reach)
    late = (ready - first.report_utc).total_seconds() / 60
    if late <= 0:
        return a
    if delay_hours and delay_date not in (None, first.date):
        # A later day's delay and a day-one slip can't both be judged in one assessment;
        # the delay's own verdict stands, as before.
        return a
    late -= delay_hours * 60
    if late <= 0:
        return a
    minutes = -(-late // REACH_ROUNDING_MINUTES) * REACH_ROUNDING_MINUTES
    slipped = assess(world, crew_id, pairing_id, delay_hours + minutes / 60, delay_date)
    return replace(slipped, reach_slip_hours=round(minutes / 60, 2))


def _row(world: World, a: Assessment, strategy: Strategy) -> tuple:
    """(cost, delay, assessment, crew, cost breakdown, strategy) for one legal choice."""
    crew = world.crew[a.crew_id]
    costs = world.costs
    breakdown = {"callout": costs.callout(crew.rank, a.on_reserve)}
    if allowance := costs.seniority_allowance(crew.rank, world.seniority.get(a.crew_id) or 0):
        breakdown["seniority_allowance"] = allowance
    if a.positioning is not None:
        breakdown["positioning"] = costs.deadhead_positioning
    elif travel := costs.travel(world.reachability.get(a.crew_id) or 0):
        breakdown["travel"] = travel
    if a.departure_slip_hours:
        breakdown["delay"] = round(a.departure_slip_hours * costs.delay_per_duty_hour)
    return (sum(breakdown.values()), a.departure_slip_hours, a, crew, breakdown, strategy)


def cabin_minimum(world: World, pairing_id: str) -> int:
    """Fewest cabin crew the trip may fly with, sized on its largest aircraft."""
    seats = max(world.flights[f].seats for f in world.pairing(pairing_id).flight_ids)
    return -(-seats // SEATS_PER_CABIN_CREW)


def cabin_present(world: World, pairing_id: str, crew_ids: Iterable[str]) -> int:
    """How many of the trip's cabin crew fly all of it, i.e. aren't reported unavailable on
    any of its days (or only from after that day's last departure)."""
    return sum(1 for c in crew_ids if not _misses_part_of(world, c, pairing_id))


def _misses_part_of(world: World, crew_id: str, pairing_id: str) -> bool:
    out = {on for on, _ in world.unavailable.get(crew_id, ())}
    for day in world.pairing(pairing_id).days:
        if day.date not in out:
            continue
        since = world.unavailable_from.get((crew_id, day.date))
        if since is None or any(world.flights[f].dep_utc >= since for f in day.flight_ids):
            return True
    return False


def _may_operate_short(world: World, pairing_id: str) -> bool:
    """Does the trip keep its cabin minimum without the missing member?

    Everyone reported unavailable on its days counts as missing (at least one, the seat being
    asked about), so two out on one trip can't both be flown "one short"."""
    pairing = world.pairing(pairing_id)
    cabin = [s.crew_id for s in pairing.crew if s.role in (Rank.CABIN_CREW, Rank.SENIOR_CABIN_CREW)]
    missing = max(1, len(cabin) - cabin_present(world, pairing_id, cabin))
    return len(cabin) - missing >= cabin_minimum(world, pairing_id)


def find_options(
    world: World,
    pairing_id: str,
    role: Rank | str,
    delay_hours: float = 0.0,
    delay_date: date | None = None,
) -> OptionsResult:
    """Every legal way to fill ``role`` on ``pairing_id``, cheapest first.

    With a delay, candidates are judged against the delayed schedule, since the cover has to
    fly the day as it will now run.
    """
    role = Rank(role)
    pairing = world.pairing(pairing_id)
    if any(f in world.cancelled for f in pairing.flight_ids):
        raise TripCancelled(f"{pairing_id} is cancelled: there is nothing to re-crew")
    first_report = world.days_of(pairing_id)[0].report_utc
    if first_report < world.snapshot_utc:
        raise AlreadyStarted(
            f"{pairing_id} reported at {first_report:%Y-%m-%d %H:%M}Z, before now "
            f"({world.snapshot_utc:%Y-%m-%d %H:%M}Z); it can no longer be re-crewed"
        )
    on_board = {s.crew_id for s in pairing.crew}
    airline = world.airline_of_pairing(pairing_id)

    def candidates(rank: Rank, qualified: frozenset[str] | None = None) -> list:
        """Crew of ``rank`` at the trip's airline, not already on it, holding ``qualified``
        when given."""
        return sorted(
            (
                c
                for c in world.crew.values()
                if c.rank is rank
                and c.crew_id not in on_board
                and (airline is None or world.airline_of_crew.get(c.crew_id) == airline)
                and (qualified is None or c.crew_id in qualified)
            ),
            key=lambda c: c.crew_id,
        )

    pool = candidates(role)
    assessed = [_assess(world, c.crew_id, pairing_id, delay_hours, delay_date) for c in pool]
    legal = [a for a in assessed if a.legal]
    illegal = [a for a in assessed if not a.legal]

    rows = [_row(world, a, Strategy(_strategy(a))) for a in legal]
    rows.sort(key=lambda r: (r[0], r[1], r[2].crew_id))

    # Short of own-rank cover: add the qualified other rank after them, most experienced
    # first. Regulation allows exactly these two substitutions.
    substitute = {
        Rank.CAPTAIN: (Rank.FIRST_OFFICER, world.command_qualified, Strategy.COMMAND_UPGRADE),
        Rank.FIRST_OFFICER: (Rank.CAPTAIN, world.right_seat_qualified, Strategy.RIGHT_SEAT),
    }.get(role)
    if substitute and len(rows) < SUBSTITUTE_BELOW:
        other, qualified, strategy = substitute
        extra = [
            _assess(world, c.crew_id, pairing_id, delay_hours, delay_date)
            for c in candidates(other, qualified)
        ]
        more = [_row(world, a, strategy) for a in extra if a.legal]
        more.sort(key=lambda r: (-(world.experience.get(r[2].crew_id) or 0), r[0], r[2].crew_id))
        rows += more

    options = []
    for i, (cost, slip, a, crew, breakdown, strategy) in enumerate(rows, start=1):
        options.append(
            Option(
                rank=i,
                strategy=strategy,
                crew_id=a.crew_id,
                name=crew.name,
                crew_rank=crew.rank,
                base=crew.base,
                cost_inr=cost,
                cost_breakdown=breakdown,
                delay_hours=slip,
                positioning_flight=a.positioning.flight_id if a.positioning else None,
                reachability_minutes=world.reachability.get(a.crew_id),
                seniority=world.seniority.get(a.crew_id),
                risk_score=world.risk.get(a.crew_id),
                resilience=_resilience(world, a, crew.rank, crew.base),
                experience_years=world.experience.get(a.crew_id),
            )
        )

    costs = world.costs
    if role is Rank.CABIN_CREW and _may_operate_short(world, pairing_id):
        options.append(
            Option(
                rank=len(options) + 1,
                strategy=Strategy.SHORT_CREW,
                crew_id=None,
                name="Operate one cabin crew short",
                crew_rank=None,
                base=None,
                cost_inr=0,
                cost_breakdown={},
                delay_hours=0.0,
            )
        )

    n_flights = len(pairing.flight_ids)
    cancel_cost = n_flights * costs.cancellation_per_flight
    cancel = Option(
        rank=len(options) + 1,
        strategy=Strategy.CANCEL,
        crew_id=None,
        name=f"Cancel all {n_flights} flights of the pairing",
        crew_rank=None,
        base=None,
        cost_inr=cancel_cost,
        cost_breakdown={"cancellation": cancel_cost},
        delay_hours=0.0,
    )
    options.append(cancel)

    best: dict[Strategy, Option] = {}
    for o in options[:-1]:
        best.setdefault(o.strategy, o)
    strategies = [*best.values(), cancel]

    crew_options = [o for o in options if o.crew_id is not None]
    policies: dict[str, Option] = {}
    if crew_options:
        policies["cheapest"] = crew_options[0]
        policies["least_delay"] = min(
            crew_options, key=lambda o: (o.delay_hours, o.cost_inr, o.crew_id)
        )
        policies["keeps_most_reserve_cover"] = min(
            crew_options, key=lambda o: (-(o.resilience or 0), o.cost_inr, o.crew_id)
        )

    excluded = tuple(
        Exclusion(
            crew_id=a.crew_id,
            name=world.crew[a.crew_id].name,
            stage=_stage(a),
            rule_ids=tuple(dict.fromkeys(v.rule_id for v in a.blocking)),
            reasons=tuple(v.detail for v in a.blocking),
        )
        for a in illegal
    )

    dropped = Counter(e.stage for e in excluded)
    funnel = [FunnelStage("considered", len(pool), 0, "")]
    remaining = len(pool)
    for stage, _, reason in FUNNEL:
        if n := dropped.get(stage, 0):
            remaining -= n
            funnel.append(FunnelStage(stage, remaining, n, reason))
    funnel.append(FunnelStage("legal", remaining, 0, ""))

    return OptionsResult(
        pairing_id=pairing_id,
        role=role,
        options=tuple(options),
        strategies=tuple(strategies),
        excluded=excluded,
        funnel=tuple(funnel),
        policies=policies,
    )
