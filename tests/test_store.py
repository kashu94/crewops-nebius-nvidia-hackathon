"""DynamoDB store against moto's in-memory DynamoDB. Never touches a real AWS account."""

from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from crewops.config import Settings
from crewops.domain import DataError, Rank
from crewops.engine import build_world, find_options
from crewops.ingestion import load_seed as loader
from crewops.ingestion.seed import load_seed
from crewops.store import schema
from crewops.store.items import from_dynamo, from_item, to_dynamo, to_item
from crewops.store.ops_store import SORT_KEYS, OpsStore, seed_records, table_for

SETTINGS = Settings.from_env({"CREWOPS_STAGE": "test", "AWS_REGION": "ap-southeast-2"})
EXPECTED_ITEMS = 1441  # the operation, rules, costs, 21 contacts, 3 airlines, 9 emergency gates


@pytest.fixture(autouse=True)
def fake_aws(monkeypatch):
    for key in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-southeast-2")
    with mock_aws():
        yield


def create_table():
    def key(name, kind):
        return {"AttributeName": name, "KeyType": kind}

    def gsi(name, pk, sk):
        return {
            "IndexName": name,
            "KeySchema": [key(pk, "HASH"), key(sk, "RANGE")],
            "Projection": {"ProjectionType": "ALL"},
        }

    attrs = [schema.PK, schema.SK, schema.GSI1_PK, schema.GSI1_SK, schema.GSI2_PK, schema.GSI2_SK]
    boto3.client("dynamodb", region_name=SETTINGS.aws_region).create_table(
        TableName=SETTINGS.table_name,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[key(schema.PK, "HASH"), key(schema.SK, "RANGE")],
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in attrs],
        GlobalSecondaryIndexes=[
            gsi(schema.GSI1, schema.GSI1_PK, schema.GSI1_SK),
            gsi(schema.GSI2, schema.GSI2_PK, schema.GSI2_SK),
        ],
    )
    return table_for(SETTINGS, boto3.Session(region_name=SETTINGS.aws_region))


def normalised(seed):
    """Same records, each collection in the store's deterministic order."""
    changes = {}
    for f in fields(seed):
        value = getattr(seed, f.name)
        if isinstance(value, tuple) and value:
            changes[f.name] = tuple(sorted(value, key=SORT_KEYS[type(value[0])]))
    return replace(seed, **changes)


@pytest.fixture(scope="module")
def seed():
    return load_seed()


def test_value_conversion():
    assert to_dynamo(20.93) == Decimal("20.93")
    assert to_dynamo(datetime(2026, 10, 12, 2, 30, tzinfo=UTC)) == "2026-10-12T02:30:00Z"
    assert to_dynamo(Rank.CAPTAIN) == "Captain"
    assert to_dynamo((1, 2.5)) == [1, Decimal("2.5")]
    assert from_dynamo({"a": [Decimal("3"), Decimal("0.75")]}) == {"a": [3, 0.75]}
    with pytest.raises(TypeError):
        to_dynamo(object())


def test_every_record_round_trips_through_an_item(seed):
    for record in seed_records(seed):
        item = to_item(record)
        assert item[schema.PK] and item[schema.SK] and item["entity"]
        assert from_item(item) == record


def test_keys_follow_the_documented_layout(seed):
    crew = to_item(next(c for c in seed.crew if c.crew_id == "C-1042"))
    assert (crew["pk"], crew["sk"], crew["gsi1pk"]) == ("CREW#C-1042", "PROFILE", "BASE#BLR")
    assert crew["gsi1sk"] == "RANK#Captain#C-1042"
    flight = to_item(next(f for f in seed.flights if f.flight_id == "DX401-2026-10-12"))
    assert flight["gsi1pk"] == "DATE#2026-10-12"
    assert flight["gsi1sk"] == "DEP#2026-10-12T02:30:00Z#DX401-2026-10-12"
    assert to_item(seed.costs)["pk"] == "CONFIG"


def test_seed_round_trips_through_the_table(seed):
    store = OpsStore(create_table())
    counts = store.put_seed(seed)
    assert sum(counts.values()) == EXPECTED_ITEMS
    assert counts["crew"] == 150 and counts["certification"] == 600
    assert counts["contact"] == 21
    assert counts["airline"] == 3 and counts["emergency_gate"] == 9
    assert store.load_seed() == normalised(seed)


