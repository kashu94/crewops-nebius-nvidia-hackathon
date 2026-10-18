"""The airlines crew control works for, and the stations' emergency gates.

Three fictional airlines share the operations centre, and controllers work across all of
them. Each owns its aircraft, its flight numbers (airline code plus three digits) and its
crew. Cover only ever comes from the same airline, since crew are employed, trained and
paid by one.

Each station holds emergency gates back for disruptions. A departure goes to one when every
ordinary gate is taken, before falling back to a remote stand.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from crewops.domain.models import STATION_RE, DataError, _list, _str

AIRLINE_CODE_RE = re.compile(r"^[A-Z]{2}$")
EMERGENCY_GATE_RE = re.compile(r"^[A-Z]{3}-E\d{1,2}$")


@dataclass(frozen=True, slots=True)
class Airline:
    code: str
    name: str
    aircraft: tuple[str, ...]
    color: str

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], ctx: str = "airline") -> Airline:
        code = _str(d, "code", ctx)
        if not AIRLINE_CODE_RE.match(code):
            raise DataError(f"{ctx}.code: two capital letters, got {code!r}")
        color = _str(d, "color", ctx)
        if not re.match(r"^#[0-9a-fA-F]{6}$", color):
            raise DataError(f"{ctx}.color: a #rrggbb colour, got {color!r}")
        return cls(
            code=code,
            name=_str(d, "name", ctx),
            aircraft=tuple(str(a) for a in _list(d, "aircraft", ctx)),
            color=color,
        )


@dataclass(frozen=True, slots=True)
class EmergencyGate:
    gate: str
    station: str

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], ctx: str = "emergency_gate") -> EmergencyGate:
        gate, station = _str(d, "gate", ctx), _str(d, "station", ctx)
        if not EMERGENCY_GATE_RE.match(gate):
            raise DataError(f"{ctx}.gate: like BLR-E1, got {gate!r}")
        if not STATION_RE.match(station) or not gate.startswith(f"{station}-"):
            raise DataError(f"{ctx}.station: the gate's own station, got {station!r}")
        return cls(gate=gate, station=station)


__all__ = [
    "AIRLINE_CODE_RE",
    "EMERGENCY_GATE_RE",
    "Airline",
    "EmergencyGate",
]
