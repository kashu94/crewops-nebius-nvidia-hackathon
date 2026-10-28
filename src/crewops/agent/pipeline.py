"""One question in, one verified answer out.

    (the previous turn, if any) -> route -> check what the question states
          -> planned tool calls -> follow-ups
          -> (model draft, when one is plugged in) -> verify -> fall back to the template
          -> redact personal data

The planned calls run with the caller's own role checks, so a crew member's question can
only ever reach their own records. A model draft is accepted only if the verifier traces
every claim in it to this turn's tool results; otherwise the deterministic rendering of
those results is the answer.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from crewops.agent import injection
from crewops.agent.breaker import CircuitOpen
from crewops.agent.context import Turn, as_reply, carry_ids
from crewops.agent.crew_self import crew_self, with_next_trip
from crewops.agent.entities import Vocabulary, extract, spoken_ids
from crewops.agent.intents import Intent, Tier
from crewops.agent.pii import redact, redact_text
from crewops.agent.planner import MISSING, ToolCall, false_premise, followup_calls, seed_calls
from crewops.agent.render import render
from crewops.agent.roles_help import about_roles, help_for
from crewops.agent.router import Route, Router, route_by_rules
from crewops.agent.rule_topics import ALL, rule_topic
from crewops.agent.smalltalk import small_talk
from crewops.agent.verifier import Verification, keeps_every_fact, verify
from crewops.domain import Role
from crewops.tools import Caller, CrewOpsTools, TraceEntry, allowed_tools, dispatch
from crewops.tools.dispatch import own_records
from crewops.tools.errors import AMBIGUOUS_QUERY, NEEDS_CONFIRMATION

log = logging.getLogger(__name__)

MAX_QUESTION = 1000
FIRST_PERSON_RE = re.compile(r"\b(I|me|my|mine)\b", re.I)
QUEUE_RE = re.compile(
    r"\b(waiting (for|on) (me|my (approval|sign[- ]?off))|pending approvals?|"
    r"(plans?|anything|something|resolutions?) (for me )?to (approve|sign off)|"
    r"my (queue|approvals)|"
    r"what('s| is) waiting|what should i (do|work on|look at|pick up)( next| now| first)?|"
    r"what('s| is) next|where (do|should) i start|what needs my attention)\b",
    re.I,
)
MY_DESK_RE = re.compile(r"\b(my desk|my disruptions|on my plate|assigned to me|for me)\b", re.I)
SCOPE = (
    "I can help with crew control: rosters and reserves, duty hours, legality, cover for "
    "a sick call or delay, disruption impact, gates and callout messages. What would you "
    "like to check?"
)
MAX_FOLLOWUP_ROUNDS = 3
CREW_CONTROL = frozenset({Role.CONTROLLER, Role.APPROVER, Role.AUDITOR})
ABOUT_A_CREW_MEMBER = frozenset(
    {
        Intent.LOOKUP_CERT,
        Intent.LOOKUP_DUTY_CLOCK,
        Intent.LOOKUP_ROSTER,
        Intent.LOOKUP_RISK,
        Intent.CHECK_LEGALITY,
    }
)
"""Questions about one crew member's records. "My" only means the asker when they're crew."""

ToolRunner = Callable[[str, dict[str, Any]], TraceEntry]
"""(tool name, arguments) -> the recorded call, run for the signed-in caller."""
Drafter = Callable[[str, Route, list[TraceEntry], ToolRunner, frozenset[str]], str]
"""(question, route, trace so far, run a tool, tools the caller may use) -> draft answer.
The model advisor plugs in here. It can only reach tools through the runner it's given, so
every call goes through the caller's role checks and lands in the trace the draft is
verified against. An empty draft means no answer and the template is used."""
Polisher = Callable[[str, str], str]
"""(question, verified answer) -> the same answer reworded for speech, then re-verified."""
Screen = Callable[[str], str | None]
"""text -> replacement message if blocked, None if it passes."""


@dataclass(slots=True)
class Answer:
    question: str
    text: str
    route: Route | None
    trace: list[TraceEntry] = field(default_factory=list)
    verification: Verification | None = None
    source: str = "template"
    """template, explained (the template reworded for speech), model, clarify (a question
    back) or refused."""
    awaiting: str | None = None
    """confirmation (an id, name or premise to confirm) or detail (something missing)."""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Everything that leaves the process goes through ``redact``."""
        return redact(
            {
                "question": self.question,
                "text": self.text,
                "source": self.source,
                "awaiting": self.awaiting,
                "route": self.route.to_dict() if self.route else None,
                "verified": self.verification.ok if self.verification else False,
                "verification": self.verification.summary() if self.verification else None,
                "tools": [e.to_dict() | {"args": e.args, "ms": e.ms} for e in self.trace],
                "notes": list(self.notes),
            }
        )


