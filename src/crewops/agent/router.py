"""Classify a question: which intent, therefore which tier, and why.

Three stages, each only reached when the one before abstains.

1. Rules. Ordered patterns, most specific first, first match wins. They settle the common
   question shapes with no model call, and every decision names the rule that fired.
2. Semantic match. The closest example question (ids masked) lends its intent but never its
   ids or answer, and only if the similarity clears a floor measured on paraphrases.
3. Triage model. A small model picks the kind of question, nothing else. With no model or
   an unusable reply we default to a lookup, which reads data and changes nothing.

Entities always come from this question's own text, whichever stage picked the intent.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from crewops.agent.entities import (
    AIRCRAFT_RE,
    CREW_RE,
    FLIGHT_ID_RE,
    FLIGHT_NO_RE,
    PAIRING_RE,
    RULE_RE,
    Entities,
    Vocabulary,
    extract,
)
from crewops.agent.intents import Confidence, Intent, Tier

log = logging.getLogger(__name__)

NOT_ABOUT_ONE = r"^(?!.*\b(?:impact|affect|effect|cover|replace|legal|cancel|risk)\w*).*?"
"""Prefix that stops a rule from taking a question about one disruption's effect or cover."""


def _p(*alternatives: str) -> re.Pattern[str]:
    return re.compile("|".join(alternatives), re.I)


# --------------------------------------------------------------------------
# How a desk says someone can't fly
# --------------------------------------------------------------------------

_SICK = (
    r"\bsick\b", r"\bsick ?call\b", r"\bcall(s|ed|ing)? in\b", r"\bwent sick\b",
    r"\bgone sick\b", r"\boff sick\b", r"\bunwell\b", r"\bill\b", r"\billness\b",
    r"\bmedical\b.*\b(issue|problem)\b", r"\binjured\b", r"\bhurt\b",
)  # fmt: skip
_UNAVAILABLE = (
    r"\bnot available\b", r"\bunavailable\b", r"\bno longer available\b",
    r"\bcan'?t (fly|operate|make|do|take)\b", r"\bcannot (fly|operate|make|take)\b",
    r"\bunable to (fly|operate|report|make)\b", r"\bunable\b",
    r"\bno[- ]?show\b", r"\bdidn'?t show\b", r"\bfailed to report\b",
    r"\babsent\b", r"\bmissing\b", r"\bawol\b",
    r"\bis out\b", r"\bare out\b", r"\bout for\b", r"\bdropped out\b",
    r"\bbailed\b", r"\bfell (out|through)\b", r"\bpulled\b", r"\bwithdrawn\b",
    r"\btaken off\b", r"\bremoved from\b", r"\bstood down\b", r"\bstand down\b",
)  # fmt: skip
_OUT_OF_HOURS = (
    r"\btimed? out\b", r"\btiming out\b", r"\bout of hours\b", r"\bran out of hours\b",
    r"\bmaxed out\b", r"\bfatigued?\b", r"\bbust(ed|s)?\b", r"\bblown\b",
    r"\bover (the )?limit\b", r"\bexceeded\b",
)  # fmt: skip
_NOT_LEGAL = (
    r"\bexpired\b", r"\bhas expired\b", r"\blapsed\b", r"\bout of date\b", r"\binvalid\b",
    r"\bnot qualified\b", r"\bunqualified\b", r"\bnot rated\b", r"\bno rating\b",
    r"\bout of currency\b", r"\blost currency\b", r"\bgrounded\b",
    r"\bnon[- ]?compliant\b", r"\billegal\b",
)  # fmt: skip
_ROSTER_GAP = (
    r"\buncrewed\b", r"\buncovered\b", r"\bunassigned\b", r"\bvacant\b",
    r"\bshort[- ]?(crewed|staffed)\b", r"\bunderstaffed\b",
    r"\bgap\b", r"\bopen (slot|seat|position)\b", r"\bone short\b",
    r"\bneeds? (a |an )?(captain|first officer|fo|pilot|crew|cover|someone|body)\b",
    r"\bno (captain|first officer|fo|pilot|crew)\b",
)  # fmt: skip
_NEEDS_COVER = (
    r"\bwho (can|could|should|else)\b", r"\bwho'?s (available|free|around)\b",
    r"\bwho do i (use|call|send)\b", r"\breplacement\b",
    r"\bcover(ing)? (for|the|this|that)\b",
    r"\bneed (a |an |some ?)?(cover|replacement|body|one|captain|fo|pilot|crew)\b",
    r"\bfind (me )?(a |an |someone|somebody)\b", r"\bget me\b",
    r"\bstand[- ]?in\b", r"\bback[- ]?fill\b", r"\bsubstitute\b", r"\bswap\b",
    r"\bcall out\b", r"\bcallout\b", r"\bwho takes\b", r"\bwho'?ll (take|fly)\b",
    r"\bwho (covers|will cover)\b",
)  # fmt: skip

