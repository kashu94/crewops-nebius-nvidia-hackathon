"""The live-report worker against moto's DynamoDB loaded with the seed week."""

import json
from datetime import UTC, datetime

import pytest
from conftest import TEST_SETTINGS
from test_reports import VALID_DETAILS, report

from crewops.ingestion import report_worker
from crewops.ingestion.seed import load_seed
from crewops.store.ops_store import OpsStore
from crewops.store.reports import disruptions_with_status, get_disruption, rejections_for

NOW = datetime(2026, 10, 13, 5, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def seed():
    return load_seed()


@pytest.fixture
def table(ops_table, seed, monkeypatch):
    OpsStore(ops_table).put_seed(seed)
    monkeypatch.setenv("CREWOPS_STAGE", TEST_SETTINGS.stage)
    monkeypatch.setenv("AWS_REGION", TEST_SETTINGS.aws_region)
    return ops_table


def handle(table, doc):
    body = doc if isinstance(doc, str) else json.dumps(doc)
    return report_worker.handle_message(table, body, NOW)


def rejection(table, report_id):
    """The single rejection recorded for a report id (fails if there are none or several)."""
    (item,) = rejections_for(table, report_id)
    return item


@pytest.mark.parametrize("type_", list(VALID_DETAILS))
def test_valid_report_of_every_type_is_stored(table, type_):
    doc = report(type_, VALID_DETAILS[type_], report_id=f"r-{type_}")
    outcome = handle(table, doc)
    assert outcome.status == "stored"
    item = get_disruption(table, f"r-{type_}")
    assert item["status"] == "REPORTED" and item["report"]["type"] == type_


def test_stored_with_audit_event_and_status_index(table):
    handle(table, report())
    audit = table.query(
        KeyConditionExpression="pk = :pk AND begins_with(sk, :a)",
        ExpressionAttributeValues={":pk": "DISRUPTION#r-00000001", ":a": "AUDIT#"},
    )["Items"]
    assert [(a["action"], a["actor"]) for a in audit] == [("REPORTED", "u-1")]
    assert [d["disruption_id"] for d in disruptions_with_status(table)] == ["r-00000001"]


def test_duplicate_delivery_is_stored_once(table):
    assert handle(table, report()).status == "stored"
    assert handle(table, report()).status == "duplicate"
    audits = table.query(
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": "DISRUPTION#r-00000001"},
    )["Items"]
    assert len(audits) == 2  # one disruption item and one audit event, not four


@pytest.mark.parametrize(
    ("doc", "reason"),
    [
        (report(roles=("admin",)), "may not file reports"),
        (report(roles=("crew",), crew_id="C-2087"), "their own unavailability"),
        (
            report(details={"crew_id": "C-0000", "from_date": "2026-10-13", "reason": "sick"}),
            "unknown crew C-0000",
        ),
        (
            report("flight_delay", {"flight_id": "DX999-2026-10-13", "delay_minutes": 30}),
            "unknown flight",
        ),
        (
            report(
                "multiple_crew_unavailable",
                {"crew_ids": ["C-1042", "C-0000"], "from_date": "2026-10-13", "reason": "sick"},
            ),
            "unknown crew C-0000",
        ),
        (
            report(
                "gate_closure",
                {
                    "gate": "BLR-G9",
                    "from_utc": "2026-10-14T06:00:00Z",
                    "to_utc": "2026-10-14T07:00:00Z",
                    "reason": "x",
                },
            ),
            "unknown gate BLR-G9",
        ),
        (
            report(
                "aircraft_technical",
                {
                    "aircraft": "VT-ZZZ",
                    "from_utc": "2026-10-14T06:00:00Z",
                    "to_utc": "2026-10-14T07:00:00Z",
                },
            ),
            "unknown aircraft VT-ZZZ",
        ),
        (
            report(
                "certification_lapse",
                {"crew_id": "C-1042", "cert_type": "dangerous_goods", "valid_to": "2026-10-15"},
            )
            | {"report_id": "r-certcheck"},
            None,  # C-1042 does hold that certificate: accepted
        ),
    ],
)
def test_reports_that_fail_checks_are_rejected_and_recorded(table, doc, reason):
    outcome = handle(table, doc)
    if reason is None:
        assert outcome.status == "stored"
        return
    assert outcome.status == "rejected" and reason in outcome.reason
    assert get_disruption(table, doc["report_id"]) is None
    assert reason in rejection(table, doc["report_id"])["reason"]


def test_certificate_not_on_record_is_rejected(table):
    # Every crew member in the seed holds all four certificates, so remove one to test.
    table.delete_item(Key={"pk": "CREW#C-1042", "sk": "CERT#licence"})
    doc = report(
        "certification_lapse",
        {"crew_id": "C-1042", "cert_type": "licence", "valid_to": "2026-10-15"},
    )
    outcome = handle(table, doc)
    assert outcome.status == "rejected" and "no licence certification" in outcome.reason


def test_malformed_messages_are_rejected_not_retried(table):
    assert handle(table, "{nope").status == "rejected"
    bad = report(report_id="r-badfield")
    bad["details"] = {"crew_id": "C-1042"}
    outcome = handle(table, bad)
    assert outcome.status == "rejected" and outcome.report_id == "r-badfield"
    assert rejection(table, "r-badfield")["payload"].startswith("{")


def test_rejected_payload_with_decimals_is_stored_as_text(table):
    doc = report(report_id="r-decimals")
    doc["details"] = {"crew_id": "C-1042", "from_date": "2026-10-13", "reason": "sick", "x": 1.5}
    doc["type"] = "volcano"
    outcome = handle(table, doc)
    assert outcome.status == "rejected"
    assert '"x": 1.5' in rejection(table, "r-decimals")["payload"]


def test_handler_reports_only_unexpected_failures_for_retry(table, monkeypatch):
    # The handler stamps arrival with the real clock: report "now", as the server does.
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    good = {"messageId": "m-1", "body": json.dumps(report(reported_utc=now))}
    bad_data = {"messageId": "m-2", "body": "{nope"}
    boom = {
        "messageId": "m-3",
        "body": json.dumps(report(report_id="r-explodes", reported_utc=now)),
    }

    real = report_worker.store_report

    def flaky(table_, rep, received):
        if rep.report_id == "r-explodes":
            raise RuntimeError("DynamoDB unavailable")
        return real(table_, rep, received)

    monkeypatch.setattr(report_worker, "store_report", flaky)
    monkeypatch.setattr("crewops.store.ops_store.table_for", lambda settings: table)

    out = report_worker.handler({"Records": [good, bad_data, boom]})

    assert out == {"batchItemFailures": [{"itemIdentifier": "m-3"}]}
    assert get_disruption(table, "r-00000001") is not None


# -- Inputs that must never become poison messages --------------------------------------


def test_oversized_or_malformed_id_is_filed_as_unidentified(table):
    huge = report(report_id="x" * 3000)
    outcome = handle(table, huge)
    assert outcome.status == "rejected" and outcome.report_id is None
    item = rejection(table, "unidentified")
    assert len(item["reason"]) <= 1000 and len(item["payload"]) <= 4000


def test_huge_field_cannot_make_an_oversized_rejection(table):
    doc = report(report_id="r-hugetype")
    doc["type"] = "t" * 500_000
    outcome = handle(table, doc)
    assert outcome.status == "rejected"
    assert len(rejection(table, "r-hugetype")["reason"]) < 300


def test_unencodable_text_is_rejected_up_front(table):
    body = json.dumps(report(report_id="r-surrogat", narrative="ok")).replace(
        '"narrative": "ok"', '"narrative": "\\ud800"'
    )
    outcome = handle(table, body)
    assert outcome.status == "rejected" and "cannot be stored" in outcome.reason


def test_each_refused_message_keeps_its_own_record(table):
    for message_id in ("m-1", "m-2"):
        report_worker.handle_message(table, "{nope", NOW, message_id)
    report_worker.handle_message(table, "{nope", NOW, "m-1")  # redelivery of m-1
    assert len(rejections_for(table, "unidentified")) == 2


def test_resend_after_reference_removed_is_still_a_duplicate(table):
    assert handle(table, report()).status == "stored"
    table.delete_item(Key={"pk": "CREW#C-1042", "sk": "PROFILE"})
    assert handle(table, report()).status == "duplicate"
    assert rejections_for(table, "r-00000001") == []


def test_id_reused_for_different_content_is_refused(table):
    assert handle(table, report()).status == "stored"
    other = report("flight_delay", VALID_DETAILS["flight_delay"])
    outcome = handle(table, other)
    assert outcome.status == "rejected" and "already used for a different report" in outcome.reason
    assert get_disruption(table, "r-00000001")["report"]["type"] == "crew_unavailable"


def test_report_from_the_future_is_refused(table):
    doc = report(reported_utc="2026-10-13T06:00:00Z")  # 59 minutes after NOW
    outcome = handle(table, doc)
    assert outcome.status == "rejected" and "ahead of the time it arrived" in outcome.reason
    small_skew = report(report_id="r-skewok01", reported_utc="2026-10-13T05:04:00Z")
    assert handle(table, small_skew).status == "stored"


def test_resend_with_a_new_timestamp_is_a_duplicate(table):
    # A client resending a report stamps it again; that is still the same report.
    assert handle(table, report()).status == "stored"
    restamped = report(reported_utc="2026-10-13T05:00:45Z")
    assert handle(table, restamped).status == "duplicate"
    assert rejections_for(table, "r-00000001") == []
    stored = get_disruption(table, "r-00000001")
    assert stored["report"]["reported_utc"] == "2026-10-13T05:00:00Z"  # first copy kept


def test_resend_with_changed_details_is_still_a_conflict(table):
    assert handle(table, report()).status == "stored"
    changed = report(details={"crew_id": "C-1042", "from_date": "2026-10-13", "reason": "fatigue"})
    outcome = handle(table, changed)
    assert outcome.status == "rejected" and "different report" in outcome.reason
