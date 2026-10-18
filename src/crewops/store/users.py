"""User profiles: the access facts Cognito does not hold (a crew member's own crew id, a
controller's desk), written only by the team command and read on every request."""

from __future__ import annotations

from typing import Any

from crewops.store import schema
from crewops.store.schema import SEPARATOR as S

ENTITY = "user_profile"


def profile_key(username: str) -> dict[str, str]:
    return {schema.PK: f"USER{S}{username.lower()}", schema.SK: "PROFILE"}


def profile_item(
    username: str, name: str, roles: list[str], crew_id: str | None, desk: str | None
) -> dict[str, Any]:
    item: dict[str, Any] = {
        **profile_key(username),
        "entity": ENTITY,
        "username": username.lower(),
        "name": name,
        "roles": sorted(roles),
    }
    if crew_id:
        item["crew_id"] = crew_id
    if desk:
        item["desk"] = desk
    return item


def get_profile(table: Any, username: str) -> dict[str, Any] | None:
    return table.get_item(Key=profile_key(username), ConsistentRead=True).get("Item")


def put_profile(table: Any, item: dict[str, Any]) -> None:
    table.put_item(Item=item)


def delete_profile(table: Any, username: str) -> None:
    table.delete_item(Key=profile_key(username))


def list_profiles(table: Any) -> list[dict[str, Any]]:
    """Filtered scan. Fine at this size: the team is small and only the team command calls
    this."""
    from boto3.dynamodb.conditions import Attr

    out: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"FilterExpression": Attr("entity").eq(ENTITY)}
    while True:
        page = table.scan(**kwargs)
        out += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return out
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
