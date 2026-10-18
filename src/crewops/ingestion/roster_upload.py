"""Lambda handler for objects landing under ``uploads/`` in the roster bucket.

Reads the file, loads the current operation from DynamoDB and runs ``roster.ingest``. If
the upload is accepted its records are written. A result report is written either way:

    uploads/week-38.json  ->  results/week-38.json.result.json

Rejection is a normal outcome, so we report it and return. Only unexpected errors (S3 or
DynamoDB unavailable) raise and get the event retried, which is safe because the writes
are idempotent.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import unquote_plus

import boto3

from crewops.config import Settings
from crewops.ingestion.roster import MAX_UPLOAD_BYTES, IngestResult, Status, ingest

UPLOADS_PREFIX = "uploads/"
RESULTS_PREFIX = "results/"

log = logging.getLogger()
log.setLevel(logging.INFO)


def result_key(upload_key: str) -> str:
    return f"{RESULTS_PREFIX}{upload_key.removeprefix(UPLOADS_PREFIX)}.result.json"


def process(s3: Any, store: Any, bucket: str, key: str) -> IngestResult:
    size = s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
    if size > MAX_UPLOAD_BYTES:
        # HEAD first so we never pull a huge object into Lambda memory
        result = IngestResult(
            key,
            Status.REJECTED,
            errors=(f"upload is {size} bytes; the limit is {MAX_UPLOAD_BYTES}",),
        )
        records: list[Any] = []
    else:
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        result, records = ingest(raw, store.load_seed(), key)

    if result.status is Status.ACCEPTED:
        written = store.put_records(records)
        log.info("accepted %s: wrote %s", key, dict(written))
    else:
        log.warning("rejected %s: %s", key, "; ".join(result.errors))

    s3.put_object(
        Bucket=bucket,
        Key=result_key(key),
        Body=json.dumps(result.to_dict(), indent=2).encode(),
        ContentType="application/json",
    )
    return result


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    from crewops.store.ops_store import OpsStore, table_for

    settings = Settings.from_env()
    s3 = boto3.client("s3", region_name=settings.aws_region)
    store = OpsStore(table_for(settings))

    outcomes = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        if not key.startswith(UPLOADS_PREFIX) or key.endswith(".result.json"):
            log.info("ignored %s", key)
            continue
        result = process(s3, store, bucket, key)
        outcomes.append({"upload": key, "status": str(result.status)})
    return {"processed": outcomes}
