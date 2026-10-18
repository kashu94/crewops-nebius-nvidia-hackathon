"""Greetings, thanks and "who am I": answered from the sign-in, before any routing.

Not crew-control questions, so no tool runs. The only roster fact stated is the caller's
own identity, taken from their verified sign-in (and their crew record, for crew). Without
this, "hello" becomes a lookup with nothing to look up, and "what is my name" can match a
certificates question by similarity.
"""

from __future__ import annotations

import re

from crewops.domain.desks import DESKS
from crewops.engine import World
from crewops.tools import Caller

_FILLER = re.compile(r"\b(alexa|crewops|crew ops|please|there|so much|a lot|again)\b", re.I)
_PUNCT = re.compile(r"[^\w\s']")

GREETING_RE = re.compile(
    r"^((hi+|hello|hey|hiya|namaste|good (morning|afternoon|evening|day))\s*)+$", re.I
)
HOW_RE = re.compile(
    r"^((hi+|hello|hey)\s+)?(how are you( doing)?( today)?|how's it going|what's up)$", re.I
)
THANKS_RE = re.compile(
    r"^((ok(ay)?|great|perfect|cool|nice|awesome|got it)?\s*"
    r"(thanks|thank you|thank u|thx|cheers|much appreciated)( very much)?)$|^(ok(ay)?|great|"
    r"perfect|cool|got it|noted|fine|alright|sounds good)$",
    re.I,
)
BYE_RE = re.compile(r"^(bye|goodbye|good bye|see you|see ya|bye bye|good night|that's all)$", re.I)
IDENTITY_RE = re.compile(
    r"\b(who am i|what(?:'s| is) my (name|role|desk|crew id|id|rank|base|job)|"
    r"(which|what) desk (am i|do i)|(am i|i am) (a |an )?(controller|approver|reporter|"
    r"auditor|admin|crew)|my (name|role|desk) (please|\?))\b",
    re.I,
)

HELP = (
    "Ask me about rosters and reserves, duty hours and legality, cover for a sick call or a "
    "delay, a disruption's impact, gates, or a rule."
)


def _bare(text: str) -> str:
    """Drop fillers and punctuation so the phrase regexes can match the whole thing."""
    out = _PUNCT.sub(" ", _FILLER.sub(" ", text))
    return re.sub(r"\s+", " ", out).strip()


def _name(caller: Caller, world: World) -> str | None:
    if caller.crew_id and (crew := world.crew.get(caller.crew_id)) and crew.name:
        return crew.name
    desk = next((d for d in DESKS if d.desk_id == caller.desk), None)
    return desk.controller.split()[0] if desk else None


def who(caller: Caller, world: World) -> str:
    roles = ", ".join(sorted(r.value.replace("_", " ") for r in caller.roles))
    if caller.crew_id:
        crew = world.crew.get(caller.crew_id)
        if crew:
            return (
                f"You're {crew.name} ({crew.crew_id}), {crew.rank.value} based at {crew.base}, "
                f"signed in as {caller.user_id}."
            )
        return f"You're signed in as {caller.user_id}, crew {caller.crew_id}."
    desk = next((d for d in DESKS if d.desk_id == caller.desk), None)
    if desk:
        return (
            f"You're {desk.controller}, signed in as {caller.user_id}: {roles} on {desk.desk_id}."
        )
    return f"You're signed in as {caller.user_id}, with the {roles} role."


def small_talk(question: str, caller: Caller, world: World) -> str | None:
    """Reply to a greeting, thanks, goodbye or "who am I". None for anything else."""
    bare = _bare(question)
    if IDENTITY_RE.search(question):
        return who(caller, world)
    if not bare or len(bare) > 40:
        return None
    name = _name(caller, world)
    hello = f"Hello {name}." if name else "Hello."
    if GREETING_RE.match(bare):
        return f"{hello} {HELP}"
    if HOW_RE.match(bare):
        return f"All good, thanks{', ' + name if name else ''}. {HELP}"
    if THANKS_RE.match(bare):
        return "You're welcome. Anything else?"
    if BYE_RE.match(bare):
        return "Goodbye." if not name else f"Goodbye, {name}."
    return None


__all__ = ["HELP", "small_talk", "who"]
