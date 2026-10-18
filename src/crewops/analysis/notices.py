"""Who must be told about a disruption, and what each of them is told.

Crew control is always told (the desk alert). The rest depends on what broke:

==========================  ==================================================================
Report                      Told, besides crew control
==========================  ==================================================================
Sick crew (one or more)     only if a seat has no legal cover: operations monitoring and
                            customer care, as the flight may not operate
Certificate lapse           the crew member (on their CrewOps page) and training and crew
                            records, who book the renewal
Flight delay                flight information (screens, website, app), operations
Aircraft fault              monitoring (tells the captain), the departure station's duty
                            manager and gate control, the gate agent of the next departure
                            at the same gate, and customer care for the booked passengers;
                            for a fault, maintenance control first
Station closure             that station's duty manager, the duty managers at the other end
                            of every affected flight, operations monitoring, flight
                            information and customer care
Gate closure, gate change   the station's gate control and duty manager, flight information
                            and customer care; operations monitoring when a departure slips
==========================  ==================================================================

Email alerts carry flights, gates, stations and times, never a crew member's name or id:
those are read in CrewOps, signed in. Each notice names the person on duty for the team,
from the contact directory, so the record shows who was told, not only which team.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from crewops.analysis.gates import REMOTE_STAND, NextAtGate, StandMove
from crewops.domain import Flight, Report, ReportType
from crewops.domain.contacts import NETWORK, TEAMS, Contact, Team
from crewops.engine.world import World

CREW = "crew"
"""The audience of an in-app notice to one crew member."""


@dataclass(frozen=True, slots=True)
class LateLeg:
    """A departure that now leaves late: the reported one, or a later leg on the same
    aircraft that day, which slips with it (as the duty check assumes)."""

    flight_id: str
    minutes: int


def _hhmm(value: datetime) -> str:
    return value.strftime("%H:%MZ %d %b")


class Directory:
    def __init__(self, contacts: Iterable[Contact]) -> None:
        self.by_key = {(c.team, c.scope): c for c in contacts}

    def notice(
        self, team: Team, scope: str, why: str, message: str, about: str | None = None
    ) -> dict[str, Any]:
        person = self.by_key.get((team, scope))
        return {
            "team": team.value,
            "team_title": TEAMS[team].title,
            "scope": scope,
            "contact": person.name if person else None,
            "contact_title": person.title if person else None,
            "channel": "email",
            "why": why,
            "message": message,
            "about": about,
        }


def _late_lines(world: World, late: Sequence[LateLeg]) -> list[str]:
    out = []
    for leg in late:
        f = world.flights.get(leg.flight_id)
        if f is None:
            continue
        new = f.dep_utc + timedelta(minutes=leg.minutes)
        out.append(
            f"{f.flight_no} {f.dep_station}-{f.arr_station} now departs {_hhmm(new)} "
            f"(was {_hhmm(f.dep_utc)}, {leg.minutes} min late)"
        )
    return out


def _booked(flights: Iterable[Flight | None]) -> int:
    return sum(f.booked or 0 for f in flights if f is not None)


def _moves_line(moves: Sequence[StandMove]) -> str:
    return "; ".join(
        f"{m.flight_no} {m.from_gate} to {m.to_gate}"
        + (f" (bus boarding, +{m.delay_minutes} min)" if m.to_gate == REMOTE_STAND else "")
        for m in moves
    )


def _gate_notices(
    d: Directory, world: World, moves: Sequence[StandMove], nexts: Sequence[NextAtGate]
) -> list[dict[str, Any]]:
    """Gate control and the next departure's gate agent at each gate a late flight holds."""
    out = []
    for n in nexts:
        station = n.gate.split("-")[0]
        moved = next((m for m in moves if m.flight_id == n.flight_id), None)
        if moved:
            state = f"the gate is still taken when it should board, so it moves to {moved.to_gate}"
        else:
            state = f"the gate is free in time, {n.buffer_minutes} min before it boards"
        out.append(
            d.notice(
                Team.STATION_OPS,
                station,
                f"tell the gate agent of {n.flight_no}, the next departure at {n.gate}",
                f"{n.flight_no} is next at {n.gate}, boarding from {_hhmm(n.boarding_from_utc)}: "
                f"{state}.",
                about=n.flight_id,
            )
        )
    for station in sorted({m.from_gate.split("-")[0] for m in moves}):
        here = [m for m in moves if m.from_gate.startswith(f"{station}-")]
        out.append(
            d.notice(
                Team.GATE_CONTROL,
                station,
                "reallocate the gates",
                f"Gate moves at {station}: {_moves_line(here)}.",
            )
        )
    return out


