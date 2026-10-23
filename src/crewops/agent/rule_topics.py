"""General questions about a rule, asked in everyday words: "what's the FDP limit", "how much
rest does crew need", "what are the rules". Mapped to the rule id, so the usual explain_rule
call answers with the rule's current text.

Without this, "what is the flight duty period limit" read like a crew lookup with nothing to
look up, and "maximum flight hours" like a duty-clock question missing its crew id. A
question naming a crew member, trip or flight, or pointing at one ("my", "she"), is about
them, not the rule, and is left alone.
"""

from __future__ import annotations

import re

ALL = "ALL"

SPECIFIC_RE = re.compile(r"\b([cp]\s*-?\s*\d{3,6}|(dx|ks|ng)\s*\d{3,4})\b", re.I)
POINTS_AT_RE = re.compile(r"\b(my|i|me|he|she|him|his|her|they|them|their)\b", re.I)
GENERAL_RE = re.compile(
    r"\b(rule|rules|limit|limits|max(imum)?|min(imum)?|how (much|many|long)|allowed|"
    r"need|needs|required|regulation|cap)\b",
    re.I,
)
ALL_RE = re.compile(
    r"^(what|which|list|tell me|show)( me)?( are| is)?( all)? (the |your )?"
    r"(rules|legality rules|duty rules|regulations)( (do|does) (you|crewops) (check|use|apply))?$|"
    r"^(what|which) rules (do|does) (you|crewops|cover) (check|use|apply|follow)$",
    re.I,
)
TOPICS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("RULE-FDP-01", re.compile(r"\b(flight duty period|fdp|duty period)\b", re.I)),
    ("RULE-REST-04", re.compile(r"\b(rest|sleep|turnaround between duties)\b", re.I)),
    (
        "RULE-FLT-03",
        re.compile(r"\b(flight|block|flying) hours?\b|\b28 days\b|\bin a month\b", re.I),
    ),
    ("RULE-DUTY-02", re.compile(r"\bduty hours?\b|\b7 days\b|\bin a week\b|\bweekly\b", re.I)),
    ("RULE-QUAL-05", re.compile(r"\b(type rating|rating|qualified|qualification)s?\b", re.I)),
    ("RULE-CERT-06", re.compile(r"\b(certificate|certification|licen[cs]e|medical)s?\b", re.I)),
    (
        "RULE-BASE-07",
        re.compile(r"\b(other base|another base|own base|deadhead|positioning)\b", re.I),
    ),
)


def rule_topic(question: str) -> str | None:
    """The rule a general question asks about, ``ALL`` for "what are the rules", else None."""
    text = re.sub(r"[^\w\s'-]", " ", question).strip()
    text = re.sub(r"\b(alexa|crewops|please)\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    if ALL_RE.match(text):
        return ALL
    if SPECIFIC_RE.search(text) or POINTS_AT_RE.search(text):
        return None
    if not GENERAL_RE.search(text):
        return None
    return next((rule_id for rule_id, pattern in TOPICS if pattern.search(text)), None)


__all__ = ["ALL", "rule_topic"]
