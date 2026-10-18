"""Typed records for one crew operation.

Each record is a frozen dataclass built by ``from_dict``, which validates while parsing and
names the failing field (``crew.rank: unknown value 'Pilot'``), so nothing downstream sees a
raw dict or a timestamp string.

Instants are aware UTC datetimes, calendar fields are dates, on-call windows are UTC times,
and money is whole rupees.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Any

STATION_RE = re.compile(r"^[A-Z]{3}$")


class DataError(ValueError):
    """Record failed to parse; the message names the field."""


class Rank(StrEnum):
    CAPTAIN = "Captain"
    FIRST_OFFICER = "First Officer"
    SENIOR_CABIN_CREW = "Senior Cabin Crew"
    CABIN_CREW = "Cabin Crew"

    @property
    def is_pilot(self) -> bool:
        return self in (Rank.CAPTAIN, Rank.FIRST_OFFICER)


class AircraftType(StrEnum):
    A320 = "A320"
    ATR72 = "ATR72"


class CrewStatus(StrEnum):
    ACTIVE = "active"
    LEAVE = "leave"
    TRAINING = "training"


class CertType(StrEnum):
    LICENCE = "licence"
    MEDICAL_CLASS1 = "medical_class1"
    RECURRENT_TRAINING = "recurrent_training"
    DANGEROUS_GOODS = "dangerous_goods"


def _req(d: Mapping[str, Any], key: str, ctx: str) -> Any:
    if not isinstance(d, Mapping):
        raise DataError(f"{ctx}: expected an object, got {type(d).__name__}")
    if key not in d or d[key] is None:
        raise DataError(f"{ctx}.{key}: missing")
    return d[key]


def _str(d: Mapping[str, Any], key: str, ctx: str) -> str:
    value = _req(d, key, ctx)
    if not isinstance(value, str) or not value.strip():
        raise DataError(f"{ctx}.{key}: expected a non-empty string, got {value!r}")
    return value.strip()


def _int(d: Mapping[str, Any], key: str, ctx: str, minimum: int = 0) -> int:
    value = _req(d, key, ctx)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DataError(f"{ctx}.{key}: expected an integer, got {value!r}")
    if value < minimum:
        raise DataError(f"{ctx}.{key}: must be >= {minimum}, got {value}")
    return value


def _float(d: Mapping[str, Any], key: str, ctx: str, minimum: float = 0.0) -> float:
    value = _req(d, key, ctx)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DataError(f"{ctx}.{key}: expected a number, got {value!r}")
    if value < minimum:
        raise DataError(f"{ctx}.{key}: must be >= {minimum}, got {value}")
    return float(value)


def _enum[E: StrEnum](enum: type[E], d: Mapping[str, Any], key: str, ctx: str) -> E:
    value = _req(d, key, ctx)
    try:
        return enum(value)
    except ValueError:
        allowed = ", ".join(e.value for e in enum)
        raise DataError(f"{ctx}.{key}: unknown value {value!r} (allowed: {allowed})") from None


def _station(d: Mapping[str, Any], key: str, ctx: str) -> str:
    value = _str(d, key, ctx)
    if not STATION_RE.match(value):
        raise DataError(f"{ctx}.{key}: expected a 3-letter station code, got {value!r}")
    return value


def parse_utc(value: Any, ctx: str = "value") -> datetime:
    """``2026-10-12T02:30:00Z`` -> aware UTC datetime."""
    if not isinstance(value, str):
        raise DataError(f"{ctx}: expected an ISO-8601 UTC timestamp, got {value!r}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise DataError(f"{ctx}: expected an ISO-8601 UTC timestamp, got {value!r}") from None
    if parsed.tzinfo is None:
        raise DataError(f"{ctx}: timestamp has no timezone, got {value!r}")
    return parsed.astimezone(UTC)


def parse_date(value: Any, ctx: str = "value") -> date:
    if not isinstance(value, str):
        raise DataError(f"{ctx}: expected a YYYY-MM-DD date, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise DataError(f"{ctx}: expected a YYYY-MM-DD date, got {value!r}") from None


def parse_hhmm(value: Any, ctx: str = "value") -> time:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise DataError(f"{ctx}: expected HH:MM, got {value!r}")
    try:
        return time.fromisoformat(value)
    except ValueError:
        raise DataError(f"{ctx}: expected HH:MM, got {value!r}") from None


def _list(d: Mapping[str, Any], key: str, ctx: str) -> list[Any]:
    value = _req(d, key, ctx)
    if not isinstance(value, list):
        raise DataError(f"{ctx}.{key}: expected a list, got {type(value).__name__}")
    return value


@dataclass(frozen=True, slots=True)
class Crew:
    crew_id: str
    name: str
    rank: Rank
    base: str
    ratings: tuple[AircraftType, ...]
    seniority: int
    """Years of service. Paid as a callout allowance, so it only affects rank via cost."""
    reachability_minutes: int
    status: CrewStatus
    airline: str | None = None
    """Two-letter code of the employing airline. Cover only comes from the same one."""
    experience_years: int | None = None
    """Years flying, which orders a command upgrade among first officers."""
    command_qualified: bool = False
    """FO cleared to command (upgrade trained), can fill a captain's seat."""
    right_seat_qualified: bool = False
    """Captain current in the right seat, can fill an FO's seat."""

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], ctx: str = "crew") -> Crew:
        ratings = _list(d, "ratings", ctx)
        try:
            parsed = tuple(AircraftType(r) for r in ratings)
        except ValueError:
            raise DataError(f"{ctx}.ratings: unknown aircraft type in {ratings!r}") from None
        return cls(
            crew_id=_str(d, "crew_id", ctx),
            name=_str(d, "name", ctx),
            rank=_enum(Rank, d, "rank", ctx),
            base=_station(d, "base", ctx),
            ratings=parsed,
            seniority=_int(d, "seniority", ctx),
            reachability_minutes=_int(d, "reachability_minutes", ctx),
            status=_enum(CrewStatus, d, "status", ctx),
            airline=_str(d, "airline", ctx) if d.get("airline") is not None else None,
            experience_years=_int(d, "experience_years", ctx)
            if d.get("experience_years") is not None
            else None,
            command_qualified=_flag(d, "command_qualified", ctx),
            right_seat_qualified=_flag(d, "right_seat_qualified", ctx),
        )


