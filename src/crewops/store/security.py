"""Security events: questions refused as attempts to override the assistant's rules.

    SECURITY / AUDIT#<at>#BLOCKED_PROMPT#<id>

Written as audit events, so the admin's activity log lists them with everything else: who,
when, what caught it (the override check or the Bedrock Guardrail) and the question, cut
short and with personal data already removed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from crewops.store import schema
from crewops.store.schema import SEPARATOR as S

PK = "SECURITY"
ACTION = "BLOCKED_PROMPT"
MAX_EXCERPT = 160


def log_blocked(table: Any, user_id: str, caught_by: str, question: str, at: datetime) -> None:
    when = at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    excerpt = " ".join(question.split())[:MAX_EXCERPT]
    table.put_item(
        Item={
            schema.PK: PK,
            schema.SK: f"AUDIT{S}{when}{S}{ACTION}{S}{uuid.uuid4().hex[:8]}",
            "entity": "audit_event",
            "action": ACTION,
            "actor": user_id,
            "at": when,
            "reason": f'{caught_by.removeprefix("blocked: ")}: "{excerpt}"',
        }
    )


def log_password_reset(table: Any, actor: str, username: str, at: datetime) -> None:
    """An admin reset someone's password: who, whose and when. Never the password."""
    when = at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    table.put_item(
        Item={
            schema.PK: "ACCOUNTS",
            schema.SK: f"AUDIT{S}{when}{S}PASSWORD_RESET{S}{uuid.uuid4().hex[:8]}",
            "entity": "audit_event",
            "action": "PASSWORD_RESET",
            "actor": actor,
            "at": when,
            "reason": f"password reset for {username}",
        }
    )
