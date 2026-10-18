"""Alexa accounts linked to CrewOps users, and whether each allowed notifications.

    ALEXA / USER#<Alexa user id>

Written by the skill: when a linked account makes a request (who it is, their roles and
desk, and the Alexa API endpoint for that account's region) and when the person allows or
withdraws notifications. Read by the notifier to know whose Echo to make chime.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from boto3.dynamodb.conditions import Key

from crewops.store import schema
from crewops.store.items import from_dynamo
from crewops.store.schema import SEPARATOR as S

PK = "ALEXA"


def _key(alexa_user_id: str) -> dict[str, str]:
    return {schema.PK: PK, schema.SK: f"USER{S}{alexa_user_id}"}


def _iso(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def save_link(
    table: Any,
    alexa_user_id: str,
    api_endpoint: str,
    username: str,
    roles: list[str],
    desk: str | None,
    at: datetime,
    locale: str | None = None,
) -> None:
    """Who this Alexa account is in CrewOps now, and the language its Echo speaks; keeps
    whether it allowed notifications."""
    table.update_item(
        Key=_key(alexa_user_id),
        UpdateExpression=(
            "SET #e = :entity, alexa_user_id = :id, api_endpoint = :endpoint, "
            "username = :user, #roles = :roles, desk = :desk, #locale = :locale, "
            "updated_utc = :at"
        ),
        ExpressionAttributeNames={"#e": "entity", "#roles": "roles", "#locale": "locale"},
        ExpressionAttributeValues={
            ":entity": "alexa_link",
            ":id": alexa_user_id,
            ":endpoint": api_endpoint,
            ":user": username,
            ":roles": sorted(roles),
            ":desk": desk,
            ":locale": locale or "en-IN",
            ":at": _iso(at),
        },
    )


def set_notifications(
    table: Any, alexa_user_id: str, api_endpoint: str, allowed: bool, at: datetime
) -> None:
    table.update_item(
        Key=_key(alexa_user_id),
        UpdateExpression=(
            "SET #e = :entity, alexa_user_id = :id, api_endpoint = :endpoint, "
            "notifications = :allowed, updated_utc = :at"
        ),
        ExpressionAttributeNames={"#e": "entity"},
        ExpressionAttributeValues={
            ":entity": "alexa_link",
            ":id": alexa_user_id,
            ":endpoint": api_endpoint,
            ":allowed": allowed,
            ":at": _iso(at),
        },
    )


def forget(table: Any, alexa_user_id: str) -> None:
    """The skill was disabled: no more notifications to this account."""
    table.delete_item(Key=_key(alexa_user_id))


def links(table: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key(schema.PK).eq(PK)}
    while True:
        page = table.query(**kwargs)
        out += [from_dynamo(i) for i in page.get("Items", [])]
        if "LastEvaluatedKey" not in page:
            return out
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
