"""The send_report command: local validation, and sending to moto's SQS."""

import json

import boto3
from conftest import TEST_SETTINGS

from crewops.domain.reports import Report
from crewops.ingestion import send_report

ARGS = [
    "crew_unavailable",
    "--user",
    "u-17",
    "--role",
    "reporter",
    "--details",
    '{"crew_id": "C-1042", "from_date": "2026-10-13", "reason": "sick"}',
]


def test_dry_run_prints_a_valid_report(capsys):
    assert send_report.main([*ARGS, "--dry-run", "--report-id", "r-dryrun01"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert Report.from_dict(doc).report_id == "r-dryrun01"


def test_invalid_details_are_caught_before_sending(capsys):
    args = [*ARGS[:-1], '{"crew_id": "C-1042"}', "--dry-run"]
    assert send_report.main(args) == 1
    assert "Invalid report" in capsys.readouterr().err


def test_sends_to_the_ingest_queue(moto_aws, monkeypatch, capsys):
    monkeypatch.setattr(send_report, "load_env_file", lambda: {})
    monkeypatch.setenv("CREWOPS_STAGE", TEST_SETTINGS.stage)
    monkeypatch.setenv("AWS_REGION", TEST_SETTINGS.aws_region)
    sqs = boto3.client("sqs", region_name=TEST_SETTINGS.aws_region)
    url = sqs.create_queue(QueueName=TEST_SETTINGS.ingest_queue)["QueueUrl"]
    monkeypatch.setenv("AWS_PROFILE", "crewops")  # required by the command ...
    monkeypatch.setattr(send_report, "sqs_client", lambda settings: sqs)  # ... but never read

    assert send_report.main([*ARGS, "--report-id", "r-sent0001"]) == 0

    body = sqs.receive_message(QueueUrl=url)["Messages"][0]["Body"]
    assert json.loads(body)["report_id"] == "r-sent0001"
    assert "Sent report r-sent0001" in capsys.readouterr().out
