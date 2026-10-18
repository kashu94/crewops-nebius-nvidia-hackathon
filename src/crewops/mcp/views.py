"""What a disruption looks like to the desk: who owns it, what needs covering, and who else
wants the same crew.

Everything here is read from the stored analysis and the loaded operation; nothing is
decided. The contention check is the cross-controller part: an option that another open
disruption also ranks, or that another submitted plan already uses, is shown with that
disruption's desk, so two controllers do not call the same person for two trips.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from crewops.domain.desks import DESKS, owning_desks
from crewops.store import plans as plan_store
from crewops.store.items import from_dynamo
from crewops.store.reports import disruptions_with_status

OPEN = ("REPORTED", "OPTIONS_READY", "SUBMITTED")


def near(ours: set[str], theirs: Iterable[str]) -> bool:
    """Trips on the same or adjacent days compete: one person cannot fly both, and the rest
    rule links consecutive days."""
    mine = {date.fromisoformat(d) for d in ours}
    return any(abs((date.fromisoformat(d) - m).days) <= 1 for d in theirs for m in mine)


def _rows(tools: Any, entity: str, filters: Mapping[str, Any]) -> list[dict[str, Any]]:
    try:
        return tools.lookup(entity, dict(filters))["rows"]
    except Exception:  # an id the operation no longer has: show what is known
        return []


def desks_of_disruption(
    tools: Any, analysis: Mapping[str, Any], pairing_ids: Iterable[str] = ()
) -> list[dict[str, str]]:
    """The desk that plans a disruption: the one it was assigned when analysed (the plan
    service decides the same way)."""

    def aircraft(pairing_id: str) -> str | None:
        p = tools.world.pairings.get(pairing_id)
        return p.aircraft if p else None

    return [
        {"desk_id": d.desk_id, "controller": d.controller} for d in owning_desks(analysis, aircraft)
    ]


def flight_rows(
    tools: Any, flight_ids: Iterable[str], late: Mapping[str, int] | None = None
) -> list[dict[str, Any]]:
    """Flights as a table shows them: number, route, times, gate and passengers booked,
    with the new departure time when one leaves late."""
    late = late or {}
    gate = {g.flight_id: g.gate for g in tools.gates}
    out = []
    for fid in dict.fromkeys(flight_ids):
        f = tools.world.flights.get(fid)
        if f is None:
            continue
        minutes = late.get(fid, 0)
        out.append(
            {
                "flight_id": fid,
                "flight_no": f.flight_no,
                "from": f.dep_station,
                "to": f.arr_station,
                "aircraft": f.aircraft,
                "dep_utc": _iso(f.dep_utc),
                "new_dep_utc": _iso(f.dep_utc + timedelta(minutes=minutes)) if minutes else None,
                "late_minutes": minutes,
                "gate": gate.get(fid),
                "seats": f.seats,
                "booked": f.booked,
            }
        )
    out.sort(key=lambda r: r["dep_utc"])
    return out


def reference(tools: Any, first: Any = None, last: Any = None) -> dict[str, Any]:
    """What a reporter picks from when recording a disruption: crew, flights, aircraft,
    gates and stations, each with what tells them apart; flights and dates only from
    ``first`` to ``last`` (the days a disruption may be reported for)."""
    world, seed = tools.world, tools.seed
    days = [d for d in tools.week if (first is None or d >= first) and (last is None or d <= last)]
    gate = {g.flight_id: g.gate for g in tools.gates}
    return {
        "crew": [
            {
                "crew_id": c.crew_id,
                "name": c.name,
                "rank": c.rank.value,
                "base": c.base,
                "airline": c.airline,
            }
            for c in sorted(seed.crew, key=lambda c: c.crew_id)
        ],
        "airlines": [
            {"code": a.code, "name": a.name, "color": a.color, "aircraft": list(a.aircraft)}
            for a in seed.airlines
        ],
        "flights": [
            {
                "flight_id": f.flight_id,
                "flight_no": f.flight_no,
                "date": f.date.isoformat(),
                "from": f.dep_station,
                "to": f.arr_station,
                "dep_utc": _iso(f.dep_utc),
                "gate": gate.get(f.flight_id),
            }
            for f in sorted(world.flights.values(), key=lambda f: f.dep_utc)
            if days and days[0] <= f.date <= days[-1]
        ],
        "aircraft": sorted({f.aircraft for f in world.flights.values()}),
        "gates": sorted({g.gate for g in tools.gates}, key=lambda g: (g.split("-")[0], len(g), g)),
        "stations": sorted({f.dep_station for f in world.flights.values()}),
        "cert_types": sorted({c.cert_type.value for c in seed.certifications}),
        "week": [d.isoformat() for d in days],
        "today": world.snapshot_utc.date().isoformat(),
        # Leave, booked by an admin, may run to the end of the rostered period.
        "leave_until": tools.week[-1].isoformat() if tools.week else None,
    }


def operations(
    tools: Any,
    open_on: Mapping[str, list[dict[str, Any]]],
    out_days: Mapping[str, set[str]],
    unavailable: list[dict[str, Any]],
    partial: Mapping[tuple[str, str], str] | None = None,
    week: list[str] | None = None,
) -> dict[str, Any]:
    """The week's flights with airline, aircraft and crew; each crew member marked when
    reported unavailable that day (from a time, only on legs leaving after it), and each
    flight with the open disruptions touching it."""
    partial = partial or {}
    from crewops.domain import Rank

    world, seed = tools.world, tools.seed
    by_flight: dict[str, Any] = {}
    for pairing in world.pairings.values():
        for day in pairing.days:
            for fid in day.flight_ids:
                by_flight[fid] = pairing

    def person(crew_id: str, on: str, dep: str) -> dict[str, Any]:
        crew = world.crew.get(crew_id)
        since = partial.get((crew_id, on))
        return {
            "crew_id": crew_id,
            "name": crew.name if crew else crew_id,
            "out": on in out_days.get(crew_id, set()) and (since is None or dep >= since),
        }

    days = set(week) if week else None
    flights = []
    for f in sorted(world.flights.values(), key=lambda f: f.dep_utc):
        if days is not None and f.date.isoformat() not in days:
            continue
        pairing = by_flight.get(f.flight_id)
        on, dep = f.date.isoformat(), _iso(f.dep_utc)
        crew = list(pairing.crew) if pairing else []
        pilots = {
            role: [person(s.crew_id, on, dep) for s in crew if s.role is role]
            for role in (Rank.CAPTAIN, Rank.FIRST_OFFICER)
        }
        cabin = [
            person(s.crew_id, on, dep)
            for s in crew
            if s.role in (Rank.SENIOR_CABIN_CREW, Rank.CABIN_CREW)
        ]
        flights.append(
            {
                "flight_id": f.flight_id,
                "flight_no": f.flight_no,
                "date": on,
                "from": f.dep_station,
                "to": f.arr_station,
                "dep_utc": _iso(f.dep_utc),
                "arr_utc": _iso(f.arr_utc),
                "aircraft": f.aircraft,
                "aircraft_type": f.aircraft_type.value,
                "airline": world.airline_of_aircraft.get(f.aircraft),
                "seats": f.seats,
                "booked": f.booked,
                "pairing_id": pairing.pairing_id if pairing else None,
                "captain": (pilots[Rank.CAPTAIN] or [None])[0],
                "first_officer": (pilots[Rank.FIRST_OFFICER] or [None])[0],
                "cabin": cabin,
                "cancelled": f.flight_id in world.cancelled,
                "disruptions": open_on.get(f.flight_id, []),
            }
        )
    return {
        "week": week if week is not None else [d.isoformat() for d in tools.week],
        "airlines": [
            {"code": a.code, "name": a.name, "color": a.color}
            for a in (seed.airlines if seed else ())
        ],
        "flights": flights,
        "unavailable": unavailable,
    }


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def seat_context(tools: Any, seat: Mapping[str, Any]) -> dict[str, Any]:
    """The pairing behind a seat: aircraft, dates, each flight's state and the crew on it."""
    pairing = next(iter(_rows(tools, "pairings", {"pairing_id": seat["pairing_id"]})), None)
    if pairing is None:
        return {}
    impact = seat.get("impact") or {}
    uncovered = set(impact.get("uncovered_flights") or [])
    at_risk = set(impact.get("at_risk_flights") or [])
    affected = set(impact.get("affected_dates") or [])
    flight_ids = [f for day in pairing["days"] for f in day["flights"]]
    flights = _rows(tools, "flights", {"flight_id": flight_ids}) if flight_ids else []
    flights.sort(key=lambda f: f["dep_utc"])
    names = {
        c["crew_id"]: c
        for c in _rows(tools, "crew", {"crew_id": [m["crew_id"] for m in pairing["crew"]]})
    }
    aircraft_type = flights[0]["aircraft_type"] if flights else None
    out_id = seat.get("crew_out")
    out_base = names.get(out_id, {}).get("base") if out_id else None
    return {
        "aircraft": pairing["aircraft"],
        "aircraft_type": aircraft_type,
        "base": out_base,
        "dates": [d["date"] for d in pairing["days"]],
        "affected_dates": sorted(affected),
        "requirement": (
            f"A {seat['role']}"
            + (f" rated on the {aircraft_type}" if aircraft_type else "")
            + ", legal for every rule on the affected days."
        ),
        "flights": [
            {
                "flight_id": f["flight_id"],
                "flight_no": f["flight_no"],
                "date": f["date"],
                "from": f["dep_station"],
                "to": f["arr_station"],
                "dep_utc": f["dep_utc"],
                "arr_utc": f["arr_utc"],
                "seats": f["seats"],
                "booked": f.get("booked"),
                "state": "uncrewed"
                if f["flight_id"] in uncovered
                else "at_risk"
                if f["flight_id"] in at_risk
                else "crewed",
            }
            for f in flights
        ],
        "crew": [
            {
                "crew_id": m["crew_id"],
                "name": names.get(m["crew_id"], {}).get("name"),
                "role": m["role"],
                "state": "out" if m["crew_id"] == out_id and m["role"] == seat["role"] else "on",
            }
            for m in pairing["crew"]
        ],
    }


