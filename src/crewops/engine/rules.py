"""The seven legality rules.

Each rule returns a ``RuleVerdict`` with the numbers behind it rather than a bool, because
"exceeds 60h/7d by 1h20m" is something a controller can act on and "illegal" isn't.

Rules only look at a ``CrewState`` and the duty days being checked, so they can be tested
without a store. Thresholds come from a ``RuleBook`` built from the rule parameters in the
data, which lets an admin change a limit without a code change.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum

from crewops.domain import AircraftType, CertType, CrewStatus, Rank, Rule
from crewops.engine.duty import DutyDay, calendar_window, format_hours, hours_between

FDP = "RULE-FDP-01"
DUTY = "RULE-DUTY-02"
FLIGHT = "RULE-FLT-03"
REST = "RULE-REST-04"
QUAL = "RULE-QUAL-05"
CERT = "RULE-CERT-06"
BASE = "RULE-BASE-07"

ALL_RULES = (FDP, DUTY, FLIGHT, REST, QUAL, CERT, BASE)


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class RuleVerdict:
    rule_id: str
    status: Verdict
    detail: str
    used: float | None = None
    limit: float | None = None
    headroom: float | None = None
    date: date | None = None

    @property
    def failed(self) -> bool:
        return self.status is Verdict.FAIL


def ok(rule_id: str, detail: str, **numbers: object) -> RuleVerdict:
    return RuleVerdict(rule_id, Verdict.PASS, detail, **numbers)  # type: ignore[arg-type]


def fail(rule_id: str, detail: str, **numbers: object) -> RuleVerdict:
    return RuleVerdict(rule_id, Verdict.FAIL, detail, **numbers)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class RuleBook:
    base_fdp_hours: float = 13.0
    reduction_per_extra_sector_hours: float = 0.5
    free_sectors: int = 2
    max_duty_hours: float = 60.0
    duty_window_days: int = 7
    max_flight_hours: float = 100.0
    flight_window_days: int = 28
    min_rest_hours: float = 12.0

    def fdp_limit(self, n_sectors: int) -> float:
        """Max flight duty period for a day of ``n_sectors`` legs."""
        extra = max(0, n_sectors - self.free_sectors)
        return self.base_fdp_hours - self.reduction_per_extra_sector_hours * extra

    @classmethod
    def from_rules(cls, rules: Iterable[Rule]) -> RuleBook:
        """Read thresholds from rule parameters; anything absent keeps its default."""
        by_id: Mapping[str, Rule] = {r.rule_id: r for r in rules}

        def param(rule_id: str, key: str, default: float) -> float:
            rule = by_id.get(rule_id)
            return float(rule.params.get(key, default)) if rule else default

        d = cls()
        return cls(
            base_fdp_hours=param(FDP, "base_fdp_hours", d.base_fdp_hours),
            reduction_per_extra_sector_hours=param(
                FDP, "reduction_per_extra_sector_hours", d.reduction_per_extra_sector_hours
            ),
            free_sectors=int(param(FDP, "free_sectors", d.free_sectors)),
            max_duty_hours=param(DUTY, "max_duty_hours", d.max_duty_hours),
            duty_window_days=int(param(DUTY, "window_days", d.duty_window_days)),
            max_flight_hours=param(FLIGHT, "max_flight_hours", d.max_flight_hours),
            flight_window_days=int(param(FLIGHT, "window_days", d.flight_window_days)),
            min_rest_hours=param(REST, "min_rest_hours", d.min_rest_hours),
        )


@dataclass(frozen=True, slots=True)
class Duty:
    """A duty day already on someone's roster."""

    pairing_id: str
    date: date
    report_utc: datetime
    release_utc: datetime
    block_hours: float

    @property
    def duty_hours(self) -> float:
        return hours_between(self.report_utc, self.release_utc)


