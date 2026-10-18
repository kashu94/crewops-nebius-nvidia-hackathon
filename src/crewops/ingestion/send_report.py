"""Send a live report to the ingest queue (for testing and demos).

    uv run python -m crewops.ingestion.send_report crew_unavailable \\
        --user u-17 --role reporter \\
        --details '{"crew_id": "C-1042", "from_date": "2026-10-13", "reason": "sick"}'

In the finished system the MCP server sends reports on behalf of a signed-in user, taking
the sender's identity from their verified login. This command lets a developer state the
identity directly, so it is for development only; the worker still enforces the role rules.

The report is validated locally with the same parser the worker uses before it is sent.
Re-sending with the same ``--report-id`` is safe: the worker stores a report only once.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

from crewops.config import Settings, load_env_file
from crewops.domain import DataError
from crewops.domain.reports import Report, ReportType, Role


def sqs_client(settings: Settings):
    import boto3

    return boto3.Session(region_name=settings.aws_region).client("sqs")


def build_report(args: argparse.Namespace, now: datetime) -> dict[str, Any]:
    try:
        details = json.loads(args.details)
    except json.JSONDecodeError as exc:
        raise DataError(f"--details is not valid JSON: {exc}") from None
    return {
        "report_id": args.report_id or uuid.uuid4().hex,
        "type": args.type,
        "reported_by": {"user_id": args.user, "roles": args.role, "crew_id": args.crew_id},
        "reported_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "details": details,
        "narrative": args.narrative or "",
    }


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("type", choices=[t.value for t in ReportType])
    parser.add_argument("--details", required=True, help="type-specific details as JSON")
    parser.add_argument("--user", required=True, help="sender's user id")
    parser.add_argument(
        "--role",
        action="append",
        required=True,
        choices=[r.value for r in Role],
        help="sender's role; repeat for several",
    )
    parser.add_argument("--crew-id", default=None, help="sender's own crew id, if crew")
    parser.add_argument("--report-id", default=None, help="reuse to resend the same report")
    parser.add_argument("--narrative", default=None)
    parser.add_argument("--dry-run", action="store_true", help="validate and print only")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        message = build_report(args, datetime.now(UTC))
        Report.from_dict(message)  # fail here rather than as a rejection in the worker
    except DataError as exc:
        print(f"Invalid report: {exc}", file=sys.stderr)
        return 1

    body = json.dumps(message)
    if args.dry_run:
        print(body)
        return 0

    load_env_file()
    profile = os.environ.get("AWS_PROFILE")
    if not profile or profile == "default":
        print("Set AWS_PROFILE (for example in .env) to a named profile.", file=sys.stderr)
        return 1

    settings = Settings.from_env()
    sqs = sqs_client(settings)
    url = sqs.get_queue_url(QueueName=settings.ingest_queue)["QueueUrl"]
    sqs.send_message(QueueUrl=url, MessageBody=body)
    print(f"Sent report {message['report_id']} to {settings.ingest_queue}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
