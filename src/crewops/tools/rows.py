"""The operation as plain rows, for the ``lookup`` tool, and the filters over them.

Rows use the dataset's own field names (``disruption_risk_score``, ``oncall_window_utc``),
the vocabulary controllers and the data files share. Every value is JSON-safe: dates and
instants as ISO strings, enums as their values.

Counting, sorting and grouping happen here, never in the model. A model eyeballing fifty
rows miscounts; ``len(rows)`` does not.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any

from crewops.ingestion.seed import SeedData
from crewops.store.items import record_dict
from crewops.tools.errors import INVALID_ARGUMENT, UNRESOLVED_ENTITY, ToolError

ENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    "crew": (
        "crew_id",
        "name",
        "rank",
        "base",
        "ratings",
        "seniority",
        "reachability_minutes",
        "status",
        "airline",
        "experience_years",
        "command_qualified",
        "right_seat_qualified",
    ),
    "flights": (
        "flight_id",
        "flight_no",
        "date",
        "dep_station",
        "arr_station",
        "dep_utc",
        "arr_utc",
        "block_hours",
        "aircraft",
        "aircraft_type",
        "seats",
        "booked",
        "cancelled",
    ),
    "pairings": ("pairing_id", "aircraft", "dates", "days", "crew"),
    "pairing_crew": ("pairing_id", "aircraft", "dates", "crew_id", "role", "name"),
    "pairing_days": ("pairing_id", "aircraft", "date", "flights", "report_utc", "release_utc"),
    "reserves": ("crew_id", "name", "rank", "base", "dates", "oncall_window_utc"),
    "certifications": ("crew_id", "cert_type", "valid_from", "valid_to"),
    "risk_signals": ("crew_id", "as_of_utc", "disruption_risk_score", "drivers"),
    "duty_clocks": (
        "crew_id",
        "as_of_utc",
        "duty_hours_7d",
        "flight_hours_28d",
        "last_rest_ended",
    ),
    "roster_exceptions": ("crew_id", "date", "rule", "note"),
    "costs": (
        "currency",
        "reserve_callout_pilot",
        "reserve_callout_cabin",
        "dayoff_callout_pilot",
        "dayoff_callout_cabin",
        "deadhead_positioning",
        "delay_cost_per_duty_hour",
        "cancellation_per_flight",
        "hotel_overnight",
        "seniority_allowance_pilot",
        "seniority_allowance_cabin",
        "travel_per_10_minutes",
    ),
}
"""The fields of every entity, in the order rows carry them. Listed in the tool's
description so the model never has to guess a column name."""

ENTITIES = tuple(ENTITY_FIELDS)

FIELD_ALIASES: dict[str, str] = {
    "departure": "dep_station",
    "origin": "dep_station",
    "from": "dep_station",
    "departure_station": "dep_station",
    "destination": "arr_station",
    "arrival": "arr_station",
    "to": "arr_station",
    "arrival_station": "arr_station",
    "date": "dates",
    "crew": "crew_id",
    "employee_id": "crew_id",
    "pairing": "pairing_id",
    "flight": "flight_no",
    "flight_number": "flight_no",
    "tail": "aircraft",
    "registration": "aircraft",
    "expiry": "valid_to",
    "expiry_date": "valid_to",
    "expires": "valid_to",
    "valid_until": "valid_to",
    "certification": "cert_type",
    "home_base": "base",
    "station": "base",
    "position": "rank",
    "rating": "ratings",
    "risk": "disruption_risk_score",
    "risk_score": "disruption_risk_score",
}
"""Near-miss field names mapped to real ones, only where the meaning is unambiguous. An alias
applies only when the entity has no field of that exact name: ``date`` on pairings means
"flies on that date" (``dates`` contains it), while flights keep their own ``date``."""

RANGE_OPS = ("gte", "lte", "gt", "lt")

NESTED_FIELDS = {"pairings": {"days", "crew"}, "reserves": {"oncall_window_utc"}}
"""Fields holding objects: shown in rows, but not something to filter, sort or group on."""


def plain(value: Any) -> Any:
    """A JSON-safe copy: ISO dates and instants, enum values, lists for tuples."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        items = [plain(v) for v in value]
        return sorted(items) if isinstance(value, set | frozenset) else items
    return value


