"""What each tool takes, as JSON Schema: shown to the model, and enforced before any call.

The same schemas feed Bedrock's Converse API (``bedrock_tool_config``) and the MCP server's
``list_crew_tools``. Ids carry patterns, so a constructed id like ``"BLR->BOM"`` is refused with a
message saying what a real one looks like, before it reaches the engine.
"""

from __future__ import annotations

from typing import Any

from crewops.domain import Rank
from crewops.tools.rows import ENTITIES, ENTITY_FIELDS

CREW_ID = {"type": "string", "pattern": r"^C-[0-9]{4}$", "description": "Crew id, e.g. C-1042."}
PAIRING_ID = {
    "type": "string",
    "pattern": r"^P-[0-9]{4}$",
    "description": "Pairing id, e.g. P-2291.",
}
FLIGHT_ID = {
    "type": "string",
    "pattern": r"^[A-Z]{2}[0-9]{3}-[0-9]{4}-[0-9]{2}-[0-9]{2}$",
    "description": "Flight id: number and date, e.g. DX402-2026-10-13.",
}
FLIGHT_NO = {
    "type": "string",
    "pattern": r"^[A-Z]{2}[0-9]{3}$",
    "description": "Flight number, e.g. DX402. Most fly on several days: pass date with it.",
}
DATE = {
    "type": "string",
    "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
    "description": "ISO date, e.g. 2026-10-13.",
}
UTC = {
    "type": "string",
    "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2})?Z$",
    "description": "UTC instant, e.g. 2026-10-13T06:00:00Z.",
}
AIRCRAFT = {"type": "string", "pattern": r"^VT-[A-Z]{3}$", "description": "Tail, e.g. VT-DXB."}
STATION = {"type": "string", "pattern": r"^[A-Z]{3}$", "description": "Station code, e.g. BLR."}
ROLE = {
    "type": "string",
    "enum": [r.value for r in Rank],
    "description": "The seat to fill.",
}
GATE = {"type": "string", "pattern": r"^[A-Z]{3}-G[0-9]{1,2}$", "description": "e.g. BLR-G1."}

ID_HINTS = {
    "crew_id": "suggest_crew_ids",
    "pairing_id": "suggest_pairing_ids",
    "flight_no": "suggest_flight_nos",
}
"""Which did-you-mean tool to offer when an id has the wrong shape."""


def _obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


def _fields() -> str:
    return "\n".join(f"  {e}: {', '.join(f)}" for e, f in ENTITY_FIELDS.items())


