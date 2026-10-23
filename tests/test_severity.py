"""How serious a disruption is, and absences on days with nothing to fly."""

from test_plans import AT, disruption
from test_reports import report

from crewops.analysis.runner import Operation, analyse_by_id
from crewops.analysis.severity import severity
from crewops.domain import Report
from crewops.ingestion.seed import load_seed
from crewops.store.items import from_dynamo
from crewops.store.ops_store import OpsStore
from crewops.store.plans import audit_trail
from crewops.store.reports import get_disruption, store_report


def seat(strategy="reserve_callout", status="options", dates=("2026-10-16",), passengers=150):
    return {
        "pairing_id": "P-2291",
        "status": status,
        "recommended": {"strategy": strategy} if strategy else None,
        "impact": {"affected_dates": list(dates), "passengers": passengers},
    }


def test_a_reserve_with_time_to_spare_is_low_and_no_cover_is_high():
    calm = severity({"seats": [seat()]}, "2026-10-12T05:00:00Z")
    assert calm == {"level": "low", "reasons": ["a reserve covers it"]}
    assert severity({}, None) == {"level": "low", "reasons": ["nothing to cover"]}
    for bad in (seat(status="no_legal_cover", strategy=None), seat("cancel"), seat("short_crew")):
        assert severity({"seats": [bad]}, "2026-10-12T05:00:00Z")["level"] == "high"
    broken = {"delays": [{"pairing_id": "P-2201", "duty_breaches_limit": True}]}
    assert severity(broken)["level"] == "high"
    assert severity({"seats": [seat()], "joint_plan": {"unfilled": [{}]}})["level"] == "high"


def test_lesser_factors_make_it_medium_and_enough_of_them_high():
    soon = severity({"seats": [seat(dates=("2026-10-13",))]}, "2026-10-13T05:00:00Z")
    assert soon == {"level": "medium", "reasons": ["flies today"]}
    callout = severity({"seats": [seat("day_off_callout")]}, "2026-10-12T05:00:00Z")
    assert callout["level"] == "medium" and "day-off callout" in callout["reasons"][0]
    piled = severity(
        {"seats": [seat("positioning", dates=("2026-10-13",), passengers=360)]},
        "2026-10-12T22:00:00Z",
    )
    assert piled["level"] == "high" and len(piled["reasons"]) == 3
    late = severity({"late_departures": [{"flight_id": "DX401", "minutes": 150}]})
    assert late == {"level": "medium", "reasons": ["departures up to 2h30 late"]}


def test_a_real_sick_report_is_rated(ops_table):
    OpsStore(ops_table).put_seed(load_seed())
    disruption(ops_table, "r-sick1042", "C-1042", "2026-10-13", "2026-10-14")
    item = get_disruption(ops_table, "r-sick1042")
    rated = severity(from_dynamo(item["analysis"]), "2026-10-13T05:00:00Z")
    assert rated["level"] in ("medium", "high") and "flies today" in rated["reasons"]


def test_sick_on_a_day_off_is_recorded_not_put_on_a_desk(ops_table):
    """C-5417 flies on the 14th and the 17th; sick on the 15th and 16th there is nothing to
    cover, so no desk takes it, nobody is alerted, and they are not called out those days."""
    OpsStore(ops_table).put_seed(load_seed())
    details = {"crew_id": "C-5417", "from_date": "2026-10-15", "to_date": "2026-10-16"}
    doc = report(report_id="r-offday01", details={**details, "reason": "sick"})
    store_report(ops_table, Report.from_dict(doc), AT)
    assert analyse_by_id(ops_table, "r-offday01", Operation.load(ops_table)).outcome == "stored"
    item = get_disruption(ops_table, "r-offday01")
    analysis = from_dynamo(item["analysis"])
    assert item["status"] == "CLOSED" and "desk" not in analysis
    assert analysis["handling"]["kind"] == "recorded"
    assert any("next duty is 2026-10-17" in n for n in analysis["notes"])
    (closed,) = [e for e in audit_trail(ops_table, "r-offday01") if e["action"] == "CLOSED"]
    assert closed["actor"] == "system:analysis" and closed["reason"].startswith("Recorded")
    # Still out on those days, so not offered as cover for anyone else.
    world = Operation.load(ops_table).world
    assert {d.isoformat() for d, _ in world.unavailable["C-5417"]} == {
        "2026-10-15",
        "2026-10-16",
    }


