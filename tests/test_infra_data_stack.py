"""Synthesize the data stack locally (no AWS access) and check what it would create."""

import pytest

pytest.importorskip("aws_cdk")

from aws_cdk import App  # noqa: E402
from aws_cdk.assertions import Match, Template  # noqa: E402
from crewops_infra.data_stack import DataStack  # noqa: E402

from crewops.config import Settings  # noqa: E402


def synth(stage: str) -> Template:
    settings = Settings.from_env({"CREWOPS_STAGE": stage})
    stack = DataStack(App(), f"CrewOps-{stage}-Data", settings=settings)
    return Template.from_stack(stack)


def table(template: Template) -> dict:
    (resource,) = template.find_resources("AWS::DynamoDB::GlobalTable").values()
    return resource


def test_single_table_with_keys_stream_and_ttl():
    t = synth("dev")
    t.resource_count_is("AWS::DynamoDB::GlobalTable", 1)
    t.has_resource_properties(
        "AWS::DynamoDB::GlobalTable",
        {
            "TableName": "crewops-dev-ops",
            "BillingMode": "PAY_PER_REQUEST",
            "KeySchema": [
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            "StreamSpecification": {"StreamViewType": "NEW_AND_OLD_IMAGES"},
            "TimeToLiveSpecification": {"AttributeName": "expires_at", "Enabled": True},
            "GlobalSecondaryIndexes": Match.array_with(
                [Match.object_like({"IndexName": "gsi1"}), Match.object_like({"IndexName": "gsi2"})]
            ),
        },
    )


def test_dev_stage_is_disposable():
    resource = table(synth("dev"))
    assert resource["DeletionPolicy"] == "Delete"
    replica = resource["Properties"]["Replicas"][0]
    assert replica.get("DeletionProtectionEnabled") in (None, False)


def test_other_stages_are_protected():
    resource = table(synth("demo"))
    assert resource["DeletionPolicy"] == "Retain"
    replica = resource["Properties"]["Replicas"][0]
    assert replica["DeletionProtectionEnabled"] is True
    pitr = replica["PointInTimeRecoverySpecification"]
    assert pitr["PointInTimeRecoveryEnabled"] is True


def test_outputs_table_name_and_stream():
    outputs = synth("dev").find_outputs("*")
    assert {"TableName", "TableStreamArn"} <= set(outputs)
