"""Plans: a controller's proposed cover for a disruption, and every decision on it.

A plan sits under its disruption (``DISRUPTION#id / PLAN#id``) and gsi1 indexes it by status
(``PLAN#status``), so an approver's queue is a single query. Each transition is one
transaction that also moves the disruption and writes an audit event. Every write in it is
conditioned on the status it expects, so if two people act at once only one wins.

Committing writes the roster too. Each affected pairing's crew list is replaced only if it is
still exactly the list the plan was made against. Each crew member the plan moves also has a
roster version (``CREW#id / ROSTER_VERSION``). The commit bumps it, conditioned on the value
read before the legality re-check, and storing a report that takes someone out bumps it as
well. Two commits touching the same person, or a commit racing a sick report, therefore
can't both go through; the loser is refused and the plan gets checked again.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from crewops.plans.fingerprint import approval_seal
from crewops.store import schema
from crewops.store.items import from_dynamo, to_dynamo
from crewops.store.reports import STATUS_OPTIONS_READY, disruption_pk
from crewops.store.schema import SEPARATOR as S
from crewops.store.versions import read_versions, version_key  # noqa: F401

STATUS_SUBMITTED = "SUBMITTED"
STATUS_COMMITTED = "COMMITTED"
STATUS_CLOSED = "CLOSED"
"""Closed with no plan, because there was no legal cover. The controller records what was
done instead (flights cancelled, say)."""
PLAN_SUBMITTED = "SUBMITTED"
PLAN_COMMITTED = "COMMITTED"
PLAN_REJECTED = "REJECTED"
PLAN_WITHDRAWN = "WITHDRAWN"
PLAN_STALE = "STALE"


class Conflict(Exception):
    """A condition failed: someone else changed the plan, disruption or roster first."""

    def __init__(self, message: str, codes: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.codes = codes

    @property
    def transient(self) -> bool:
        """Lost a race with another transaction on the same items. Nothing is known to have
        changed, so a retry is safe."""
        return "TransactionConflict" in self.codes and "ConditionalCheckFailed" not in self.codes


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def plan_key(disruption_id: str, plan_id: str) -> dict[str, str]:
    return {schema.PK: disruption_pk(disruption_id), schema.SK: f"PLAN{S}{plan_id}"}


def pairing_key(pairing_id: str) -> dict[str, str]:
    return {schema.PK: f"PAIRING{S}{pairing_id}", schema.SK: "META"}


def plan_item(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        **plan_key(plan["disruption_id"], plan["plan_id"]),
        schema.GSI1_PK: f"PLAN{S}{plan['status']}",
        schema.GSI1_SK: f"AT{S}{plan['submitted_utc']}{S}{plan['plan_id']}",
        "entity": "plan",
        **to_dynamo(plan),
    }


def _audit(disruption_id: str, action: str, actor: str, at: str, **extra: Any) -> dict[str, Any]:
    return {
        "Put": {
            "Item": {
                schema.PK: disruption_pk(disruption_id),
                # The plan id keeps two events of one kind in the same second apart.
                schema.SK: f"AUDIT{S}{at}{S}{action}{S}{extra.get('plan_id', '')}",
                "entity": "audit_event",
                "disruption_id": disruption_id,
                "action": action,
                "actor": actor,
                "at": at,
                **{k: to_dynamo(v) for k, v in extra.items() if v is not None},
            }
        }
    }


def _move_disruption(disruption_id: str, frm: str, to: str, plan_id: str | None) -> dict[str, Any]:
    update = "SET #status = :to, #gsi1pk = :index"
    values: dict[str, Any] = {":to": to, ":from": frm, ":index": f"STATUS{S}{to}"}
    if plan_id:
        update += ", #plan = :plan"
        values[":plan"] = plan_id
    else:
        update += " REMOVE #plan"
    return {
        "Update": {
            "Key": {schema.PK: disruption_pk(disruption_id), schema.SK: "META"},
            "UpdateExpression": update,
            "ConditionExpression": "#status = :from",
            "ExpressionAttributeNames": {
                "#status": "status",
                "#gsi1pk": schema.GSI1_PK,
                "#plan": "plan_id",
            },
            "ExpressionAttributeValues": values,
        }
    }


def _move_plan(
    plan: dict[str, Any], to: str, extra: dict[str, Any], fingerprint: str | None = None
) -> dict[str, Any]:
    names = {"#status": "status", "#gsi1pk": schema.GSI1_PK}
    values: dict[str, Any] = {
        ":to": to,
        ":from": PLAN_SUBMITTED,
        ":index": f"PLAN{S}{to}",
    }
    condition = "#status = :from"
    if fingerprint:
        # approve() already recomputed the fingerprint and compared it with the audit
        # trail's; this makes sure the stored one hasn't changed since.
        names["#fp"] = "fingerprint"
        values[":fp"] = fingerprint
        condition += " AND #fp = :fp"
    sets = ["#status = :to", "#gsi1pk = :index"]
    for i, (key, value) in enumerate(extra.items()):
        names[f"#f{i}"] = key
        values[f":v{i}"] = to_dynamo(value)
        sets.append(f"#f{i} = :v{i}")
    return {
        "Update": {
            "Key": plan_key(plan["disruption_id"], plan["plan_id"]),
            "UpdateExpression": "SET " + ", ".join(sets),
            "ConditionExpression": condition,
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": values,
        }
    }


def _transact(table: Any, actions: list[dict[str, Any]]) -> None:
    items = []
    for action in actions:
        ((kind, body),) = action.items()
        items.append({kind: {"TableName": table.name, **body}})
    try:
        table.meta.client.transact_write_items(TransactItems=items)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
            codes = tuple(
                r.get("Code", "None") for r in exc.response.get("CancellationReasons") or []
            )
            raise Conflict(str(exc), codes) from None
        raise


def submit(table: Any, plan: dict[str, Any], at: datetime) -> None:
    """Store a new plan and move its disruption from OPTIONS_READY to SUBMITTED."""
    when = _iso(at)
    _transact(
        table,
        [
            {
                "Put": {
                    "Item": plan_item(plan),
                    "ConditionExpression": "attribute_not_exists(#pk)",
                    "ExpressionAttributeNames": {"#pk": schema.PK},
                }
            },
            _move_disruption(
                plan["disruption_id"], STATUS_OPTIONS_READY, STATUS_SUBMITTED, plan["plan_id"]
            ),
            _audit(
                plan["disruption_id"],
                "SUBMITTED",
                plan["submitted_by"],
                when,
                plan_id=plan["plan_id"],
                note=plan.get("note") or None,
                fingerprint=plan.get("fingerprint") or None,
            ),
        ],
    )


def close(
    table: Any, plan: dict[str, Any], status: str, actor: str, at: datetime, reason: str
) -> None:
    """End a submitted plan (rejected, withdrawn or stale); its disruption goes back to
    OPTIONS_READY, ready for another plan."""
    when = _iso(at)
    _transact(
        table,
        [
            _move_plan(plan, status, {"decided_by": actor, "decided_utc": when, "reason": reason}),
            _move_disruption(plan["disruption_id"], STATUS_SUBMITTED, STATUS_OPTIONS_READY, None),
            _audit(
                plan["disruption_id"], status, actor, when, plan_id=plan["plan_id"], reason=reason
            ),
        ],
    )


def close_disruption(table: Any, disruption_id: str, actor: str, at: datetime, reason: str) -> None:
    """OPTIONS_READY -> CLOSED with no plan, and why."""
    when = _iso(at)
    _transact(
        table,
        [
            _move_disruption(disruption_id, STATUS_OPTIONS_READY, STATUS_CLOSED, None),
            _audit(disruption_id, STATUS_CLOSED, actor, when, reason=reason),
        ],
    )


def commit(
    table: Any,
    plan: dict[str, Any],
    rosters: dict[str, tuple[list[dict[str, str]], list[dict[str, str]]]],
    versions: dict[str, int],
    actor: str,
    at: datetime,
) -> None:
    """Approve and commit as one transaction. Pairing crews are replaced only if unchanged
    since the plan was made, roster versions bumped only if unchanged since they were read,
    and the plan, disruption and audit events written alongside."""
    when = _iso(at)
    actions: list[dict[str, Any]] = []
    for crew_id, seen in sorted(versions.items()):
        actions.append(
            {
                "Update": {
                    "Key": version_key(crew_id),
                    "UpdateExpression": "SET #v = :next, #entity = :entity",
                    "ConditionExpression": "attribute_not_exists(#v) OR #v = :seen",
                    "ExpressionAttributeNames": {"#v": "version", "#entity": "entity"},
                    "ExpressionAttributeValues": {
                        ":next": seen + 1,
                        ":seen": seen,
                        ":entity": "roster_version",
                    },
                }
            }
        )
    for pairing_id, (before, after) in sorted(rosters.items()):
        actions.append(
            {
                "Update": {
                    "Key": pairing_key(pairing_id),
                    "UpdateExpression": "SET #crew = :after",
                    "ConditionExpression": "#crew = :before",
                    "ExpressionAttributeNames": {"#crew": "crew"},
                    "ExpressionAttributeValues": {
                        ":after": to_dynamo(after),
                        ":before": to_dynamo(before),
                    },
                }
            }
        )
    # four-eyes evidence: seal = hash(plan fingerprint, approver, time), see plans.fingerprint
    seal = approval_seal(plan["fingerprint"], actor, when) if plan.get("fingerprint") else None
    decided = {"decided_by": actor, "decided_utc": when}
    if seal:
        decided["approval_seal"] = seal
    actions += [
        _move_plan(plan, PLAN_COMMITTED, decided, plan.get("fingerprint")),
        _move_disruption(
            plan["disruption_id"], STATUS_SUBMITTED, STATUS_COMMITTED, plan["plan_id"]
        ),
        _audit(
            plan["disruption_id"],
            "APPROVED",
            actor,
            when,
            plan_id=plan["plan_id"],
            fingerprint=plan.get("fingerprint") or None,
            approval_seal=seal,
        ),
        _audit(plan["disruption_id"], "COMMITTED", actor, when, plan_id=plan["plan_id"]),
    ]
    _transact(table, actions)


def get_plan(table: Any, disruption_id: str, plan_id: str) -> dict[str, Any] | None:
    item = table.get_item(Key=plan_key(disruption_id, plan_id), ConsistentRead=True).get("Item")
    return from_dynamo(item) if item else None


def plans_with_status(table: Any, status: str) -> list[dict[str, Any]]:
    """Oldest first, through the status index."""
    out: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "IndexName": schema.GSI1,
        "KeyConditionExpression": Key(schema.GSI1_PK).eq(f"PLAN{S}{status}"),
    }
    while True:
        page = table.query(**kwargs)
        out += [from_dynamo(i) for i in page.get("Items", [])]
        if "LastEvaluatedKey" not in page:
            return out
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def plans_for(table: Any, disruption_id: str) -> list[dict[str, Any]]:
    page = table.query(
        KeyConditionExpression=Key(schema.PK).eq(disruption_pk(disruption_id))
        & Key(schema.SK).begins_with(f"PLAN{S}"),
        ConsistentRead=True,
    )
    return [from_dynamo(i) for i in page.get("Items", [])]


def audit_trail(table: Any, disruption_id: str) -> list[dict[str, Any]]:
    page = table.query(
        KeyConditionExpression=Key(schema.PK).eq(disruption_pk(disruption_id))
        & Key(schema.SK).begins_with(f"AUDIT{S}"),
        ConsistentRead=True,
    )
    return [from_dynamo(i) for i in page.get("Items", [])]