class Pipeline:
    def __init__(
        self,
        tools: CrewOpsTools,
        router: Router,
        *,
        drafter: Drafter | None = None,
        polisher: Polisher | None = None,
        screen_question: Screen | None = None,
        screen_answer: Screen | None = None,
        budget_seconds: float = 6.0,
    ) -> None:
        self.tools = tools
        self.router = router
        self.vocab: Vocabulary = router.vocab
        self.drafter = drafter
        self.polisher = polisher
        self.screen_question = screen_question
        self.screen_answer = screen_answer
        self.budget_seconds = budget_seconds

    @classmethod
    def offline(cls, tools: CrewOpsTools, examples: Mapping[str, str]) -> Pipeline:
        """Rules and templates only. No embeddings, no model calls."""
        return cls(tools, Router(Vocabulary.from_world(tools.world), examples))

    def answer(self, question: str, caller: Caller, previous: Turn | None = None) -> Answer:
        """``previous`` is the caller's last turn in this conversation, already checked to be
        recent. It only fills in what this question leaves out (see ``agent.context``)."""
        started = time.monotonic()
        # voice input gives "see 3231" for C-3231
        question = spoken_ids((question or "").strip())
        if not question:
            return Answer(
                question, "What would you like to know?", None, source="clarify", awaiting="detail"
            )
        if len(question) > MAX_QUESTION:
            return Answer(
                question[:80],
                f"Please ask in under {MAX_QUESTION} characters.",
                None,
                source="refused",
            )

        # Refused before any model or guardrail call. The API logs the refusal.
        if attempt := injection.detect(question):
            return Answer(
                question,
                injection.REFUSAL,
                None,
                source="refused",
                notes=[f"blocked: override attempt ({attempt})"],
            )

        screening_note = ""
        if self.screen_question is not None:
            try:
                if blocked := self.screen_question(question):
                    return Answer(
                        question,
                        blocked,
                        None,
                        source="refused",
                        notes=["blocked: guardrail (prompt attack or denied topic)"],
                    )
            except Exception as exc:
                # Fail open. Verifier, role checks and redaction still apply, and a screening
                # outage shouldn't take the desk offline.
                log.exception("question screening failed")
                screening_note = f"question screening unavailable ({type(exc).__name__})"

        if chat := small_talk(question, caller, self.tools.world):
            return Answer(question, chat, None, source="smalltalk")
        if roles := about_roles(question, caller):
            # About the assistant or the roles, from the caller's verified roles.
            return Answer(question, roles, None, source="smalltalk")
        if topic := rule_topic(question):
            if topic == ALL:
                rules = self.tools.rules.values()
                text = f"Every cover is checked against {len(rules)} rules. " + " ".join(
                    f"{r.rule_id}: {r.text}" for r in rules
                )
                return Answer(question, text, None, source="template")
            # "what's the FDP limit": the rule itself, in its current wording
            question = f"explain {topic}"
        question = with_next_trip(question, caller, self.tools.world)
        if caller.crew_id and (own := crew_self(question, caller, self.tools.world)):
            # A crew member's question about themselves: their own roster, nobody else's.
            return Answer(question, own, None, source="template")

        if queue := self._queue(question, caller):
            return queue

        context_notes: list[str] = []
        reply = previous is not None and previous.awaiting == "detail"
        own = extract(question, self.vocab) if reply else None
        asks = reply and route_by_rules(question, own) is not None
        if reply and (combined := as_reply(question, previous, own, asks)):
            question = combined
            context_notes.append("read together with the question it answers")
        route = self.router.route(question)
        context_notes += carry_ids(question, route.entities, previous)
        if (
            caller.crew_id
            and not route.entities.crew_ids
            and not route.entities.names
            and FIRST_PERSON_RE.search(question)
        ):
            # "my duty hours", "am I legal for P-2291"
            route.entities.crew_ids.append(caller.crew_id)
            route.notes.append(f"'I' and 'my' read as the signed-in crew member, {caller.crew_id}")
        elif (
            not caller.crew_id
            and route.intent in ABOUT_A_CREW_MEMBER
            and not route.entities.crew_ids
            and not route.entities.names
            and not route.entities.pairing_ids
            and FIRST_PERSON_RE.search(question)
        ):
            # A controller asking "is my certification valid?" isn't on the roster. Listing
            # everyone's instead would be wrong.
            return Answer(
                question,
                "You're not on the crew roster, so you hold no certificates, duty hours or "
                "trips here. Ask about a crew member by id, for example: is C-1042's medical "
                "valid?",
                route,
                source="clarify",
                awaiting="detail",
            )
        allowed = allowed_tools(caller)

        def may_see(crew_id: str) -> bool:
            return "lookup" in allowed or crew_id == caller.crew_id

        if premise := false_premise(question, route.entities, self.tools, self.vocab, may_see):
            return Answer(question, premise, route, source="clarify", awaiting="confirmation")

        notes: list[str] = ([screening_note] if screening_note else []) + context_notes
        trace: list[TraceEntry] = []
        planned = seed_calls(route, question, self.vocab, self.tools)
        if previous is not None and previous.crew_ids:
            for call in planned:
                if call.name == "suggest_crew_ids":
                    call.args["prefer"] = list(previous.crew_ids[:3])
        if caller.desk and MY_DESK_RE.search(question):
            for call in planned:
                if call.name == "list_open_disruptions":
                    call.args["desk"] = caller.desk
        trace += self._run(planned, caller, allowed, notes)
        for _ in range(MAX_FOLLOWUP_ROUNDS):
            more = followup_calls(route, trace)
            if not more:
                break
            trace += self._run(more, caller, allowed, notes)

        # Only ask the model when the planned calls produced nothing usable. Otherwise the
        # template says exactly what they returned, and a model's extra conclusion (no id or
        # number in it) could slip past the verifier. Nor when triage said the question isn't
        # about crew control: there is nothing for the advisor to look up. Nor when the caller's
        # role may run none of the planned calls, or every one it ran was refused: that is a
        # refusal, not a question for the advisor to work around with other tools.
        role_blocked = bool(planned) and (
            all(c.name not in allowed and not own_records(caller, c.name, c.args) for c in planned)
            or (bool(trace) and all(e.error_code == "FORBIDDEN" for e in trace))
        )
        wants_model = not any(e.ok for e in trace) and not route.off_topic and not role_blocked
        if not trace and (seat := self._which_seat(route)):
            # the asker says which seat, never a model
            return Answer(question, seat, route, source="clarify", awaiting="detail", notes=notes)
        if self.drafter is not None and allowed and wants_model:

            def run(name: str, args: dict[str, Any]) -> TraceEntry:
                entry = dispatch(self.tools, caller, name, args)
                trace.append(entry)
                return entry

            try:
                draft = self.drafter(question, route, list(trace), run, allowed)
                checked = verify(draft, trace)
                sourced = any(
                    c.supported and c.kind in ("identifier", "number") for c in checked.claims
                )
                if not draft.strip():
                    notes.append("model gave no complete answer within its budget")
                elif checked.ok and sourced:
                    return self._finish(question, draft, route, trace, checked, "model", notes)
                elif checked.ok:
                    notes.append("model draft stated no sourced fact; not used")
                elif not checked.unsupported:
                    notes.append("model draft used no tool result; not used")
                else:
                    # unsourced values only go to the log, they could be anything
                    log.info("model draft rejected: %s", checked.summary())
                    kinds = sorted({c.kind for c in checked.unsupported})
                    notes.append(
                        f"model draft rejected: {len(checked.unsupported)} unsourced claim(s) "
                        f"({', '.join(kinds)})"
                    )
            except Exception as exc:  # a model failure must never cost the user an answer
                log.exception("drafter failed")
                notes.append(
                    "models paused after repeated failures; answered from the roster"
                    if isinstance(exc, CircuitOpen)
                    else f"model unavailable ({type(exc).__name__}); template used"
                )

        if not trace:
            return Answer(
                question,
                self._nothing_ran(route, planned, allowed, caller),
                route,
                source="clarify",
                awaiting="detail",
                notes=notes,
            )

        text = render(trace)
        checked = verify(text, trace)
        if not checked.ok:  # template bug; still only says what the tools said
            log.error("template failed verification: %s", checked.summary())
            notes.append(checked.summary())
        elif (
            self.polisher is not None
            and route.tier >= Tier.REPLACEMENT
            and all(e.ok for e in trace)
            and time.monotonic() - started < self.budget_seconds / 2
        ):
            # Only judgement answers get reworded since they read worst as templates. Lookups
            # are plain lists already. Skip if half the budget is gone.
            try:
                spoken = self.polisher(question, text)
                again = verify(spoken, trace)
                if spoken.strip() and again.ok and keeps_every_fact(text, spoken):
                    return self._finish(question, spoken, route, trace, again, "explained", notes)
                notes.append("rewording dropped or changed a fact; template used")
            except Exception as exc:
                log.exception("explainer failed")
                notes.append(
                    "models paused after repeated failures; answered from the roster"
                    if isinstance(exc, CircuitOpen)
                    else f"rewording unavailable ({type(exc).__name__}); template used"
                )
        return self._finish(question, text, route, trace, checked, "template", notes)

    # ------------------------------------------------------------------

    def _run(
        self, calls: list[ToolCall], caller: Caller, allowed: frozenset[str], notes: list[str]
    ) -> list[TraceEntry]:
        out = []
        for call in calls:
            if call.name not in allowed and not own_records(caller, call.name, call.args):
                notes.append(f"skipped {call.name}: not available to your role")
                continue
            out.append(dispatch(self.tools, caller, call.name, call.args))
        return out

    def _finish(
        self,
        question: str,
        text: str,
        route: Route,
        trace: list[TraceEntry],
        checked: Verification,
        source: str,
        notes: list[str],
    ) -> Answer:
        codes = {e.error_code for e in trace if not e.ok}
        awaiting = None
        if NEEDS_CONFIRMATION in codes:
            awaiting = "confirmation"
        elif AMBIGUOUS_QUERY in codes:
            awaiting = "detail"
        text = redact_text(text)
        if self.screen_answer is not None:
            try:
                if blocked := self.screen_answer(text):
                    return Answer(question, blocked, route, trace, checked, "refused", None, notes)
            except Exception as exc:
                log.exception("answer screening failed")
                notes.append(f"answer screening unavailable ({type(exc).__name__})")
        return Answer(question, text, route, trace, checked, source, awaiting, notes)

    @staticmethod
    def _which_seat(route: Route) -> str | None:
        """Ask which seat when cover is wanted for a pairing or flight but neither the seat
        nor who is out was given."""
        ents = route.entities
        if route.intent not in (Intent.FIND_REPLACEMENT, Intent.RANK_OPTIONS):
            return None
        where = ents.pairing_ids or ents.flight_ids or ents.flight_nos
        if not where or ents.roles or ents.crew_ids:
            return None
        return (
            f"Which seat needs cover on {where[0]}: Captain, First Officer, Senior Cabin Crew "
            "or Cabin Crew? Or tell me who is out."
        )

    def _queue(self, question: str, caller: Caller) -> Answer | None:
        """ "What's waiting for me?": an approver's sign-offs, or what on a controller's desk
        still needs a plan. Read from the live store like any open-disruption question."""
        approver = Role.APPROVER in caller.roles
        controller = Role.CONTROLLER in caller.roles and caller.desk
        if not (approver or controller) or not QUEUE_RE.search(question):
            return None
        call = ToolCall("list_open_disruptions", {} if approver else {"desk": caller.desk})
        notes: list[str] = []
        trace = self._run([call], caller, allowed_tools(caller), notes)
        entry = trace[0] if trace else None
        if entry is None or not entry.ok:
            text = entry.error if entry and entry.error else "I couldn't read the open disruptions."
            return Answer(question, text, None, source="template", trace=trace, notes=notes)
        rows = entry.result["disruptions"]

        def line(d: dict[str, Any]) -> str:
            return f"{d['disruption_id']}, {d['what']}"

        if approver:
            plans = [line(d) for d in rows if d["status"] == "SUBMITTED"]
            closes = [line(d) for d in rows if d["status"] == "RESOLVING"]
            parts = []
            if plans:
                parts.append(
                    f"{len(plans)} plan{'s' if len(plans) != 1 else ''} to approve: "
                    + "; ".join(plans[:5])
                )
            if closes:
                parts.append(
                    f"{len(closes)} resolution{'s' if len(closes) != 1 else ''} to sign off: "
                    + "; ".join(closes[:5])
                )
            text = (
                ". ".join(parts) + "."
                if parts
                else "Nothing is waiting for your approval right now."
            )
        else:
            todo = [line(d) for d in rows if d["status"] in ("REPORTED", "OPTIONS_READY")]
            waiting = [line(d) for d in rows if d["status"] in ("SUBMITTED", "RESOLVING")]
            parts = []
            if todo:
                parts.append(
                    f"{len(todo)} on {caller.desk} need{'s' if len(todo) == 1 else ''} a plan: "
                    + "; ".join(todo[:5])
                )
            if waiting:
                parts.append(f"{len(waiting)} with an approver: " + "; ".join(waiting[:5]))
            text = ". ".join(parts) + "." if parts else f"Nothing open on {caller.desk} right now."
        return Answer(question, text, None, source="template", trace=trace, notes=notes)

    def _nothing_ran(
        self, route: Route, planned: list[ToolCall], allowed: frozenset[str], caller: Caller
    ) -> str:
        """Never return empty here. Silence would sound like "all clear"."""
        if planned and not allowed & {c.name for c in planned}:
            return f"Your role doesn't give access to that information. {help_for(caller)}"
        ents = route.entities
        if (
            route.decided_by == "default"
            and not planned
            and not (ents.crew_ids or ents.pairing_ids or ents.flight_ids)
        ):
            # crew control's own scope; anyone else hears what their role can ask
            return (
                SCOPE
                if caller.roles & CREW_CONTROL
                else f"I can't help with that. {help_for(caller)}"
            )
        if seat := self._which_seat(route):
            return seat
        what = route.intent.value.replace("_", " ").lower()
        text = f"I read this as {what}, but couldn't run it."
        if needed := MISSING.get(route.intent):
            text += f" I need {needed}."
        if route.intent is Intent.LOOKUP_CONTROLLERS:
            text = "Ask about the controller desks, or how many open disruptions each has."
        return text + " Nothing was checked, so this doesn't mean all is clear."