def test_engine_answers_the_same_from_the_table(seed):
    store = OpsStore(create_table())
    store.put_seed(seed)
    from_disk = find_options(build_world(seed), "P-2291", Rank.CAPTAIN)
    from_table = find_options(build_world(store.load_seed()), "P-2291", Rank.CAPTAIN)
    assert [(o.crew_id, o.cost_inr) for o in from_table.options] == [
        (o.crew_id, o.cost_inr) for o in from_disk.options
    ]


def test_writing_twice_replaces_rather_than_duplicates(seed):
    store = OpsStore(create_table())
    store.put_seed(seed)
    store.put_seed(seed)
    assert sum(1 for _ in store.scan()) == EXPECTED_ITEMS


def test_items_outside_the_operation_are_ignored_on_load(seed):
    table = create_table()
    store = OpsStore(table)
    store.put_seed(seed)
    table.put_item(Item={"pk": "DISRUPTION#D-1", "sk": "META", "entity": "disruption"})
    assert store.load_seed() == normalised(seed)


def test_duplicate_keys_are_refused_before_any_write(seed):
    store = OpsStore(create_table())
    crew = seed.crew[0]
    with pytest.raises(DataError, match="same key"):
        store.put_records([seed.crew[1], crew, crew])
    assert sum(1 for _ in store.scan()) == 0


def test_loader_dry_run_needs_no_aws(monkeypatch, capsys):
    monkeypatch.setattr(loader, "load_env_file", lambda: {})  # never read a developer's .env
    assert loader.main(["--dry-run", "--week-only"]) == 0
    assert f"{EXPECTED_ITEMS} items would be written" in capsys.readouterr().out


def test_loader_refuses_without_named_profile(monkeypatch, capsys):
    monkeypatch.setattr(loader, "load_env_file", lambda: {})
    assert loader.main(["--week-only"]) == 1
    assert "AWS_PROFILE" in capsys.readouterr().err


def test_loader_writes_with_profile(monkeypatch, capsys, seed):
    table = create_table()
    monkeypatch.setattr(loader, "load_env_file", lambda: {})
    monkeypatch.setenv("CREWOPS_STAGE", "test")
    monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
    monkeypatch.setenv("AWS_PROFILE", "crewops")
    # The loader must see a named profile, but boto3 must not look it up. Hand it the moto
    # table directly so no local profile or credentials get read.
    monkeypatch.setattr("crewops.store.ops_store.table_for", lambda settings: table)
    assert loader.main(["--week-only"]) == 0
    assert f"Done: {EXPECTED_ITEMS} items." in capsys.readouterr().out


def test_loader_replace_empties_a_disposable_table_first(monkeypatch, capsys, seed):
    table = create_table()
    table.put_item(Item={"pk": "FLIGHT#DX412-2026-09-15", "sk": "FLIGHT", "entity": "flight"})
    monkeypatch.setattr(loader, "load_env_file", lambda: {})
    monkeypatch.setenv("CREWOPS_STAGE", "dev")
    monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
    monkeypatch.setenv("AWS_PROFILE", "crewops")
    monkeypatch.setattr("crewops.store.ops_store.table_for", lambda settings: table)
    assert loader.main(["--replace", "--week-only"]) == 0
    assert "1 items deleted" in capsys.readouterr().out
    assert sum(1 for _ in OpsStore(table).scan()) == EXPECTED_ITEMS  # the old week is gone


def test_loader_never_replaces_a_kept_stage(monkeypatch, capsys):
    monkeypatch.setattr(loader, "load_env_file", lambda: {})
    monkeypatch.setenv("CREWOPS_STAGE", "prod")
    monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
    monkeypatch.setenv("AWS_PROFILE", "crewops")

    def boom(settings):
        raise AssertionError("must not touch the table")

    monkeypatch.setattr("crewops.store.ops_store.table_for", boom)
    assert loader.main(["--replace", "--week-only"]) == 1
    assert "not disposable" in capsys.readouterr().err
