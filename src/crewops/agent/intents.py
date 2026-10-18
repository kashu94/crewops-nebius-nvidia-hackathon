"""What kind of question was asked, and how deep the answer has to go.

The tier is derived from the intent, so the two can't disagree. Lookup is a fact from the
roster ("who is on reserve at BLR?"), replacement is legality or cover for one disruption
("who covers P-2291?"), and consequence covers plans, what-ifs and several disruptions at
once.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class Tier(IntEnum):
    LOOKUP = 1
    REPLACEMENT = 2
    CONSEQUENCE = 3


class Intent(StrEnum):
    LOOKUP_ROSTER = "LOOKUP_ROSTER"
    LOOKUP_RESERVE = "LOOKUP_RESERVE"
    LOOKUP_DUTY_CLOCK = "LOOKUP_DUTY_CLOCK"
    LOOKUP_CERT = "LOOKUP_CERT"
    LOOKUP_RISK = "LOOKUP_RISK"
    LOOKUP_FLIGHT = "LOOKUP_FLIGHT"
    LOOKUP_CREW = "LOOKUP_CREW"
    EXPLAIN_RULE = "EXPLAIN_RULE"
    CHECK_GATE = "CHECK_GATE"
    LOOKUP_CONTROLLERS = "LOOKUP_CONTROLLERS"
    FIND_REPLACEMENT = "FIND_REPLACEMENT"
    CHECK_LEGALITY = "CHECK_LEGALITY"
    IMPACT_OF_EVENT = "IMPACT_OF_EVENT"
    RANK_OPTIONS = "RANK_OPTIONS"
    SIMULATE_WHATIF = "SIMULATE_WHATIF"
    JOINT_PLAN = "JOINT_PLAN"
    RESOLVE_ILLEGAL = "RESOLVE_ILLEGAL"
    DRAFT_NOTIFICATION = "DRAFT_NOTIFICATION"

    @property
    def tier(self) -> Tier:
        return _TIER[self]


_LOOKUPS = {
    Intent.LOOKUP_ROSTER,
    Intent.LOOKUP_RESERVE,
    Intent.LOOKUP_DUTY_CLOCK,
    Intent.LOOKUP_CERT,
    Intent.LOOKUP_RISK,
    Intent.LOOKUP_FLIGHT,
    Intent.LOOKUP_CREW,
    Intent.EXPLAIN_RULE,
    Intent.CHECK_GATE,
    Intent.LOOKUP_CONTROLLERS,
}
_REPLACEMENTS = {Intent.FIND_REPLACEMENT, Intent.CHECK_LEGALITY, Intent.IMPACT_OF_EVENT}
_TIER = {
    i: Tier.LOOKUP
    if i in _LOOKUPS
    else Tier.REPLACEMENT
    if i in _REPLACEMENTS
    else Tier.CONSEQUENCE
    for i in Intent
}


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
