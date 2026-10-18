"""Each rule against hand-built crew, no dataset involved."""

from dataclasses import replace
from datetime import UTC, date, datetime

from crewops.domain import AircraftType, CertType, CrewStatus, Rank, Rule
from crewops.engine.duty import DutyDay
from crewops.engine.rules import (
    ALL_RULES,
    CrewState,
    Duty,
    RuleBook,
    blocking,
    check_base,
    check_certifications,
    check_duty_window,
    check_fdp,
    check_flight_window,
    check_qualification,
    check_rest,
    evaluate,
)

BOOK = RuleBook()
D15 = date(2026, 9, 15)


def at(d: int, h: float) -> datetime:
    return datetime(2026, 9, d, int(h), int(round((h % 1) * 60)), tzinfo=UTC)


def duty_day(d: int = 15, report: float = 6, release: float = 15.5, sectors: int = 3) -> DutyDay:
    return DutyDay(
        date=date(2026, 9, d),
        report_utc=at(d, report),
        release_utc=at(d, release),
        n_sectors=sectors,
        block_hours=6.0,
        aircraft_type=AircraftType.A320,
        dep_station="BLR",
    )


def crew(**overrides) -> CrewState:
    base = CrewState(
        crew_id="C-1",
        name="T. Test",
        rank=Rank.CAPTAIN,
        base="BLR",
        ratings=(AircraftType.A320,),
        status=CrewStatus.ACTIVE,
        last_rest_ended=at(14, 10),
        certs=((CertType.LICENCE, date(2025, 1, 1), date(2027, 1, 1)),),
    )
    return replace(base, **overrides)


def test_fdp_pass_and_fail_carry_numbers():
    ok = check_fdp(duty_day(report=6, release=15.5, sectors=3), BOOK)
    assert not ok.failed and ok.used == 9.5 and ok.limit == 12.5 and ok.headroom == 3.0
    bad = check_fdp(duty_day(report=6, release=18.75, sectors=4), BOOK)
    assert bad.failed and bad.used == 12.75 and bad.limit == 12.0
    assert "by 0h45m" in bad.detail


def test_duty_window_counts_history_and_new_duty():
    c = crew(daily_duty={date(2026, 9, d): 10.0 for d in range(9, 15)})  # 60h already
    v = check_duty_window(c, duty_day(), BOOK)
    assert v.failed and v.used == 69.5
    assert "by 9h30m" in v.detail


def test_duty_window_ignores_days_outside_window():
    c = crew(daily_duty={date(2026, 9, 8): 50.0, date(2026, 9, 9): 5.0})
    v = check_duty_window(c, duty_day(), BOOK)
    assert not v.failed and v.used == 14.5


def test_duty_window_counts_earlier_days_of_same_pairing():
    c = crew(daily_duty={date(2026, 9, d): 9.0 for d in range(10, 15)})  # 45h
    day1, day2 = duty_day(15), duty_day(16)
    assert not check_duty_window(c, day1, BOOK).failed  # 54.5h
    assert check_duty_window(c, day2, BOOK, earlier=[day1]).failed  # 45 + 9.5 + 9.5 - 9 (10th out)


def test_duty_window_adds_rostered_days_after_history():
    later = Duty("P-9", date(2026, 9, 15), at(15, 1), at(15, 11), 5.0)
    c = crew(daily_duty={date(2026, 9, d): 9.0 for d in range(11, 15)}, duties=(later,))
    v = check_duty_window(c, duty_day(16), BOOK)
    assert v.used == 36 + 10 + 9.5
    # ...but not when that rostered duty is the pairing being covered.
    assert check_duty_window(c, duty_day(16), BOOK, exclude_pairing="P-9").used == 45.5


def test_flight_window():
    c = crew(daily_flight={date(2026, 9, d): 4.0 for d in range(1, 15)})  # 56h
    assert not check_flight_window(c, duty_day(), BOOK).failed
    heavy = crew(daily_flight={date(2026, 9, d): 7.0 for d in range(1, 15)})  # 98h
    assert check_flight_window(heavy, duty_day(), BOOK).failed


def test_rest_still_resting():
    v = check_rest(crew(last_rest_ended=at(15, 8)), duty_day(report=6), BOOK)
    assert v.failed and "still resting" in v.detail


