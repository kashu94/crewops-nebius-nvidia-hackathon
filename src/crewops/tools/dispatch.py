"""Run one tool call: check who is asking, check the arguments, call, record.

``dispatch`` never raises. A failure reaches the model as an error code and a message it can
act on (ask the user, try another tool), never as an exception that ends the turn.

Who may call what is decided here, in code, from the caller's verified roles:

* controller, approver and auditor: every tool;
* reporter (station and ground staff): gates, rules and desks;
* admin and super admin: desks, issue counts and rules. They configure; they do not
  look into crew records;
* crew: their own duty clock, their own callout brief, their own legality, and the rules.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from crewops.domain import Role
from crewops.engine import UnknownEntity
from crewops.tools.errors import (
    FORBIDDEN,
    INTERNAL,
    INVALID_ARGUMENT,
    UNRESOLVED_ENTITY,
    ToolError,
)
from crewops.tools.port import CrewOpsTools
from crewops.tools.rows import plain
from crewops.tools.schemas import ID_HINTS, SCHEMA_BY_NAME, TOOL_NAMES

log = logging.getLogger(__name__)

RULE_TOOLS = frozenset({"explain_rule", "search_rules"})
ROLE_TOOLS: dict[Role, frozenset[str]] = {
    Role.CONTROLLER: TOOL_NAMES,
    Role.APPROVER: TOOL_NAMES,
    Role.AUDITOR: TOOL_NAMES,
    Role.REPORTER: RULE_TOOLS | {"check_gate", "list_controllers", "suggest_flight_nos"},
    Role.ADMIN: RULE_TOOLS
    | {"list_controllers", "controller_issue_counts", "list_open_disruptions"},
    Role.SUPER_ADMIN: RULE_TOOLS
    | {"list_controllers", "controller_issue_counts", "list_open_disruptions"},
    Role.CREW: RULE_TOOLS | {"duty_clock", "notification_brief", "check_legality"},
}
CREW_SELF_ONLY = frozenset({"duty_clock", "notification_brief", "check_legality"})
"""Tools crew may call only about themselves."""
CREW_OWN_RECORDS = frozenset({"crew", "certifications", "pairing_crew"})
"""What crew may look up, and only filtered to their own crew id: their profile, their
certificates and their trips."""


@dataclass(frozen=True, slots=True)
class Caller:
    """Who is asking, as verified at sign-in. Never taken from the conversation."""

    user_id: str
    roles: frozenset[Role]
    crew_id: str | None = None
    """The caller's own crew id, when they are crew."""
    desk: str | None = None
    """The controller's desk, when they are a controller."""


def allowed_tools(caller: Caller) -> frozenset[str]:
    """Every tool any of the caller's roles grants. Only these are shown to the model."""
    out: frozenset[str] = frozenset()
    for role in caller.roles:
        out |= ROLE_TOOLS.get(role, frozenset())
    return out


def own_records(caller: Caller, name: str, args: Mapping[str, Any]) -> bool:
    """A crew member looking up their own profile, certificates or trips."""
    return (
        Role.CREW in caller.roles
        and name == "lookup"
        and bool(caller.crew_id)
        and args.get("entity") in CREW_OWN_RECORDS
        and (args.get("filters") or {}).get("crew_id") == caller.crew_id
    )


def authorize(caller: Caller, name: str, args: Mapping[str, Any]) -> None:
    staff = any(name in ROLE_TOOLS.get(r, ()) for r in caller.roles if r is not Role.CREW)
    if staff:
        return
    if Role.CREW in caller.roles and name in ROLE_TOOLS[Role.CREW]:
        if name not in CREW_SELF_ONLY:
            return
        if caller.crew_id and args.get("crew_id") == caller.crew_id:
            return
        raise ToolError(FORBIDDEN, f"crew may use {name} only about themselves")
    if own_records(caller, name, args):
        return  # "is my medical valid?": their own records, filtered to them
    raise ToolError(FORBIDDEN, f"your role may not use {name}")


# --------------------------------------------------------------------------
# Argument validation: the subset of JSON Schema the tool schemas use
# --------------------------------------------------------------------------

_TYPES = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, Mapping),
    "array": lambda v: isinstance(v, list),
}


def _bad(path: str, why: str) -> ToolError:
    return ToolError(INVALID_ARGUMENT, f"{path}: {why}")


