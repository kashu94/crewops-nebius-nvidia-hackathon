"""Lambda handler: new disruptions arriving on the DynamoDB stream.

The event source mapping filters the stream so only newly inserted disruption items reach
this function; the check is repeated here so the handler is safe on its own. Each record's
new image is analysed and the result stored with ``store_analysis``, which moves the
disruption from REPORTED to OPTIONS_READY exactly once.

A record whose stored data cannot be analysed (a malformed report, say) is recorded as an
ANALYSIS_FAILED audit event and skipped: retrying could not help. Only unexpected failures,
such as DynamoDB being unavailable, are returned as batch item failures; Lambda retries
from that record, and after the mapping's retry limit it is noted in the analysis
dead-letter queue so one bad record never blocks the stream. Either way the disruption
stays REPORTED, and ``python -m crewops.analysis.run --pending`` analyses it again.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from boto3.dynamodb.types import TypeDeserializer

from crewops.analysis.runner import Operation, analyse_item
from crewops.domain import DataError
from crewops.store.reports import record_analysis_failure

PERMANENT_ERRORS = (DataError, KeyError, ValueError, TypeError)
"""Errors from the stored data itself, not from AWS: retrying cannot help."""

log = logging.getLogger()
log.setLevel(logging.INFO)

_deserialize = TypeDeserializer().deserialize


def new_disruption(record: dict[str, Any]) -> dict[str, Any] | None:
    """The new image of an inserted disruption item, as plain Python values, or None."""
    if record.get("eventName") != "INSERT":
        return None
    image = record.get("dynamodb", {}).get("NewImage") or {}
    if image.get("entity", {}).get("S") != "disruption":
        return None
    return {key: _deserialize(value) for key, value in image.items()}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    from crewops.config import Settings
    from crewops.store.ops_store import table_for

    records = event.get("Records", [])
    items = [(r, new_disruption(r)) for r in records]
    if not any(item for _, item in items):
        return {"batchItemFailures": []}

    table = table_for(Settings.from_env())
    operation = Operation.load(table)
    for record, item in items:
        if item is None:
            continue
        try:
            result = analyse_item(table, item, operation)
            log.info("disruption %s %s", result.disruption_id, result.outcome)
        except PERMANENT_ERRORS as exc:
            # Would fail the same way on every retry: record it and move on. The disruption
            # stays REPORTED and can be analysed again with ``--pending`` once fixed.
            disruption_id = str(item.get("disruption_id"))
            log.exception("cannot analyse %s; recorded and skipped", disruption_id)
            record_analysis_failure(
                table, disruption_id, f"{type(exc).__name__}: {exc}", datetime.now(UTC)
            )
        except Exception:
            sequence = record["dynamodb"]["SequenceNumber"]
            log.exception("could not analyse record %s; it will be retried", sequence)
            # Report this record and stop: Lambda retries from the first failure onward,
            # so later records in the batch are retried too, in order.
            return {"batchItemFailures": [{"itemIdentifier": sequence}]}
    return {"batchItemFailures": []}
