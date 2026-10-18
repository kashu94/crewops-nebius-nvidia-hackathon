"""Deterministic legality engine: duty arithmetic, the seven rules, assessment and ranking.

No model call happens here. Every number an agent repeats comes from this package.
"""

from crewops.engine.legality import Assessment, assess
from crewops.engine.options import (
    AlreadyStarted,
    Option,
    OptionsResult,
    Strategy,
    TripCancelled,
    find_options,
)
from crewops.engine.rules import ALL_RULES, RuleBook, RuleVerdict, Verdict
from crewops.engine.world import Positioning, UnknownEntity, World, build_world

__all__ = [
    "ALL_RULES",
    "AlreadyStarted",
    "Assessment",
    "Option",
    "OptionsResult",
    "Positioning",
    "RuleBook",
    "RuleVerdict",
    "Strategy",
    "TripCancelled",
    "UnknownEntity",
    "Verdict",
    "World",
    "assess",
    "build_world",
    "find_options",
]
