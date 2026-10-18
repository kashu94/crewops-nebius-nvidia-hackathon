"""Is this crew member legal for this pairing, and if not, why?

``assess`` is the only place the rest of the system gets a legality answer from. Before the
rules run it applies the two things that change when a duty happens. Positioning: someone
based elsewhere flies in first, so day one slips until they can report (later days keep
their schedule). A what-if delay: a late first departure stretches day one, which is how a
delay pushes someone past their duty-period limit.

It also checks the reserve on-call window. A reserve whose window doesn't cover the report
time is unavailable, not a cheaper option.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from crewops.engine.duty import DutyDay
from crewops.engine.rules import (
    BASE,
    QUAL,
    REST,
    RuleVerdict,
    blocking,
    evaluate,
    fail,
)
from crewops.engine.world import Positioning, World


@dataclass(frozen=True, slots=True)
class Assessment:
    crew_id: str
    pairing_id: str
    verdicts: tuple[RuleVerdict, ...]
    positioning: Positioning | None
    delay_hours: float
    """The what-if delay applied to one duty day (the first unless another was given)."""
    on_reserve: bool
    """On call that day with a window covering the required report time."""
    days: tuple[DutyDay, ...]
    """The duty days as evaluated, after positioning and delay."""
    reach_slip_hours: float = 0.0
    """Departure slip because, called now, they can't reach the airport by report time. Set
    by the option search, which judged them on that slip."""

    @property
    def legal(self) -> bool:
        return not any(v.failed for v in self.verdicts)

    @property
    def blocking(self) -> list[RuleVerdict]:
        return blocking(self.verdicts)

    @property
    def departure_slip_hours(self) -> float:
        """Positioning delay, else the time to reach the airport when called now."""
        if self.positioning:
            return self.positioning.delay_hours
        return self.reach_slip_hours


def _intra_pairing_rest(days: list[DutyDay], world: World) -> list[RuleVerdict]:
    """Rest between consecutive days of the same pairing.

    The published roster always respects it, so only a delay or positioning slip can break
    it. When one eats into the overnight it has to show up as a rest breach.
    """
    out = []
    minimum = world.book.min_rest_hours
    for prev, nxt in zip(days, days[1:], strict=False):
        rest = round((nxt.report_utc - prev.release_utc).total_seconds() / 3600, 2)
        if rest < minimum:
            out.append(
                fail(
                    REST,
                    f"only {rest}h rest between {prev.date} and {nxt.date} of the pairing",
                    used=rest,
                    limit=minimum,
                    headroom=round(rest - minimum, 2),
                    date=nxt.date,
                )
            )
    return out


def assess(
    world: World,
    crew_id: str,
    pairing_id: str,
    delay_hours: float = 0.0,
    delay_date: date | None = None,
) -> Assessment:
    """Evaluate all seven rules for one crew member on every day of one pairing."""
    if delay_hours < 0:
        raise ValueError("delay_hours cannot be negative")

    crew = world.state(crew_id)
    days = list(world.days_of(pairing_id))
    first = days[0]

    # Crew already on this pairing: a rest ending after one of its days is rest from that day,
    # not rest still owed before it.
    own = [d for d in crew.duties if d.pairing_id == pairing_id]
    if crew.last_rest_ended and any(d.release_utc <= crew.last_rest_ended for d in own):
        crew = replace(crew, last_rest_ended=None)

    # The base rule is about calling someone in; crew rostered on the pairing are in position
    # by definition.
    rostered_here = bool(own)

    positioning = None
    if crew.base != first.dep_station and not rostered_here:
        positioning = world.positioning(crew, first)
        if positioning and positioning.delay_hours:
            # Only day one waits. Later days keep their schedule, so the late release eats
            # the overnight and _intra_pairing_rest catches it if rest drops below minimum.
            days[0] = days[0].shifted(positioning.delay_hours)

    if delay_hours:
        index = 0
        if delay_date is not None:
            matching = [i for i, d in enumerate(days) if d.date == delay_date]
            if not matching:
                raise ValueError(f"{pairing_id} has no duty day on {delay_date}")
            index = matching[0]
        days[index] = days[index].delayed(delay_hours)

    verdicts = evaluate(
        crew,
        days,
        world.book,
        exclude_pairing=pairing_id,
        positioned=positioning is not None or rostered_here,
    )
    verdicts += _intra_pairing_rest(days, world)

    # crew only fly their own airline's aircraft
    theirs = world.airline_of_crew.get(crew_id)
    flies = world.airline_of_pairing(pairing_id)
    if theirs and flies and theirs != flies:
        names = world.airline_names
        verdicts.append(
            fail(
                QUAL,
                f"works for {names.get(theirs, theirs)}, not {names.get(flies, flies)}, "
                f"which flies {world.pairing(pairing_id).aircraft}",
                date=days[0].date,
            )
        )

    # Reported unavailable in another open disruption, whatever the roster says. Shown as a
    # QUAL failure ("not available to fly").
    duty_dates = {d.date for d in days}
    for when, report_id in world.unavailable.get(crew_id, ()):
        if when in duty_dates:
            verdicts.append(
                fail(QUAL, f"reported unavailable on {when} (report {report_id})", date=when)
            )
            break

    reserves = world.reserves.get(crew_id, ())
    rostered = [r for r in reserves if first.date in r.dates]
    on_reserve = any(r.covers(days[0].report_utc) for r in rostered)
    if rostered and not on_reserve:
        r = rostered[0]
        verdicts.append(
            fail(
                BASE,
                f"on-call window {r.window_start:%H:%M}-{r.window_end:%H:%M}Z does not cover "
                f"report at {days[0].report_utc:%H:%M}Z",
                date=first.date,
            )
        )

    return Assessment(
        crew_id=crew_id,
        pairing_id=pairing_id,
        verdicts=tuple(verdicts),
        positioning=positioning,
        delay_hours=delay_hours,
        on_reserve=on_reserve,
        days=tuple(days),
    )
