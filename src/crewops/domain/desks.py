"""Controller desks: who plans which disruption.

Three controllers share the operation. A new disruption goes to whoever has the fewest
open ones when it's analysed (ties go to fewest overall, then desk order), so load evens
out and the reporter never picks. Any desk can read and suggest on any disruption, but only
the owning desk plans it. Cover is still checked across desks, because two disruptions on
different desks can want the same reserve.

Disruptions analysed before desks were assigned fall back to the desk watching the aircraft
they touch (``Desk.aircraft``).

Controllers don't fly; their names aren't on the crew roster.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Desk:
    desk_id: str
    controller: str
    aircraft: tuple[str, ...]


DESKS: tuple[Desk, ...] = (
    Desk("desk-1", "Ananya Iyer", ("VT-DXA", "VT-DXB")),
    Desk("desk-2", "Rohit Malhotra", ("VT-DXC", "VT-DXD")),
    Desk("desk-3", "Divya Rao", ("VT-DXE", "VT-DXF")),
)


def desks_for(aircraft: set[str] | frozenset[str]) -> list[Desk]:
    """The desks covering any of ``aircraft``, in desk order."""
    return [d for d in DESKS if aircraft & set(d.aircraft)]


def desk(desk_id: str | None) -> Desk | None:
    return next((d for d in DESKS if d.desk_id == desk_id), None)


def assign_desk(open_counts: Mapping[str, int], totals: Mapping[str, int]) -> Desk:
    """Fewest open, then fewest overall, then desk order."""
    return min(
        DESKS,
        key=lambda d: (open_counts.get(d.desk_id, 0), totals.get(d.desk_id, 0), d.desk_id),
    )


def owning_desks(
    analysis: Mapping[str, Any], aircraft_of_pairing: Callable[[str], str | None]
) -> list[Desk]:
    """The assigned desk, or for older analyses without one, the desks watching the aircraft
    it touches."""
    assigned = desk(analysis.get("desk"))
    if assigned is not None:
        return [assigned]
    seats = analysis.get("seats") or []
    if seats:
        aircraft = {aircraft_of_pairing(s["pairing_id"]) for s in seats} - {None}
    else:
        aircraft = set(analysis.get("aircraft") or [])
    return desks_for(aircraft)  # type: ignore[arg-type]