def _flag(d: Mapping[str, Any], key: str, ctx: str) -> bool:
    value = d.get(key, False)
    if not isinstance(value, bool):
        raise DataError(f"{ctx}.{key}: expected true or false, got {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class Certification:
    crew_id: str
    cert_type: CertType
    valid_from: date
    valid_to: date

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], ctx: str = "certification") -> Certification:
        return cls(
            crew_id=_str(d, "crew_id", ctx),
            cert_type=_enum(CertType, d, "cert_type", ctx),
            valid_from=parse_date(_req(d, "valid_from", ctx), f"{ctx}.valid_from"),
            valid_to=parse_date(_req(d, "valid_to", ctx), f"{ctx}.valid_to"),
        )


@dataclass(frozen=True, slots=True)
class DailyHours:
    date: date
    duty_hours: float
    flight_hours: float


@dataclass(frozen=True, slots=True)
class DutyClock:
    """Accrued hours for one crew member up to the snapshot."""

    crew_id: str
    as_of_utc: datetime
    duty_hours_7d: float
    flight_hours_28d: float
    last_rest_ended: datetime
    """When their last rest finished, i.e. the earliest they may report again."""
    daily_history: tuple[DailyHours, ...]

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], ctx: str = "duty_clock") -> DutyClock:
        history = []
        for i, day in enumerate(_list(d, "daily_history", ctx)):
            c = f"{ctx}.daily_history[{i}]"
            history.append(
                DailyHours(
                    date=parse_date(_req(day, "date", c), f"{c}.date"),
                    duty_hours=_float(day, "duty_hours", c),
                    flight_hours=_float(day, "flight_hours", c),
                )
            )
        return cls(
            crew_id=_str(d, "crew_id", ctx),
            as_of_utc=parse_utc(_req(d, "as_of_utc", ctx), f"{ctx}.as_of_utc"),
            duty_hours_7d=_float(d, "duty_hours_7d", ctx),
            flight_hours_28d=_float(d, "flight_hours_28d", ctx),
            last_rest_ended=parse_utc(_req(d, "last_rest_ended", ctx), f"{ctx}.last_rest_ended"),
            daily_history=tuple(sorted(history, key=lambda h: h.date)),
        )


@dataclass(frozen=True, slots=True)
class ReserveDuty:
    """Dates a crew member is on call, and the daily window a callout must fall in."""

    crew_id: str
    base: str
    dates: tuple[date, ...]
    window_start: time
    window_end: time

    def covers(self, when: datetime) -> bool:
        moment = when.astimezone(UTC)
        return moment.date() in self.dates and (
            self.window_start <= moment.time().replace(tzinfo=None) <= self.window_end
        )

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], ctx: str = "reserve") -> ReserveDuty:
        window = _req(d, "oncall_window_utc", ctx)
        dates = _list(d, "dates", ctx)
        start = parse_hhmm(_req(window, "start", f"{ctx}.oncall_window_utc"), f"{ctx}.start")
        end = parse_hhmm(_req(window, "end", f"{ctx}.oncall_window_utc"), f"{ctx}.end")
        if end <= start:
            raise DataError(f"{ctx}.oncall_window_utc: end {end} is not after start {start}")
        return cls(
            crew_id=_str(d, "crew_id", ctx),
            base=_station(d, "base", ctx),
            dates=tuple(sorted(parse_date(x, f"{ctx}.dates[{i}]") for i, x in enumerate(dates))),
            window_start=start,
            window_end=end,
        )


