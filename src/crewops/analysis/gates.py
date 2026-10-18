"""Gates and stands: where each departure boards when a gate closes or a flight runs late.

A ``GateSlot`` holds a gate from boarding start (or from the aircraft's arrival when it
parks overnight) until departure. A flight loses its gate in two ways. The gate closes (jet
bridge fault, security sweep), and every departure holding it during the closure moves to a
gate at the same station that is free for its whole slot. Or the flight before it at that
gate leaves late and is still there when boarding should start, so the next one moves.

If no ordinary gate is free it gets the station's emergency gate; each station keeps one or
two back for this (``emergency_gates.json``). Only when those are taken too does it board
from a remote stand by bus, which takes ``REMOTE_BOARDING`` longer, and that delay is checked
against crew duty like any other. Deterministic: gates are tried in order and each move is
booked before the next is placed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from crewops.domain import Flight, GateSlot

REMOTE_STAND = "remote stand"
REMOTE_BOARDING = timedelta(minutes=15)
"""Bus boarding from a remote stand takes this much longer than boarding at a gate."""
NEXT_WITHIN = timedelta(hours=3)
"""The next departure at a late flight's gate is only told if it boards within this long of
the late one leaving."""


@dataclass(frozen=True, slots=True)
class StandMove:
    flight_id: str
    flight_no: str
    from_gate: str
    to_gate: str
    """Another gate at the station, or ``REMOTE_STAND``."""
    start_utc: datetime
    end_utc: datetime
    booked: int | None
    delay_minutes: int
    """Added to the departure: bus boarding from a remote stand, else 0."""
    why: str
    alternatives: tuple[str, ...] = ()
    """Other gates at the station free for the whole slot, for the controller to choose."""
    emergency: bool = False
    """It took one of the station's emergency gates."""

    def to_dict(self) -> dict[str, object]:
        return {
            "flight_id": self.flight_id,
            "flight_no": self.flight_no,
            "from_gate": self.from_gate,
            "to_gate": self.to_gate,
            "boarding_from_utc": _iso(self.start_utc),
            "departs_utc": _iso(self.end_utc),
            "booked": self.booked,
            "delay_minutes": self.delay_minutes,
            "why": self.why,
            "alternatives": list(self.alternatives),
            "emergency": self.emergency,
        }


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def station_of(gate: str) -> str:
    return gate.split("-")[0]


def station_gates(gates: Iterable[GateSlot], station: str) -> list[str]:
    """Every gate on record at a station, in order (G2 before G10)."""
    found = {g.gate for g in gates if station_of(g.gate) == station}
    return sorted(found, key=lambda g: (len(g), g))


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