def test_rest_downstream_conflict():
    nxt = Duty("P-2", date(2026, 9, 16), at(16, 2), at(16, 10), 5.0)  # 10.5h after 15:30
    v = check_rest(crew(duties=(nxt,)), duty_day(), BOOK)
    assert v.failed and "only 10.5h rest before P-2" in v.detail


def test_rest_upstream_conflict():
    prev = Duty("P-0", date(2026, 9, 14), at(14, 12), at(14, 20), 5.0)  # 10h before 06:00
    v = check_rest(crew(duties=(prev,), last_rest_ended=None), duty_day(), BOOK)
    assert v.failed and "only 10.0h rest after P-0" in v.detail


def test_rest_double_booked_and_excluded_pairing():
    clash = Duty("P-3", D15, at(15, 8), at(15, 14), 5.0)
    assert "double-booked" in check_rest(crew(duties=(clash,)), duty_day(), BOOK).detail
    assert not check_rest(crew(duties=(clash,)), duty_day(), BOOK, exclude_pairing="P-3").failed


def test_qualification():
    assert check_qualification(crew(ratings=(AircraftType.ATR72,)), duty_day()).failed
    assert check_qualification(crew(status=CrewStatus.LEAVE), duty_day()).failed
    assert not check_qualification(crew(), duty_day()).failed


def test_certification_expiry_only():
    expired = crew(certs=((CertType.RECURRENT_TRAINING, date(2024, 1, 1), date(2026, 9, 14)),))
    assert check_certifications(expired, duty_day()).failed
    future_start = crew(certs=((CertType.LICENCE, date(2030, 1, 1), date(2032, 1, 1)),))
    assert not check_certifications(future_start, duty_day()).failed


def test_base():
    assert not check_base(crew(), duty_day(), positioned=False).failed
    away = crew(base="DEL")
    assert check_base(away, duty_day(), positioned=False).failed
    assert not check_base(away, duty_day(), positioned=True).failed


def test_evaluate_checks_every_rule_and_base_once():
    verdicts = evaluate(crew(), [duty_day(15), duty_day(16)], BOOK)
    assert {v.rule_id for v in verdicts} == set(ALL_RULES)
    assert sum(v.rule_id == "RULE-BASE-07" for v in verdicts) == 1
    assert len(verdicts) == 13


def test_blocking_deduplicates():
    c = crew(ratings=())
    verdicts = evaluate(c, [duty_day(15), duty_day(16)], BOOK)
    assert [v.rule_id for v in blocking(verdicts)] == ["RULE-QUAL-05"]


def test_rulebook_reads_params_and_keeps_defaults():
    book = RuleBook.from_rules(
        [
            Rule("RULE-DUTY-02", "x", {"max_duty_hours": 55, "window_days": 7}),
            Rule("RULE-REST-04", "x", {"min_rest_hours": 10}),
        ]
    )
    assert book.max_duty_hours == 55 and book.min_rest_hours == 10
    assert book.base_fdp_hours == 13.0


def test_duty_window_checks_windows_ending_on_later_rostered_days():
    # Rostered 11h on each of the 5 days after the new duty: the window ending on the
    # last of them holds 55h + the new 9.5h, even though the window ending today does not.
    later = tuple(
        Duty(f"P-{i}", date(2026, 9, 15 + i), at(15 + i, 1), at(15 + i, 12), 6.0)
        for i in range(1, 6)
    )
    c = crew(duties=later, last_rest_ended=None)
    v = check_duty_window(c, duty_day(15, report=0, release=9.5, sectors=2), BOOK)
    assert v.failed and v.used == 64.5
    assert "in the 7 days ending 2026-09-20" in v.detail


def test_duty_window_counts_a_history_day_and_its_rostered_duty_once():
    # History already records the rostered duty on 09-14: it must not be added twice.
    same_day = Duty("P-1", date(2026, 9, 14), at(14, 1), at(14, 11), 6.0)
    c = crew(daily_duty={date(2026, 9, 14): 10.0}, duties=(same_day,))
    assert c.duty_in_window(date(2026, 9, 8), date(2026, 9, 14)) == 10.0