DISRUPTION_RE = _p(*_SICK, *_UNAVAILABLE, *_OUT_OF_HOURS, *_NOT_LEGAL, *_ROSTER_GAP)
NEEDS_COVER_RE = _p(*_NEEDS_COVER)
ASKS_IMPACT_RE = _p(
    r"\bwhich flights?\b", r"\bwhat (flights?|happens|is affected)\b",
    r"\bhow many (flights?|passengers?|pax|people)\b", r"\baffected\b", r"\bat risk\b",
    r"\bimpact\b", r"\bknock[- ]?on\b", r"\bdownstream\b", r"\bwithout (a )?crew\b",
    r"\bwhat (does|would) that (leave|break|hit)\b", r"\bgets? hit\b", r"\bstrand",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Rule:
    intent: Intent
    pattern: re.Pattern[str]
    name: str


RULES: tuple[Rule, ...] = (
    # Gates first, or "delayed" reads as a disruption.
    Rule(Intent.CHECK_GATE, _p(r"\bgates?\b", r"\b[a-z]{3}-g\d+\b"), "gate"),
    # "Controller" names a desk, never a crew member.
    Rule(Intent.LOOKUP_CONTROLLERS, _p(r"\bcontrollers?\b"), "controllers"),
    # "What disruptions are on my desk", "how many are open" read the live store.
    Rule(
        Intent.LOOKUP_CONTROLLERS,
        _p(
            # not "the impact of the disruption on P-2291" or "who covers", those have
            # their own intents
            NOT_ABOUT_ONE + r"\b(open|active|current|pending|unresolved|ongoing|outstanding|"
            r"how many|which|what|list|show)\b[^.?!]{0,30}\bdisruptions?\b",
            NOT_ABOUT_ONE + r"\bdisruptions?\b[^.?!]{0,30}\b(open|on (my|the) desk|pending|"
            r"right now|today|active|outstanding)\b",
            NOT_ABOUT_ONE + r"\bmy desk\b",
        ),
        "open disruptions",
    ),
    # "likely to go sick" is a risk score question, not a sick call
    Rule(
        Intent.LOOKUP_RISK,
        _p(r"\blikely to (go|call|be) (sick|off)\b", r"\bflagged\b", r"\brisk score\b",
           r"\bdisruption[- ]risk\b", r"\brisk signal", r"\bwhat drives\b"),
        "risk",
    ),
    # Tier 3. "Draft the callout" also says "callout", so the verb has to decide.
    Rule(
        Intent.DRAFT_NOTIFICATION,
        _p(r"\bdraft\b", r"\bnotification\b", r"\bnotify\b", r"\bmessage (to|for|telling)\b",
           r"\bwrite (the |a )?(message|note|text|sms)\b", r"\binform\b",
           r"\blet (them|him|her|the crew) know\b"),
        "notification",
    ),
    Rule(
        Intent.JOINT_PLAN,
        _p(r"\bjoint\b", r"\bboth\b.*\bsick\b", r"\bsimultaneous", r"\bacross both\b",
           r"\bboth\b.*\bcall(ed)? in\b", r"\bcover the two\b"),
        "joint",
    ),
    Rule(
        Intent.SIMULATE_WHATIF,
        _p(r"\bwhat if\b", r"\bsuppose\b", r"\binstead\b", r"\bwould happen\b",
           r"\bif .* were\b"),
        "whatif",
    ),
    Rule(
        Intent.RESOLVE_ILLEGAL,
        _p(r"\blapsed\b", r"\bexpired\b.*\bresolve\b", r"\billegal\b", r"\bresolve their\b",
           r"\bnon-?compliant\b", r"\b(run|ran) out\b.*\b(sort|fix|resolve)\b"),
        "resolve_illegal",
    ),
    Rule(
        # no bare "rank", "what is C-2087's rank" is a lookup
        Intent.RANK_OPTIONS,
        _p(r"\branked\b", r"\brank (the |these )?options\b", r"\brank them\b",
           r"\brecommend", r"\bwhat should\b", r"\bshould (it|we|they|the desk)\b",
           r"\bbest (option|course|plan|way)\b", r"\bresolution options\b",
           r"\boptions with costs?\b", r"\boptimal\b", r"\bproduce .*options\b",
           r"\brecovery plan\b", r"\boutline .*\bplan\b", r"\bbriefing\b", r"\bsummary\b",
           r"\bcheapest\b", r"\bleast (cost|expensive)\b", r"\bchoices\b", r"\bin order\b",
           r"\bput .* back together\b", r"\bhow (do|should) (we|the desk) (recover|handle|fix)\b"),
        "rank",
    ),
    # Tier 2.
    Rule(
        Intent.CHECK_LEGALITY,
        _p(r"\bbreach", r"\bis it legal\b", r"\bdoes any rule\b", r"\bany rule\b",
           r"\blegal(ly)? (to |for )?(assign|cover|operate)", r"\bviolat",
           r"\bexceed", r"\bwithin (the )?limits?\b", r"\blegal\s*\?", r"\ballowed\b",
           r"\bproposed to (cover|operate|fly)\b", r"\bproposed\b",
           r"\bincluding any planned\b", r"\bif (assigned|rostered)\b",
           r"\bearliest .*\breport\b", r"\bmay report\b", r"\bwhen can .*report\b",
           r"\bsoonest\b.*\b(sign on|report|start)\b", r"\bbreak (anything|any rules?)\b",
           r"\b(ok|okay) to (fly|finish|operate)\b", r"\bbe flown (down|in|over) to\b",
           r"\b(able|fit) to (take|fly|operate|do)\b", r"\babove \d+ hours\b", r"\blegal for\b",
           r"\b(can|could) (reserve |captain |fo |first officer )?C-\d{4}\b"
           r"[^.?!]*\b(cover|take|operate|fly)\b"),
        "legality",
    ),
    # a disruption plus "which flights" wants consequences, not a name
    Rule(
        Intent.IMPACT_OF_EVENT,
        _p(r"\baffected\b", r"\bat risk\b", r"\bimmediately\b.*\bflights?\b",
           r"\bwhich flights\b.*\b(lose|lost|uncrewed|uncovered)\b",
           r"\bknock[- ]?on\b", r"\bdownstream\b", r"\bimpact\b",
           r"\bclosure\b", r"\bcloses?\b.*\b\d{2}:\d{2}\b", r"\bgets? hit\b",
           r"\bstrand", r"\bwhich services\b", r"\bhow many (people|passengers)\b"),
        "impact",
    ),
    Rule(
        # "find me a captain who is A320-rated, BLR-based" is a filter, not cover
        Intent.LOOKUP_CREW,
        re.compile(
            r"\bfind (?:me )?(?:a |an )?(?:captain|first officer|fo|pilot|crew member)\b"
            r"[^.?!]{0,120}?\b(?:-rated|-based|duty hours?|not flying)\b",
            re.I,
        ),
        "attribute-filter",
    ),
    Rule(
        Intent.FIND_REPLACEMENT,
        _p(r"\bexcluded\b", r"\bwho (got |was )?(dropped|ruled out|eliminated)\b"),
        "excluded-from-search",
    ),
    Rule(Intent.FIND_REPLACEMENT, NEEDS_COVER_RE, "cover-request"),
    # someone can't fly, nothing else said: they want cover
    Rule(Intent.FIND_REPLACEMENT, DISRUPTION_RE, "disruption"),
    # Tier 1.
    Rule(
        Intent.LOOKUP_DUTY_CLOCK,
        _p(r"\bduty hours?\b", r"\bblock hours?\b", r"\bflight hours?\b", r"\bheadroom\b",
           r"\baccrued\b", r"\bduty clock\b", r"\brest\b.*\bhow much\b",
           r"\bduty (cap|limit|time)\b", r"\bhow much have they flown\b"),
        "duty_clock",
    ),
    Rule(
        Intent.LOOKUP_RESERVE,
        _p(r"\breserves?\b", r"\bon-?call\b", r"\bstandby\b", r"\bstanding by\b"),
        "reserve",
    ),
    Rule(
        Intent.LOOKUP_CERT,
        _p(r"\bcertificat", r"\bexpir", r"\bmedical\b", r"\blicen[cs]e\b", r"\brecurrent\b",
           r"\bdangerous goods\b", r"\bvalid(ity)?\b", r"\bpaperwork\b", r"\blapses\b"),
        "cert",
    ),
    Rule(
        Intent.EXPLAIN_RULE,
        _p(r"\bwhat (is|does)\b.*\brule\b", r"\bexplain\b.*\brule\b", r"\bwhich rule\b",
           r"\brule\b.*\bmean\b", r"\bRULE-[A-Z]{3,4}-\d{2}\b"),
        "explain_rule",
    ),
    Rule(
        Intent.LOOKUP_FLIGHT,
        _p(r"\bwhich flights?\b", r"\bflights? (depart|arrive|from|to|operate|fly)\b",
           r"\bhow many flights\b", r"\bhow many seats\b",
           r"\bwhich aircraft (operates|flies)\b", r"\bblock time\b",
           r"\bdepartures?\b", r"\barrivals?\b", r"\bschedule\b",
           r"\bstations?\b.*\b(serve|network|nonstop|non-stop)\b",
           r"\bnetwork\b", r"\bnonstop\b", r"\bnon-stop\b", r"\broutes?\b",
           r"\bflights?\b.{0,25}\b(before|after|since|between|on or (before|after))\b",
           r"\bwhat (leaves|departs|arrives|operates|flies)\b", r"\bsectors?\b",
           r"\bwhat do we (operate|fly)\b", r"\bwhat tail\b", r"\bget to directly\b",
           r"\b(did|does|is|will) [A-Z]{2}\d{3}\b", r"\bshow (me )?(the )?flights\b",
           r"\bflights in\b",
           r"\b(count|how many)\b.*\boperat"),
        "flight",
    ),
    Rule(
        Intent.LOOKUP_ROSTER,
        _p(r"\brostered?\b", r"\bpairing\b", r"\bwho is (flying|operating|on)\b",
           r"\bcrew (complement|list|of)\b", r"\bline-?up\b",
           r"\bwho'?s the\b.*\btrip\b"),
        "roster",
    ),
    Rule(
        # a bare "how many" has to say what it counts to land here
        Intent.LOOKUP_CREW,
        _p(r"\bwho\b", r"\blist all\b", r"\bqualified\b", r"\brating\b", r"\bbased at\b",
           r"\bhome base\b", r"\brated on\b", r"\b(rank|seniority|reachability)\b",
           r"\bwhere is C-\d{4} based\b",
           r"\bhow many\b\s+(crew|captains?|first officers?|pilots?|cabin crew|"
           r"senior cabin crew|people|reserves?)\b"),
        "crew",
    ),
)  # fmt: skip

PERSON_ASPECTS = frozenset(
    {Intent.LOOKUP_DUTY_CLOCK, Intent.LOOKUP_CERT, Intent.LOOKUP_RISK, Intent.LOOKUP_ROSTER}
)
"""Tier-1 intents that each answer one aspect of one person. A question asking for several
("duty headroom, expiring certificates and risk for C-1042") becomes a crew profile, or
all but one aspect would go unanswered."""


# --------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Route:
    intent: Intent
    entities: Entities
    confidence: Confidence = Confidence.HIGH
    decided_by: str = "rule"
    """rule, semantic, triage or default."""
    matched: str | None = None
    """Rule name or example question id that decided."""
    notes: list[str] = field(default_factory=list)
    off_topic: bool = False
    """Triage said the question isn't about crew control: no tool or model should run."""

    @property
    def tier(self) -> Tier:
        return self.intent.tier

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "tier": int(self.tier),
            "confidence": self.confidence.value,
            "decided_by": self.decided_by,
            "matched": self.matched,
            "entities": self.entities.to_dict(),
            "notes": list(self.notes),
        }


