"""Synthesize the ingestion stack locally (no AWS access) and check what it would create."""

import json

import pytest

pytest.importorskip("aws_cdk")

from aws_cdk import App, Environment  # noqa: E402
from aws_cdk.assertions import Match, Template  # noqa: E402
from crewops_infra.data_stack import DataStack  # noqa: E402
from crewops_infra.ingestion_stack import IngestionStack  # noqa: E402

from crewops.config import Settings  # noqa: E402

ENV = Environment(account="111111111111", region="ap-southeast-2")


OPERATOR = "arn:aws:iam::111111111111:user/crewops-deployer"


def synth(stage: str = "dev", senders: str = "") -> Template:
    settings = Settings.from_env({"CREWOPS_STAGE": stage, "CREWOPS_QUEUE_SENDERS": senders})
    app = App()
    data = DataStack(app, "Data", settings=settings, env=ENV)
    ingestion = IngestionStack(app, "Ingestion", settings=settings, table=data.table, env=ENV)
    return Template.from_stack(ingestion)


def test_private_encrypted_bucket_named_per_account():
    t = synth()
    t.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "BucketName": "crewops-dev-rosters-111111111111",
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "BucketEncryption": Match.object_like({}),
        },
    )


def test_loader_function_runs_our_handler():
    synth().has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Handler": "crewops.ingestion.roster_upload.handler",
            "Runtime": "python3.12",
            "Architectures": ["arm64"],
            "Timeout": 60,
            "Environment": {"Variables": {"CREWOPS_STAGE": "dev"}},
        },
    )


def test_trigger_only_for_json_under_uploads():
    (notifications,) = synth().find_resources("Custom::S3BucketNotifications").values()
    (config,) = notifications["Properties"]["NotificationConfiguration"][
        "LambdaFunctionConfigurations"
    ]
    assert config["Events"] == ["s3:ObjectCreated:*"]
    rules = {(r["Name"], r["Value"]) for r in config["Filter"]["Key"]["FilterRules"]}
    assert rules == {("prefix", "uploads/"), ("suffix", ".json")}


def test_loader_can_use_the_table():
    statements = synth().find_resources("AWS::IAM::Policy")
    text = str(statements)
    for action in ("dynamodb:BatchWriteItem", "dynamodb:Scan", "dynamodb:PutItem"):
        assert action in text


def test_non_dev_stage_keeps_the_bucket():
    buckets = synth("demo").find_resources("AWS::S3::Bucket")
    (bucket,) = buckets.values()
    assert bucket["DeletionPolicy"] == "Retain"


def test_ingest_queue_with_dead_letters_after_three_tries():
    t = synth()
    t.has_resource_properties(
        "AWS::SQS::Queue",
        {
            "QueueName": "crewops-dev-ingest",
            "VisibilityTimeout": 180,
            "RedrivePolicy": Match.object_like({"maxReceiveCount": 3}),
            "SqsManagedSseEnabled": True,
        },
    )
    t.has_resource_properties(
        "AWS::SQS::Queue",
        {"QueueName": "crewops-dev-ingest-dlq", "MessageRetentionPeriod": 14 * 24 * 3600},
    )


def test_report_worker_reads_the_queue_with_partial_batch_failures():
    t = synth()
    t.has_resource_properties(
        "AWS::Lambda::Function",
        {"Handler": "crewops.ingestion.report_worker.handler", "Timeout": 30},
    )
    t.has_resource_properties(
        "AWS::Lambda::EventSourceMapping",
        {"BatchSize": 10, "FunctionResponseTypes": ["ReportBatchItemFailures"]},
    )


def test_report_worker_gets_only_the_table_actions_it_uses():
    t = synth()
    policies = t.find_resources("AWS::IAM::Policy")
    worker = next(p for k, p in policies.items() if k.startswith("ReportWorker"))
    actions = set()
    for statement in worker["Properties"]["PolicyDocument"]["Statement"]:
        action = statement["Action"]
        actions.update(action if isinstance(action, list) else [action])
    dynamo = {a for a in actions if a.startswith("dynamodb:")}
    assert dynamo == {
        "dynamodb:GetItem",
        "dynamodb:Query",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
    }
    # Whatever storing a sick report sends must be allowed. moto doesn't enforce IAM, so
    # this is the only check that the deployed worker may do what the tests do.
    assert worker_writes() <= dynamo
    resources = str(worker["Properties"]["PolicyDocument"])
    assert "/index/*" in resources  # the status index query is allowed too


def test_one_queue_policy_keeps_tls_and_admits_only_the_server_and_operators():
    template = synth(senders=OPERATOR)
    policies = [
        p
        for p in template.find_resources("AWS::SQS::QueuePolicy").values()
        if "IngestQueue" in json.dumps(p["Properties"]["Queues"])
        and "DeadLetters" not in json.dumps(p["Properties"]["Queues"])
    ]
    (policy,) = policies
    by_sid = {s.get("Sid"): s for s in policy["Properties"]["PolicyDocument"]["Statement"]}
    tls = [s for s in by_sid.values() if "aws:SecureTransport" in json.dumps(s)]
    assert tls and tls[0]["Effect"] == "Deny"
    deny = by_sid["OnlyTheMcpServerSendsReports"]
    assert deny["Effect"] == "Deny" and deny["Action"] == "sqs:SendMessage"
    allowed = json.dumps(deny["Condition"]["ArnNotEquals"]["aws:PrincipalArn"])
    assert ":role/crewops-dev-mcp-runtime" in allowed and OPERATOR in allowed


def test_without_operators_only_the_server_may_send():
    policies = synth().find_resources("AWS::SQS::QueuePolicy").values()
    (deny,) = [
        s
        for p in policies
        for s in p["Properties"]["PolicyDocument"]["Statement"]
        if s.get("Sid") == "OnlyTheMcpServerSendsReports"
    ]
    allowed = json.dumps(deny["Condition"]["ArnNotEquals"]["aws:PrincipalArn"])
    assert ":role/crewops-dev-mcp-runtime" in allowed and ":user/" not in allowed


def worker_writes() -> set[str]:
    """The DynamoDB actions storing a crew-out report needs, read off the transaction it
    actually sends: each Put, Update, Delete or ConditionCheck is its own IAM action."""
    from datetime import UTC, datetime

    from crewops.domain import Report
    from crewops.store.reports import store_report

    sent: list[dict] = []

    class Client:
        def transact_write_items(self, TransactItems):  # noqa: N803 (boto3's name)
            sent.extend(TransactItems)

    class Table:
        name = "t"
        meta = type("Meta", (), {"client": Client()})()

    doc = {
        "report_id": "r-iamcheck1",
        "type": "multiple_crew_unavailable",
        "reported_by": {"user_id": "u-1", "roles": ["reporter"]},
        "reported_utc": "2026-10-13T05:00:00Z",
        "details": {"crew_ids": ["C-1042", "C-3310"], "from_date": "2026-10-13", "reason": "sick"},
    }
    store_report(Table(), Report.from_dict(doc), datetime(2026, 10, 13, 5, 1, tzinfo=UTC))
    kinds = {k for item in sent for k in item}
    names = {"Put": "PutItem", "Update": "UpdateItem", "Delete": "DeleteItem"}
    names["ConditionCheck"] = "ConditionCheckItem"
    return {f"dynamodb:{names[k]}" for k in kinds}