@dataclass(frozen=True, slots=True)
class RiskSignal:
    """Supplied forecast. Shown next to an option, never affects legality or rank."""

    crew_id: str
    as_of_utc: datetime
    score: float
    drivers: tuple[str, ...]

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], ctx: str = "risk_signal") -> RiskSignal:
        score = _float(d, "disruption_risk_score", ctx)
        if score > 1.0:
            raise DataError(f"{ctx}.disruption_risk_score: must be <= 1.0, got {score}")
        return cls(
            crew_id=_str(d, "crew_id", ctx),
            as_of_utc=parse_utc(_req(d, "as_of_utc", ctx), f"{ctx}.as_of_utc"),
            score=score,
            drivers=tuple(str(x) for x in _list(d, "drivers", ctx)),
        )


@dataclass(frozen=True, slots=True)
class Flight:
    flight_id: str
    """Unique per day, e.g. ``DX401-2026-10-12``."""
    flight_no: str
    """What people say, e.g. ``DX401``. Flies on several dates."""
    date: date
    dep_station: str
    arr_station: str
    dep_utc: datetime
    arr_utc: datetime
    block_hours: float
    aircraft: str
    aircraft_type: AircraftType
    seats: int
    booked: int | None = None
    """Passengers booked, if known. Never more than seats."""

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], ctx: str = "flight") -> Flight:
        dep = parse_utc(_req(d, "dep_utc", ctx), f"{ctx}.dep_utc")
        arr = parse_utc(_req(d, "arr_utc", ctx), f"{ctx}.arr_utc")
        if arr <= dep:
            raise DataError(f"{ctx}: arrival {arr:%H:%MZ} is not after departure {dep:%H:%MZ}")
        return cls(
            flight_id=_str(d, "flight_id", ctx),
            flight_no=_str(d, "flight_no", ctx),
            date=parse_date(_req(d, "date", ctx), f"{ctx}.date"),
            dep_station=_station(d, "dep_station", ctx),
            arr_station=_station(d, "arr_station", ctx),
            dep_utc=dep,
            arr_utc=arr,
            block_hours=_float(d, "block_hours", ctx),
            aircraft=_str(d, "aircraft", ctx),
            aircraft_type=_enum(AircraftType, d, "aircraft_type", ctx),
            seats=_int(d, "seats", ctx, minimum=1),
            booked=_booked(d, ctx),
        )


def _booked(d: Mapping[str, Any], ctx: str) -> int | None:
    if d.get("booked") is None:
        return None
    booked = _int(d, "booked", ctx)
    if booked > d["seats"]:
        raise DataError(f"{ctx}.booked: {booked} is more than the {d['seats']} seats")
    return booked


@dataclass(frozen=True, slots=True)
class PairingDay:
    """One duty day of a pairing: which legs it flies and when the crew is on duty."""

    date: date
    flight_ids: tuple[str, ...]
    report_utc: datetime
    release_utc: datetime

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], ctx: str = "day") -> PairingDay:
        report = parse_utc(_req(d, "report_utc", ctx), f"{ctx}.report_utc")
        release = parse_utc(_req(d, "release_utc", ctx), f"{ctx}.release_utc")
        if release <= report:
            raise DataError(f"{ctx}: release is not after report")
        flights = _list(d, "flights", ctx)
        if not flights:
            raise DataError(f"{ctx}.flights: a duty day needs at least one flight")
        return cls(
            date=parse_date(_req(d, "date", ctx), f"{ctx}.date"),
            flight_ids=tuple(str(f) for f in flights),
            report_utc=report,
            release_utc=release,
        )


@dataclass(frozen=True, slots=True)
class PairingSeat:
    """One crew position on a pairing."""

    crew_id: str
    role: Rank


@dataclass(frozen=True, slots=True)
class Pairing:
    """A crew trip on one aircraft: one or more duty days, and who flies it."""

    pairing_id: str
    aircraft: str
    days: tuple[PairingDay, ...]
    crew: tuple[PairingSeat, ...]

    @property
    def first_day(self) -> PairingDay:
        return self.days[0]

    @property
    def flight_ids(self) -> tuple[str, ...]:
        return tuple(f for day in self.days for f in day.flight_ids)

    def holder(self, role: Rank) -> tuple[str, ...]:
        """Crew ids rostered in ``role`` on this pairing."""
        return tuple(seat.crew_id for seat in self.crew if seat.role is role)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], ctx: str = "pairing") -> Pairing:
        days = [
            PairingDay.from_dict(x, f"{ctx}.days[{i}]") for i, x in enumerate(_list(d, "days", ctx))
        ]
        if not days:
            raise DataError(f"{ctx}.days: a pairing needs at least one day")
        seats = [
            PairingSeat(
                crew_id=_str(x, "crew_id", f"{ctx}.crew[{i}]"),
                role=_enum(Rank, x, "role", f"{ctx}.crew[{i}]"),
            )
            for i, x in enumerate(_list(d, "crew", ctx))
        ]
        return cls(
            pairing_id=_str(d, "pairing_id", ctx),
            aircraft=_str(d, "aircraft", ctx),
            days=tuple(sorted(days, key=lambda day: day.date)),
            crew=tuple(seats),
        )


