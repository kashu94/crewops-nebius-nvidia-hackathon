"""Conversation memory on AgentCore Memory (short-term events only).

Each answered question becomes one event, keyed by the signed-in user (actor) and the
conversation id the client sends (session). The event holds the question and answer as
conversational turns plus the ``Turn`` the next question may use. Before answering we read
back the latest event of that conversation.

Memory never decides anything and never costs an answer. If it is unreachable or slow the
question is answered on its own and the failure logged. Operational facts (what is waiting
for whom) always come from the table, not from memory. Events expire with the memory's
retention and no extraction strategy runs over them.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from crewops.agent.context import Turn

log = logging.getLogger(__name__)

CONVERSATION_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")
"""AgentCore's session id rule, which is what a client may send as a conversation id."""
ACTOR_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,254}$")
MAX_EVENTS = 100
"""One page, the most AgentCore returns at once. We sort by time ourselves because the
service doesn't promise an order."""


def valid_conversation(conversation_id: str) -> bool:
    return bool(CONVERSATION_RE.match(conversation_id or ""))


class ConversationMemory:
    def __init__(self, client: Any, memory_id: str) -> None:
        """``client`` is a ``bedrock-agentcore`` client with short timeouts."""
        self.client = client
        self.memory_id = memory_id

    def last_turn(self, actor: str, conversation: str, now: datetime) -> Turn | None:
        """Latest turn if it is recent. None otherwise, or on any error."""
        if not (ACTOR_RE.match(actor) and valid_conversation(conversation)):
            return None
        try:
            events = self.client.list_events(
                memoryId=self.memory_id,
                actorId=actor,
                sessionId=conversation,
                includePayloads=True,
                maxResults=MAX_EVENTS,
            ).get("events", [])
        except Exception:
            log.exception("memory read failed; answering without context")
            return None
        events = sorted(events, key=lambda e: e.get("eventTimestamp") or 0, reverse=True)
        for event in events:
            for part in event.get("payload", []):
                turn = Turn.from_dict(_blob(part.get("blob")))
                if turn is not None:
                    return turn if turn.fresh(now) else None
        return None

    def remember(self, actor: str, conversation: str, turn: Turn, answer_text: str) -> None:
        """Failures are logged, never raised."""
        if not (ACTOR_RE.match(actor) and valid_conversation(conversation)):
            return
        try:
            self.client.create_event(
                memoryId=self.memory_id,
                actorId=actor,
                sessionId=conversation,
                eventTimestamp=datetime.now(UTC),
                payload=[
                    {"conversational": {"content": {"text": turn.question}, "role": "USER"}},
                    {"conversational": {"content": {"text": answer_text}, "role": "ASSISTANT"}},
                    {"blob": json.dumps(turn.to_dict())},
                ],
            )
        except Exception:
            log.exception("memory write failed; the next question will stand on its own")


def _blob(value: Any) -> Any:
    """We write turns as JSON strings but accept an already-decoded object too."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def memory_client(region: str) -> Any:
    """Gives up fast. Memory isn't worth blowing the time budget for."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(
            connect_timeout=1, read_timeout=1, retries={"max_attempts": 1, "mode": "standard"}
        ),
    )
