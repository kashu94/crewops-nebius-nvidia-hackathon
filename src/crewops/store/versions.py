"""Roster versions: one small item per crew member, moved on by anything that changes whether
they can fly. A commit conditions on the version it read, so two changes to the same person
at the same moment cannot both succeed."""

from __future__ import annotations

from typing import Any

from crewops.store import schema
from crewops.store.schema import SEPARATOR as S


def version_key(crew_id: str) -> dict[str, str]:
    return {schema.PK: f"CREW{S}{crew_id}", schema.SK: "ROSTER_VERSION"}


def bump_version(crew_id: str) -> dict[str, Any]:
    """Transaction action that bumps the version with no condition. Used when something
    takes a crew member out (a sick report), so any commit that read the old version fails."""
    return {
        "Update": {
            "Key": version_key(crew_id),
            "UpdateExpression": "SET #entity = :entity ADD #v :one",
            "ExpressionAttributeNames": {"#v": "version", "#entity": "entity"},
            "ExpressionAttributeValues": {":one": 1, ":entity": "roster_version"},
        }
    }


def read_versions(table: Any, crew_ids: set[str]) -> dict[str, int]:
    """Each crew member's roster version now (0 if never changed). Read before the
    legality re-check, so a change after it is caught by the commit's condition."""
    out = {}
    for crew_id in sorted(crew_ids):
        item = table.get_item(Key=version_key(crew_id), ConsistentRead=True).get("Item") or {}
        out[crew_id] = int(item.get("version", 0))
    return out