@dataclass(frozen=True, slots=True)
class RosterException:
    """A known breach already flagged on the published roster."""

    crew_id: str
    date: date
    rule_id: str
    note: str

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], ctx: str = "exception") -> RosterException:
        return cls(
            crew_id=_str(d, "crew_id", ctx),
            date=parse_date(_req(d, "date", ctx), f"{ctx}.date"),
            rule_id=_str(d, "rule", ctx),
            note=str(d.get("note") or ""),
        )


@dataclass(frozen=True, slots=True)
class GateSlot:
    """A boarding gate held for one departure, from boarding start to departure."""

    gate: str
    pairing_id: str
    flight_id: str
    date: date
    start_utc: datetime
    end_utc: datetime
    aircraft_type: AircraftType

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], ctx: str = "gate") -> GateSlot:
        start = parse_utc(_req(d, "boarding_start_time", ctx), f"{ctx}.boarding_start_time")
        end = parse_utc(_req(d, "boarding_end_time", ctx), f"{ctx}.boarding_end_time")
        if end <= start:
            raise DataError(f"{ctx}: boarding end is not after boarding start")
        return cls(
            gate=_str(d, "boarding_gate_number", ctx),
            pairing_id=_str(d, "pairing_id", ctx),
            flight_id=_str(d, "flight_id", ctx),
            date=parse_date(_req(d, "date", ctx), f"{ctx}.date"),
            start_utc=start,
            end_utc=end,
            aircraft_type=_enum(AircraftType, d, "aircraft_type", ctx),
        )


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    text: str
    params: Mapping[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], ctx: str = "rule") -> Rule:
        params = d.get("params") or {}
        if not isinstance(params, Mapping):
            raise DataError(f"{ctx}.params: expected an object")
        return cls(
            rule_id=_str(d, "rule_id", ctx),
            text=_str(d, "text", ctx),
            params=dict(params),
        )


@dataclass(frozen=True, slots=True)
class CostTable:
    """Rupee costs of each way to cover a disruption."""

    currency: str
    reserve_callout_pilot: int
    reserve_callout_cabin: int
    dayoff_callout_pilot: int
    dayoff_callout_cabin: int
    deadhead_positioning: int
    delay_per_duty_hour: int
    cancellation_per_flight: int
    hotel_overnight: int
    seniority_allowance_pilot: int = 0
    """Per year of seniority on a callout, so senior crew cost more to call in."""
    seniority_allowance_cabin: int = 0
    travel_per_10_minutes: int = 0
    """Travel allowance per 10 minutes the crew member needs to reach the airport."""

    def callout(self, rank: Rank, on_reserve: bool) -> int:
        if on_reserve:
            return self.reserve_callout_pilot if rank.is_pilot else self.reserve_callout_cabin
        return self.dayoff_callout_pilot if rank.is_pilot else self.dayoff_callout_cabin

    def seniority_allowance(self, rank: Rank, seniority: int) -> int:
        per_year = (
            self.seniority_allowance_pilot if rank.is_pilot else self.seniority_allowance_cabin
        )
        return per_year * seniority

    def travel(self, reach_minutes: int) -> int:
        return self.travel_per_10_minutes * -(-reach_minutes // 10)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], ctx: str = "costs") -> CostTable:
        return cls(
            currency=_str(d, "currency", ctx),
            reserve_callout_pilot=_int(d, "reserve_callout_pilot", ctx),
            reserve_callout_cabin=_int(d, "reserve_callout_cabin", ctx),
            dayoff_callout_pilot=_int(d, "dayoff_callout_pilot", ctx),
            dayoff_callout_cabin=_int(d, "dayoff_callout_cabin", ctx),
            deadhead_positioning=_int(d, "deadhead_positioning", ctx),
            delay_per_duty_hour=_int(d, "delay_cost_per_duty_hour", ctx),
            cancellation_per_flight=_int(d, "cancellation_per_flight", ctx),
            hotel_overnight=_int(d, "hotel_overnight", ctx),
            seniority_allowance_pilot=int(d.get("seniority_allowance_pilot", 0)),
            seniority_allowance_cabin=int(d.get("seniority_allowance_cabin", 0)),
            travel_per_10_minutes=int(d.get("travel_per_10_minutes", 0)),
        )
