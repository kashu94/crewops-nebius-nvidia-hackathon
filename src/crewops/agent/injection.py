"""A fast first check for attempts to override the assistant's rules.

The real defences don't depend on this. The assistant only has read-only tools filtered by
the caller's role, so no wording can make it submit, approve or change anything, and the
Bedrock Guardrail screens every question for prompt attacks. This runs before both. It
catches the usual override phrasings without a model call, and keeps working while the
model service is paused (``crewops.agent.breaker``) and the guardrail can't be reached.
Every refusal goes in the admin's activity log.

It's a pattern list, so new wording gets round it. Treat it as a first check only.
"""

from __future__ import annotations

import re

# Anchors an order to the start of a sentence (or after "please", "now"...), so a crew
# control question that happens to use the same verbs doesn't match.
_ORDER = r"(?:^|[.!?;]\s+|\b(?:please|now|then|and)\s+)"

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(rx, re.I))
    for name, rx in (
        (
            "ignore instructions",
            # "ignore your/previous instructions", but not "can we ignore the rest rule?"
            r"\b(ignore|disregard|forget|override)\s+(all\s+)?(of\s+)?(your|previous|prior"
            r"|above|earlier)\s+(\w+\s+)?(instructions?|rules|prompts?|guidelines"
            r"|restrictions|guardrails?)\b",
        ),
        ("reveal prompt", r"\b(reveal|show|print|repeat|leak)\b[^.?!]{0,30}\bsystem prompt\b"),
        ("system prompt", r"\b(developer mode|jailbreak|DAN mode)\b"),
        (
            "role change",
            r"\b(you are now|from now on,? you are|pretend (to be|you are))\s+(an?\s+|the\s+)?"
            r"(\w+\s+)?(admin(istrator)?|super ?admin|root|unrestricted|developer)\b"
            + r"|"
            + _ORDER
            + r"act as\s+(an?\s+|the\s+)?(admin(istrator)?|super ?admin|root|unrestricted)\b",
        ),
        (
            "bypass checks",
            _ORDER
            + r"(bypass|turn off|disable|skip)\b[^.?!]{0,30}\b(rbac|role checks?|permissions?"
            r"|four[- ]eyes|guardrails?|security)\b",
        ),
        (
            "unauthorised action",
            _ORDER
            + r"(approve|authori[sz]e)\b[^.?!]{0,40}\bwithout\b[^.?!]{0,20}\b(approval|approver"
            r"|review|four[- ]eyes)\b",
        ),
        (
            "history tampering",
            _ORDER + r"(clear|delete|erase|wipe)\b[^.?!]{0,20}\b(duty|audit) (history|log|trail)\b",
        ),
    )
)

REFUSAL = (
    "Security alert: that reads as an attempt to override CrewOps' rules, so it was refused and "
    "logged for the administrator. Ask about crew, flights, rules or disruptions instead."
)


def detect(text: str) -> str | None:
    for name, pattern in PATTERNS:
        if pattern.search(text):
            return name
    return None