def test_two_seats_on_one_trip_are_the_same_passengers_and_lead_counts_from_today():
    from datetime import date

    two = {"seats": [seat(passengers=180), seat(passengers=180)]}
    assert "passengers" not in " ".join(severity(two, "2026-10-12T05:00:00Z")["reasons"])
    later = {"seats": [seat(dates=("2026-10-16",))]}
    assert severity(later, "2026-10-12T05:00:00Z")["level"] == "low"
    assert severity(later, "2026-10-12T05:00:00Z", date(2026, 10, 16))["reasons"] == ["flies today"]
    flown = {"seats": [seat(status="already_started", dates=("2026-10-16",))]}
    assert severity(flown, None, date(2026, 10, 16)) == {
        "level": "low",
        "reasons": ["nothing to cover"],
    }


def test_a_trip_under_way_that_lost_its_captain_is_high():
    # A. Nair out on the first day of P-2291 after it reported: the rest of the trip has no
    # captain, and no reserve call-out reaches a trip already flying.
    out = seat(status="already_started", strategy=None, dates=("2026-10-13",))
    out |= {
        "role": "Captain",
        "impact": {**out["impact"], "uncovered_flights": ["KS412-2026-10-13"]},
    }
    assert severity({"seats": [out]}, "2026-10-13T11:40:00Z") == {
        "level": "high",
        "reasons": ["P-2291 is under way without its captain"],
    }
    cancelled = {**out, "status": "cancelled"}
    assert severity({"seats": [cancelled]})["level"] == "low"


def _sick(ops_table, report_id, details):
    store_report(ops_table, Report.from_dict(report(report_id=report_id, **details)), AT)
    assert analyse_by_id(ops_table, report_id, Operation.load(ops_table)).outcome == "stored"
    return get_disruption(ops_table, report_id)


def test_a_sick_reserve_or_a_mixed_group_still_goes_to_a_desk(ops_table):
    OpsStore(ops_table).put_seed(load_seed())
    # C-3310 flies nothing but is on call at BLR all week. One reserve fewer, so a desk is told.
    reserve = _sick(
        ops_table,
        "r-reserve1",
        {"details": {"crew_id": "C-3310", "from_date": "2026-10-15", "reason": "sick"}},
    )
    assert reserve["status"] == "OPTIONS_READY"
    assert any("one reserve fewer" in n for n in from_dynamo(reserve["analysis"])["notes"])
    # One of the two has a flight to cover, so the report is worked, not just recorded.
    mixed = _sick(
        ops_table,
        "r-mixed001",
        {
            "type_": "multiple_crew_unavailable",
            "details": {
                "crew_ids": ["C-1042", "C-2087"],
                "from_date": "2026-10-13",
                "to_date": "2026-10-13",
                "reason": "sick",
            },
        },
    )
    assert mixed["status"] == "OPTIONS_READY" and from_dynamo(mixed["analysis"])["desk"]


def test_planned_leave_is_recrewed_in_advance_without_an_escalation_clock(ops_table):
    from crewops.notify.changes import Change
    from crewops.notify.escalation import schedule_check

    OpsStore(ops_table).put_seed(load_seed())
    leave = _sick(
        ops_table,
        "r-leave1042",
        {
            "type_": "planned_leave",
            "details": {
                "crew_id": "C-1042",
                "from_date": "2026-10-13",
                "to_date": "2026-10-14",
                "reason": "annual_leave",
            },
            "roles": ("admin",),
        },
    )
    analysis = from_dynamo(leave["analysis"])
    assert leave["status"] == "OPTIONS_READY" and analysis["seats"]
    assert "on planned leave" in analysis["summary"]
    assert any("re-crew these trips in advance" in n for n in analysis["notes"])

    class Scheduler:
        def create_schedule(self, **_):
            raise AssertionError("no escalation for planned leave")

    change = Change("r-leave1042", "REPORTED", "OPTIONS_READY", "planned_leave")
    kwargs = {"prefix": "p", "function_arn": "f", "role_arn": "r", "minutes": 10}
    assert schedule_check(Scheduler(), change, now=AT, **kwargs) is None


def test_planned_leave_does_not_chime_push_or_email_teams():
    from test_alexa import stream_record

    from crewops.alexa.notifier import recipients
    from crewops.notify.changes import change_of, team_alerts

    record = stream_record("REPORTED", "OPTIONS_READY", "desk-2")
    record["dynamodb"]["NewImage"]["report"]["M"]["type"] = {"S": "planned_leave"}
    rohit = {"username": "rohit", "roles": ["controller"], "desk": "desk-2", "notifications": True}
    assert recipients(record, [rohit]) == []  # no Echo chime, so no desktop push either
    assert team_alerts(record, change_of(record), "dev") == []


def test_a_several_day_absence_read_on_its_second_day_still_flies_today():
    from datetime import date

    running = {"seats": [seat(dates=("2026-10-15", "2026-10-16"))]}
    assert severity(running, "2026-10-14T05:00:00Z", date(2026, 10, 16))["reasons"] == [
        "flies today"
    ]