@dataclass(frozen=True, slots=True)
class CrewState:
    """Everything the rules need about one crew member at the snapshot."""

    crew_id: str
    name: str
    rank: Rank
    base: str
    ratings: tuple[AircraftType, ...]
    status: CrewStatus
    last_rest_ended: datetime | None
    daily_duty: Mapping[date, float] = field(default_factory=dict)
    daily_flight: Mapping[date, float] = field(default_factory=dict)
    certs: tuple[tuple[CertType, date, date], ...] = ()
    """(type, valid_from, valid_to)."""
    duties: tuple[Duty, ...] = ()

    def _per_day(
        self, history: Mapping[date, float], hours_of, exclude_pairing: str | None
    ) -> dict[date, float]:
        """Hours per calendar day from recorded history or rostered duty, never both.

        History can stop short of a rostered day (the snapshot day records 0 for a duty flown
        that morning). Taking the larger of the two counts the duty once either way.
        """
        rostered: dict[date, float] = {}
        for d in self.duties:
            if d.pairing_id != exclude_pairing:
                rostered[d.date] = rostered.get(d.date, 0.0) + hours_of(d)
        days = set(history) | set(rostered)
        return {d: max(history.get(d, 0.0), round(rostered.get(d, 0.0), 2)) for d in days}

    def duty_in_window(self, start: date, end: date, exclude_pairing: str | None = None) -> float:
        per_day = self._per_day(self.daily_duty, lambda d: d.duty_hours, exclude_pairing)
        return round(sum(h for d, h in per_day.items() if start <= d <= end), 2)

    def flight_in_window(self, start: date, end: date, exclude_pairing: str | None = None) -> float:
        per_day = self._per_day(self.daily_flight, lambda d: d.block_hours, exclude_pairing)
        return round(sum(h for d, h in per_day.items() if start <= d <= end), 2)

    def rostered_dates(self, exclude_pairing: str | None = None) -> set[date]:
        return {d.date for d in self.duties if d.pairing_id != exclude_pairing}


def check_fdp(day: DutyDay, book: RuleBook) -> RuleVerdict:
    used, limit = day.fdp_hours, book.fdp_limit(day.n_sectors)
    numbers = {"used": used, "limit": limit, "headroom": round(limit - used, 2), "date": day.date}
    if used > limit:
        over = format_hours(used - limit)
        return fail(
            FDP,
            f"FDP {used}h exceeds {limit}h limit ({day.n_sectors} sectors) by {over}",
            **numbers,
        )
    return ok(FDP, f"FDP {used}h of {limit}h ({day.n_sectors} sectors)", **numbers)


def _worst_window(
    crew: CrewState,
    day: DutyDay,
    earlier: Iterable[DutyDay],
    window_days: int,
    in_window,
    hours_of,
    exclude_pairing: str | None,
) -> tuple[float, date]:
    """Largest total across every window the new duty can push over the limit.

    Not only the window ending on the duty date but also each one ending on a later rostered
    day, since a duty added today can breach the limit on a trip three days out.
    """
    new_days = [*earlier, day]
    horizon = day.date + timedelta(days=window_days - 1)
    ends = {day.date} | {d for d in crew.rostered_dates(exclude_pairing) if day.date < d <= horizon}
    worst: tuple[float, date] | None = None
    for end in sorted(ends):
        start, _ = calendar_window(end, window_days)
        total = round(
            in_window(start, end, exclude_pairing)
            + sum(hours_of(d) for d in new_days if start <= d.date <= end),
            2,
        )
        if worst is None or total > worst[0]:
            worst = (total, end)
    assert worst is not None
    return worst


def _window_verdict(
    rule_id: str, total: float, end: date, day: DutyDay, limit: float, window_days: int
) -> RuleVerdict:
    numbers = {"used": total, "limit": limit, "headroom": round(limit - total, 2), "date": day.date}
    window = f"{limit:g}h/{window_days}d"
    where = f"on {day.date}" if end == day.date else f"in the {window_days} days ending {end}"
    if total > limit:
        over = format_hours(total - limit)
        return fail(rule_id, f"would exceed {window} by {over} {where} (total {total}h)", **numbers)
    return ok(rule_id, f"{total}h of {window} {where}", **numbers)


def check_duty_window(
    crew: CrewState,
    day: DutyDay,
    book: RuleBook,
    earlier: Iterable[DutyDay] = (),
    exclude_pairing: str | None = None,
) -> RuleVerdict:
    """Duty hours in every calendar window the new duty falls into.

    Earlier days of the same pairing count too. A two-day cover can be legal on each day alone
    and still breach once day one sits inside day two's window.
    """
    total, end = _worst_window(
        crew,
        day,
        earlier,
        book.duty_window_days,
        crew.duty_in_window,
        lambda d: d.fdp_hours,
        exclude_pairing,
    )
    return _window_verdict(DUTY, total, end, day, book.max_duty_hours, book.duty_window_days)


def check_flight_window(
    crew: CrewState,
    day: DutyDay,
    book: RuleBook,
    earlier: Iterable[DutyDay] = (),
    exclude_pairing: str | None = None,
) -> RuleVerdict:
    total, end = _worst_window(
        crew,
        day,
        earlier,
        book.flight_window_days,
        crew.flight_in_window,
        lambda d: d.block_hours,
        exclude_pairing,
    )
    return _window_verdict(FLIGHT, total, end, day, book.max_flight_hours, book.flight_window_days)