def _late_notices(
    d: Directory,
    world: World,
    late: Sequence[LateLeg],
    lead: str,
    crew_line: str,
) -> list[dict[str, Any]]:
    if not late:
        return []
    lines = _late_lines(world, late)
    flights = [world.flights.get(leg.flight_id) for leg in late]
    first = flights[0]
    booked = _booked(flights)
    out = [
        d.notice(
            Team.FLIGHT_INFO,
            NETWORK,
            "update the airport screens, the website and the app",
            f"{lead}. " + "; ".join(lines) + ".",
        ),
        d.notice(
            Team.OPS_MONITORING,
            NETWORK,
            "tell the captain by radio or datalink",
            f"{lead}. " + "; ".join(lines) + f". {crew_line}",
        ),
        d.notice(
            Team.CUSTOMER_CARE,
            NETWORK,
            f"tell the {booked} booked passengers by text and app",
            f"{lead}. {booked} passengers booked across {len(lines)} flight(s): "
            + "; ".join(lines)
            + ".",
        ),
    ]
    if first is not None:
        out.append(
            d.notice(
                Team.STATION_OPS,
                first.dep_station,
                "hold boarding and look after passengers at the gate",
                f"{lead}. " + lines[0] + ".",
            )
        )
    return out


def notices_for(
    world: World,
    report: Report,
    contacts: Iterable[Contact],
    *,
    late: Sequence[LateLeg] = (),
    moves: Sequence[StandMove] = (),
    nexts: Sequence[NextAtGate] = (),
    crew_line: str = "",
    uncovered_flights: Sequence[str] = (),
    kept_gate: str | None = None,
) -> list[dict[str, Any]]:
    """Every notice a disruption needs, in the order they would go out."""
    d = Directory(contacts)
    kind, det = report.report_type, report.details
    out: list[dict[str, Any]] = []

    if kind is ReportType.CERTIFICATION_LAPSE:
        crew = world.crew.get(det["crew_id"])
        cert = det["cert_type"].replace("_", " ")
        out.append(
            d.notice(
                Team.CREW_RECORDS,
                NETWORK,
                "book the renewal and tell the crew member",
                f"A crew member's {cert} expires on {det['valid_to']} and they are rostered "
                "after it. Crew control is taking them off those trips.",
            )
        )
        out.append(
            {
                "team": CREW,
                "team_title": "The crew member",
                "scope": det["crew_id"],
                "contact": crew.name if crew else None,
                "contact_title": None,
                "channel": "in_app",
                "why": "they cannot fly after the expiry until it is renewed",
                "message": f"Your {cert} expires on {det['valid_to']}. You are taken off trips "
                "after that date until it is renewed; training and crew records will contact "
                "you to book it.",
                "about": det["crew_id"],
            }
        )
        return out

    if kind in (
        ReportType.CREW_UNAVAILABLE,
        ReportType.MULTIPLE_CREW_UNAVAILABLE,
        ReportType.PLANNED_LEAVE,
    ):
        if uncovered_flights:
            flights = [world.flights.get(f) for f in uncovered_flights]
            names = ", ".join(sorted({f.flight_no for f in flights if f}))
            booked = _booked(flights)
            out.append(
                d.notice(
                    Team.OPS_MONITORING,
                    NETWORK,
                    "a flight may not operate",
                    f"No legal crew cover yet for {names}: it may be delayed or cancelled.",
                )
            )
            out.append(
                d.notice(
                    Team.CUSTOMER_CARE,
                    NETWORK,
                    f"{booked} booked passengers may need rebooking",
                    f"{names} may be cancelled for want of crew; {booked} passengers booked.",
                )
            )
        return out

    if kind is ReportType.AIRCRAFT_TECHNICAL:
        out.append(
            d.notice(
                Team.MAINTENANCE_CONTROL,
                NETWORK,
                "confirm when the aircraft can fly again",
                f"{det['aircraft']} unavailable {det['from_utc']} to {det['to_utc']}.",
            )
        )
        out += _late_notices(d, world, late, f"{det['aircraft']} technical fault", crew_line)
        out += _gate_notices(d, world, moves, nexts)
        return out

    if kind is ReportType.FLIGHT_DELAY:
        f = world.flights.get(det["flight_id"])
        lead = f"{f.flight_no if f else det['flight_id']} delayed {det['delay_minutes']} min"
        out += _late_notices(d, world, late, lead, crew_line)
        out += _gate_notices(d, world, moves, nexts)
        return out

    if kind is ReportType.STATION_DISRUPTION:
        station = det["station"]
        lead = f"{station} closed {det['from_utc']} to {det['to_utc']} ({det['reason']})"
        out.append(
            d.notice(
                Team.STATION_OPS,
                station,
                "run the station through the closure",
                f"{lead}. {len(late)} departure(s) on our network slip until it reopens.",
            )
        )
        others = sorted(
            {
                s
                for leg in late
                if (fl := world.flights.get(leg.flight_id))
                for s in (fl.dep_station, fl.arr_station)
                if s != station
            }
        )
        for other in others:
            out.append(
                d.notice(
                    Team.STATION_OPS,
                    other,
                    f"flights to or from {station} run late",
                    f"{lead}. Flights at {other} affected: "
                    + "; ".join(
                        line
                        for leg, line in zip(late, _late_lines(world, late), strict=False)
                        if (fl := world.flights.get(leg.flight_id))
                        and other in (fl.dep_station, fl.arr_station)
                    )
                    + ".",
                )
            )
        out += _late_notices(d, world, late, lead, crew_line)
        out += _gate_notices(d, world, moves, nexts)
        return out

    if kind is ReportType.GATE_CHANGE and kept_gate:
        f = world.flights.get(det["flight_id"])
        label = f.flight_no if f else det["flight_id"]
        station = kept_gate.split("-")[0]
        message = (
            f"{label} cannot move to {det['new_gate']}, which is held then; it stays at "
            f"{kept_gate}. Nothing changes for passengers."
        )
        return [
            d.notice(Team.GATE_CONTROL, station, "the requested gate is taken", message),
            d.notice(Team.STATION_OPS, station, "boarding stays where it was", message),
        ]

    if kind in (ReportType.GATE_CLOSURE, ReportType.GATE_CHANGE):
        gate = det.get("gate") or det.get("new_gate", "")
        station = gate.split("-")[0] if "-" in gate else ""
        flights = [world.flights.get(m.flight_id) for m in moves]
        if kind is ReportType.GATE_CHANGE and not moves:
            f = world.flights.get(det["flight_id"])
            flights = [f]
            station = f.dep_station if f else station
        booked = _booked(flights)
        summary = _moves_line(moves) if moves else f"{det.get('flight_id', '')} to {gate}"
        if station:
            out.append(
                d.notice(
                    Team.GATE_CONTROL, station, "reallocate the gates", f"Gate moves: {summary}."
                )
            )
            if kind is ReportType.GATE_CLOSURE and det.get("cause") == "external":
                out.append(
                    d.notice(
                        Team.STATION_OPS,
                        station,
                        "ready the emergency gates: more gates may close",
                        f"{gate} is closed by an outside cause ({det['reason']}). Keep the "
                        f"station's emergency gates ready for further moves.",
                    )
                )
                out.append(
                    d.notice(
                        Team.OPS_MONITORING,
                        NETWORK,
                        f"{station} may lose more gates",
                        f"{gate} closed ({det['reason']}); inbound captains may be held or "
                        "parked on remote stands.",
                    )
                )
            out.append(
                d.notice(
                    Team.STATION_OPS,
                    station,
                    "move boarding staff and signage",
                    f"Gate moves at {station}: {summary}.",
                )
            )
        if moves or kind is ReportType.GATE_CHANGE:
            out.append(
                d.notice(
                    Team.FLIGHT_INFO,
                    NETWORK,
                    "show the new gates on screens, website and app",
                    f"New gates: {summary}.",
                )
            )
            out.append(
                d.notice(
                    Team.CUSTOMER_CARE,
                    NETWORK,
                    f"send the gate change to {booked} booked passengers",
                    f"Gate change for {booked} passengers: {summary}.",
                )
            )
        if late:
            out.append(
                d.notice(
                    Team.OPS_MONITORING,
                    NETWORK,
                    "tell the captain of a departure boarding by bus",
                    "; ".join(_late_lines(world, late)) + f". {crew_line}",
                )
            )
        return out

    return out


__all__ = ["CREW", "LateLeg", "notices_for"]
