"""A crew member asking about themselves: their trips, their reported absences, their rank and
base, their standby days. Answered from their own roster, before routing.

The general routes are built for crew control, who look things up about anyone; for a crew
member most of those lookups are refused, so "what are my flights today?" came back as "your
role doesn't give access". These answers only ever read the asker's own records.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, timedelta

from crewops.engine import World
from crewops.tools import Caller

FLY_RE = re.compile(
    r"\b(my (next |upcoming )?(flights?|trips?|schedule|roster|pairings?|duty days?|duties)\b|"
    r"when (do|am) i (fly|flying|on duty|working|rostered)|"
    r"do i (fly|have (a |any )?(flights?|trips?|duty))|"
    r"am i (flying|rostered|on duty|working)|where (do|am) i (fly|flying))",
    re.I,
)
OUT_RE = re.compile(
    r"\b(when am i (not available|unavailable|off|absent|away|on leave|out)|"
    r"my (absences?|leave|sick (days|leave)|unavailab\w*|days off)|"
    r"(marked|reported|filed|booked) (me |myself )?(as )?(absent|unavailable|sick|on leave|out)|"
    r"days? i(?: am|'m)? (out|off|away|unavailable|absent))",
    re.I,
)
RANK_RE = re.compile(
    r"\b(am i an? (captain|first officer|senior cabin crew|cabin crew|reserve)|"
    r"(what|which) (is|'s) my (rank|role|position|seat|base|airline)|what (rank|role) am i|"
    r"which airline do i)",
    re.I,
)
STANDBY_RE = re.compile(
    r"\b(am i on (call|standby|reserve)|my (standby|on[- ]call|reserve)|"
    r"when am i on (call|standby))",
    re.I,
)
TRIP_RE = re.compile(r"\bp\s*-?\s*(\d{3,6})\b", re.I)
TODAY_RE = re.compile(r"\btoday\b", re.I)
TOMORROW_RE = re.compile(r"\btomorrow\b", re.I)
WEEK_RE = re.compile(r"\b(this|next) week\b", re.I)

AHEAD_DAYS = 60
SHOW = 6


def _day(d: date) -> str:
    return f"{d.day} {d:%b}"


def _ranges(days: Iterable[date]) -> list[str]:
    """Consecutive days as "21 Oct" or "21 Oct to 23 Oct"."""
    out: list[str] = []
    run: list[date] = []
    for d in sorted(set(days)):
        if run and (d - run[-1]).days != 1:
            out.append(_day(run[0]) if len(run) == 1 else f"{_day(run[0])} to {_day(run[-1])}")
            run = []
        run.append(d)
    if run:
        out.append(_day(run[0]) if len(run) == 1 else f"{_day(run[0])} to {_day(run[-1])}")
    return out


def _window(question: str, today: date) -> tuple[date, date, str]:
    if TODAY_RE.search(question):
        return today, today, "today"
    if TOMORROW_RE.search(question):
        tomorrow = today + timedelta(days=1)
        return tomorrow, tomorrow, "tomorrow"
    if m := WEEK_RE.search(question):
        monday = today - timedelta(days=today.weekday())
        if m.group(1).lower() == "this":
            return today, monday + timedelta(days=6), "this week"
        return monday + timedelta(days=7), monday + timedelta(days=13), "next week"
    return today, today + timedelta(days=AHEAD_DAYS), "coming up"


def _trips(world: World, crew_id: str, first: date, last: date) -> list[str]:
    lines = []
    for pairing in world.pairings.values():
        seat = next((s for s in pairing.crew if s.crew_id == crew_id), None)
        if seat is None:
            continue
        for day in pairing.days:
            if first <= day.date <= last:
                flights = ", ".join(f.split("-")[0] for f in day.flight_ids)
                lines.append(
                    (
                        day.date,
                        f"{_day(day.date)}: {pairing.pairing_id} on {pairing.aircraft} as "
                        f"{seat.role.value}, report {day.report_utc:%H:%M}Z, flights {flights}",
                    )
                )
    return [text for _, text in sorted(lines)]


NEXT_TRIP_RE = re.compile(
    r"\b(my )?(next|upcoming) (trip|flight|pairing|duty)\b|\bmy (trip|pairing)\b", re.I
)


def next_trip(world: World, crew_id: str) -> str | None:
    """The id of the crew member's next trip that hasn't finished, or None."""
    today = world.snapshot_utc.date()
    upcoming = [
        (min(d.date for d in p.days), p.pairing_id)
        for p in world.pairings.values()
        if any(s.crew_id == crew_id for s in p.crew) and any(d.date >= today for d in p.days)
    ]
    return min(upcoming)[1] if upcoming else None


def with_next_trip(question: str, caller: Caller, world: World) -> str:
    """ "am I legal for my next trip" with that trip's id added, so the legality check runs."""
    if (
        not caller.crew_id
        or TRIP_RE.search(question)
        or not NEXT_TRIP_RE.search(question)
        or not re.search(r"\blegal|\bfit to fly|\ballowed to (fly|operate)", question, re.I)
    ):
        return question
    trip = next_trip(world, caller.crew_id)
    return f"{question} ({trip})" if trip else question