LIVE_OPTION_KEYS = (
    "rank",
    "action",
    "strategy",
    "crew_id",
    "name",
    "crew_rank",
    "cost_inr",
    "cost_breakdown",
    "delay_hours",
    "positioning_flight",
    "base",
    "reachability_minutes",
    "risk_score",
    "experience_years",
    "resilience",
)


def re_crewed(tools: Any, seat: Mapping[str, Any]) -> dict[str, Any] | None:
    """Who holds the seat now, if an approved plan filled it after the analysis (often a
    plan for the same person out on another day of the trip), or that the trip was
    cancelled. None while the person out still holds it. A filled seat needs no cover, and
    listing options for it would mislead the desk."""
    pairing = tools.world.pairings.get(seat.get("pairing_id"))
    out, role = seat.get("crew_out"), seat.get("role")
    if pairing is None or not out or not role:
        return None
    holders = [s.crew_id for s in pairing.crew if s.role.value == role]
    if out in holders:
        return None
    if not pairing.crew:
        return {"cancelled": True, "holders": []}
    return {
        "cancelled": False,
        "holders": [
            {"crew_id": c, "name": (tools.world.crew.get(c) and tools.world.crew[c].name)}
            for c in holders
        ],
    }


def live_options(tools: Any, seat: Mapping[str, Any]) -> dict[str, Any] | None:
    """The option search for a seat against today's rules and roster, which is what
    approval re-checks against. None when the trip has started or been cancelled, or the
    search fails."""
    if seat.get("status") in ("already_started", "cancelled"):
        return None
    try:
        r = tools.find_options(
            pairing_id=seat["pairing_id"],
            role=seat["role"],
            crew_id=seat.get("crew_out"),
            delay_hours=float(seat.get("delay_hours") or 0),
            delay_date=seat.get("delay_date"),
        )
    except Exception:  # the trip or person changed since: show the stored analysis only
        return None
    pick = lambda o: {k: o.get(k) for k in LIVE_OPTION_KEYS} if o else None  # noqa: E731
    options = [pick(o) for o in r.get("options", [])]
    by_crew = {o["crew_id"]: o for o in options if o.get("crew_id")}

    def full(o: Mapping[str, Any] | None) -> dict[str, Any] | None:
        # Policy and strategy picks are shorter copies: use the option's full record.
        return by_crew.get(o.get("crew_id")) or pick(o) if o else None

    return {
        "options": options,
        "policies": {name: full(o) for name, o in (r.get("policies") or {}).items()},
        "strategies": [full(o) for o in r.get("strategies", [])],
        "cancellation": pick(r.get("cancellation")),
        "short_crew": pick(r.get("short_crew")),
        "legal_options": r.get("legal_options"),
        "funnel": r.get("funnel", []),
        "excluded": [
            {k: e.get(k) for k in ("crew_id", "name", "stage", "rule_ids", "reasons")}
            for e in r.get("excluded", [])
        ],
        "excluded_count": r.get("excluded_count"),
    }