def is_multi_event(text: str, ents: Entities) -> bool:
    """Two disruptions at once. Two crew ids alone don't count ("can C-1042 cover for
    C-2087?")."""
    if not DISRUPTION_RE.search(text):
        return False
    named_twice = len(ents.crew_ids) >= 2 or len(ents.pairing_ids) >= 2 or len(ents.aircraft) >= 2
    said_twice = re.search(r"\bboth\b|\bsimultaneous|\btwo\b.*\bcaptains?\b", text, re.I)
    return named_twice or bool(said_twice)


def route_by_rules(text: str, ents: Entities) -> Route | None:
    multi = is_multi_event(text, ents)
    for rule in RULES:
        if not rule.pattern.search(text):
            continue
        intent, notes = rule.intent, []
        if intent is Intent.RANK_OPTIONS and multi:
            intent = Intent.JOINT_PLAN
            notes.append("joint plan: two disruptions at once")
        elif intent is Intent.FIND_REPLACEMENT and ASKS_IMPACT_RE.search(text):
            intent = Intent.IMPACT_OF_EVENT
            notes.append("impact: a disruption stated with an impact question")
        elif intent is Intent.FIND_REPLACEMENT and multi:
            intent = Intent.JOINT_PLAN
            notes.append("joint plan: two disruptions at once")
        elif intent in PERSON_ASPECTS and ents.crew_ids:
            also = sorted(
                r.name
                for r in RULES
                if r.intent in PERSON_ASPECTS and r.intent is not intent and r.pattern.search(text)
            )
            if also:
                intent = Intent.LOOKUP_CREW
                notes.append(f"crew profile: asks for {rule.name} and {', '.join(also)}")
        return Route(intent, ents, Confidence.HIGH, "rule", rule.name, notes)
    return None


