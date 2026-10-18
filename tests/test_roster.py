"""Roster uploads: parsing, merging and validation. Pure, no AWS."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from crewops.domain import DataError
from crewops.ingestion.roster import (
    MAX_UPLOAD_BYTES,
    Status,
    ingest,
    merge,
    parse_upload,
)
from crewops.ingestion.seed import default_seed_dir, load_seed
from crewops.store.items import _crew, _pairing, to_dynamo

SEED_DIR = default_seed_dir()


@pytest.fixture(scope="module")
def seed():
    return load_seed()


def raw(doc) -> bytes:
    return json.dumps(doc).encode()


def as_json(record_dict) -> dict:
    """A record in its JSON shape (dates and times as strings, numbers as numbers)."""
    return json.loads(json.dumps(to_dynamo(record_dict), default=float))


def test_seed_rosters_file_uploads_unchanged(seed):
    doc = (SEED_DIR / "rosters.json").read_bytes()
    result, records = ingest(doc, seed, "uploads/rosters.json")
    assert result.status is Status.ACCEPTED, result.errors
    assert result.counts == {"pairings": 39, "flagged_exceptions": 1}
    assert result.warnings == ()  # the known C-2087 warning is not repeated
    assert len(records) == 40


def test_seed_rules_file_uploads_unchanged(seed):
    result, _ = ingest((SEED_DIR / "rules.json").read_bytes(), seed, "uploads/rules.json")
    assert result.status is Status.ACCEPTED and result.counts == {"rules": 7}


def test_changed_record_replaces_by_identity(seed):
    crew = next(c for c in seed.crew if c.crew_id == "C-3310")
    changed = as_json(_crew(replace(crew, reachability_minutes=30)))
    result, records = ingest(raw({"crew": [changed]}), seed, "uploads/c.json")
    assert result.status is Status.ACCEPTED and result.counts == {"crew": 1}
    assert records[0].reachability_minutes == 30
    merged = merge(seed, parse_upload({"crew": [changed]}))
    assert len(merged.crew) == len(seed.crew)
    assert next(c for c in merged.crew if c.crew_id == "C-3310").reachability_minutes == 30


def test_new_record_is_added(seed):
    newcomer = as_json(_crew(replace(seed.crew[0], crew_id="C-9001", name="Z. New")))
    merged = merge(seed, parse_upload({"crew": [newcomer]}))
    assert len(merged.crew) == len(seed.crew) + 1


def test_pairing_with_unknown_crew_is_rejected(seed):
    pairing = seed.pairings[0]
    seats = [{"crew_id": "C-0000", "role": "Captain"}]
    doc = {"pairings": [{**as_json(_pairing(pairing)), "crew": seats}]}
    result, records = ingest(raw(doc), seed, "uploads/p.json")
    assert result.status is Status.REJECTED
    assert any("unknown crew C-0000" in e for e in result.errors)
    assert records == []


def test_double_booking_is_rejected(seed):
    a, b = seed.pairings[0], seed.pairings[1]
    intruder = a.holder(a.crew[0].role)[0]
    clash = as_json(_pairing(replace(b, days=a.days)))
    clash["crew"] = [{"crew_id": intruder, "role": str(a.crew[0].role)}]
    result, _ = ingest(raw({"pairings": [clash]}), seed, "uploads/p.json")
    assert result.status is Status.REJECTED
    assert any("double-booked" in e for e in result.errors)


def test_new_warning_is_reported_but_accepted(seed):
    cert = next(c for c in seed.certifications if c.crew_id == "C-1042")
    odd = {
        "crew_id": cert.crew_id,
        "cert_type": str(cert.cert_type),
        "valid_from": "2099-01-29",  # after any valid_to in the data
        "valid_to": cert.valid_to.isoformat(),
    }
    result, _ = ingest(raw({"certifications": [odd]}), seed, "uploads/c.json")
    assert result.status is Status.ACCEPTED
    assert len(result.warnings) == 1 and "C-1042" in result.warnings[0]


def test_costs_upload(seed):
    costs = json.loads((SEED_DIR / "costs.json").read_text())
    costs.pop("notes", None)
    result, records = ingest(raw({"costs": costs}), seed, "uploads/costs.json")
    assert result.status is Status.ACCEPTED and result.counts == {"costs": 1}
    assert records == [seed.costs]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"{not json", "not valid JSON"),
        (b"\xff\xfe", "not valid JSON"),
        (b"[]", "expected a JSON object"),
        (b"{}", "no records"),
        (raw({"crews": []}), "unknown key"),
        (raw({"crew": {}}), "crew: expected a list"),
        (raw({"crew": [{"crew_id": "C-1"}]}), "crew[0].ratings: missing"),
    ],
)
def test_malformed_uploads_are_rejected(seed, body, message):
    result, records = ingest(body, seed, "uploads/x.json")
    assert result.status is Status.REJECTED and records == []
    assert any(message in e for e in result.errors), result.errors


def test_duplicate_identity_in_one_upload(seed):
    row = as_json(_crew(seed.crew[0]))
    with pytest.raises(DataError, match="more than once"):
        parse_upload({"crew": [row, row]})


def test_oversized_upload(seed):
    result, _ = ingest(b" " * (MAX_UPLOAD_BYTES + 1), seed, "uploads/big.json")
    assert result.status is Status.REJECTED and "limit" in result.errors[0]


def test_result_report_shape(seed):
    result, _ = ingest((SEED_DIR / "rosters.json").read_bytes(), seed, "uploads/r.json")
    report = result.to_dict()
    assert set(report) == {"upload", "status", "records", "errors", "warnings"}
    assert report["status"] == "accepted"
    assert Path(report["upload"]).name == "r.json"