def build_rows(
    seed: SeedData, cancelled: frozenset[str] = frozenset()
) -> dict[str, list[dict[str, Any]]]:
    """Every entity's rows, built once per loaded operation. ``cancelled``: flights an
    approved plan cancelled, marked on their rows."""
    crew = []
    for c in seed.crew:
        row = plain(record_dict(c)) | {
            "airline": c.airline,
            "experience_years": c.experience_years,
            "command_qualified": c.command_qualified,
            "right_seat_qualified": c.right_seat_qualified,
        }
        crew.append({k: row[k] for k in ENTITY_FIELDS["crew"]})
    identity = {c["crew_id"]: c for c in crew}
    pairings = []
    pairing_crew = []
    pairing_days = []
    for p in seed.pairings:
        row = plain(record_dict(p))
        row["dates"] = [d["date"] for d in row["days"]]
        pairings.append({k: row[k] for k in ENTITY_FIELDS["pairings"]})
        for seat in row["crew"]:
            who = identity.get(seat["crew_id"], {})
            pairing_crew.append(
                {
                    "pairing_id": p.pairing_id,
                    "aircraft": p.aircraft,
                    "dates": row["dates"],
                    "crew_id": seat["crew_id"],
                    "role": seat["role"],
                    "name": who.get("name"),
                }
            )
        for day in row["days"]:
            pairing_days.append({"pairing_id": p.pairing_id, "aircraft": p.aircraft, **day})

    reserves = []
    for r in seed.reserves:
        row = plain(record_dict(r))
        who = identity.get(r.crew_id, {})
        reserves.append(
            {
                "crew_id": row["crew_id"],
                "name": who.get("name"),
                "rank": who.get("rank"),
                "base": row["base"],
                "dates": row["dates"],
                "oncall_window_utc": row["oncall_window_utc"],
            }
        )

    clocks = []
    for c in seed.duty_clocks:
        row = plain(record_dict(c))
        clocks.append({k: row[k] for k in ENTITY_FIELDS["duty_clocks"]})

    return {
        "crew": crew,
        "flights": [
            plain(record_dict(f)) | {"cancelled": f.flight_id in cancelled} for f in seed.flights
        ],
        "pairings": pairings,
        "pairing_crew": pairing_crew,
        "pairing_days": pairing_days,
        "reserves": reserves,
        "certifications": [plain(record_dict(c)) for c in seed.certifications],
        "risk_signals": [plain(record_dict(r)) for r in seed.risk_signals],
        "duty_clocks": clocks,
        "roster_exceptions": [plain(record_dict(e)) for e in seed.exceptions],
        "costs": [plain(record_dict(seed.costs))],
    }


def field_for(entity: str, key: str, nested_ok: bool = False) -> str:
    """The real column ``key`` names, or an error listing the valid ones."""
    known = ENTITY_FIELDS[entity]
    column = key if key in known else FIELD_ALIASES.get(key.strip().lower().replace(" ", "_"))
    if column not in known:
        raise ToolError(
            UNRESOLVED_ENTITY, f"{entity} has no field {key!r}. Valid fields: {', '.join(known)}"
        )
    if not nested_ok and column in NESTED_FIELDS.get(entity, ()):
        flat = [f for f in known if f not in NESTED_FIELDS[entity]]
        raise ToolError(
            INVALID_ARGUMENT,
            f"{entity}.{column} holds nested records; use one of {', '.join(flat)}, or the "
            "pairing_crew and pairing_days entities",
        )
    return column


def _scalar(value: Any) -> bool:
    return isinstance(value, str | int | float | bool)


def _check_value(column: str, value: Any, in_list: bool = False) -> None:
    """A filter value: a scalar, a range, or a non-empty list of those. Anything else would
    either crash or silently match every row."""
    if isinstance(value, Mapping):
        bad = sorted(set(value) - set(RANGE_OPS))
        if bad or not value:
            raise ToolError(
                INVALID_ARGUMENT,
                f"filter on {column!r}: operators must be among {', '.join(RANGE_OPS)}"
                + (f", got {', '.join(map(repr, bad))}" if bad else ", got none"),
            )
        if not all(_scalar(b) and not isinstance(b, bool) for b in value.values()):
            raise ToolError(
                INVALID_ARGUMENT, f"filter on {column!r}: range bounds must be numbers or dates"
            )
        return
    if isinstance(value, list) and not in_list:
        if not value:
            raise ToolError(INVALID_ARGUMENT, f"filter on {column!r}: empty list matches nothing")
        for v in value:
            _check_value(column, v, in_list=True)
        return
    if not _scalar(value):
        raise ToolError(
            INVALID_ARGUMENT,
            f"filter on {column!r}: expected a value, a list of values, or a range",
        )


