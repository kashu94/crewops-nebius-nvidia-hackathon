"""Duty-time arithmetic.

Two conventions give plausible but wrong legality answers if you miss them. Duty and
flight-hour limits use calendar-day windows (UTC dates, including the duty date), not rolling
168- or 672-hour windows from an instant. And the flight duty period limit shrinks with
sector count, 13 h minus half an hour per sector beyond the second, so four sectors cap at 12 h.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from crewops.domain import AircraftType


def hours_between(start: datetime, end: datetime) -> float:
    """Elapsed hours, rounded to two decimals."""
    return round((end - start).total_seconds() / 3600.0, 2)


def calendar_window(end: date, days: int) -> tuple[date, date]:
    """Inclusive window of ``days`` calendar days ending on ``end``.

    Seven days ending the 14th run 8th to 14th, not from the 7th. Easiest off-by-one in the
    engine to get wrong, so it lives here and nowhere else.
    """
    return end - timedelta(days=days - 1), end


def format_hours(value: float) -> str:
    """``1.33`` -> ``'1h20m'``, the way a breach is read out."""
    total_minutes = round(abs(value) * 60)
    return f"{total_minutes // 60}h{total_minutes % 60:02d}m"


@dataclass(frozen=True, slots=True)
class DutyDay:
    date: date
    report_utc: datetime
    release_utc: datetime
    n_sectors: int
    block_hours: float
    aircraft_type: AircraftType
    dep_station: str

    @property
    def fdp_hours(self) -> float:
        return hours_between(self.report_utc, self.release_utc)

    def delayed(self, hours: float) -> DutyDay:
        """Late departure. Crew still report on time, so only release moves and the day gets
        longer; that's how a delay breaches FDP."""
        return replace(self, release_utc=self.release_utc + timedelta(hours=hours))

    def shifted(self, hours: float) -> DutyDay:
        """Whole day moved later (crew positioning in from another base). Length unchanged."""
        shift = timedelta(hours=hours)
        return replace(
            self, report_utc=self.report_utc + shift, release_utc=self.release_utc + shift
        )
