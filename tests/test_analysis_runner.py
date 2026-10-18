"""Stored analysis, the stream handler and the run command, against moto's DynamoDB."""

from datetime import UTC, datetime, timedelta

import pytest
from boto3.dynamodb.types import TypeSerializer
from conftest import TEST_SETTINGS
from test_reports import report

from crewops.analysis import run as run_command
from crewops.analysis import stream_handler
from crewops.analysis.runner import Operation, analyse_by_id
from crewops.domain import Report
from crewops.ingestion.seed import load_seed
from crewops.store.items import to_dynamo
from crewops.store.ops_store import OpsStore
from crewops.store.reports import (
    AnalysisOutcome,
    disruption_item,
    disruptions_with_status,
    get_disruption,
    store_analysis,
    store_report,
)

RECEIVED = datetime.now(UTC) - timedelta(hours=1)
"""When the report was stored: real time, because the analysis stamps its audit event with
the real clock, and the trail must read in the order things happened. The report itself is
about the operating week."""
_serialize = TypeSerializer().serialize


@pytest.fixture(scope="module")
def seed():
    return load_seed()


@pytest.fixture
def table(ops_table, seed, monkeypatch):
    OpsStore(ops_table).put_seed(seed)
    monkeypatch.setenv("CREWOPS_STAGE", TEST_SETTINGS.stage)
    monkeypatch.setenv("AWS_REGION", TEST_SETTINGS.aws_region)
    return ops_table


def stored(table, **overrides) -> Report:
    r = Report.from_dict(report(**overrides))
    store_report(table, r, RECEIVED)
    return r


def audits(table, disruption_id):
    return table.query(
        KeyConditionExpression="pk = :p AND begins_with(sk, :a)",
        ExpressionAttributeValues={":p": f"DISRUPTION#{disruption_id}", ":a": "AUDIT#"},
    )["Items"]


def stream_record(item, event="INSERT", sequence="100"):
    image = {k: _serialize(v) for k, v in to_dynamo(item).items()}
    return {"eventName": event, "dynamodb": {"NewImage": image, "SequenceNumber": sequence}}


def test_analysis_is_stored_and_status_moves_on(table):
    stored(table)
    result = analyse_by_id(table, "r-00000001", Operation.load(table))
    assert result.outcome == "stored" and "C-3310" in result.summary

    item = get_disruption(table, "r-00000001")
    assert item["status"] == "OPTIONS_READY" and item["gsi1pk"] == "STATUS#OPTIONS_READY"
    seat = item["analysis"]["seats"][0]
    assert seat["recommended"]["crew_id"] == "C-3310"
    assert [a["action"] for a in audits(table, "r-00000001")] == ["REPORTED", "OPTIONS_READY"]
    assert [d["disruption_id"] for d in disruptions_with_status(table, "OPTIONS_READY")] == [
        "r-00000001"
    ]
    assert disruptions_with_status(table) == []  # nothing left in REPORTED


def test_analysing_twice_is_a_no_op(table):
    stored(table)
    operation = Operation.load(table)
    assert analyse_by_id(table, "r-00000001", operation).outcome == "stored"
    assert analyse_by_id(table, "r-00000001", operation).outcome == "not_reported"
    assert len(audits(table, "r-00000001")) == 2


def test_store_analysis_refuses_once_status_has_moved(table):
    stored(table)
    now = datetime(2026, 10, 13, 6, tzinfo=UTC)
    assert store_analysis(table, "r-00000001", {"x": 1.5}, now) is AnalysisOutcome.STORED
    assert store_analysis(table, "r-00000001", {"x": 2}, now) is AnalysisOutcome.ALREADY_ANALYSED
    assert get_disruption(table, "r-00000001")["analysis"] == {"x": 1.5}


def test_missing_disruption(table):
    assert analyse_by_id(table, "r-nonexist", Operation.load(table)).outcome == "missing"


def test_stream_handler_analyses_inserted_disruptions_only(table, monkeypatch):
    r = stored(table)
    monkeypatch.setattr("crewops.store.ops_store.table_for", lambda settings: table)
    item = disruption_item(r, RECEIVED)
    event = {
        "Records": [
            stream_record(item, event="MODIFY", sequence="1"),
            stream_record({"pk": "CREW#C-1", "sk": "PROFILE", "entity": "crew"}, sequence="2"),
            stream_record(item, sequence="3"),
        ]
    }
    assert stream_handler.handler(event) == {"batchItemFailures": []}
    assert get_disruption(table, "r-00000001")["status"] == "OPTIONS_READY"


def test_stream_handler_skips_loading_when_nothing_to_do(monkeypatch):
    def boom(settings):
        raise AssertionError("should not touch the table")

    monkeypatch.setattr("crewops.store.ops_store.table_for", boom)
    event = {"Records": [{"eventName": "REMOVE", "dynamodb": {"SequenceNumber": "1"}}]}
    assert stream_handler.handler(event) == {"batchItemFailures": []}


