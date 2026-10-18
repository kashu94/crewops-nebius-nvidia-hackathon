"""Browsers subscribed to desktop alerts (web push).

    PUSH / <endpoint hash>     username, endpoint, keys

One per browser, owned by whoever turned desktop alerts on in it last: a shared desk
computer gets the alerts of the person signed in now, never also those of the one before.
The notifier reads them all and forgets one the push service says is gone.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from crewops.store import schema
from crewops.store.items import from_dynamo

PK = "PUSH"
MAX_PER_PERSON = 5


def _sk(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()[:32]


def subscriptions(table: Any, username: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key(schema.PK).eq(PK)}
    while True:
        page = table.query(**kwargs)
        out += [from_dynamo(i) for i in page.get("Items", [])]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return [s for s in out if username is None or s.get("username") == username]


def save(table: Any, username: str, endpoint: str, p256dh: str, auth: str, at: datetime) -> None:
    """This browser's alerts go to ``username`` from now on (it takes it over if needed)."""
    table.put_item(
        Item={
            schema.PK: PK,
            schema.SK: _sk(endpoint),
            "entity": "push_subscription",
            "username": username,
            "endpoint": endpoint,
            "p256dh": p256dh,
            "auth": auth,
            "created_utc": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )


def forget(table: Any, username: str, endpoint: str) -> None:
    """Drop the browser if it is still ``username``'s; someone else's is left alone."""
    try:
        table.delete_item(
            Key={schema.PK: PK, schema.SK: _sk(endpoint)},
            ConditionExpression=Attr("username").eq(username),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