def resolve_filters(entity: str, filters: Mapping[str, Any]) -> dict[str, Any]:
    """Filter keys mapped onto real columns; range operators checked, never ignored.

    A dropped bound would silently widen the result, so an unknown operator is an error.
    """
    out: dict[str, Any] = {}
    for key, value in filters.items():
        column = field_for(entity, key)
        _check_value(column, value)
        if column in out:
            raise ToolError(INVALID_ARGUMENT, f"two filters name the same field {column!r}")
        out[column] = value
    return out


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _equal(have: Any, want: Any) -> bool:
    """Equal as numbers when both are numbers, else as case-insensitive text."""
    if isinstance(have, bool) or isinstance(want, bool):
        return have == want
    a, b = _number(have), _number(want)
    if a is not None and b is not None and not (isinstance(have, str) and isinstance(want, str)):
        return a == b
    return str(have).strip().lower() == str(want).strip().lower()


def _compare(have: Any, op: str, bound: Any) -> bool:
    """Numbers compare as numbers, text (ISO dates and times) as text. Mixing the two would
    give an ordering that means nothing, so it is an error, not a silent count."""
    a, b = _number(have), _number(bound)
    if a is None or b is None:
        if not (isinstance(have, str) and isinstance(bound, str)) or (a is None) != (b is None):
            raise ToolError(
                INVALID_ARGUMENT,
                f"cannot compare {have!r} with {bound!r}: range bounds must be the same kind "
                "of value as the field (a number, or an ISO date)",
            )
        a, b = have, bound
    return {"gte": a >= b, "lte": a <= b, "gt": a > b, "lt": a < b}[op]


def _name_matches(have: Any, want: Any) -> bool:
    """A crew name matches in full ("A. Nair") or by surname alone ("Nair")."""
    if not isinstance(have, str) or not isinstance(want, str):
        return False
    full, wanted = have.strip().lower(), want.strip().lower()
    return bool(wanted) and (full == wanted or full.split()[-1:] == [wanted])


def row_matches(row: Mapping[str, Any], column: str, want: Any) -> bool:
    """One row against one filter: equality, any of (a list), or a range (a dict).

    Against a list-valued column (``ratings``, ``dates``), a value or a range matches when
    any element does.
    """
    have = row.get(column)
    if isinstance(want, Mapping):
        if have is None:
            return False
        values = have if isinstance(have, list) else [have]
        return any(
            _scalar(h) and all(_compare(h, op, bound) for op, bound in want.items()) for h in values
        )
    if isinstance(want, list):
        return any(row_matches(row, column, w) for w in want)
    if isinstance(have, list):
        return any(_equal(h, want) for h in have)
    if column == "name":
        return _name_matches(have, want)
    return _equal(have, want)


def _sort_key(value: Any) -> tuple[int, Any]:
    number = _number(value)
    if number is not None and not isinstance(value, str):
        return (0, number)
    return (1, str(value))


def sort_rows(rows: list[dict[str, Any]], column: str, desc: bool) -> list[dict[str, Any]]:
    """Sorted on ``column``; rows without a value always go last, whichever direction."""
    present = [r for r in rows if r.get(column) is not None]
    missing = [r for r in rows if r.get(column) is None]
    present.sort(key=lambda r: _sort_key(r[column]), reverse=desc)
    return present + missing


def group_rows(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    """One row per distinct combination of ``columns``, with its count."""
    counts: dict[tuple[Any, ...], int] = {}
    for r in rows:
        key = tuple(tuple(v) if isinstance(v, list) else v for v in (r.get(c) for c in columns))
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: tuple(_sort_key(v) for v in kv[0]))
    return [
        {c: list(v) if isinstance(v, tuple) else v for c, v in zip(columns, key, strict=True)}
        | {"count": n}
        for key, n in ordered
    ]
