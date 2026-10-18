"""The tool calls a question obviously needs, made before any model is consulted.

The router has already pulled out the ids, so for the common question shapes the opening
move is known: "who is on reserve at BLR on 13 Oct" is one ``lookup``. Making those calls
here saves a model round trip and means the model always sees real data before it says
anything. Calls that follow from a first result (a pairing found by aircraft and date, then
cover for it) are made here too, because carrying an id from one result to the next call is
bookkeeping a model does badly.

The false-premise check lives here too. "FO C-2087" when the roster says C-2087 is a
captain changes the answer, so we ask instead of silently going along with it.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from crewops.agent.entities import Entities, Vocabulary, stated_bases, stated_ranks, stated_ratings
from crewops.agent.intents import Intent
from crewops.agent.router import Route
from crewops.tools import CrewOpsTools, TraceEntry

COVER_INTENTS = frozenset({Intent.FIND_REPLACEMENT, Intent.RANK_OPTIONS})
NAMED_PERSON_RE = re.compile(r"^[A-Z]\. [A-Z]")


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


MISSING: dict[Intent, str] = {
    Intent.CHECK_LEGALITY: "a crew member, and the pairing or flight to check them against",
    Intent.FIND_REPLACEMENT: "who is unavailable, or which pairing or flight needs cover",
    Intent.RANK_OPTIONS: "which pairing, flight or crew member needs cover",
    Intent.IMPACT_OF_EVENT: "who or what is disrupted",
    Intent.JOINT_PLAN: "at least two crew members, pairings or aircraft",
    Intent.SIMULATE_WHATIF: "what to change, and which pairing or flight it affects",
    Intent.RESOLVE_ILLEGAL: "the crew member and the date of the assignment in question",
    Intent.EXPLAIN_RULE: "a rule id such as RULE-REST-04, or the question in words",
    Intent.LOOKUP_DUTY_CLOCK: "a crew id such as C-1042",
    Intent.LOOKUP_CERT: (
        "whose certificates (a crew id such as C-1042), which kind (licence, medical, "
        "recurrent training, dangerous goods) or a horizon such as within 30 days"
    ),
    Intent.DRAFT_NOTIFICATION: "the crew member and the pairing they are called out for",
    Intent.LOOKUP_CREW: "something to narrow by: a crew id, a name, a rank, a base or a rating",
}


def _clean(**args: Any) -> dict[str, Any]:
    return {k: v for k, v in args.items() if v not in (None, {}, [])}


def _window(ents: Entities) -> tuple[str | None, str | None]:
    """ "closed 08:00-14:00Z on 15 Oct" -> two UTC instants. An end before the start means
    the window runs past midnight."""
    if not (ents.date and len(ents.times) >= 2):
        return None, None
    start = f"{ents.date}T{ents.times[0]}:00Z"
    end_day = date.fromisoformat(ents.date)
    if ents.times[1] <= ents.times[0]:
        end_day += timedelta(days=1)
    return start, f"{end_day.isoformat()}T{ents.times[1]}:00Z"


def _flight_filters(ents: Entities) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if ents.stations:
        filters["dep_station"] = ents.stations[0]
    if len(ents.stations) > 1:
        filters["arr_station"] = ents.stations[1]
    if ents.date_range:
        filters["date"] = ents.date_range
    elif ents.date:
        filters["date"] = ents.date
    if ents.flight_nos:
        filters["flight_no"] = ents.flight_nos[0]
    if ents.aircraft:
        filters["aircraft"] = ents.aircraft[0]
    return filters


def _crew_filters(ents: Entities, bare_names: bool = True) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if ents.crew:
        filters["crew_id"] = ents.crew
    elif len(ents.names) == 1 and (bare_names or NAMED_PERSON_RE.search(ents.names[0])):
        # "A. Nair" always. A bare capitalised word ("Nair") only when a rule matched a crew
        # question, otherwise "Play some music" would look up a crew member called Play.
        filters["name"] = ents.names[0]
    if ents.role:
        filters["rank"] = ents.role
    if ents.stations:
        filters["base"] = ents.stations[0]
    if ents.aircraft_types:
        filters["ratings"] = ents.aircraft_types[0]
    return filters


def _flight_ref(ents: Entities) -> dict[str, Any]:
    if ents.flight_ids:
        return {"flight_id": ents.flight_ids[0]}
    if ents.flight_nos:
        return _clean(flight_no=ents.flight_nos[0], date=ents.date)
    return {}


def seed_calls(
    route: Route, question: str, vocab: Vocabulary, tools: CrewOpsTools
) -> list[ToolCall]:
    ents, intent = route.entities, route.intent
    if route.decided_by == "default" and not (ents.crew_ids or ents.pairing_ids or ents.flight_ids):
        # Nothing recognised the question and it names no exact id. A city or capitalised
        # word alone ("a hotel in Mumbai") is no reason to read out the roster, so only
        # offer neighbours for a malformed id.
        offers = [
            ("suggest_crew_ids", ents.malformed_crew_ids),
            ("suggest_pairing_ids", ents.malformed_pairing_ids),
            ("suggest_flight_nos", ents.malformed_flight_nos),
        ]
        return [ToolCall(name, {"near": near[0]}) for name, near in offers if near]
    # "Can C-3305 cover P-2291?" names someone who isn't on the pairing, so it's really a
    # legality check on them, not a request to replace them.
    if (
        intent in COVER_INTENTS | {Intent.SIMULATE_WHATIF}
        and ents.crew
        and ents.pairing
        and ents.pairing not in dict(tools.crew_pairings.get(ents.crew, []))
    ):
        intent = Intent.CHECK_LEGALITY
    calls: list[ToolCall] = []

    def add(name: str, **args: Any) -> None:
        calls.append(ToolCall(name, _clean(**args)))

    for near in ents.malformed_crew_ids[:1]:
        add("suggest_crew_ids", near=near)
    for near in ents.malformed_pairing_ids[:1]:
        add("suggest_pairing_ids", near=near)
    for near in ents.malformed_flight_nos[:1]:
        add("suggest_flight_nos", near=near)

    flight = _flight_ref(ents)
    delay_h = round(ents.delay_minutes / 60, 2) if ents.delay_minutes else None
    start, end = _window(ents)

    match intent:
        case Intent.EXPLAIN_RULE if ents.rule_ids:
            for rule_id in ents.rule_ids:
                add("explain_rule", rule_id=rule_id)
        case Intent.EXPLAIN_RULE:
            add("search_rules", query=question)

        case Intent.LOOKUP_DUTY_CLOCK if ents.crew:
            add("duty_clock", crew_id=ents.crew, date=ents.date)

        case Intent.LOOKUP_RESERVE:
            filters = _clean(
                crew_id=ents.crew,
                base=ents.stations[0] if ents.stations else None,
                dates=ents.date_range or ents.date,
                rank=ents.role,
            )
            add("lookup", entity="reserves", filters=filters)
            if ents.crew:
                add("lookup", entity="crew", filters={"crew_id": ents.crew})

        case Intent.LOOKUP_FLIGHT:
            lowered = question.lower()
            if re.search(r"\b(longest|most|highest|largest)\b.*\bblock\b", lowered):
                add("lookup", entity="flights", sort_by="block_hours", sort_desc=True, limit=5)
            elif re.search(r"\b(most|largest|highest)\b.*\bseats?\b", lowered):
                add("lookup", entity="flights", sort_by="seats", sort_desc=True, limit=5)
            elif re.search(r"\b(stations?|serve|nonstop|non-stop|network|routes?)\b", lowered):
                add(
                    "lookup",
                    entity="flights",
                    filters=_flight_filters(ents),
                    group_by=["arr_station"],
                )
            else:
                add("lookup", entity="flights", filters=_flight_filters(ents))

        case Intent.LOOKUP_CERT if ents.crew or ents.cert_types or ents.horizon_days:
            # Guard is on the case above. With no one, no cert type and no horizon we ask back
            # (MISSING) rather than dump 600 rows.
            filters = _clean(
                crew_id=ents.crew, cert_type=ents.cert_types[0] if ents.cert_types else None
            )
            if ents.horizon_days:
                start_day = date.fromisoformat(ents.date) if ents.date else vocab.today
                last = start_day + timedelta(days=ents.horizon_days)
                filters["valid_to"] = {"gte": start_day.isoformat(), "lte": last.isoformat()}
            add("lookup", entity="certifications", filters=filters)

        case Intent.LOOKUP_RISK:
            add("lookup", entity="risk_signals", filters=_clean(crew_id=ents.crew))

        case Intent.LOOKUP_CREW if _crew_filters(ents, route.decided_by == "rule"):
            add("lookup", entity="crew", filters=_crew_filters(ents, route.decided_by == "rule"))
            if ents.crew and re.search(r"\b(duty|hours|headroom)\b", question, re.I):
                add("duty_clock", crew_id=ents.crew, date=ents.date)
            if ents.crew and re.search(r"\b(cert|licen|medical|expir)", question, re.I):
                add("lookup", entity="certifications", filters={"crew_id": ents.crew})
            if ents.crew and re.search(r"\brisk\b", question, re.I):
                add("lookup", entity="risk_signals", filters={"crew_id": ents.crew})

        case Intent.LOOKUP_ROSTER if ents.pairing:
            add("lookup", entity="pairing_crew", filters={"pairing_id": ents.pairing})
        case Intent.LOOKUP_ROSTER if ents.crew:
            add("lookup", entity="pairing_crew", filters={"crew_id": ents.crew})
        case Intent.LOOKUP_ROSTER if ents.aircraft and ents.date:
            add(
                "lookup",
                entity="pairing_crew",
                filters=_clean(aircraft=ents.aircraft[0], date=ents.date, role=ents.role),
            )

        case Intent.CHECK_GATE:
            at = f"{ents.date}T{ents.times[0]}:00Z" if ents.date and ents.times else None
            if flight or ents.gate_numbers:
                add(
                    "check_gate",
                    **flight,
                    boarding_gate_number=ents.gate_numbers[0] if ents.gate_numbers else None,
                    delay_minutes=ents.delay_minutes,
                    at_utc=None if flight else at,
                    date=None if flight else ents.date,
                )
            else:
                add(
                    "check_gate",
                    station=ents.stations[0] if ents.stations else None,
                    date=ents.date,
                )

        case Intent.LOOKUP_CONTROLLERS:
            per_desk = re.search(
                r"\b(each|every|per|all)\b[^.?!]{0,20}\b(controllers?|desks?)\b|\bworkload\b",
                question,
                re.I,
            )
            if per_desk:
                add("controller_issue_counts")
            elif re.search(r"\b(disruptions?|issues?|cases?|my desk)\b", question, re.I):
                # "my desk" is filled in by the pipeline from the signed-in controller.
                add("list_open_disruptions")
            else:
                add("list_controllers")

        case Intent.DRAFT_NOTIFICATION if ents.crew and ents.pairing:
            add("notification_brief", crew_id=ents.crew, pairing_id=ents.pairing)

        case Intent.CHECK_LEGALITY | Intent.SIMULATE_WHATIF if ents.crew and (
            ents.pairing or flight or (ents.aircraft and ents.date)
        ):
            target = (
                {"pairing_id": ents.pairing}
                if ents.pairing
                else flight or {"aircraft": ents.aircraft[0], "date": ents.date}
            )
            add("check_legality", crew_id=ents.crew, **target, delay_h=delay_h)
        case Intent.CHECK_LEGALITY | Intent.SIMULATE_WHATIF if delay_h and (flight or ents.pairing):
            event = dict(flight) if flight else _clean(pairing_id=ents.pairing, date=ents.date)
            add("simulate", event={**event, "delay_hours": delay_h})
        case Intent.CHECK_LEGALITY | Intent.SIMULATE_WHATIF if (
            delay_h and ents.aircraft and ents.date
        ):
            add(
                "lookup",
                entity="pairing_days",
                filters={"aircraft": ents.aircraft[0], "date": ents.date},
            )
        case Intent.CHECK_LEGALITY if re.search(
            r"\bearliest\b|\brest\b|\breport next\b", question, re.I
        ):
            add("explain_rule", rule_id="RULE-REST-04")
        case Intent.CHECK_LEGALITY if ents.role and (ents.pairing or flight):
            # "Who is legal for the Captain seat on P-2291?"
            target = {"pairing_id": ents.pairing} if ents.pairing else flight
            add("find_options", **target, role=ents.role)

        case Intent.IMPACT_OF_EVENT:
            event: dict[str, Any] = {}
            if flight:
                event = dict(flight)
            elif ents.crew:
                event = _clean(crew_id=ents.crew, date=ents.date)
            elif ents.pairing:
                event = _clean(pairing_id=ents.pairing, date=ents.date)
            elif ents.stations or ents.aircraft:
                where = (
                    {"station": ents.stations[0]}
                    if ents.stations
                    else {"aircraft": ents.aircraft[0]}
                )
                event = _clean(
                    **where, from_utc=start, to_utc=end, date=None if start else ents.date
                )
            if event:
                add("ripple", event=event)

        case Intent.RANK_OPTIONS if (ents.stations and start) or (delay_h and ents.aircraft):
            if ents.stations and start:
                add("ripple", event={"station": ents.stations[0], "from_utc": start, "to_utc": end})
            elif ents.date:
                add(
                    "lookup",
                    entity="pairing_days",
                    filters={"aircraft": ents.aircraft[0], "date": ents.date},
                )

        case Intent.FIND_REPLACEMENT | Intent.RANK_OPTIONS if ents.crew:
            target = {"pairing_id": ents.pairing} if ents.pairing else _clean(date=ents.date)
            add("find_options", crew_id=ents.crew, **target, delay_hours=delay_h)
            if route.intent is Intent.RANK_OPTIONS or not ents.pairing:
                add("ripple", event=_clean(crew_id=ents.crew, date=ents.date))
        case Intent.FIND_REPLACEMENT | Intent.RANK_OPTIONS if ents.pairing and ents.role:
            add("find_options", pairing_id=ents.pairing, role=ents.role, delay_hours=delay_h)
        case Intent.FIND_REPLACEMENT | Intent.RANK_OPTIONS if flight and ents.role:
            add("find_options", **flight, role=ents.role, delay_hours=delay_h)
        case Intent.FIND_REPLACEMENT | Intent.RANK_OPTIONS if ents.aircraft and ents.date:
            add(
                "lookup",
                entity="pairing_crew",
                filters=_clean(aircraft=ents.aircraft[0], date=ents.date, role=ents.role),
            )

        case Intent.JOINT_PLAN if len(ents.crew_ids) >= 2:
            add("joint_plan", events=[_clean(crew_id=c, date=ents.date) for c in ents.crew_ids])
        case Intent.JOINT_PLAN if len(ents.pairing_ids) >= 2 and ents.role:
            add(
                "joint_plan",
                events=[{"pairing_id": p, "role": ents.role} for p in ents.pairing_ids],
            )
        case Intent.JOINT_PLAN if len(ents.aircraft) >= 2 and ents.date:
            for tail in ents.aircraft:
                add(
                    "lookup",
                    entity="pairing_crew",
                    filters=_clean(aircraft=tail, date=ents.date, role=ents.role),
                )

        case Intent.RESOLVE_ILLEGAL if ents.crew:
            add("find_options", crew_id=ents.crew, date=ents.date)

    return calls


def followup_calls(route: Route, trace: Sequence[TraceEntry]) -> list[ToolCall]:
    """Calls that follow from what the seed calls returned, so no model has to carry ids
    across."""
    intent, ents = route.intent, route.entities
    done = {e.tool for e in trace}
    ok = [e for e in trace if e.ok]

    def rows(tool: str, entity: str) -> list[dict[str, Any]]:
        return [
            r
            for e in ok
            if e.tool == tool and isinstance(e.result, dict) and e.result.get("entity") == entity
            for r in e.result.get("rows", [])
        ]

    seats = rows("lookup", "pairing_crew")
    days = rows("lookup", "pairing_days")
    delay_h = round(ents.delay_minutes / 60, 2) if ents.delay_minutes else None

    if intent in COVER_INTENTS and "find_options" not in done and seats:
        seat = seats[0]
        return [
            ToolCall("find_options", _clean(pairing_id=seat["pairing_id"], crew_id=seat["crew_id"]))
        ]
    if intent is Intent.JOINT_PLAN and "joint_plan" not in done and len(seats) >= 2:
        events = [{"pairing_id": s["pairing_id"], "crew_id": s["crew_id"]} for s in seats[:8]]
        return [ToolCall("joint_plan", {"events": events})]
    if delay_h and days and "simulate" not in done:
        day = days[0]
        return [
            ToolCall(
                "simulate",
                {
                    "event": {
                        "pairing_id": day["pairing_id"],
                        "date": day["date"],
                        "delay_hours": delay_h,
                    }
                },
            )
        ]
    if intent is Intent.RESOLVE_ILLEGAL and "check_legality" not in done and ents.crew:
        for e in ok:
            if e.tool == "find_options" and e.result.get("crew_out") == ents.crew:
                return [
                    ToolCall(
                        "check_legality",
                        {"crew_id": ents.crew, "pairing_id": e.result["pairing_id"]},
                    )
                ]
    return []


def false_premise(
    question: str,
    ents: Entities,
    tools: CrewOpsTools,
    vocab: Vocabulary,
    may_see: Callable[[str], bool] = lambda crew_id: True,
) -> str | None:
    """A question back when something the question states contradicts the roster.

    None when everything checks out, or when it names nobody real (the tools report unknown
    ids themselves). ``may_see`` says whether the caller may learn a crew member's rank,
    base and rating. For anyone they can't see we check nothing, so nothing leaks.
    """
    crew = {cid: c for cid, c in tools.world.crew.items() if may_see(cid)}
    for crew_id, claimed in stated_ranks(question):
        if crew_id in crew and crew[crew_id].rank.value != claimed:
            actual = crew[crew_id].rank.value
            return (
                f"{crew_id} is a {actual}, not a {claimed}. Did you mean someone else, or shall "
                f"I go ahead with {crew_id} as a {actual}?"
            )
    for crew_id, claimed in stated_bases(question, vocab):
        if crew_id in crew and crew[crew_id].base != claimed:
            actual = crew[crew_id].base
            return (
                f"{crew_id} is based at {actual}, not {claimed}. Did you mean someone else, or "
                f"shall I go ahead with {crew_id} as {actual}-based?"
            )
    for crew_id, claimed in stated_ratings(question):
        if crew_id in crew and claimed not in {r.value for r in crew[crew_id].ratings}:
            have = "/".join(r.value for r in crew[crew_id].ratings)
            return f"{crew_id} is rated on {have}, not {claimed}. Did you mean someone else?"
    if ents.unknown_stations:
        word = ents.unknown_stations[0]
        near = difflib.get_close_matches(word, sorted(vocab.stations), n=1)
        if near:
            return f"{word} isn't one of our stations. Did you mean {near[0]}?"
        return f"{word} isn't one of our stations ({', '.join(sorted(vocab.stations))})."
    return None
