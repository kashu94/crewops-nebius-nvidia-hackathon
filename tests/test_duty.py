from datetime import UTC, date, datetime

import pytest

from crewops.domain import AircraftType
from crewops.engine.duty import DutyDay, calendar_window, format_hours, hours_between
from crewops.engine.rules import RuleBook


def day(report_h: int = 6, release_h: int = 15, sectors: int = 3) -> DutyDay:
    return DutyDay(
        date=date(2026, 10, 13),
        report_utc=datetime(2026, 10, 13, report_h, tzinfo=UTC),
        release_utc=datetime(2026, 10, 13, release_h, tzinfo=UTC),
        n_sectors=sectors,
        block_hours=6.0,
        aircraft_type=AircraftType.A320,
        dep_station="BLR",
    )


@pytest.mark.parametrize(
    ("sectors", "limit"), [(1, 13.0), (2, 13.0), (3, 12.5), (4, 12.0), (6, 11.0)]
)
def test_fdp_limit_shrinks_after_second_sector(sectors, limit):
    assert RuleBook().fdp_limit(sectors) == limit


def test_calendar_window_is_inclusive():
    assert calendar_window(date(2026, 10, 12), 7) == (date(2026, 10, 6), date(2026, 10, 12))
    assert calendar_window(date(2026, 10, 12), 1) == (date(2026, 10, 12), date(2026, 10, 12))


@pytest.mark.parametrize(
    ("hours", "text"), [(1.33, "1h20m"), (0.25, "0h15m"), (8.25, "8h15m"), (1.999, "2h00m")]
)
def test_format_hours(hours, text):
    assert format_hours(hours) == text


def test_hours_between_rounds():
    a = datetime(2026, 10, 13, 6, tzinfo=UTC)
    b = datetime(2026, 10, 13, 15, 30, tzinfo=UTC)
    assert hours_between(a, b) == 9.5


def test_delay_stretches_day_shift_moves_it():
    d = day()
    assert d.fdp_hours == 9.0
    assert d.delayed(1.5).fdp_hours == 10.5
    assert d.delayed(1.5).report_utc == d.report_utc
    shifted = d.shifted(3.0)
    assert shifted.fdp_hours == 9.0
    assert shifted.report_utc.hour == 9