def _check(
    value: Any, spec: Mapping[str, Any], path: str, hints: frozenset[str] | None = None
) -> None:
    kind = spec.get("type")
    if kind and not _TYPES[kind](value):
        raise _bad(path, f"expected {kind}, got {type(value).__name__}")
    if "enum" in spec and value not in spec["enum"]:
        raise _bad(path, f"{value!r} is not one of {', '.join(map(str, spec['enum']))}")
    if isinstance(value, str):
        if (n := spec.get("minLength")) is not None and len(value) < n:
            raise _bad(path, f"at least {n} characters")
        if (n := spec.get("maxLength")) is not None and len(value) > n:
            raise _bad(path, f"at most {n} characters")
        if (pattern := spec.get("pattern")) and not _matches(pattern, value):
            leaf = path.rsplit(".", 1)[-1]
            tool = ID_HINTS.get(leaf)
            usable = tool is not None and (hints is None or tool in hints)
            hint = f" If unsure, call {tool}." if usable else ""
            raise ToolError(
                UNRESOLVED_ENTITY,
                f"{path}={value!r} is not a valid {leaf} ({spec.get('description', pattern)}). "
                f"Look it up rather than constructing it.{hint}",
            )
    if _TYPES["number"](value):
        if not math.isfinite(value):
            raise _bad(path, "must be a finite number")
        if (n := spec.get("minimum")) is not None and value < n:
            raise _bad(path, f"must be at least {n}")
        if (n := spec.get("exclusiveMinimum")) is not None and value <= n:
            raise _bad(path, f"must be more than {n}")
        if (n := spec.get("maximum")) is not None and value > n:
            raise _bad(path, f"must be at most {n}")
    if isinstance(value, list):
        if (n := spec.get("minItems")) is not None and len(value) < n:
            raise _bad(path, f"at least {n} items")
        if (n := spec.get("maxItems")) is not None and len(value) > n:
            raise _bad(path, f"at most {n} items")
        if items := spec.get("items"):
            for i, v in enumerate(value):
                _check(v, items, f"{path}[{i}]", hints)
    if isinstance(value, Mapping) and "properties" in spec:
        props = spec["properties"]
        if spec.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(props))
            if unknown:
                allowed = ", ".join(props) or "none"
                raise _bad(path, f"unknown {', '.join(map(repr, unknown))}; allowed: {allowed}")
        for key in spec.get("required", ()):
            if key not in value or value[key] is None:
                raise _bad(path, f"{key!r} is required")
        for key, v in value.items():
            if v is not None and key in props:
                _check(v, props[key], f"{path}.{key}", hints)


def _matches(pattern: str, value: str) -> bool:
    return re.fullmatch(pattern, value) is not None


ID_KEYS = frozenset(
    {
        "crew_id",
        "pairing_id",
        "flight_id",
        "flight_no",
        "aircraft",
        "station",
        "rule_id",
        "boarding_gate_number",
    }
)
"""Ids are upper case in the data; "c-1042" said or typed is the same id, not a new one."""


def _normalize(value: Any, key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        return {k: _normalize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v, key) for v in value]
    if key in ID_KEYS and isinstance(value, str):
        return value.strip().upper()
    return value


def validate_args(name: str, args: Any, allowed: frozenset[str] | None = None) -> dict[str, Any]:
    """The arguments, ids upper-cased, checked against the tool's schema, nulls dropped.

    ``allowed`` (the caller's tools) limits which did-you-mean tools an error may point to.
    Filter values inside ``lookup`` are left as given; they compare case-insensitively.
    """
    if not isinstance(args, Mapping):
        raise _bad(name, "arguments must be an object")
    args = {k: (v if k == "filters" else _normalize(v, k)) for k, v in args.items()}
    _check(args, SCHEMA_BY_NAME[name], name, allowed)
    return {k: v for k, v in args.items() if v is not None}


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TraceEntry:
    tool: str
    args: Any
    result: Any = None
    error_code: str | None = None
    error: str | None = None
    ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error_code is None

    def to_dict(self) -> dict[str, Any]:
        if self.ok:
            return {"tool": self.tool, "ok": True, "result": self.result}
        return {"tool": self.tool, "ok": False, "error_code": self.error_code, "error": self.error}


def dispatch(tools: CrewOpsTools, caller: Caller, name: str, args: Any) -> TraceEntry:
    """Run one tool call for ``caller``. Never raises."""
    started = time.perf_counter()

    def done(result: Any = None, err: ToolError | None = None) -> TraceEntry:
        ms = int((time.perf_counter() - started) * 1000)
        if err is None:
            return TraceEntry(name, args, result=result, ms=ms)
        return TraceEntry(name, args, error_code=err.code, error=err.message, ms=ms)

    if name not in TOOL_NAMES:
        return done(err=ToolError(UNRESOLVED_ENTITY, f"unknown tool {name!r}"))
    try:
        clean = validate_args(name, args, allowed_tools(caller))
        authorize(caller, name, clean)
        result = getattr(tools, name)(**clean)
        return done(result=plain(result))
    except ToolError as exc:
        return done(err=exc)
    except UnknownEntity as exc:
        return done(err=ToolError(UNRESOLVED_ENTITY, str(exc)))
    except Exception as exc:  # a tool bug must not end the turn, and must not be hidden
        log.exception("tool %s failed", name)
        return done(err=ToolError(INTERNAL, f"{type(exc).__name__}: {exc}"))
