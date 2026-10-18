"""What the previous turn of a conversation lends the next one.

Two things carry over, both deterministic and both noted on the answer.

A reply to a question back. When the last answer asked for a missing detail ("Which seat
needs cover on P-2291?") and the reply is only that detail, a few words giving a seat, a
date or one id ("Captain", "13 Oct") and asking nothing of its own, it is read together
with the question it answers. Anything else is a new question.

He or she. A question that refers to one person without naming one, right after a turn
about exactly one crew member, is about that crew member. Not when it speaks of the asker
("I", "my"), of several people ("reserves", "crew") or of a plan's decision ("has she
approved it?"), and not when it names a role beside the pronoun ("her captain"), since the
pronoun may then mean someone else. "They" is never carried because it is as often plural
as singular. "That pairing" and "that flight" work the same way.

Nothing else is remembered or inferred. Carried ids go through the same role checks as
typed ones, and a turn older than ``FRESH_SECONDS`` lends nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

FRESH_SECONDS = 15 * 60
MAX_REPLY_WORDS = 4
MAX_REMEMBERED = 1000
"""Questions longer than the pipeline accepts are never remembered."""

PERSON_RE = re.compile(r"\b(he|she|him|her|his|hers)\b", re.I)
NOT_THE_LAST_PERSON_RE = re.compile(
    r"\b(I|me|my|mine|crews?|reserves|captains|officers|everyone|anyone|all|list|"
    r"plans?|approv\w*|submit\w*|reject\w*|withdr[ae]w\w*)\b",
    re.I,
)
"""Questions where "he" or "she" may well be someone other than the last crew member."""
POSSESSIVE_ROLE_RE = re.compile(
    r"\b(his|her)\s+(captain|first officer|co-?pilot|purser|senior cabin crew|cabin crew|"
    r"crew|colleague|replacement|cover|reserve)\b",
    re.I,
)
""""Her captain" is the person named by the role, not the one asked about."""
PAIRING_RE = re.compile(r"\b(that|this|the same) (pairing|trip)\b", re.I)
FLIGHT_RE = re.compile(r"\b(that|this|the same) flight\b", re.I)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class Turn:
    """One answered question, as much as the next question may use."""

    question: str
    at: str
    awaiting: str | None = None
    crew_ids: tuple[str, ...] = ()
    pairing_ids: tuple[str, ...] = ()
    flight_ids: tuple[str, ...] = ()

    @classmethod
    def of(cls, answer: Any, at: datetime) -> Turn:
        """Question, whether it asked something back, and the ids its route used."""
        ents = answer.route.entities if answer.route else None
        return cls(
            question=answer.question[:MAX_REMEMBERED],
            at=_iso(at),
            awaiting=answer.awaiting,
            crew_ids=tuple(ents.crew_ids) if ents else (),
            pairing_ids=tuple(ents.pairing_ids) if ents else (),
            flight_ids=tuple(ents.flight_ids) if ents else (),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "at": self.at,
            "awaiting": self.awaiting,
            "crew_ids": list(self.crew_ids),
            "pairing_ids": list(self.pairing_ids),
            "flight_ids": list(self.flight_ids),
        }

    @classmethod
    def from_dict(cls, data: Any) -> Turn | None:
        """None for anything that doesn't look like a stored turn. Stored data isn't trusted."""
        if not isinstance(data, dict):
            return None
        question, at = data.get("question"), data.get("at")
        if not isinstance(question, str) or not isinstance(at, str):
            return None

        def ids(key: str) -> tuple[str, ...]:
            value = data.get(key) or []
            return tuple(v for v in value if isinstance(v, str)) if isinstance(value, list) else ()

        awaiting = data.get("awaiting")
        return cls(
            question=question[:MAX_REMEMBERED],
            at=at,
            awaiting=awaiting if isinstance(awaiting, str) else None,
            crew_ids=ids("crew_ids"),
            pairing_ids=ids("pairing_ids"),
            flight_ids=ids("flight_ids"),
        )

    def fresh(self, now: datetime) -> bool:
        try:
            at = datetime.strptime(self.at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            return False
        return 0 <= (now - at).total_seconds() <= FRESH_SECONDS


def as_reply(
    question: str, previous: Turn | None, entities: Any, asks_something: bool
) -> str | None:
    """The question to route when ``question`` answers the previous turn's question back,
    or None when it stands on its own.

    ``entities`` is what the reply names by itself. ``asks_something`` says whether it
    matches a question rule by itself ("duty hours C-3310" does, "Captain" does not).
    """
    if previous is None or previous.awaiting != "detail" or asks_something:
        return None
    if len(question.split()) > MAX_REPLY_WORDS:
        return None
    ids = (
        len(entities.crew_ids)
        + len(entities.pairing_ids)
        + len(entities.flight_ids)
        + len(entities.flight_nos)
    )
    if not (entities.roles or entities.dates or ids == 1):
        return None
    return f"{previous.question} {question}"


def carry_ids(question: str, entities: Any, previous: Turn | None) -> list[str]:
    """Add the ids that "he", "that pairing" or "that flight" refer to. One note per id."""
    if previous is None:
        return []
    notes = []
    if (
        len(previous.crew_ids) == 1
        and not entities.crew_ids
        and not entities.names
        and PERSON_RE.search(question)
        and not NOT_THE_LAST_PERSON_RE.search(question)
        and not POSSESSIVE_ROLE_RE.search(question)
    ):
        entities.crew_ids.append(previous.crew_ids[0])
        notes.append(f"read as {previous.crew_ids[0]}, from your previous question")
    if len(previous.pairing_ids) == 1 and not entities.pairing_ids and PAIRING_RE.search(question):
        entities.pairing_ids.append(previous.pairing_ids[0])
        notes.append(f"'that pairing' read as {previous.pairing_ids[0]}")
    if len(previous.flight_ids) == 1 and not entities.flight_ids and FLIGHT_RE.search(question):
        entities.flight_ids.append(previous.flight_ids[0])
        notes.append(f"'that flight' read as {previous.flight_ids[0]}")
    return notes
