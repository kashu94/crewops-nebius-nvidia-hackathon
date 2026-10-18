"""A plan's fingerprint: SHA-256 over exactly what was submitted, for four-eyes evidence.

On submit we hash everything that defines the plan (SIGNED_FIELDS): the disruption, who
replaces whom on which trip, costs, what's left uncovered, the rosters it was made against,
and who submitted it when. The approver sees the same hash on the approval card. Approval
recomputes it from the stored plan and refuses on a mismatch, so if the stored record was
edited in between, it can't be approved. The approval gets its own seal, SHA-256 over the
plan fingerprint, approver and time, stored with the decision and in the audit trail.

This is tamper evidence, not a signature. Nobody holds a private key, and anyone with the
plan can recompute both hashes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

SIGNED_FIELDS = (
    "plan_id",
    "disruption_id",
    "desk",
    "submitted_by",
    "submitted_utc",
    "note",
    "assignments",
    "uncovered",
    "total_cost_inr",
    "rosters_before",
)


def _canon(value: Any) -> Any:
    """Normalise so the hash doesn't depend on storage. A float written to DynamoDB comes
    back as a Decimal, so numbers become their shortest decimal text; dict keys are sorted."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float | Decimal):
        number = Decimal(str(value)).normalize()
        return format(number, "f") if number == number.to_integral() else str(number)
    if isinstance(value, Mapping):
        return {str(k): _canon(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list | tuple):
        return [_canon(v) for v in value]
    return str(value)


def fingerprint(plan: Mapping[str, Any]) -> str:
    body = {k: _canon(plan.get(k)) for k in SIGNED_FIELDS}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def approval_seal(plan_fingerprint: str, approver: str, at: str) -> str:
    return hashlib.sha256(f"{plan_fingerprint}|{approver}|{at}".encode()).hexdigest()