def _tool(name: str, description: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "description": " ".join(description.split()), "input_schema": schema}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "lookup",
        "description": (
            "Rows from the operation (the rostered calendar): crew, flights, pairings, "
            "reserves, certifications, "
            "risk signals, duty clocks, roster exceptions, costs. Filters combine as AND: put "
            "every condition in one call. A value is equality (case-insensitive), a list means "
            "any of, a dict is a range with gte/lte/gt/lt (ISO dates compare correctly). On a "
            "list field (ratings, dates) a value or range matches when any element does: "
            "{'date': '2026-10-14'} finds pairings flying that day. A name matches in full or by "
            "surname. 'count' is always exact: use it for 'how many', never count rows yourself. "
            "Use group_by for 'how many per X', sort_by and limit for 'top N'. Fields:\n"
            + _fields()
        ),
        "input_schema": _obj(
            {
                "entity": {"type": "string", "enum": list(ENTITIES)},
                "filters": {
                    "type": "object",
                    "description": "e.g. {'base': 'BLR', 'rank': 'Captain', "
                    "'valid_to': {'lte': '2026-10-18'}}",
                },
                "sort_by": {"type": "string"},
                "sort_desc": {"type": "boolean", "description": "Highest first."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "group_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 3,
                    "description": "Fields to count by, e.g. ['rank', 'base'].",
                },
            },
            ("entity",),
        ),
    },
    _tool(
        "duty_clock",
        """A crew member's hours as of a date (default: now): that day's own duty and flight
        hours, the 7-day duty total and 28-day flight total with limits and headroom.
        Calendar-day windows, rostered duties included.""",
        _obj({"crew_id": CREW_ID, "date": DATE}, ("crew_id",)),
    ),
    _tool(
        "notification_brief",
        """Every fact a callout message needs for one crew member on one pairing: report time
        and station, legs per day, overnights, and an acknowledge-by time from their
        reachability. Call this before drafting a callout; never write times from memory.""",
        _obj({"crew_id": CREW_ID, "pairing_id": PAIRING_ID}, ("crew_id", "pairing_id")),
    ),
    _tool(
        "check_legality",
        """All seven legality rules for one crew member on one pairing, with the numbers behind
        each verdict. Name the pairing by pairing_id, by a flight on it (flight_id, or flight_no
        with date), or by aircraft with date ('C-5417's VT-DXB duty on 17 Oct'). delay_h tests a
        late departure on the named flight's day (or delay_date, else day one).""",
        _obj(
            {
                "crew_id": CREW_ID,
                "pairing_id": PAIRING_ID,
                "flight_id": FLIGHT_ID,
                "flight_no": FLIGHT_NO,
                "date": DATE,
                "aircraft": AIRCRAFT,
                "delay_h": {"type": "number", "minimum": 0, "maximum": 24},
                "delay_date": DATE,
            },
            ("crew_id",),
        ),
    ),
    _tool(
        "find_options",
        """Every legal way to fill a seat, cheapest first, with cancellation as the baseline,
        and why everyone else is out (the funnel). Name who is out with crew_id (their pairing
        and seat are found for you; add date if they fly several pairings), or name the pairing
        (pairing_id, or flight_id, or flight_no with date) and role. delay_hours judges cover
        against a delayed schedule.""",
        _obj(
            {
                "pairing_id": PAIRING_ID,
                "flight_id": FLIGHT_ID,
                "flight_no": FLIGHT_NO,
                "date": DATE,
                "crew_id": CREW_ID,
                "role": ROLE,
                "delay_hours": {"type": "number", "minimum": 0, "maximum": 24},
                "delay_date": DATE,
            }
        ),
    ),
    _tool(
        "same_pairing",
        """Whether two crew members fly a pairing together this week. Name each by crew id or
        by name, optionally with rank ('Captain A. Nair'). Use this instead of two lookups.""",
        _obj(
            {
                "a": {"type": "string", "minLength": 1, "maxLength": 80},
                "b": {"type": "string", "minLength": 1, "maxLength": 80},
            },
            ("a", "b"),
        ),
    ),
    _tool(
        "ripple",
        """The blast radius of a disruption: uncovered flights, later flights at risk,
        passengers, aircraft and desks. Name it the way it was said: pairing_id; crew_id (with
        date for one day, without for every remaining pairing); a flight; an aircraft with a
        date or window; or a station with from_utc/to_utc (or a date).""",
        _obj(
            {
                "event": _obj(
                    {
                        "pairing_id": PAIRING_ID,
                        "crew_id": CREW_ID,
                        "flight_id": FLIGHT_ID,
                        "flight_no": FLIGHT_NO,
                        "date": DATE,
                        "aircraft": AIRCRAFT,
                        "station": STATION,
                        "from_utc": UTC,
                        "to_utc": UTC,
                    }
                )
            },
            ("event",),
        ),
    ),
    _tool(
        "simulate",
        """What a delay does to one duty day: the duty period against its limit, which rostered
        crew stop being legal and why, and, if no crew could legally fly the whole day, the
        fewest trailing legs to drop and what cancelling them costs. Name the late flight
        (flight_id, or flight_no with date), or the pairing with an optional date.""",
        _obj(
            {
                "event": _obj(
                    {
                        "pairing_id": PAIRING_ID,
                        "flight_id": FLIGHT_ID,
                        "flight_no": FLIGHT_NO,
                        "date": DATE,
                        "delay_hours": {"type": "number", "exclusiveMinimum": 0, "maximum": 24},
                    },
                    ("delay_hours",),
                )
            },
            ("event",),
        ),
    ),
    _tool(
        "joint_plan",
        """Cheapest cover for several seats at once, never giving one person two seats. Each
        event is who is out (crew_id, with date if they fly several pairings) or a pairing with
        a role. Plans with equal totals are equally correct; the count of ties is returned.""",
        _obj(
            {
                "events": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 8,
                    "items": _obj(
                        {
                            "crew_id": CREW_ID,
                            "pairing_id": PAIRING_ID,
                            "flight_id": FLIGHT_ID,
                            "flight_no": FLIGHT_NO,
                            "date": DATE,
                            "role": ROLE,
                        }
                    ),
                }
            },
            ("events",),
        ),
    ),
    _tool(
        "check_gate",
        """Boarding gates. With a flight (flight_id, or flight_no with date): its actual gate,
        whether a claimed gate matches, and with delay_minutes whether the delayed boarding runs
        into the next flight at that gate. With only a gate: who holds it at at_utc (default
        now) and its schedule. With neither: the gate inventory, or with a date, how many were
        in use; station narrows either count.""",
        _obj(
            {
                "flight_id": FLIGHT_ID,
                "flight_no": FLIGHT_NO,
                "date": DATE,
                "boarding_gate_number": GATE,
                "delay_minutes": {"type": "number", "minimum": 0, "maximum": 1440},
                "at_utc": UTC,
                "station": STATION,
            }
        ),
    ),
    _tool(
        "explain_rule",
        """The text and parameters of one legality rule, by id.""",
        _obj(
            {
                "rule_id": {
                    "type": "string",
                    "pattern": r"^RULE-[A-Z]{3,4}-[0-9]{2}$",
                    "description": "e.g. RULE-REST-04.",
                }
            },
            ("rule_id",),
        ),
    ),
    _tool(
        "search_rules",
        """The rules closest to a legality question that names no rule id ('how much sleep
        does a pilot need between duties'). If a rule id is known, use explain_rule. Scores
        rank candidates against each other; they are not a confidence.""",
        _obj(
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 7},
            },
            ("query",),
        ),
    ),
    _tool(
        "suggest_crew_ids",
        """Real crew ids near one that does not exist or has the wrong shape ('C-10'). Only
        candidates to offer the user: never act on one they have not confirmed. ``prefer``:
        ids the conversation was just about, offered first when they exist.""",
        _obj(
            {
                "near": {"type": "string", "minLength": 1, "maxLength": 20},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                "prefer": {
                    "type": "array",
                    "items": {"type": "string", "pattern": "^C-\\d{4}$"},
                    "maxItems": 3,
                },
            },
            ("near",),
        ),
    ),
    _tool(
        "suggest_pairing_ids",
        """Real pairing ids near one that does not exist or has the wrong shape ('P-22'). Only
        candidates to offer the user: never act on one they have not confirmed.""",
        _obj(
            {
                "near": {"type": "string", "minLength": 1, "maxLength": 20},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            ("near",),
        ),
    ),
    _tool(
        "suggest_flight_nos",
        """Real flight numbers near one that does not exist or has the wrong shape ('DX9999').
        Only candidates to offer the user: never act on one they have not confirmed.""",
        _obj(
            {
                "near": {"type": "string", "minLength": 1, "maxLength": 20},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            ("near",),
        ),
    ),
    _tool(
        "list_controllers",
        """The controller desks and the aircraft each covers. Use for any question naming a
        controller (Ananya, Rohit, Divya): controllers are not crew.""",
        _obj({}),
    ),
    _tool(
        "list_open_disruptions",
        """The disruptions being worked right now, from the live store: each one's id, what
        happened, its status and its desk. ``desk`` (desk-1, desk-2, desk-3) keeps only that
        desk's.""",
        _obj({"desk": {"type": "string", "enum": ["desk-1", "desk-2", "desk-3"]}}),
    ),
    _tool(
        "controller_issue_counts",
        """Open disruptions per controller desk, from the live store. A disruption touching
        aircraft of two desks counts at both.""",
        _obj({}),
    ),
]

TOOL_NAMES = frozenset(t["name"] for t in TOOL_SCHEMAS)
SCHEMA_BY_NAME = {t["name"]: t["input_schema"] for t in TOOL_SCHEMAS}


MODEL_KEYWORDS = frozenset({"type", "description", "properties", "required", "enum", "items"})
"""The JSON Schema subset sent to a model. Bounds, patterns and "no extra fields" are enforced
by ``validate_args`` before any call, so a model that ignores or rejects those keywords still
cannot get past them; the model only needs to know the shape and the id formats."""


def for_model(schema: dict[str, Any]) -> dict[str, Any]:
    """``schema`` reduced to ``MODEL_KEYWORDS``, an id pattern folded into the description."""
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "properties":
            out[key] = {name: for_model(spec) for name, spec in value.items()}
        elif key == "items":
            out[key] = for_model(value)
        elif key in MODEL_KEYWORDS:
            out[key] = value
    if "pattern" in schema and "description" not in schema:
        out["description"] = f"Format: {schema['pattern']}"
    return out


def bedrock_tool_config(names: frozenset[str] | set[str] | None = None) -> dict[str, Any]:
    """The ``toolConfig`` for Bedrock's Converse API, limited to ``names`` when given."""
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": {"json": for_model(t["input_schema"])},
                }
            }
            for t in TOOL_SCHEMAS
            if names is None or t["name"] in names
        ]
    }
