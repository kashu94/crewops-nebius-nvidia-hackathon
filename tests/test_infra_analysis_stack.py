"""Synthesize the analysis stack locally (no AWS access) and check what it would create."""

import json

import pytest

pytest.importorskip("aws_cdk")

from aws_cdk import App, Environment  # noqa: E402
from aws_cdk.assertions import Template  # noqa: E402
from crewops_infra.analysis_stack import AnalysisStack  # noqa: E402
from crewops_infra.data_stack import DataStack  # noqa: E402

from crewops.config import Settings  # noqa: E402

ENV = Environment(account="111111111111", region="ap-southeast-2")


@pytest.fixture(scope="module")
def template():
    settings = Settings.from_env({"CREWOPS_STAGE": "dev"})
    app = App()
    data = DataStack(app, "Data", settings=settings, env=ENV)
    return Template.from_stack(
        AnalysisStack(app, "Analysis", settings=settings, table=data.table, env=ENV)
    )


def test_stream_triggers_only_on_new_disruptions(template):
    (mapping,) = template.find_resources("AWS::Lambda::EventSourceMapping").values()
    props = mapping["Properties"]
    assert props["StartingPosition"] == "LATEST"
    assert props["FunctionResponseTypes"] == ["ReportBatchItemFailures"]
    assert props["MaximumRetryAttempts"] == 3 and props["BisectBatchOnFunctionError"] is True
    (pattern,) = [json.loads(f["Pattern"]) for f in props["FilterCriteria"]["Filters"]]
    assert pattern == {
        "eventName": ["INSERT"],
        "dynamodb": {"NewImage": {"entity": {"S": ["disruption"]}}},
    }
    assert "DestinationConfig" in props  # failed records go to the dead-letter queue


def test_analyser_function(template):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Handler": "crewops.analysis.stream_handler.handler",
            "Runtime": "python3.12",
            "Architectures": ["arm64"],
            "Timeout": 120,
            "MemorySize": 1024,
        },
    )


def test_analyser_gets_only_the_table_actions_it_uses(template):
    (policy,) = template.find_resources("AWS::IAM::Policy").values()
    actions = set()
    for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
        action = statement["Action"]
        actions.update(action if isinstance(action, list) else [action])
    stream_reading = {  # granted by the event source to read the change stream
        "dynamodb:DescribeStream",
        "dynamodb:GetRecords",
        "dynamodb:GetShardIterator",
        "dynamodb:ListStreams",
    }
    assert stream_reading <= actions
    table_actions = {a for a in actions if a.startswith("dynamodb:")} - stream_reading
    assert table_actions == {
        "dynamodb:Scan",
        "dynamodb:GetItem",
        "dynamodb:Query",
        "dynamodb:UpdateItem",
        "dynamodb:PutItem",
    }