def check_rest(
    crew: CrewState, day: DutyDay, book: RuleBook, exclude_pairing: str | None = None
) -> RuleVerdict:
    """Minimum rest before this duty and before whatever they're rostered for next, since
    covering tonight's trip can strand tomorrow's departure."""
    minimum = book.min_rest_hours
    # last_rest_ended = earliest they may report again
    if crew.last_rest_ended is not None and day.report_utc < crew.last_rest_ended:
        short = hours_between(day.report_utc, crew.last_rest_ended)
        return fail(
            REST,
            f"still resting until {crew.last_rest_ended:%Y-%m-%d %H:%M}Z, "
            f"{format_hours(short)} after report on {day.date}",
            limit=minimum,
            date=day.date,
        )

    duties = [d for d in crew.duties if d.pairing_id != exclude_pairing]
    # report an overlap before any short rest around it; two trips at once is the real problem
    for duty in duties:
        if duty.report_utc < day.release_utc and day.report_utc < duty.release_utc:
            return fail(
                REST, f"double-booked: {duty.pairing_id} overlaps on {day.date}", date=day.date
            )

    for duty in duties:
        if duty.report_utc >= day.release_utc:
            rest = hours_between(day.release_utc, duty.report_utc)
            if rest < minimum:
                return fail(
                    REST,
                    f"only {rest}h rest before {duty.pairing_id} on {duty.date}",
                    used=rest,
                    limit=minimum,
                    headroom=round(rest - minimum, 2),
                    date=day.date,
                )
        elif duty.release_utc <= day.report_utc:
            rest = hours_between(duty.release_utc, day.report_utc)
            if rest < minimum:
                return fail(
                    REST,
                    f"only {rest}h rest after {duty.pairing_id} on {duty.date}",
                    used=rest,
                    limit=minimum,
                    headroom=round(rest - minimum, 2),
                    date=day.date,
                )

    return ok(REST, f"at least {minimum:g}h rest either side", limit=minimum, date=day.date)


def check_qualification(crew: CrewState, day: DutyDay) -> RuleVerdict:
    if day.aircraft_type not in crew.ratings:
        return fail(QUAL, f"not rated on {day.aircraft_type}", date=day.date)
    if crew.status is not CrewStatus.ACTIVE:
        return fail(QUAL, f"status is {crew.status}, not active", date=day.date)
    return ok(QUAL, f"{day.aircraft_type} rated and active", date=day.date)


def check_certifications(crew: CrewState, day: DutyDay) -> RuleVerdict:
    """Every certification still valid on the duty date.

    Only expiry is checked. Several licence records in the data have a ``valid_from`` years in
    the future, and enforcing it would ground every pilot over a data quirk.
    """
    for cert_type, _valid_from, valid_to in crew.certs:
        if day.date > valid_to:
            return fail(
                CERT, f"{cert_type} expired {valid_to}, invalid on {day.date}", date=day.date
            )
    return ok(CERT, f"all {len(crew.certs)} certifications valid on {day.date}", date=day.date)


def check_base(crew: CrewState, day: DutyDay, positioned: bool) -> RuleVerdict:
    """Own base, or another base with positioning arranged."""
    if crew.base == day.dep_station:
        return ok(BASE, f"own base ({crew.base})", date=day.date)
    if positioned:
        return ok(BASE, f"positioning from {crew.base} to {day.dep_station}", date=day.date)
    return fail(
        BASE,
        f"based at {crew.base}, duty starts at {day.dep_station} with no positioning flight",
        date=day.date,
    )


def evaluate(
    crew: CrewState,
    days: list[DutyDay],
    book: RuleBook,
    exclude_pairing: str | None = None,
    positioned: bool = False,
) -> list[RuleVerdict]:
    """All rules against every day of a pairing.

    Base only applies to day one, that's where the crew has to get to. Later days start
    wherever the pairing overnighted.
    """
    verdicts: list[RuleVerdict] = []
    for i, day in enumerate(days):
        verdicts += [
            check_qualification(crew, day),
            check_certifications(crew, day),
            check_fdp(day, book),
            check_duty_window(crew, day, book, days[:i], exclude_pairing),
            check_flight_window(crew, day, book, days[:i], exclude_pairing),
            check_rest(crew, day, book, exclude_pairing),
        ]
        if i == 0:
            verdicts.append(check_base(crew, day, positioned))
    return verdicts


def blocking(verdicts: Iterable[RuleVerdict]) -> list[RuleVerdict]:
    """The failures only, each distinct rule-and-detail once."""
    seen: set[tuple[str, str]] = set()
    out = []
    for v in verdicts:
        if v.failed and (v.rule_id, v.detail) not in seen:
            seen.add((v.rule_id, v.detail))
            out.append(v)
    return out