ID_PATTERNS = (CREW_RE, PAIRING_RE, FLIGHT_ID_RE, FLIGHT_NO_RE, RULE_RE, AIRCRAFT_RE)


def is_bare_identifier(text: str) -> bool:
    """Only ids, no words. Similarity would then measure the shared id, not the kind of
    question."""
    for pattern in ID_PATTERNS:
        text = pattern.sub("", text)
    return not re.search(r"[A-Za-z]{2,}", text)


TRIAGE_INSTRUCTIONS = """\
You classify airline crew-control questions. Reply with JSON only:
{"intent": "<INTENT>", "confidence": "high|medium|low"}

Intents:
  Tier 1  LOOKUP_ROSTER LOOKUP_RESERVE LOOKUP_DUTY_CLOCK LOOKUP_CERT LOOKUP_RISK
          LOOKUP_FLIGHT LOOKUP_CREW EXPLAIN_RULE CHECK_GATE LOOKUP_CONTROLLERS
  Tier 2  FIND_REPLACEMENT CHECK_LEGALITY IMPACT_OF_EVENT
  Tier 3  RANK_OPTIONS SIMULATE_WHATIF JOINT_PLAN RESOLVE_ILLEGAL DRAFT_NOTIFICATION

Ids are already extracted; classify only the kind of question. A question that states no
disruption or action is a lookup: never invent a scenario the user did not describe.
Flights, aircraft, airports, gates, crew, rosters, reserves, duty, certificates, rules and
disruptions are all crew control. Only a question with nothing to do with the airline's
operation is {"intent": "NONE", "confidence": "high"}.
"""
NOT_CREW_CONTROL = "NONE"

