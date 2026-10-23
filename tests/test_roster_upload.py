"""The roster-upload Lambda end to end, against moto's S3 and DynamoDB."""

import json

import boto3
import pytest
from conftest import TEST_SETTINGS

from crewops.ingestion import roster_upload
from crewops.ingestion.seed import default_seed_dir, load_seed
from crewops.store.ops_store import OpsStore

BUCKET = "crewops-test-rosters-000000000000"


@pytest.fixture
def env(ops_table, monkeypatch):
    monkeypatch.setenv("CREWOPS_STAGE", TEST_SETTINGS.stage)
    monkeypatch.setenv("AWS_REGION", TEST_SETTINGS.aws_region)
    s3 = boto3.client("s3", region_name=TEST_SETTINGS.aws_region)
    s3.create_bucket(
        Bucket=BUCKET,
        CreateBucketConfiguration={"LocationConstraint": TEST_SETTINGS.aws_region},
    )
    store = OpsStore(ops_table)
    store.put_seed(load_seed())
    return s3, store


def upload(s3, key: str, body: bytes) -> dict:
    s3.put_object(Bucket=BUCKET, Key=key, Body=body)
    return {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}]}


def report(s3, key: str) -> dict:
    body = s3.get_object(Bucket=BUCKET, Key=roster_upload.result_key(key))["Body"].read()
    return json.loads(body)


def test_accepted_upload_is_written_and_reported(env):
    s3, store = env
    crew = json.loads((default_seed_dir() / "crew.json").read_text())
    target = next(c for c in crew if c["crew_id"] == "C-3310")
    target["reachability_minutes"] = 30
    event = upload(s3, "uploads/reach.json", json.dumps({"crew": [target]}).encode())

    out = roster_upload.handler(event)

    assert out == {"processed": [{"upload": "uploads/reach.json", "status": "accepted"}]}
    assert report(s3, "uploads/reach.json")["records"] == {"crew": 1}
    stored = next(c for c in store.load_seed().crew if c.crew_id == "C-3310")
    assert stored.reachability_minutes == 30


def test_rejected_upload_changes_nothing(env):
    s3, store = env
    before = store.load_seed()
    bad = {"pairings": [{"pairing_id": "P-9999", "aircraft": "VT-DXA", "days": [], "crew": []}]}
    event = upload(s3, "uploads/bad.json", json.dumps(bad).encode())

    out = roster_upload.handler(event)

    assert out["processed"][0]["status"] == "rejected"
    assert "at least one day" in report(s3, "uploads/bad.json")["errors"][0]
    assert store.load_seed() == before


def test_url_encoded_keys_are_decoded(env):
    s3, _ = env
    key = "uploads/week 38.json"
    s3.put_object(Bucket=BUCKET, Key=key, Body=(default_seed_dir() / "rules.json").read_bytes())
    event = {
        "Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": "uploads/week+38.json"}}}]
    }

    out = roster_upload.handler(event)

    assert out["processed"][0] == {"upload": key, "status": "accepted"}
    assert report(s3, key)["status"] == "accepted"


def test_oversized_object_is_rejected_without_reading(env, monkeypatch):
    s3, _ = env
    monkeypatch.setattr(roster_upload, "MAX_UPLOAD_BYTES", 10)
    event = upload(s3, "uploads/big.json", b'{"crew": []}  ')

    out = roster_upload.handler(event)

    assert out["processed"][0]["status"] == "rejected"
    assert "the limit is 10" in report(s3, "uploads/big.json")["errors"][0]


def test_objects_outside_uploads_are_ignored(env):
    s3, _ = env
    event = upload(s3, "results/x.json.result.json", b"{}")
    assert roster_upload.handler(event) == {"processed": []}


def test_result_key():
    assert roster_upload.result_key("uploads/a/b.json") == "results/a/b.json.result.json"


def test_moving_a_flight_to_another_gate_leaves_one_slot(env):
    s3, store = env
    slots = json.loads((default_seed_dir() / "boarding_gates.json").read_text())
    slot = next(g for g in slots if g["flight_id"] == "DX403-2026-10-13")
    assert slot["boarding_gate_number"] == "BLR-G1"
    moved = {**slot, "boarding_gate_number": "BLR-G6"}
    event = upload(s3, "uploads/gate.json", json.dumps({"boarding_gates": [moved]}).encode())

    out = roster_upload.handler(event)

    assert out["processed"][0]["status"] == "accepted"
    held = [g.gate for g in store.load_seed().gates if g.flight_id == "DX403-2026-10-13"]
    assert held == ["BLR-G6"]
