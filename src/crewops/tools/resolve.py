"""Is this id real, and if not, what is near it? Suggest, never substitute.

``C-1042`` and ``C-1024`` differ by one transposed digit. In this roster one is a captain
and the other is nobody, and acting on a "corrected" id would send a different person to an
aircraft. So a near match only ever comes back as a question for the user.

Two kinds of nearness are offered:

* a rearrangement of the same digits (a transposition): a likely typo, so the user is asked
  to confirm (``NEEDS_CONFIRMATION``);
* anything else close: listed as the nearest real ids, without implying a typo.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from crewops.tools.errors import NEEDS_CONFIRMATION, UNRESOLVED_ENTITY, ToolError

MAX_SUGGESTIONS = 4
LIKELY = "likely"
POSSIBLE = "possible"


@dataclass(frozen=True, slots=True)
class Suggestion:
    value: str
    label: str
    confidence: str

    def __str__(self) -> str:
        return f"{self.value} ({self.label})" if self.label else self.value


@dataclass(frozen=True, slots=True)
class Resolution:
    kind: str
    query: str
    exists: bool
    suggestions: tuple[Suggestion, ...] = field(default_factory=tuple)
    universe_size: int = 0
    example: str = ""

    @property
    def likely(self) -> list[Suggestion]:
        return [s for s in self.suggestions if s.confidence == LIKELY]

    def message(self) -> str:
        if self.exists:
            return f"{self.query} exists"
        if likely := self.likely:
            text = f"There is no {self.kind} {self.query}. Did you mean {likely[0]}?"
            if likely[1:]:
                text += " Or " + ", ".join(str(s) for s in likely[1:]) + "?"
            return text + " Confirm which; I will not guess."
        if self.suggestions:
            nearest = ", ".join(str(s) for s in self.suggestions)
            return (
                f"There is no {self.kind} {self.query}. Nearest existing: {nearest}. "
                "If you meant one of those, say so explicitly."
            )
        plural = "" if self.universe_size == 1 else "s"
        example = f", for example {self.example}" if self.example else ""
        return (
            f"There is no {self.kind} {self.query}, and nothing close to it. "
            f"There are {self.universe_size} {self.kind}{plural}{example}."
        )

    def error(self) -> ToolError:
        code = NEEDS_CONFIRMATION if self.likely else UNRESOLVED_ENTITY
        return ToolError(code, self.message())


def transpositions(value: str) -> set[str]:
    """Every string reachable by swapping two digits of the number in ``value``."""
    match = re.search(r"\d+", value)
    if not match:
        return set()
    head, digits, tail = value[: match.start()], match.group(), value[match.end() :]
    out = set()
    for i in range(len(digits)):
        for j in range(i + 1, len(digits)):
            if digits[i] != digits[j]:
                swapped = list(digits)
                swapped[i], swapped[j] = swapped[j], swapped[i]
                out.add(head + "".join(swapped) + tail)
    return out


def digit_prefix_matches(query: str, ids: list[str], limit: int) -> list[str]:
    """Real ids sharing the longest run of leading digits with ``query``.

    For an id of the wrong length ("C-10" against four-digit ids), where whole-string
    similarity punishes the length itself and finds nothing useful.
    """
    digits = "".join(ch for ch in query if ch.isdigit())
    if not digits:
        return []

    def shared(candidate: str) -> int:
        n = 0
        for a, b in zip(digits, (ch for ch in candidate if ch.isdigit()), strict=False):
            if a != b:
                break
            n += 1
        return n

    ranked = sorted((c for c in ids if shared(c) > 0), key=lambda c: (-shared(c), c))
    return ranked[:limit]


def resolve(
    kind: str,
    query: str,
    universe: Mapping[str, Any],
    label: Callable[[Any], str] | None = None,
) -> Resolution:
    """Whether ``query`` names a member of ``universe`` (id -> record), and what is near."""
    ids = sorted(universe)
    if query in universe:
        return Resolution(kind, query, True, universe_size=len(ids))
    render = label or (lambda _: "")
    seen: set[str] = set()
    found: list[Suggestion] = []

    def offer(candidate: str, confidence: str) -> None:
        if candidate in universe and candidate not in seen:
            seen.add(candidate)
            found.append(Suggestion(candidate, render(universe[candidate]), confidence))

    for candidate in sorted(transpositions(query)):
        offer(candidate, LIKELY)
    if not any(len(query) == len(i) for i in ids):
        for candidate in digit_prefix_matches(query, ids, MAX_SUGGESTIONS):
            offer(candidate, POSSIBLE)
    for candidate in difflib.get_close_matches(query, ids, n=MAX_SUGGESTIONS, cutoff=0.8):
        offer(candidate, POSSIBLE)
    return Resolution(
        kind,
        query,
        False,
        tuple(found[:MAX_SUGGESTIONS]),
        universe_size=len(ids),
        example=ids[0] if ids else "",
    )