def crew_self(question: str, caller: Caller, world: World) -> str | None:
    """The answer to a crew member's question about themselves, or None to route as usual."""
    crew_id = caller.crew_id
    crew = world.crew.get(crew_id or "")
    if not crew_id or crew is None:
        return None
    today = world.snapshot_utc.date()
    out_days = [d for d, _ in world.unavailable.get(crew_id, ())]
    standby = sorted(
        d for duty in world.reserves.get(crew_id, ()) for d in duty.dates if d >= today
    )

    if (m := TRIP_RE.search(question)) and re.search(
        r"\blegal|\bfly|\btrip|\bpairing", question, re.I
    ):
        pairing_id = f"P-{m.group(1)}"
        if pairing_id not in world.pairings:
            mine = _trips(world, crew_id, today, today + timedelta(days=AHEAD_DAYS))
            nearest = f" Your next: {mine[0]}." if mine else ""
            return f"There's no trip {pairing_id}.{nearest}"
        return None  # a real trip: the legality engine answers

    if OUT_RE.search(question):
        upcoming = [d for d in out_days if d >= today - timedelta(days=5)]
        if not upcoming:
            return "You have no days reported unavailable or on leave."
        return "You're reported unavailable on " + "; ".join(_ranges(upcoming)) + "."

    if STANDBY_RE.search(question):
        if not standby:
            return "You're not on reserve standby."
        duty = world.reserves[crew_id][0]
        days = "; ".join(_ranges(standby[:30]))
        return (
            f"You're on call at {duty.base}, {duty.window_start:%H:%M} to "
            f"{duty.window_end:%H:%M}Z, on {days}."
        )

    if FLY_RE.search(question):
        first, last, label = _window(question, today)
        trips = _trips(world, crew_id, first, last)
        away = [d for d in out_days if first <= d <= last]
        note = f" You're reported unavailable on {'; '.join(_ranges(away))}." if away else ""
        if not trips:
            if standby and any(first <= d <= last for d in standby):
                return (
                    f"No trips {label}; you're on reserve standby, so you may be called out.{note}"
                )
            return f"No trips {label}.{note}"
        more = f" And {len(trips) - SHOW} more." if len(trips) > SHOW else ""
        return f"Your trips {label}: " + "; ".join(trips[:SHOW]) + "." + more + note

    if RANK_RE.search(question):
        airline = world.airline_names.get(world.airline_of_crew.get(crew_id, ""), "")
        reserve = ", on reserve standby" if standby else ""
        flies = f" with {airline}" if airline else ""
        return f"You're a {crew.rank.value}{flies}, based at {crew.base}{reserve}."
    return None