Triage = Callable[[str, str], str]
"""(instructions, question) -> the model's raw reply."""
IntentSearch = Callable[[str], Sequence[Any]]
"""question -> hits from the intents index, best first (``search.vectors.Hit``)."""


class Router:
    def __init__(
        self,
        vocab: Vocabulary,
        examples: Mapping[str, str],
        *,
        search: IntentSearch | None = None,
        floor: float = 0.45,
        triage: Triage | None = None,
    ) -> None:
        """``examples`` maps example question ids to their text. Their intents come from the
        rules, so there's no second hand-kept list to drift."""
        self.vocab = vocab
        self.search = search
        self.floor = floor
        self.triage = triage
        self.example_intents: dict[str, Intent] = {}
        for qid, prompt in examples.items():
            if decided := route_by_rules(prompt, extract(prompt, vocab)):
                self.example_intents[qid] = decided.intent

    def route(self, text: str) -> Route:
        ents = extract(text, self.vocab)
        if decided := route_by_rules(text, ents):
            return decided
        if semantic := self._semantic(text, ents):
            return semantic
        if self.triage is not None:
            return self._triage(text, ents)
        return Route(
            Intent.LOOKUP_CREW,
            ents,
            Confidence.LOW,
            "default",
            notes=["no rule matched and nothing similar enough; answered as a lookup"],
        )

    def _semantic(self, text: str, ents: Entities) -> Route | None:
        if self.search is None or is_bare_identifier(text):
            return None
        try:
            hits = list(self.search(text))
        except Exception:
            # Embeddings or the index unreachable (or models paused): triage or a lookup
            # answers instead, as when nothing is similar enough.
            log.exception("semantic search failed; falling through")
            return None
        if not hits:
            return None
        best = hits[0]
        intent = self.example_intents.get(best.key)
        if intent is None or best.semantic < self.floor:
            return None
        return Route(
            intent,
            ents,
            Confidence.MEDIUM,
            "semantic",
            best.key,
            [f"closest example {best.key} (similarity {best.semantic:.2f})"],
        )

    def _triage(self, text: str, ents: Entities) -> Route:
        try:
            raw = self.triage(TRIAGE_INSTRUCTIONS, text)  # type: ignore[misc]
            payload = json.loads(_json_part(raw))
            name = str(payload["intent"]).strip().upper()
            if name == NOT_CREW_CONTROL:
                # nothing to look up: the pipeline answers with what it can help with
                return Route(
                    Intent.LOOKUP_CREW,
                    ents,
                    Confidence.LOW,
                    "default",
                    notes=["triage: not a crew-control question"],
                    off_topic=True,
                )
            intent = Intent(name)
            confidence = Confidence(str(payload.get("confidence", "medium")).strip().lower())
        except Exception as exc:  # any failure falls back to a lookup, with the reason
            return Route(
                Intent.LOOKUP_CREW,
                ents,
                Confidence.LOW,
                "default",
                notes=[f"triage gave no usable answer ({type(exc).__name__}); a lookup"],
            )
        if confidence is Confidence.HIGH:
            confidence = Confidence.MEDIUM  # never trust a model as much as a rule
        return Route(intent, ents, confidence, "triage")


def _json_part(raw: str) -> str:
    """Strip code fences or chatter around the JSON object."""
    start, end = raw.find("{"), raw.rfind("}")
    return raw[start : end + 1] if start != -1 and end > start else raw
