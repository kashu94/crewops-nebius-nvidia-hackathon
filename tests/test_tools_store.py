"""Tools over the live table (moto): open disruptions per desk, and who is already out."""

from datetime import UTC, datetime

import pytest
from test_reports import VALID_DETAILS, report

from crewops.domain import Report, Role
from crewops.ingestion.seed import load_seed
from crewops.store.ops_store import OpsStore
from crewops.store.reports import store_report
from crewops.tools import Caller, CrewOpsTools, dispatch

RECEIVED = datetime(2026, 10, 13, 5, 1, tzinfo=UTC)
CONTROLLER = Caller("u-ctl", frozenset({Role.CONTROLLER}))


@pytest.fixture(scope="module")
def seed():
    return load_seed()


@pytest.fixture
def table(ops_table, seed):
    OpsStore(ops_table).put_seed(seed)
    return ops_table


def file(table, report_id, type_, details):
    store_report(table, Report.from_dict(report(type_, details, report_id=report_id)), RECEIVED)


def counts(tools):
    r = dispatch(tools, CONTROLLER, "controller_issue_counts", {})
    assert r.ok, r.error
    return {d["controller"]: d["disruption_ids"] for d in r.result["desks"]}, r.result


def analysed(table, report_id):
    """Analyse a filed report as the stream does: that is when it gets its desk."""
    from crewops.analysis.runner import Operation, analyse_by_id

    assert analyse_by_id(table, report_id, Operation.load(table)).outcome == "stored"


def test_each_analysed_disruption_goes_to_the_least_loaded_desk(table):
    for report_id, type_ in (
        ("r-sick0001", "crew_unavailable"),
        ("r-tech0001", "aircraft_technical"),
        ("r-stn00001", "station_disruption"),
        ("r-gate0001", "gate_closure"),
    ):
        file(table, report_id, type_, VALID_DETAILS[type_])
        analysed(table, report_id)
    by_desk, raw = counts(CrewOpsTools.load(table))
    # One each in desk order, then the fourth to the desk given fewest overall (a tie, so
    # desk order again). The aircraft it touches never matters.
    assert by_desk["Ananya Iyer"] == ["r-gate0001", "r-sick0001"]
    assert by_desk["Rohit Malhotra"] == ["r-tech0001"]
    assert by_desk["Divya Rao"] == ["r-stn00001"]
    assert raw["unassigned"] == [] and raw["desks"][0]["open_issues"] == 2


def test_counts_are_read_fresh_and_closed_ones_drop_out(table):
    tools = CrewOpsTools.load(table)
    assert counts(tools)[0]["Ananya Iyer"] == []
    file(table, "r-sick0001", "crew_unavailable", VALID_DETAILS["crew_unavailable"])
    assert counts(tools)[1]["unassigned"] == ["r-sick0001"]  # reported, not yet analysed
    analysed(table, "r-sick0001")
    assert counts(tools)[0]["Ananya Iyer"] == ["r-sick0001"]  # same tools, new data


def test_a_report_not_yet_analysed_has_no_desk(table):
    file(
        table,
        "r-sick0002",
        "crew_unavailable",
        {"crew_id": "C-3310", "from_date": "2026-10-13", "reason": "sick"},  # reserve, no pairing
    )
    by_desk, raw = counts(CrewOpsTools.load(table))
    assert raw["unassigned"] == ["r-sick0002"]
    assert not any(by_desk.values())


def test_crew_out_in_an_open_disruption_is_not_offered(table):
    file(
        table,
        "r-sick0003",
        "crew_unavailable",
        {"crew_id": "C-3310", "from_date": "2026-10-13", "reason": "sick"},
    )
    r = dispatch(CrewOpsTools.load(table), CONTROLLER, "find_options", {"crew_id": "C-1042"})
    assert r.ok and r.result["recommended"]["crew_id"] != "C-3310"
    assert "C-3310" not in [o["crew_id"] for o in r.result["options"]]


def test_the_same_seat_reported_for_another_day_goes_to_the_same_desk(table):
    from crewops.store.items import from_dynamo
    from crewops.store.reports import get_disruption

    # Something else first, so least-loaded alone would send the second report elsewhere.
    file(table, "r-gate0001", "gate_closure", VALID_DETAILS["gate_closure"])
    analysed(table, "r-gate0001")  # desk-1
    for report_id, on in (("r-c104213", "2026-10-13"), ("r-c104214", "2026-10-14")):
        file(
            table,
            report_id,
            "crew_unavailable",
            {"crew_id": "C-1042", "from_date": on, "reason": "sick"},
        )
        analysed(table, report_id)
    first, second = (
        from_dynamo(get_disruption(table, r)["analysis"]) for r in ("r-c104213", "r-c104214")
    )
    # Both days are P-2291's one captain seat: the second joins the desk already on it.
    assert first["desk"] == "desk-2" and second["desk"] == "desk-2"
    assert "The same seat is already open in r-c104213" in second["notes"][-1]