def test_stream_handler_reports_the_first_failure_for_retry(table, monkeypatch):
    r = stored(table)
    monkeypatch.setattr("crewops.store.ops_store.table_for", lambda settings: table)

    def broken(table_, item, operation):
        raise RuntimeError("DynamoDB unavailable")

    monkeypatch.setattr(stream_handler, "analyse_item", broken)
    item = disruption_item(r, RECEIVED)
    event = {"Records": [stream_record(item, sequence="7"), stream_record(item, sequence="8")]}
    assert stream_handler.handler(event) == {"batchItemFailures": [{"itemIdentifier": "7"}]}


def test_run_command_analyses_pending(table, monkeypatch, capsys):
    stored(table)
    stored(
        table,
        report_id="r-00000002",
        details={"crew_id": "C-3310", "from_date": "2026-10-13", "reason": "sick"},
    )
    monkeypatch.setattr(run_command, "load_env_file", lambda: {})
    monkeypatch.setenv("AWS_PROFILE", "crewops")
    monkeypatch.setattr("crewops.store.ops_store.table_for", lambda settings: table)

    assert run_command.main(["--pending"]) == 0
    out = capsys.readouterr().out
    assert "r-00000001: stored" in out and "r-00000002: stored" in out
    assert "nothing to cover" in out

    assert run_command.main(["--pending"]) == 0
    assert "Nothing to analyse" in capsys.readouterr().out


def test_run_command_needs_a_named_profile(monkeypatch, capsys):
    monkeypatch.setattr(run_command, "load_env_file", lambda: {})
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    assert run_command.main(["--pending"]) == 1
    assert "AWS_PROFILE" in capsys.readouterr().err


# -- Regressions -----------------------------------------------------------------------


def test_crew_reported_unavailable_elsewhere_is_not_offered(table):
    stored(table)  # r-00000001: C-1042 sick, P-2291 needs a captain
    stored(
        table,
        report_id="r-00000002",
        details={"crew_id": "C-3310", "from_date": "2026-10-13", "reason": "sick"},
    )
    result = analyse_by_id(table, "r-00000001", Operation.load(table))
    assert result.outcome == "stored"
    seat = get_disruption(table, "r-00000001")["analysis"]["seats"][0]
    assert seat["recommended"]["crew_id"] != "C-3310"
    assert "C-3310" not in [o["crew_id"] for o in seat["options"]]


def test_unavailability_names_the_other_report(table):
    from crewops.engine import find_options

    stored(
        table,
        report_id="r-00000002",
        details={"crew_id": "C-3310", "from_date": "2026-10-13", "reason": "sick"},
    )
    world = Operation.load(table).world
    excluded = {e.crew_id: e for e in find_options(world, "P-2291", "Captain").excluded}
    assert excluded["C-3310"].reasons == ("reported unavailable on 2026-10-13 (report r-00000002)",)


def test_open_certification_lapse_takes_crew_out_after_expiry(table):
    stored(
        table,
        report_id="r-certlapse",
        type_="certification_lapse",
        details={"crew_id": "C-3310", "cert_type": "licence", "valid_to": "2026-10-14"},
    )
    unavailable = Operation.load(table).world.unavailable["C-3310"]
    assert min(d for d, _ in unavailable).isoformat() == "2026-10-15"


def test_permanent_failure_is_recorded_not_retried(table, monkeypatch):
    r = stored(table)
    monkeypatch.setattr("crewops.store.ops_store.table_for", lambda settings: table)

    def malformed(table_, item, operation):
        raise KeyError("details")

    monkeypatch.setattr(stream_handler, "analyse_item", malformed)
    out = stream_handler.handler({"Records": [stream_record(disruption_item(r, RECEIVED))]})

    assert out == {"batchItemFailures": []}
    actions = [a["action"] for a in audits(table, "r-00000001")]
    assert actions == ["REPORTED", "ANALYSIS_FAILED"]
    assert get_disruption(table, "r-00000001")["status"] == "REPORTED"  # still recoverable


def test_run_command_carries_on_after_a_failure(table, monkeypatch, capsys):
    stored(table)
    stored(
        table,
        report_id="r-00000002",
        details={"crew_id": "C-3310", "from_date": "2026-10-13", "reason": "sick"},
    )
    monkeypatch.setattr(run_command, "load_env_file", lambda: {})
    monkeypatch.setenv("AWS_PROFILE", "crewops")
    monkeypatch.setattr("crewops.store.ops_store.table_for", lambda settings: table)

    import crewops.analysis.runner as runner

    real = runner.analyse_item

    def flaky(table_, item, operation):
        if item["disruption_id"] == "r-00000001":
            raise RuntimeError("boom")
        return real(table_, item, operation)

    monkeypatch.setattr(runner, "analyse_item", flaky)
    assert run_command.main(["--pending"]) == 1
    out = capsys.readouterr().out
    assert "r-00000001: failed (RuntimeError: boom)" in out
    assert "r-00000002: stored" in out


def test_store_analysis_on_a_missing_disruption(table):
    now = datetime(2026, 10, 13, 6, tzinfo=UTC)
    assert store_analysis(table, "r-gone0001", {}, now) is AnalysisOutcome.MISSING
