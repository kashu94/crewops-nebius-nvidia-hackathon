"""Shared fixtures for tests that need fake AWS (moto). No test ever reaches a real account."""

import boto3
import pytest
from moto import mock_aws

from crewops.config import Settings
from crewops.store import schema
from crewops.store.ops_store import table_for

TEST_SETTINGS = Settings.from_env({"CREWOPS_STAGE": "test", "AWS_REGION": "ap-southeast-2"})


@pytest.fixture
def moto_aws(monkeypatch):
    """Fake credentials and an in-memory AWS. Real profiles and keys are never consulted."""
    for key in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "NEBIUS_API_KEY", "CREWOPS_NEBIUS_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", TEST_SETTINGS.aws_region)
    with mock_aws():
        yield


@pytest.fixture
def ops_table(moto_aws):
    """The ops table with the same keys and indexes the CDK stack defines."""

    def key(name, kind):
        return {"AttributeName": name, "KeyType": kind}

    def gsi(name, pk, sk):
        return {
            "IndexName": name,
            "KeySchema": [key(pk, "HASH"), key(sk, "RANGE")],
            "Projection": {"ProjectionType": "ALL"},
        }

    attrs = [schema.PK, schema.SK, schema.GSI1_PK, schema.GSI1_SK, schema.GSI2_PK, schema.GSI2_SK]
    boto3.client("dynamodb", region_name=TEST_SETTINGS.aws_region).create_table(
        TableName=TEST_SETTINGS.table_name,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[key(schema.PK, "HASH"), key(schema.SK, "RANGE")],
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in attrs],
        GlobalSecondaryIndexes=[
            gsi(schema.GSI1, schema.GSI1_PK, schema.GSI1_SK),
            gsi(schema.GSI2, schema.GSI2_PK, schema.GSI2_SK),
        ],
    )
    return table_for(TEST_SETTINGS, boto3.Session(region_name=TEST_SETTINGS.aws_region))
