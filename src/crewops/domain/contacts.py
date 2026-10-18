"""The operational teams told about a disruption besides crew control.

Each team has a fixed job when something breaks, and ``crewops.analysis.notices`` picks
which ones a disruption needs. Who is on duty for each team, per station or for the network,
comes from seed data (``contacts.json``). No email addresses in there; an administrator
subscribes addresses to a team's alerts, same as for controllers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from crewops.domain.models import STATION_RE, DataError, _enum, _str

NETWORK = "NETWORK"
"""Scope for a team that serves every station from the operations centre."""


class Team(StrEnum):
    GATE_CONTROL = "gate_control"
    STATION_OPS = "station_ops"
    FLIGHT_INFO = "flight_info"
    OPS_MONITORING = "ops_monitoring"
    MAINTENANCE_CONTROL = "maintenance_control"
    CREW_RECORDS = "crew_records"
    CUSTOMER_CARE = "customer_care"


@dataclass(frozen=True, slots=True)
class TeamInfo:
    title: str
    job: str
    per_station: bool


TEAMS: dict[Team, TeamInfo] = {
    Team.GATE_CONTROL: TeamInfo(
        "Gate and stand control",
        "allocates gates and stands at the station and moves flights off a closed gate",
        per_station=True,
    ),
    Team.STATION_OPS: TeamInfo(
        "Station duty manager",
        "runs the station: ground handling, boarding staff and passengers at the airport",
        per_station=True,
    ),
    Team.FLIGHT_INFO: TeamInfo(
        "Flight information",
        "updates the airport display screens, the website and the app with new times and gates",
        per_station=False,
    ),
    Team.OPS_MONITORING: TeamInfo(
        "Operations monitoring",
        "watches every flight from the operations centre and tells the captain by radio or "
        "datalink",
        per_station=False,
    ),
    Team.MAINTENANCE_CONTROL: TeamInfo(
        "Maintenance control",
        "decides when an aircraft with a fault can fly again",
        per_station=False,
    ),
    Team.CREW_RECORDS: TeamInfo(
        "Training and crew records",
        "books renewals of licences, medicals and training, and tells the crew member",
        per_station=False,
    ),
    Team.CUSTOMER_CARE: TeamInfo(
        "Customer care",
        "tells booked passengers by text and app message, and rebooks them",
        per_station=False,
    ),
}


@dataclass(frozen=True, slots=True)
class Contact:
    """The person on duty for one team, at one station or for the whole network."""

    team: Team
    scope: str
    """A station code for a per-station team, otherwise ``NETWORK``."""
    name: str
    title: str

    @classmethod
    def from_dict(cls, d: Mapping[str, Any], ctx: str = "contact") -> Contact:
        team = _enum(Team, d, "team", ctx)
        scope = _str(d, "scope", ctx)
        if TEAMS[team].per_station and not STATION_RE.match(scope):
            raise DataError(f"{ctx}.scope: {team} is per station, got {scope!r}")
        if not TEAMS[team].per_station and scope != NETWORK:
            raise DataError(f"{ctx}.scope: {team} serves the network, got {scope!r}")
        return cls(team=team, scope=scope, name=_str(d, "name", ctx), title=_str(d, "title", ctx))


__all__ = ["NETWORK", "TEAMS", "Contact", "Team", "TeamInfo"]
