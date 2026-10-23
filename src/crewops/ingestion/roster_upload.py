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
from collections.abc import Iterable
from typing import Any
from urllib.parse import unquote_plus

import boto3

from crewops.config import Settings
from crewops.domain import GateSlot
from crewops.ingestion.roster import MAX_UPLOAD_BYTES, IngestResult, Status, ingest
from crewops.store import schema
from crewops.store.items import to_item

UPLOADS_PREFIX = "uploads/"
RESULTS_PREFIX = "results/"

log = logging.getLogger()
log.setLevel(logging.INFO)


def result_key(upload_key: str) -> str:
    return f"{RESULTS_PREFIX}{upload_key.removeprefix(UPLOADS_PREFIX)}.result.json"


def _key(record: Any) -> dict[str, str]:
    item = to_item(record)
    return {schema.PK: item[schema.PK], schema.SK: item[schema.SK]}


def _replaced_slots(current: Iterable[GateSlot], records: Iterable[Any]) -> list[GateSlot]:
    """Slots an upload replaces under a different key. There is one slot per flight, but it
    is stored under its gate and start time, so moving a flight to another gate or time
    would otherwise leave the old slot behind and the flight would hold two gates."""
    uploaded = [r for r in records if isinstance(r, GateSlot)]
    flights = {r.flight_id for r in uploaded}
    keep = {tuple(_key(r).values()) for r in uploaded}
    return [g for g in current if g.flight_id in flights and tuple(_key(g).values()) not in keep]


def _delete(store: Any, slots: Iterable[GateSlot]) -> None:
    with store.table.batch_writer() as batch:
        for g in slots:
            batch.delete_item(Key=_key(g))


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
        stale: list[GateSlot] = []
    else:
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        base = store.load_seed()
        result, records = ingest(raw, base, key)
        stale = _replaced_slots(base.gates, records)

    if result.status is Status.ACCEPTED:
        written = store.put_records(records)
        _delete(store, stale)
        log.info("accepted %s: wrote %s, moved %d gate slot(s)", key, dict(written), len(stale))
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
