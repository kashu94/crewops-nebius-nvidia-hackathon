"""Legality against the seed week, pinned to known answers."""

from datetime import date

import pytest

from crewops.engine import UnknownEntity, assess, build_world
from crewops.ingestion.seed import load_seed


@pytest.fixture(scope="module")
def world():
    return build_world(load_seed())


def details(a):
    return [v.detail for v in a.blocking]


def test_duty_clock_canary(world):
    c = world.state("C-1042")
    assert c.duty_in_window(date(2026, 10, 6), date(2026, 10, 12)) == 20.93


def test_every_rostered_crew_is_legal_except_known_cert_lapse(world):
    illegal = [
        (seat.crew_id, pid)
        for pid, p in world.pairings.items()
        for seat in p.crew
        if not assess(world, seat.crew_id, pid).legal
    ]
    assert illegal == [("C-5417", "P-2213")]


def test_cert_lapse_detail(world):
    a = assess(world, "C-5417", "P-2213")
    assert details(a) == ["recurrent_training expired 2026-10-15, invalid on 2026-10-17"]


def test_reserve_captain_is_clean_cover(world):
    a = assess(world, "C-3310", "P-2291")
    assert a.legal and a.on_reserve and a.positioning is None
    assert len(a.verdicts) == 13  # 7 rules on day one, 6 on day two


def test_duty_breach_quotes_the_overrun(world):
    a = assess(world, "C-2087", "P-2291")
    assert not a.legal
    assert details(a)[0] == "would exceed 60h/7d by 1h20m on 2026-10-13 (total 61.33h)"


def test_reserve_window_must_cover_report(world):
    a = assess(world, "C-3305", "P-2291")
    assert "on-call window 00:00-05:30Z does not cover report at 06:00Z" in details(a)
    assert not a.on_reserve


def test_positioning_from_other_base(world):
    # C-3555, a Deccan Express reserve based in Delhi, positioned for a Bengaluru trip.
    a = assess(world, "C-3555", "P-2213")
    assert a.legal and a.on_reserve
    assert a.positioning.flight_id == "DX402-2026-10-17"
    assert a.departure_slip_hours == 7.0


def test_positioning_slip_cannot_eat_the_overnight_rest(world):
    # C-2341 (Konkan Skyways, Delhi): positioning delays day one; day two keeps its 04:00
    # report, leaving 9.5h rest.
    a = assess(world, "C-2341", "P-2291")
    assert a.days[1].report_utc == world.days_of("P-2291")[1].report_utc
    assert details(a) == ["only 9.5h rest between 2026-10-13 and 2026-10-14 of the pairing"]


def test_duty_flown_on_snapshot_day_counts_once(world):
    # C-5837 flew 11.25h on 10-12; the duty-clock history records 0 for that day.
    c = world.state("C-5837")
    assert c.duty_in_window(date(2026, 10, 6), date(2026, 10, 12)) == 30.88
    # Covering P-2203 (11.25h on 10-14): the 7 days ending 10-14 then hold 42.13h ...
    on_the_day = c.duty_in_window(date(2026, 10, 8), date(2026, 10, 14), "P-2203") + 11.25
    assert round(on_the_day, 2) == 42.13
    # ... and the worst window is the one ending on their next trip, P-2207 on 10-18.
    duty = next(
        v for v in assess(world, "C-5837", "P-2203").verdicts if v.rule_id == "RULE-DUTY-02"
    )
    assert duty.used == 45.0
    assert duty.detail == "45.0h of 60h/7d in the 7 days ending 2026-10-18"


def test_crew_rostered_on_a_pairing_are_in_position(world):
    # P-2289 starts in DEL with BLR-based crew; the roster itself places them there.
    for seat in world.pairing("P-2289").crew:
        assert assess(world, seat.crew_id, "P-2289").legal


def test_downstream_rest_conflict(world):
    a = assess(world, "C-5820", "P-2291")
    assert details(a) == ["only 11.75h rest before P-2218 on 2026-10-15"]


def test_wrong_aircraft_type(world):
    # Each airline flies one type, so an ATR captain is also another airline's.
    assert details(assess(world, "C-3315", "P-2291")) == [
        "not rated on A320",
        "works for Nilgiri Connect, not Konkan Skyways, which flies VT-DXC",
    ]


def test_crew_of_another_airline_are_never_legal(world):
    a = assess(world, "C-2210", "P-2291")  # Deccan Express captain, Konkan trip
    assert not a.legal
    assert "works for Deccan Express, not Konkan Skyways, which flies VT-DXC" in details(a)


def test_delay_can_breach_fdp(world):
    p = world.pairing("P-2291")
    captain = p.crew[0].crew_id
    assert assess(world, captain, "P-2291").legal
    late = assess(world, captain, "P-2291", delay_hours=4.0)
    assert any(v.rule_id == "RULE-FDP-01" for v in late.blocking)


def test_unknown_ids_raise(world):
    with pytest.raises(UnknownEntity, match="no crew 'C-1024'"):
        assess(world, "C-1024", "P-2291")
    with pytest.raises(UnknownEntity, match="no pairing"):
        assess(world, "C-1042", "P-0000")


def test_negative_delay_rejected(world):
    with pytest.raises(ValueError):
        assess(world, "C-3310", "P-2291", delay_hours=-1)


def test_a_clash_is_named_before_the_rest_around_it(world):
    # C-1042 flies P-2291 on 13-14 Oct; P-2217 reports at 02:30Z on the 14th, inside
    # P-2291's second day (04:00-14:45Z) and also only 11h after its first day ends.
    assert (
        details(assess(world, "C-1042", "P-2217"))[0]
        == "double-booked: P-2291 overlaps on 2026-10-14"
    )