def reserve_capacity(tools: Any, role: str, dates: Iterable[str]) -> list[dict[str, Any]]:
    """Reserves of the seat's rank on call on any of its dates, by base."""
    wanted = set(dates)
    counts: dict[str, int] = defaultdict(int)
    for r in _rows(tools, "reserves", {}):
        if r.get("rank") == role and wanted & set(r.get("dates") or []):
            counts[r["base"]] += 1
    return [{"base": b, "on_call": n} for b, n in sorted(counts.items(), key=lambda x: -x[1])]


def contention(
    tools: Any,
    table: Any,
    disruption_id: str,
    wants: list[tuple[set[str], set[str]]],
    mine: set[tuple[str, str, str]] | frozenset[tuple[str, str, str]] = frozenset(),
) -> dict[str, list[dict[str, Any]]]:
    """For each crew id, the other open disruptions that rank them as an option, and the
    submitted plans that already use them, each with its desk. ``wants`` pairs each seat's
    option crew with that seat's dates, so a person is flagged only against trips on the
    same or adjacent days as the seat they could cover. ``mine`` holds this disruption's seats
    as (trip, role, crew out). Another disruption needing one of those exact seats is not a
    rival; it needs the same cover (see ``same_seat``)."""
    found: dict[str, list[dict[str, Any]]] = defaultdict(list)
    wants = [(ids, dates) for ids, dates in wants if ids and dates]
    if not wants:
        return {}

    def clashes(crew_id: str, their_dates: Iterable[str]) -> bool:
        theirs = list(their_dates)
        return any(crew_id in ids and near(dates, theirs) for ids, dates in wants)

    seen: set[tuple[str, str, str, str]] = set()

    def add(crew_id: str, entry: dict[str, Any]) -> None:
        key = (crew_id, entry["disruption_id"], entry["pairing_id"], entry["kind"])
        if key not in seen:
            seen.add(key)
            found[crew_id].append(entry)

    for status in OPEN:
        for item in disruptions_with_status(table, status):
            other = item["disruption_id"]
            if other == disruption_id:
                continue
            other_analysis = from_dynamo(item.get("analysis") or {})
            seats = other_analysis.get("seats", [])
            desks = desks_of_disruption(tools, other_analysis)
            for s in seats:
                if (s.get("pairing_id"), s.get("role"), s.get("crew_out")) in mine:
                    continue  # the same seat, not a rival for its cover
                their_dates = (s.get("impact") or {}).get("affected_dates") or []
                for o in s.get("options", []):
                    if o.get("crew_id") and clashes(o["crew_id"], their_dates):
                        add(
                            o["crew_id"],
                            {
                                "disruption_id": other,
                                "status": item["status"],
                                "pairing_id": s["pairing_id"],
                                "rank": o.get("rank"),
                                "desks": desks,
                                "crew_out": s.get("crew_out"),
                                "role": s.get("role"),
                                "kind": "option",
                                "report_type": from_dynamo(item.get("report") or {}).get("type"),
                            },
                        )
    for plan in plan_store.plans_with_status(table, plan_store.PLAN_SUBMITTED):
        if plan["disruption_id"] == disruption_id:
            continue
        for a in plan.get("assignments", []):
            if (a.get("pairing_id"), a.get("role"), a.get("replaces")) in mine:
                continue  # a plan already covering this very seat
            pairing = next(iter(_rows(tools, "pairings", {"pairing_id": a["pairing_id"]})), None)
            if a.get("crew_id") and pairing and clashes(a["crew_id"], pairing["dates"]):
                add(
                    a["crew_id"],
                    {
                        "disruption_id": plan["disruption_id"],
                        "status": "SUBMITTED",
                        "pairing_id": a["pairing_id"],
                        "plan_id": plan["plan_id"],
                        "desks": [
                            {"desk_id": d.desk_id, "controller": d.controller}
                            for d in DESKS
                            if d.desk_id == plan.get("desk")
                        ],
                        "crew_out": a.get("replaces"),
                        "role": a.get("role"),
                        "kind": "plan",
                        "report_type": None,
                    },
                )
    return dict(found)


def same_seat(
    tools: Any, table: Any, disruption_id: str, mine: set[tuple[str, str, str]]
) -> list[dict[str, Any]]:
    """Other open disruptions needing one of these very seats (the same person out on
    another day of the same trip): one cover fills them all."""
    if not mine:
        return []
    out = []
    for status in OPEN:
        for item in disruptions_with_status(table, status):
            if item["disruption_id"] == disruption_id:
                continue
            analysis = from_dynamo(item.get("analysis") or {})
            shared = sorted(
                f"{s['pairing_id']} {s['role']}"
                for s in analysis.get("seats", [])
                if (s.get("pairing_id"), s.get("role"), s.get("crew_out")) in mine
            )
            if shared:
                out.append(
                    {
                        "disruption_id": item["disruption_id"],
                        "status": item["status"],
                        "seats": shared,
                        "desks": desks_of_disruption(tools, analysis),
                    }
                )
    return out