class GateBoard:
    """Who holds which gate when, with moves applied as they are made."""

    def __init__(
        self,
        gates: Iterable[GateSlot],
        flights: Mapping[str, Flight],
        emergency: Mapping[str, Sequence[str]] | None = None,
        cancelled: frozenset[str] = frozenset(),
    ) -> None:
        every = sorted(gates, key=lambda g: (g.start_utc, g.flight_id))
        self.gates_of = every
        """Every slot, for the gates each station has, whoever holds them."""
        self.slots = [g for g in every if g.flight_id not in cancelled]
        """The slots of flights still operating: a cancelled flight holds no gate."""
        self.flights = flights
        self.emergency = {k: tuple(v) for k, v in (emergency or {}).items()}
        self.held: dict[str, list[tuple[datetime, datetime, str]]] = {}
        for g in self.slots:
            self.held.setdefault(g.gate, []).append((g.start_utc, g.end_utc, g.flight_id))

    def slot_of(self, flight_id: str) -> GateSlot | None:
        return next((g for g in self.slots if g.flight_id == flight_id), None)

    def holding(self, gate: str, start: datetime, end: datetime) -> list[str]:
        """Flights holding ``gate`` at any moment in ``[start, end)``."""
        return [f for s, e, f in self.held.get(gate, []) if _overlaps(s, e, start, end)]

    def extend(self, flight_id: str, gate: str, until: datetime) -> None:
        """A late departure keeps its gate until it leaves."""
        self.held[gate] = [
            (s, max(e, until) if f == flight_id else e, f) for s, e, f in self.held[gate]
        ]

    def release(self, flight_id: str, gate: str) -> None:
        self.held[gate] = [x for x in self.held.get(gate, []) if x[2] != flight_id]

    def move(
        self,
        slot: GateSlot,
        why: str,
        closed: set[str],
        until: datetime | None = None,
        current: str | None = None,
    ) -> StandMove:
        """Move one departure off ``current`` (its own gate unless an earlier move changed
        it) to the first gate at its station free for its whole slot (to ``until`` when it
        now leaves later), or to a remote stand."""
        end = until or slot.end_utc
        here = current or slot.gate
        self.release(slot.flight_id, here)
        station = station_of(slot.gate)
        ordinary = [
            gate
            for gate in station_gates(self.gates_of, station)
            if gate not in closed and gate != here and not self.holding(gate, slot.start_utc, end)
        ]
        held_back = [
            gate
            for gate in self.emergency.get(station, ())
            if gate not in closed and not self.holding(gate, slot.start_utc, end)
        ]
        target = (ordinary or held_back or [None])[0]
        flight = self.flights.get(slot.flight_id)
        if target is not None:
            self.held.setdefault(target, []).append((slot.start_utc, end, slot.flight_id))
        return StandMove(
            flight_id=slot.flight_id,
            flight_no=flight.flight_no if flight else slot.flight_id.split("-")[0],
            from_gate=here,
            to_gate=target or REMOTE_STAND,
            start_utc=slot.start_utc,
            end_utc=end,
            booked=flight.booked if flight else None,
            delay_minutes=0 if target else int(REMOTE_BOARDING.total_seconds() // 60),
            why=why,
            alternatives=tuple(g for g in (*ordinary, *held_back) if g != target),
            emergency=bool(target) and not ordinary,
        )


def close_gate(
    board: GateBoard, gate: str, start: datetime, end: datetime, reason: str
) -> list[StandMove]:
    """Every departure holding ``gate`` during the closure, moved elsewhere."""
    hit = [
        s for s in board.slots if s.gate == gate and _overlaps(s.start_utc, s.end_utc, start, end)
    ]
    return [board.move(s, f"{gate} closed ({reason})", {gate}) for s in hit]


@dataclass(frozen=True, slots=True)
class NextAtGate:
    """The departure due at a gate after a late one, and whether it is still free for it."""

    flight_id: str
    flight_no: str
    gate: str
    boarding_from_utc: datetime
    buffer_minutes: int
    """Minutes between the late departure leaving and this one's boarding; negative when
    they clash."""

    def to_dict(self) -> dict[str, object]:
        return {
            "flight_id": self.flight_id,
            "flight_no": self.flight_no,
            "gate": self.gate,
            "boarding_from_utc": _iso(self.boarding_from_utc),
            "buffer_minutes": self.buffer_minutes,
        }


def late_departures(
    board: GateBoard, late: list[tuple[str, timedelta]]
) -> tuple[list[StandMove], list[NextAtGate]]:
    """Each late departure keeps its gate until it leaves. The next departure due at that
    gate is named (its gate agent and passengers get told) and moved if the gate is still
    taken when it should start boarding.

    Departures that are late themselves (the same aircraft's later legs, other flights in the
    same disruption) board later too, so they clash only on their own late times. Once moved,
    a departure is held at its new gate and never moved onto the gate it is already at."""
    delay_of = dict(late)
    gate_of: dict[str, str] = {}  # where each moved departure now boards
    moves: list[StandMove] = []
    nexts: list[NextAtGate] = []
    for flight_id, delay in late:
        slot = board.slot_of(flight_id)
        if slot is None:
            continue
        gate = gate_of.get(flight_id, slot.gate)
        if gate == REMOTE_STAND:
            continue  # boards by bus: it holds no gate
        leaves = slot.end_utc + delay
        board.extend(flight_id, gate, leaves)
        after = sorted(
            (
                s
                for s in board.slots
                if gate_of.get(s.flight_id, s.gate) == gate
                and s.flight_id != flight_id
                and s.start_utc >= slot.start_utc
            ),
            key=lambda s: s.start_utc + delay_of.get(s.flight_id, timedelta(0)),
        )
        if not after:
            continue
        late_no = board.flights[flight_id].flight_no if flight_id in board.flights else flight_id
        nxt = after[0]
        nxt_boards = nxt.start_utc + delay_of.get(nxt.flight_id, timedelta(0))
        if nxt_boards < leaves + NEXT_WITHIN and nxt.flight_id not in {n.flight_id for n in nexts}:
            flight = board.flights.get(nxt.flight_id)
            nexts.append(
                NextAtGate(
                    nxt.flight_id,
                    flight.flight_no if flight else nxt.flight_id.split("-")[0],
                    gate,
                    nxt_boards,
                    int((nxt_boards - leaves).total_seconds() // 60),
                )
            )
        for s in after:
            own = delay_of.get(s.flight_id, timedelta(0))
            if s.start_utc + own >= leaves:
                continue
            move = board.move(
                s,
                f"{gate} still held by late {late_no}",
                set(),
                until=s.end_utc + own,
                current=gate,
            )
            gate_of[s.flight_id] = move.to_gate
            moves = [m for m in moves if m.flight_id != s.flight_id] + [move]
    return moves, nexts


__all__ = [
    "NEXT_WITHIN",
    "REMOTE_BOARDING",
    "REMOTE_STAND",
    "GateBoard",
    "NextAtGate",
    "StandMove",
    "close_gate",
    "late_departures",
    "station_gates",
    "station_of",
]
