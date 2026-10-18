"""The operating calendar to 31 December: the seed week carried forward, rotated, legal."""

from datetime import UTC, date, datetime

import pytest

from crewops.agent.entities import Vocabulary, extract_dates
from crewops.engine import build_world
from crewops.ingestion.calendar import LAST_DAY, _illegal, extend
from crewops.ingestion.seed import Severity, load_seed, validate


@pytest.fixture(scope="module")
def calendar():
    return extend(load_seed())


def test_every_week_to_the_end_of_december_is_rostered_and_legal(calendar):
    seed = load_seed()
    days = sorted({f.date for f in calendar.flights})
    assert days[0] == date(2026, 10, 12) and days[-1] <= LAST_DAY
    assert (LAST_DAY - days[-1]).days < 7
    assert len(calendar.flights) > 10 * len(seed.flights)
    assert not [i for i in validate(calendar) if i.severity is Severity.ERROR]
    # Only the seed week's deliberate exception (a lapsed certificate) breaks a rule.
    assert _illegal(calendar) == [("P-2213", "C-5417")]
    # Trip ids keep their format, a hundred on each week.
    ids = {p.pairing_id for p in calendar.pairings}
    assert {"P-2291", "P-2391", "P-2491"} <= ids
    assert all(p.days[-1].date <= LAST_DAY for p in calendar.pairings)


def test_crew_rotate_and_the_reserve_pool_stays(calendar):
    def crew_of(pairing_id):
        return {s.crew_id for p in calendar.pairings if p.pairing_id == pairing_id for s in p.crew}

    assert crew_of("P-2291") != crew_of("P-2391")  # someone else flies it the next week
    flying = {s.crew_id for p in calendar.pairings for s in p.crew}
    # Everyone fit to fly does, some weeks: not the reserve pool, nor those on leave or
    # with a lapsed certificate.
    assert len(flying) >= 120
    pool = {r.crew_id: r for r in calendar.reserves}
    assert len(pool) == len(load_seed().reserves)
    assert len(pool["C-3310"].dates) > 70  # on call every day, one record
    later = {s.crew_id for p in calendar.pairings if int(p.pairing_id[2:]) >= 2300 for s in p.crew}
    assert not later & set(pool)  # after the seed week, the reserve pool stays on call
    assert extend(load_seed()) == calendar  # the same seed always gives the same calendar


def test_now_moves_with_the_clock_and_questions_look_ahead(calendar):
    later = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)
    world = build_world(calendar, later)
    assert world.snapshot_utc == later
    assert build_world(calendar).snapshot_utc == load_seed().snapshot_utc  # tests stay fixed
    vocab = Vocabulary.from_world(world)
    assert extract_dates("who flies on the 15th?", vocab) == ["2026-11-15"]
    assert extract_dates("who flies on the 5th?", vocab) == ["2026-12-05"]
    assert extract_dates("reserves on Friday", vocab) == ["2026-11-13"]
    assert extract_dates("what is happening today", vocab) == ["2026-11-10"]


def test_every_aircraft_flies_on_from_where_it_landed_and_no_gate_is_held_twice(calendar):
    from collections import defaultdict

    by_tail = defaultdict(list)
    for f in calendar.flights:
        by_tail[f.aircraft].append(f)
    for tail, legs in by_tail.items():
        legs.sort(key=lambda f: f.dep_utc)
        for a, b in zip(legs, legs[1:], strict=False):
            # VT-DXC's two-day rotation continues across weeks rather than restarting.
            assert a.arr_station == b.dep_station and a.arr_utc <= b.dep_utc, (tail, a, b)
    by_gate = defaultdict(list)
    for g in calendar.gates:
        by_gate[g.gate].append(g)
    for gate, slots in by_gate.items():
        slots.sort(key=lambda g: g.start_utc)
        for a, b in zip(slots, slots[1:], strict=False):
            assert a.end_utc <= b.start_utc, (gate, a.flight_id, b.flight_id)
    assert sorted(g.flight_id for g in calendar.gates) == sorted(
        f.flight_id for f in calendar.flights
    )


def test_crew_only_ever_fly_their_own_airlines_aircraft_with_the_rating(calendar):
    tail_airline = {t: a.code for a in calendar.airlines for t in a.aircraft}
    crew = {c.crew_id: c for c in calendar.crew}
    flight = {f.flight_id: f for f in calendar.flights}
    for p in calendar.pairings:
        aircraft_type = flight[p.flight_ids[0]].aircraft_type
        for seat in p.crew:
            assert crew[seat.crew_id].airline == tail_airline[p.aircraft], (p.pairing_id, seat)
            if seat.role.is_pilot:
                assert aircraft_type in crew[seat.crew_id].ratings, (p.pairing_id, seat)
    assert all(f.flight_no[:2] == tail_airline[f.aircraft] for f in calendar.flights)
