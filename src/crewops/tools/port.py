"""The tools the agent and the MCP server call, over one loaded operation.

Every tool answers from the deterministic engine and the roster. None of them asks a model
anything, and none of them guesses: an id that does not exist comes back as an error naming
the nearest real ones, and a question with several possible answers (a flight number that
flies on three days) comes back as a question.

A tool either returns a JSON-safe dict or raises ``ToolError``. ``dispatch`` turns both into
something the model can read.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

from crewops.analysis.disruption import option_dict
from crewops.domain import DataError, Rank, Report, ReportType, parse_utc
from crewops.domain.desks import DESKS, owning_desks
from crewops.engine import AlreadyStarted, World, assess, find_options
from crewops.engine.duty import calendar_window
from crewops.engine.impact import Seat, delay_effect, joint_plan, pairing_impact
from crewops.engine.rules import ALL_RULES
from crewops.ingestion.seed import SeedData
from crewops.search.hybrid import bm25
from crewops.search.vectors import RULES, VectorSearch
from crewops.store.items import from_dynamo
from crewops.tools import rows as rowlib
from crewops.tools.errors import (
    ALREADY_STARTED,
    AMBIGUOUS_QUERY,
    INVALID_ARGUMENT,
    NEEDS_CONFIRMATION,
    UNAVAILABLE,
    UNRESOLVED_ENTITY,
    ToolError,
)
from crewops.tools.resolve import digit_prefix_matches, resolve, transpositions
from crewops.tools.rows import plain

MAX_ROWS = 60
"""Most rows ``lookup`` returns. The count is always exact; past this, narrow or group."""
TOP_OPTIONS = 10
TOP_EXCLUDED = 10
ID_FIELDS = {
    "crew_id": "crew",
    "pairing_id": "pairing",
    "flight_id": "flight",
    "flight_no": "flight number",
    "aircraft": "aircraft",
}
"""Filter fields naming a real thing: when nothing matches, a value that names nothing real is
an error with suggestions, not an empty answer."""

RANK_WORDS: dict[str, Rank] = {
    "captain": Rank.CAPTAIN,
    "capt": Rank.CAPTAIN,
    "first officer": Rank.FIRST_OFFICER,
    "fo": Rank.FIRST_OFFICER,
    "f/o": Rank.FIRST_OFFICER,
    "senior cabin crew": Rank.SENIOR_CABIN_CREW,
    "scc": Rank.SENIOR_CABIN_CREW,
    "cabin crew": Rank.CABIN_CREW,
    "cc": Rank.CABIN_CREW,
}
RANK_PREFIX_RE = re.compile(
    r"^("
    + "|".join(re.escape(w) for w in sorted(RANK_WORDS, key=len, reverse=True))
    + r")\.?\s+(.+)$",
    re.IGNORECASE,
)
CREW_ID_RE = re.compile(r"^C-\d{4}$")


def _iso(value: datetime) -> str:
    return plain(value)


class CrewOpsTools:
    """The tool implementations. Build once per loaded operation; every call only reads."""

    def __init__(
        self,
        world: World,
        seed: SeedData,
        *,
        search: VectorSearch | None = None,
        open_disruptions: Callable[[], Sequence[Mapping[str, Any]]] | None = None,
    ) -> None:
        self.world = world
        self.seed = seed
        self.search = search
        self.open_disruptions = open_disruptions
        self.rows = rowlib.build_rows(seed, world.cancelled)
        self.rules = {r.rule_id: r for r in seed.rules}
        self.gates = sorted(seed.gates, key=lambda g: (g.gate, g.start_utc, g.flight_id))
        self.flight_pairing: dict[str, tuple[str, date]] = {}
        self.crew_pairings: dict[str, list[tuple[str, Rank]]] = {}
        for pid, p in sorted(world.pairings.items(), key=lambda kv: (kv[1].days[0].date, kv[0])):
            for day in p.days:
                for fid in day.flight_ids:
                    self.flight_pairing[fid] = (pid, day.date)
            for seat in p.crew:
                self.crew_pairings.setdefault(seat.crew_id, []).append((pid, seat.role))
        self.week = sorted({f.date for f in world.flights.values()})

    @classmethod
    def load(cls, table: Any, search: VectorSearch | None = None) -> CrewOpsTools:
        """The tools over the live table: the roster as stored, crew taken out by open
        disruptions marked unavailable, and issue counts read fresh on every call."""
        from crewops.analysis.runner import Operation, open_disruptions

        operation = Operation.load(table)
        if operation.seed is None:
            raise RuntimeError("the operation loaded without its records")
        return cls(
            operation.world,
            operation.seed,
            search=search,
            open_disruptions=lambda: open_disruptions(table),
        )

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def _universe(self, kind: str) -> tuple[Mapping[str, Any], Callable[[Any], str]]:
        w = self.world
        if kind == "crew":
            return w.crew, lambda c: f"{c.name}, {c.rank}, {c.base}"
        if kind == "pairing":
            return (
                w.pairings,
                lambda p: (
                    f"{p.aircraft}, {len(p.days)} day{'s' if len(p.days) > 1 else ''} "
                    f"from {p.days[0].date}"
                ),
            )
        if kind == "flight":
            return w.flights, lambda f: f"{f.dep_station}->{f.arr_station} {f.date}"
        if kind == "flight number":
            numbers = {}
            for f in sorted(w.flights.values(), key=lambda f: f.date):
                numbers.setdefault(f.flight_no, f)
            return numbers, lambda f: f"{f.dep_station}->{f.arr_station}"
        if kind == "aircraft":
            tails = {}
            for f in w.flights.values():
                tails.setdefault(f.aircraft, f)
            return tails, lambda f: str(f.aircraft_type)
        if kind == "rule":
            return self.rules, lambda r: r.text[:60]
        raise ValueError(f"no id universe for {kind!r}")

    def require(self, kind: str, value: str | None) -> str:
        """The id itself if it is real; otherwise an error naming what is near."""
        if not value:
            raise ToolError(UNRESOLVED_ENTITY, f"a {kind} id is required")
        universe, label = self._universe(kind)
        result = resolve(kind, value, universe, label)
        if result.exists:
            return value
        raise result.error()

    def _date(self, value: str | None, name: str = "date") -> date | None:
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError):
            raise ToolError(
                INVALID_ARGUMENT, f"{name}={value!r} is not a date (YYYY-MM-DD)"
            ) from None

    def _in_week(self, day: date, name: str = "date") -> date:
        if not self.week or not (self.week[0] <= day <= self.week[-1]):
            raise ToolError(
                UNRESOLVED_ENTITY,
                f"{name} {day} is outside the rostered period ({self.week[0]} to {self.week[-1]})",
            )
        return day

    def _instant(self, value: str | None, name: str) -> datetime | None:
        if value is None:
            return None
        try:
            return parse_utc(value, name)
        except DataError:
            raise ToolError(
                INVALID_ARGUMENT, f"{name}={value!r} is not a UTC instant (YYYY-MM-DDTHH:MM:SSZ)"
            ) from None

    def resolve_flight(
        self, flight_id: str | None = None, flight_no: str | None = None, on: str | None = None
    ) -> str:
        """A flight id from an id, or a number and a date. Never picks a date itself."""
        if flight_id:
            return self.require("flight", flight_id)
        if not flight_no:
            raise ToolError(UNRESOLVED_ENTITY, "no flight was named")
        legs = sorted(
            (f for f in self.world.flights.values() if f.flight_no == flight_no),
            key=lambda f: f.date,
        )
        if not legs:
            near = ", ".join(
                s["flight_no"] for s in self.suggest_flight_nos(flight_no)["suggestions"]
            )
            hint = f" Nearest flight numbers: {near}." if near else ""
            raise ToolError(UNRESOLVED_ENTITY, f"there is no flight {flight_no}.{hint}")
        flies = ", ".join(str(f.date) for f in legs)
        if on is not None:
            day = self._date(on)
            match = [f for f in legs if f.date == day]
            if not match:
                raise ToolError(
                    UNRESOLVED_ENTITY,
                    f"{flight_no} does not operate on {day}. It flies on {flies}.",
                )
            return match[0].flight_id
        if len(legs) == 1:
            return legs[0].flight_id
        raise ToolError(
            AMBIGUOUS_QUERY,
            f"{flight_no} operates on {flies}. Which date? Each is a different duty day.",
        )

    def _pairing_of_flight(self, flight_id: str) -> tuple[str, date]:
        try:
            return self.flight_pairing[flight_id]
        except KeyError:
            raise ToolError(UNRESOLVED_ENTITY, f"no pairing operates {flight_id}") from None

    def _crew_pairing(self, crew_id: str, on: date | None) -> tuple[str, Rank]:
        """The one pairing a crew member flies (on ``on``, when given), and their seat on it.

        Several crew fly more than one pairing in the week; without a date that is a
        question, not something to pick.
        """
        mine = self.crew_pairings.get(crew_id, [])
        if not mine:
            raise ToolError(
                UNRESOLVED_ENTITY, f"{crew_id} is not rostered on any pairing this week"
            )
        if on is not None:
            match = [
                (p, r) for p, r in mine if any(d.date == on for d in self.world.pairings[p].days)
            ]
            if not match:
                listing = self._listing(mine)
                raise ToolError(
                    UNRESOLVED_ENTITY,
                    f"{crew_id} is not rostered on {on}. Their pairings: {listing}.",
                )
            if len(match) > 1:
                raise ToolError(
                    AMBIGUOUS_QUERY,
                    f"{crew_id} flies {len(match)} pairings on {on}: {self._listing(match)}. "
                    "Which one?",
                )
            return match[0]
        if len(mine) == 1:
            return mine[0]
        raise ToolError(
            AMBIGUOUS_QUERY,
            f"{crew_id} flies {len(mine)} pairings this week: {self._listing(mine)}. "
            "Which date or pairing?",
        )

    def _listing(self, pairings: Sequence[tuple[str, Rank]]) -> str:
        parts = []
        for pid, role in pairings:
            dates = ", ".join(str(d.date) for d in self.world.pairings[pid].days)
            parts.append(f"{pid} ({role}, {dates})")
        return "; ".join(parts)

    def _pairing_for_aircraft(self, aircraft: str, on: date, crew_id: str | None = None) -> str:
        found = [
            pid
            for pid, p in self.world.pairings.items()
            if p.aircraft == aircraft and any(d.date == on for d in p.days)
        ]
        if not found:
            known = sorted({p.aircraft for p in self.world.pairings.values()})
            if aircraft not in known:
                raise ToolError(
                    UNRESOLVED_ENTITY, f"there is no aircraft {aircraft}. Known: {', '.join(known)}"
                )
            raise ToolError(UNRESOLVED_ENTITY, f"no pairing flies {aircraft} on {on}")
        if crew_id:
            own = [p for p in found if p in dict(self.crew_pairings.get(crew_id, []))]
            if len(own) == 1:
                return own[0]
        if len(found) == 1:
            return found[0]
        raise ToolError(
            AMBIGUOUS_QUERY,
            f"{aircraft} flies {len(found)} pairings on {on}: "
            f"{', '.join(sorted(found))}. Which one?",
        )

    def _pairing_from(
        self,
        pairing_id: str | None,
        flight_id: str | None,
        flight_no: str | None,
        on: str | None,
        aircraft: str | None = None,
        crew_id: str | None = None,
    ) -> tuple[str, str | None]:
        """A pairing id from whatever was named, and the flight id when a flight was named."""
        if pairing_id:
            return self.require("pairing", pairing_id), None
        if flight_id or flight_no:
            fid = self.resolve_flight(flight_id, flight_no, on)
            return self._pairing_of_flight(fid)[0], fid
        if aircraft:
            day = self._date(on)
            if day is None:
                raise ToolError(AMBIGUOUS_QUERY, f"{aircraft} flies every day. Which date?")
            return self._pairing_for_aircraft(aircraft, self._in_week(day), crew_id), None
        raise ToolError(
            UNRESOLVED_ENTITY,
            "name a pairing_id, a flight (flight_id, or flight_no and date), "
            "or an aircraft and date",
        )

    def _crew_named(self, value: str) -> str:
        """A crew id from an id, or from a name with an optional rank ("Captain A. Nair")."""
        value = (value or "").strip()
        if not value:
            raise ToolError(UNRESOLVED_ENTITY, "a crew id or name is required")
        if CREW_ID_RE.match(value.upper()):
            return self.require("crew", value.upper())
        name, rank = value, None
        if m := RANK_PREFIX_RE.match(value):
            rank, name = RANK_WORDS[m.group(1).lower()], m.group(2).strip()
        matches = [c for c in self.world.crew.values() if rowlib._name_matches(c.name, name)]
        if rank:
            same_name = sorted(matches, key=lambda c: c.crew_id)
            matches = [c for c in matches if c.rank is rank]
            if same_name and not matches:
                listing = ", ".join(f"{c.crew_id} ({c.name}, {c.rank})" for c in same_name)
                raise ToolError(
                    NEEDS_CONFIRMATION,
                    f"no {rank} named {name!r}. Crew with that name: {listing}. "
                    "Confirm which; I will not guess.",
                )
        if not matches:
            near = self._near_names(name)
            if near:
                raise ToolError(
                    NEEDS_CONFIRMATION,
                    f"no crew member named {value!r}. Did you mean {near}? Confirm which; "
                    "I will not guess.",
                )
            raise ToolError(UNRESOLVED_ENTITY, f"no crew member named {value!r}")
        if len(matches) > 1:
            listing = ", ".join(
                f"{c.crew_id} ({c.name}, {c.rank}, {c.base})"
                for c in sorted(matches, key=lambda c: c.crew_id)
            )
            raise ToolError(
                AMBIGUOUS_QUERY,
                f"{len(matches)} crew members match {value!r}: {listing}. Say which crew id.",
            )
        return matches[0].crew_id

    def _near_names(self, name: str, limit: int = 3) -> str:
        """Crew whose full name or surname is close to ``name``. Offered back for a typo,
        never used as the answer."""
        keys: dict[str, set[str]] = {}
        for c in self.world.crew.values():
            full = c.name.lower()
            keys.setdefault(full, set()).add(c.crew_id)
            keys.setdefault(full.split()[-1], set()).add(c.crew_id)
        close = difflib.get_close_matches(name.strip().lower(), list(keys), n=limit, cutoff=0.6)
        found: list[str] = []
        for key in close:
            found.extend(cid for cid in sorted(keys[key]) if cid not in found)
        return ", ".join(
            f"{self.world.crew[cid].name} ({cid}, {self.world.crew[cid].rank})"
            for cid in found[: limit * 2]
        )

    # ------------------------------------------------------------------
    # lookup
    # ------------------------------------------------------------------

    def lookup(
        self,
        entity: str,
        filters: Mapping[str, Any] | None = None,
        sort_by: str | None = None,
        sort_desc: bool = False,
        limit: int | None = None,
        group_by: list[str] | None = None,
    ) -> dict[str, Any]:
        resolved = rowlib.resolve_filters(entity, filters or {})
        # Every id asked about must exist, even when others in the same filter matched:
        # dropping a wrong one silently would answer a smaller question than was asked.
        for col, want in resolved.items():
            if kind := ID_FIELDS.get(col):
                for value in want if isinstance(want, list) else [want]:
                    if not isinstance(value, str):
                        raise ToolError(INVALID_ARGUMENT, f"{col} must be text, got {value!r}")
                    self.require(kind, value.strip().upper())
        found = [
            r
            for r in self.rows[entity]
            if all(rowlib.row_matches(r, col, want) for col, want in resolved.items())
        ]

        if not found:
            # An empty answer is real unless the name asked about does not exist.
            name = resolved.get("name")
            if entity in ("crew", "reserves", "pairing_crew") and isinstance(name, str):
                near = self._near_names(name)
                if near:
                    raise ToolError(
                        NEEDS_CONFIRMATION,
                        f"no crew named {name!r}. Did you mean {near}? Confirm which; "
                        "I will not guess.",
                    )

        if group_by:
            columns = list(dict.fromkeys(rowlib.field_for(entity, g) for g in group_by))
            return {
                "entity": entity,
                "count": len(found),
                "groups": rowlib.group_rows(found, columns),
            }

        if sort_by:
            found = rowlib.sort_rows(found, rowlib.field_for(entity, sort_by), sort_desc)
        count = len(found)
        shown = found[:limit] if limit else found
        out: dict[str, Any] = {"entity": entity, "count": count, "rows": shown[:MAX_ROWS]}
        if len(shown) > MAX_ROWS:
            out["truncated"] = True
            out["note"] = (
                f"Showing {MAX_ROWS} of {len(shown)} rows; count is exact. Add filters, "
                "a limit, or group_by to see the rest."
            )
        return out

    # ------------------------------------------------------------------
    # Crew-facing facts
    # ------------------------------------------------------------------

    def duty_clock(self, crew_id: str, date: str | None = None) -> dict[str, Any]:
        self.require("crew", crew_id)
        crew = self.world.crew[crew_id]
        as_of = self._date(date) or self.world.snapshot_utc.date()
        history = sorted(set(crew.daily_duty) | crew.rostered_dates())
        last = max([*history, self.week[-1]])
        if history and not (history[0] <= as_of <= last):
            raise ToolError(
                UNRESOLVED_ENTITY,
                f"{as_of} is outside the recorded history ({history[0]} to {last})",
            )
        book = self.world.book
        d_start, _ = calendar_window(as_of, book.duty_window_days)
        f_start, _ = calendar_window(as_of, book.flight_window_days)
        duty = crew.duty_in_window(d_start, as_of)
        flown = crew.flight_in_window(f_start, as_of)
        return {
            "crew_id": crew_id,
            "name": crew.name,
            "rank": crew.rank.value,
            "as_of": as_of.isoformat(),
            "duty_hours_this_date": crew.duty_in_window(as_of, as_of),
            "flight_hours_this_date": crew.flight_in_window(as_of, as_of),
            "rostered_this_date": as_of in crew.rostered_dates(),
            f"duty_hours_{book.duty_window_days}d": duty,
            f"duty_limit_{book.duty_window_days}d": book.max_duty_hours,
            f"duty_headroom_{book.duty_window_days}d": round(book.max_duty_hours - duty, 2),
            f"flight_hours_{book.flight_window_days}d": flown,
            f"flight_limit_{book.flight_window_days}d": book.max_flight_hours,
            f"flight_headroom_{book.flight_window_days}d": round(book.max_flight_hours - flown, 2),
            "duty_window": f"{d_start} to {as_of}",
            "duty_window_days": book.duty_window_days,
            "flight_window": f"{f_start} to {as_of}",
            "flight_window_days": book.flight_window_days,
            "last_rest_ended": _iso(crew.last_rest_ended) if crew.last_rest_ended else None,
        }

    def notification_brief(self, crew_id: str, pairing_id: str) -> dict[str, Any]:
        """Every fact a callout message needs, read from the roster, never invented."""
        self.require("crew", crew_id)
        self.require("pairing", pairing_id)
        crew = self.world.crew[crew_id]
        pairing = self.world.pairings[pairing_id]
        days = []
        for i, day in enumerate(pairing.days):
            legs = [self.world.flights[f] for f in day.flight_ids]
            days.append(
                {
                    "date": day.date.isoformat(),
                    "report_utc": _iso(day.report_utc),
                    "release_utc": _iso(day.release_utc),
                    "report_station": legs[0].dep_station if legs else None,
                    "flights": [
                        f"{leg.flight_no} {leg.dep_station}-{leg.arr_station} "
                        f"{leg.dep_utc:%H:%M}Z-{leg.arr_utc:%H:%M}Z"
                        for leg in legs
                    ],
                    "overnight_station": legs[-1].arr_station
                    if legs and i < len(pairing.days) - 1
                    else None,
                }
            )
        role = next((s.role for s in pairing.crew if s.crew_id == crew_id), crew.rank)
        reach = self.world.reachability.get(crew_id)
        report = pairing.days[0].report_utc
        acknowledge_by = report - timedelta(minutes=reach) if reach else None
        return {
            "crew_id": crew_id,
            "name": crew.name,
            "rank": crew.rank.value,
            "role": role.value,
            "rostered_on_pairing": any(s.crew_id == crew_id for s in pairing.crew),
            "pairing_id": pairing_id,
            "aircraft": pairing.aircraft,
            "report_utc": _iso(report),
            "reachability_minutes": reach,
            "acknowledge_by_utc": _iso(acknowledge_by) if acknowledge_by else None,
            "days": days,
        }

    # ------------------------------------------------------------------
    # Legality and cover
    # ------------------------------------------------------------------

    def check_legality(
        self,
        crew_id: str,
        pairing_id: str | None = None,
        flight_id: str | None = None,
        flight_no: str | None = None,
        date: str | None = None,
        aircraft: str | None = None,
        delay_h: float = 0.0,
        delay_date: str | None = None,
    ) -> dict[str, Any]:
        self.require("crew", crew_id)
        pid, fid = self._pairing_from(pairing_id, flight_id, flight_no, date, aircraft, crew_id)
        on = self._date(delay_date, "delay_date")
        if on is None and fid and delay_h:
            on = self._pairing_of_flight(fid)[1]  # the named flight's day is the late one
        try:
            a = assess(self.world, crew_id, pid, float(delay_h), on)
        except ValueError as exc:
            raise ToolError(INVALID_ARGUMENT, str(exc)) from None
        crew = self.world.crew[crew_id]
        return {
            "crew_id": crew_id,
            "name": crew.name,
            "rank": crew.rank.value,
            "pairing_id": pid,
            "legal": a.legal,
            "rules_checked": list(ALL_RULES),
            "blocking": [plain(_verdict(v)) for v in a.blocking],
            "verdicts": [plain(_verdict(v)) for v in a.verdicts],
            "rostered_on_pairing": pid in dict(self.crew_pairings.get(crew_id, [])),
            "on_reserve": a.on_reserve,
            "positioning": plain(_positioning(a.positioning)),
            "delay_hours": a.delay_hours,
            "delay_date": on.isoformat() if on else None,
        }

    def find_options(
        self,
        pairing_id: str | None = None,
        flight_id: str | None = None,
        flight_no: str | None = None,
        date: str | None = None,
        crew_id: str | None = None,
        role: str | None = None,
        delay_hours: float = 0.0,
        delay_date: str | None = None,
    ) -> dict[str, Any]:
        crew_out = None
        if crew_id and not (pairing_id or flight_id or flight_no):
            self.require("crew", crew_id)
            day = self._date(date)
            pid, rostered_role = self._crew_pairing(crew_id, self._in_week(day) if day else None)
            if role and Rank(role) is not rostered_role:
                raise ToolError(
                    INVALID_ARGUMENT,
                    f"{crew_id} flies {pid} as {rostered_role}, not {role}. Leave role out "
                    "to cover their own seat.",
                )
            seat_role, crew_out = rostered_role, crew_id
        else:
            pid, _ = self._pairing_from(pairing_id, flight_id, flight_no, date)
            if crew_id:
                self.require("crew", crew_id)
                held = [s.role for s in self.world.pairings[pid].crew if s.crew_id == crew_id]
                if not held:
                    raise ToolError(UNRESOLVED_ENTITY, f"{crew_id} is not rostered on {pid}")
                if role and Rank(role) is not held[0]:
                    raise ToolError(
                        INVALID_ARGUMENT,
                        f"{crew_id} flies {pid} as {held[0]}, not {role}. Leave role out "
                        "to cover their own seat.",
                    )
                seat_role, crew_out = held[0], crew_id
            elif role:
                seat_role = Rank(role)
            else:
                raise ToolError(
                    UNRESOLVED_ENTITY, "which seat? Give a role, or the crew_id of who is out"
                )
        on = self._date(delay_date, "delay_date")
        try:
            result = find_options(self.world, pid, seat_role, float(delay_hours), on)
        except AlreadyStarted as exc:
            raise ToolError(ALREADY_STARTED, str(exc)) from None
        except ValueError as exc:
            raise ToolError(INVALID_ARGUMENT, str(exc)) from None
        crew_opts = result.crew_options
        return {
            "pairing_id": pid,
            "role": seat_role.value,
            "crew_out": crew_out,
            "delay_hours": float(delay_hours),
            "recommended": option_dict(result.recommended) if result.recommended else None,
            "legal_options": len(crew_opts),
            "options": [option_dict(o) | _extras(o) for o in crew_opts[:TOP_OPTIONS]],
            "cancellation": option_dict(result.cancellation),
            "short_crew": option_dict(result.short_crew) if result.short_crew else None,
            "strategies": [option_dict(o) for o in result.strategies],
            "policies": {k: option_dict(o) for k, o in result.policies.items()},
            "equal_cost_alternatives": result.equal_cost_alternatives,
            "next_tier_premium_inr": result.next_tier_premium_inr,
            "cancellation_multiple": result.cancellation_multiple,
            "funnel": [
                {
                    "stage": s.stage,
                    "remaining": s.remaining,
                    "dropped": s.dropped,
                    "reason": s.reason,
                }
                for s in result.funnel
            ],
            "excluded_count": len(result.excluded),
            "excluded": [
                {
                    "crew_id": e.crew_id,
                    "name": e.name,
                    "stage": e.stage,
                    "rule_ids": list(e.rule_ids),
                    "reasons": list(e.reasons),
                }
                for e in result.excluded[:TOP_EXCLUDED]
            ],
        }

    def same_pairing(self, a: str, b: str) -> dict[str, Any]:
        out = {}
        for key, value in (("crew_a", a), ("crew_b", b)):
            cid = self._crew_named(value)
            crew = self.world.crew[cid]
            out[key] = {
                "crew_id": cid,
                "name": crew.name,
                "rank": crew.rank.value,
                "pairings": [
                    {"pairing_id": p, "role": r.value} for p, r in self.crew_pairings.get(cid, [])
                ],
            }
        if out["crew_a"]["crew_id"] == out["crew_b"]["crew_id"]:
            raise ToolError(INVALID_ARGUMENT, f"{a!r} and {b!r} are the same crew member")
        mine = {p["pairing_id"] for p in out["crew_a"]["pairings"]}
        shared = sorted(mine & {p["pairing_id"] for p in out["crew_b"]["pairings"]})
        return out | {"shared_pairings": shared, "same_pairing": bool(shared)}

    # ------------------------------------------------------------------
    # Impact and what-ifs
    # ------------------------------------------------------------------

    RIPPLE_TARGETS = (
        ("pairing", ("pairing_id",)),
        ("crew", ("crew_id",)),
        ("flight", ("flight_id", "flight_no")),
        ("window", ("station", "aircraft")),
    )

    def ripple(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """The blast radius of a disruption, however it was named.

        Losing crew (a pairing or crew member) leaves whole duty days unflown; a flight, an
        aircraft window or a station window hits those flights, and puts the pairing's later
        flights at risk. Flights that have already departed are left out: nothing can change
        for them now.
        """
        e = dict(event)
        named = [k for k, keys in self.RIPPLE_TARGETS if any(e.get(x) for x in keys)]
        if not named:
            raise ToolError(
                UNRESOLVED_ENTITY,
                "ripple needs a pairing_id, crew_id, flight (flight_id, or flight_no and date), "
                "an aircraft with a date or window, or a station with a date or window",
            )
        if len(named) > 1:
            raise ToolError(
                INVALID_ARGUMENT,
                f"name one disruption at a time: this event names a {' and a '.join(named)}",
            )
        kind = named[0]
        if kind != "window" and (e.get("from_utc") or e.get("to_utc")):
            raise ToolError(
                INVALID_ARGUMENT, "from_utc/to_utc apply only to an aircraft or station window"
            )
        on = self._date(e.get("date"))
        if on is not None:
            self._in_week(on)
        now = self.world.snapshot_utc
        crew_loss: dict[str, set[date] | None] = {}  # pairing -> dates lost, None = all left
        hit: list[Any] = []  # flights hit directly by a flight, aircraft or station event
        described: dict[str, Any] = {}

        if kind == "pairing":
            pid = self.require("pairing", e["pairing_id"])
            days = self.world.pairings[pid].days
            if on is not None and on not in [d.date for d in days]:
                raise ToolError(UNRESOLVED_ENTITY, f"{pid} does not fly on {on}")
            if on is None:  # the first day with a flight still to go
                on = next((d.date for d in days if self._remaining(d.flight_ids)), days[0].date)
            crew_loss[pid] = {on}
            described = {"pairing_id": pid, "date": on.isoformat()}
        elif kind == "crew":
            cid = self.require("crew", e["crew_id"])
            mine = self.crew_pairings.get(cid, [])
            if not mine:
                raise ToolError(
                    UNRESOLVED_ENTITY, f"{cid} is not rostered on any pairing this week"
                )
            for pid, _ in mine:
                days = self.world.pairings[pid].days
                if on is not None:
                    if on in [d.date for d in days]:
                        crew_loss[pid] = {on}
                elif any(self._remaining(d.flight_ids) for d in days):
                    crew_loss[pid] = None  # out from now on: every remaining day
            if on is not None and not crew_loss:
                raise ToolError(UNRESOLVED_ENTITY, f"{cid} is not rostered on {on}")
            described = {"crew_id": cid, "date": on.isoformat() if on else None}
        elif kind == "flight":
            fid = self.resolve_flight(e.get("flight_id"), e.get("flight_no"), e.get("date"))
            hit = [self.world.flights[fid]]
            described = {"flight_id": fid}
        else:
            start = self._instant(e.get("from_utc"), "from_utc")
            end = self._instant(e.get("to_utc"), "to_utc")
            if start and end and end <= start:
                raise ToolError(INVALID_ARGUMENT, "to_utc must be after from_utc")
            station, aircraft = e.get("station"), e.get("aircraft")
            if not (start or end or on):
                what = station or aircraft
                raise ToolError(
                    AMBIGUOUS_QUERY, f"when is {what} disrupted? Give from_utc/to_utc or a date"
                )
            if station and station not in {f.dep_station for f in self.world.flights.values()}:
                raise ToolError(UNRESOLVED_ENTITY, f"no flights touch station {station!r}")
            if aircraft and aircraft not in {p.aircraft for p in self.world.pairings.values()}:
                known = ", ".join(sorted({p.aircraft for p in self.world.pairings.values()}))
                raise ToolError(
                    UNRESOLVED_ENTITY, f"there is no aircraft {aircraft}. Known: {known}"
                )
            for f in sorted(self.world.flights.values(), key=lambda f: (f.dep_utc, f.flight_id)):
                if aircraft and f.aircraft != aircraft:
                    continue
                if station and station not in (f.dep_station, f.arr_station):
                    continue
                if on is not None and f.date != on:
                    continue
                moment_start = f.dep_utc if (not station or f.dep_station == station) else f.arr_utc
                moment_end = f.arr_utc if (not station or f.arr_station == station) else f.dep_utc
                if start and moment_end < start:
                    continue
                if end and moment_start > end:
                    continue
                hit.append(f)
            described = {
                "station": station,
                "aircraft": aircraft,
                "date": on.isoformat() if on else None,
                "from_utc": e.get("from_utc"),
                "to_utc": e.get("to_utc"),
                "flights_in_window": [f.flight_id for f in hit],
            }

        rows: list[dict[str, Any]] = []
        departed: list[str] = []
        for pid, dates in crew_loss.items():
            i = pairing_impact(self.world, pid, dates)
            direct = [self.world.flights[f] for f in i.uncovered_flights]
            later = [self.world.flights[f] for f in i.at_risk_flights]
            rows.append(self._impact_row(pid, direct, later, departed))
        per_pairing: dict[str, list[Any]] = {}
        for f in hit:
            if f.flight_id in self.flight_pairing:
                per_pairing.setdefault(self.flight_pairing[f.flight_id][0], []).append(f)
        for pid, direct in per_pairing.items():
            # The pairing's later flights are at risk: the aircraft or crew will not be where
            # they should be.
            ids = {f.flight_id for f in direct}
            first = min(f.dep_utc for f in direct)
            legs = [
                self.world.flights[fid]
                for d in self.world.pairings[pid].days
                for fid in d.flight_ids
            ]
            later = [f for f in legs if f.flight_id not in ids and f.dep_utc > first]
            rows.append(self._impact_row(pid, direct, later, departed))
        rows = [r for r in rows if r["directly_affected_flights"] or r["at_risk_flights"]]

        aircraft_hit = sorted({r["aircraft"] for r in rows})
        out: dict[str, Any] = {
            "event": {k: v for k, v in described.items() if v is not None},
            "as_of_utc": _iso(now),
            "affected_pairings": rows,
            "directly_affected_flights": sum(len(r["directly_affected_flights"]) for r in rows),
            "at_risk_flights": sum(len(r["at_risk_flights"]) for r in rows),
            "passengers": sum(r["passengers"] for r in rows),
            "aircraft": aircraft_hit,
        }
        if departed:
            out["already_departed"] = sorted(set(departed))
        if not rows:
            out["note"] = (
                "Nothing left to affect: every flight this names has already departed."
                if departed or crew_loss or hit
                else "No flights fall within this disruption."
            )
            if kind == "crew" and not crew_loss:
                out["note"] = "This crew member has no flights left to fly this week."
        return out

    def _remaining(self, flight_ids: Sequence[str]) -> bool:
        now = self.world.snapshot_utc
        return any(self.world.flights[f].dep_utc >= now for f in flight_ids)

    def _impact_row(
        self, pid: str, direct: Sequence[Any], later: Sequence[Any], departed: list[str]
    ) -> dict[str, Any]:
        now = self.world.snapshot_utc
        departed.extend(f.flight_id for f in (*direct, *later) if f.dep_utc < now)
        direct = [f for f in direct if f.dep_utc >= now]
        later = [f for f in later if f.dep_utc >= now]
        return {
            "pairing_id": pid,
            "aircraft": self.world.pairings[pid].aircraft,
            "affected_dates": sorted({f.date.isoformat() for f in direct}),
            "directly_affected_flights": [f.flight_id for f in direct],
            "at_risk_flights": [f.flight_id for f in later],
            "passengers": sum(f.booked if f.booked is not None else f.seats for f in direct),
        }

    def simulate(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """A delay applied to one duty day: who stops being legal, and whether anyone could."""
        e = dict(event)
        delay = float(e["delay_hours"])
        pid, fid = self._pairing_from(
            e.get("pairing_id"), e.get("flight_id"), e.get("flight_no"), e.get("date")
        )
        if fid:
            on = self._pairing_of_flight(fid)[1]
        else:
            on = self._date(e.get("date")) or self.world.pairings[pid].days[0].date
        try:
            effect = delay_effect(self.world, pid, on, delay, fid)
        except ValueError as exc:
            raise ToolError(INVALID_ARGUMENT, str(exc)) from None
        before = next(d for d in self.world.days_of(pid) if d.date == on)
        return {
            "pairing_id": pid,
            "date": on.isoformat(),
            "from_flight": fid,
            "delay_hours": effect.delay_hours,
            "fdp_hours_before": before.fdp_hours,
            "fdp_hours_after": effect.fdp_hours,
            "fdp_limit": effect.fdp_limit,
            "duty_breaches_limit": effect.duty_breaches_limit,
            "changed": [
                {
                    "crew_id": b.crew_id,
                    "role": b.role.value,
                    "legal_before": True,
                    "legal_after": False,
                    "reasons": list(b.reasons),
                }
                for b in effect.breaches
            ],
            "legs_to_drop": list(effect.legs_to_drop),
            "cancellation_cost_inr": effect.cancellation_cost_inr,
        }

    def joint_plan(self, events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Cheapest cover for several seats at once; nobody gets two seats. The same person
        named out of the same seat twice (one person reported for two days of one trip) is
        one seat: whoever covers flies the whole trip."""
        seats: list[Seat] = []
        out_crew: list[str] = []
        held_seats: set[tuple[str, str, Rank]] = set()
        for i, raw in enumerate(events):
            e = dict(raw)
            if e.get("crew_id") and not (
                e.get("pairing_id") or e.get("flight_id") or e.get("flight_no")
            ):
                cid = self.require("crew", e["crew_id"])
                day = self._date(e.get("date"))
                pid, role = self._crew_pairing(cid, self._in_week(day) if day else None)
                self._same_role(i, cid, pid, role, e.get("role"))
                if (cid, pid, role) in held_seats:
                    continue  # the same seat again
                held_seats.add((cid, pid, role))
                out_crew.append(cid)
            else:
                pid, _ = self._pairing_from(
                    e.get("pairing_id"), e.get("flight_id"), e.get("flight_no"), e.get("date")
                )
                if e.get("crew_id"):
                    cid = self.require("crew", e["crew_id"])
                    held = [s.role for s in self.world.pairings[pid].crew if s.crew_id == cid]
                    if not held:
                        raise ToolError(UNRESOLVED_ENTITY, f"event {i + 1}: {cid} is not on {pid}")
                    role = held[0]
                    self._same_role(i, cid, pid, role, e.get("role"))
                    if (cid, pid, role) in held_seats:
                        continue  # the same seat again
                    held_seats.add((cid, pid, role))
                    out_crew.append(cid)
                elif e.get("role"):
                    role = Rank(e["role"])
                else:
                    raise ToolError(
                        UNRESOLVED_ENTITY, f"event {i + 1}: give a role or the crew_id who is out"
                    )
            seats.append(Seat(pid, role))

        for seat in dict.fromkeys(seats):
            holders = len(self.world.pairings[seat.pairing_id].holder(seat.role))
            asked = seats.count(seat)
            if holders == 0:
                raise ToolError(
                    INVALID_ARGUMENT, f"{seat.pairing_id} has no {seat.role} seat to cover"
                )
            if asked > holders:
                raise ToolError(
                    INVALID_ARGUMENT,
                    f"{seat.pairing_id} has {holders} {seat.role} seat"
                    f"{'s' if holders > 1 else ''}, but the events ask to cover {asked}",
                )

        # Everyone named as out is out for every date the plan covers: they cannot cover a
        # seat in another pairing while their own is being re-crewed.
        dates = sorted({d.date for s in seats for d in self.world.pairings[s.pairing_id].days})
        unavailable = {k: list(v) for k, v in self.world.unavailable.items()}
        for cid in out_crew:
            unavailable.setdefault(cid, []).extend((d, "this plan") for d in dates)
        world = replace(
            self.world, unavailable={k: tuple(sorted(set(v))) for k, v in unavailable.items()}
        )
        precomputed = []
        for s in seats:
            try:
                precomputed.append(find_options(world, s.pairing_id, s.role))
            except AlreadyStarted:
                precomputed.append(None)
        plan = joint_plan(world, seats, precomputed)
        started_seats = [s for s, r in zip(seats, precomputed, strict=True) if r is None]
        started = [{"pairing_id": s.pairing_id, "role": s.role.value} for s in started_seats]
        return {
            "assignments": [
                {"pairing_id": s.pairing_id, "role": s.role.value} | option_dict(o)
                for s, o in plan.assignments
            ],
            "unfilled": [
                {"pairing_id": s.pairing_id, "role": s.role.value}
                for s in plan.unfilled
                if s not in started_seats
            ],
            "already_started": started,
            "total_cost_inr": plan.total_cost_inr,
            "equal_cost_alternatives": plan.equal_cost_alternatives,
            "exhaustive": plan.exhaustive,
            "note": "Nobody covers two seats. Plans with the same total are equally correct.",
        }

    @staticmethod
    def _same_role(i: int, crew_id: str, pid: str, held: Rank, role: str | None) -> None:
        if role and Rank(role) is not held:
            raise ToolError(
                INVALID_ARGUMENT,
                f"event {i + 1}: {crew_id} flies {pid} as {held}, not {role}. Leave role out "
                "to cover their own seat.",
            )

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------

    def check_gate(
        self,
        flight_id: str | None = None,
        flight_no: str | None = None,
        date: str | None = None,
        boarding_gate_number: str | None = None,
        delay_minutes: float = 0.0,
        at_utc: str | None = None,
        station: str | None = None,
    ) -> dict[str, Any]:
        at = self._instant(at_utc, "at_utc")
        if delay_minutes and not (flight_id or flight_no):
            raise ToolError(INVALID_ARGUMENT, "delay_minutes needs the delayed flight")
        by_gate: dict[str, list] = {}
        for g in self.gates:
            by_gate.setdefault(g.gate, []).append(g)

        if not (flight_id or flight_no or boarding_gate_number):
            gates = sorted(g for g in by_gate if not station or g.startswith(f"{station}-"))
            if station and not gates:
                raise ToolError(UNRESOLVED_ENTITY, f"no boarding gates on record at {station!r}")
            if date:
                day = self._in_week(self._date(date))
                busy = [g for g in gates if any(s.date == day for s in by_gate[g])]
                return {
                    "date": day.isoformat(),
                    "station": station,
                    "occupied_boarding_gates": len(busy),
                    "of_boarding_gates": len(gates),
                    "occupied_by_station": _per_station(busy),
                    "occupied_gate_numbers": busy,
                }
            return {
                "station": station,
                "total_boarding_gates": len(gates),
                "gates_by_station": _per_station(gates),
                "boarding_gate_numbers": gates,
            }

        if not (flight_id or flight_no):
            gate = boarding_gate_number
            if station and not gate.startswith(f"{station}-"):
                raise ToolError(INVALID_ARGUMENT, f"gate {gate} is not at station {station}")
            if gate not in by_gate:
                raise ToolError(
                    UNRESOLVED_ENTITY,
                    f"there is no boarding gate {gate!r}. "
                    f"Known gates: {', '.join(sorted(by_gate))}",
                )
            day = self._in_week(self._date(date)) if date else None
            slots = [s for s in by_gate[gate] if day is None or s.date == day]
            out: dict[str, Any] = {"boarding_gate_number": gate}
            if day is not None:
                out["date"] = day.isoformat()
            if at is not None or day is None:
                instant = at or self.world.snapshot_utc
                holder = next(
                    (s for s in by_gate[gate] if s.start_utc <= instant < s.end_utc), None
                )
                out |= {
                    "checked_at_utc": _iso(instant),
                    "occupied": holder is not None,
                    "occupying_flight_id": holder.flight_id if holder else None,
                    "occupied_from": _iso(holder.start_utc) if holder else None,
                    "occupied_until": _iso(holder.end_utc) if holder else None,
                }
            out["schedule"] = [
                {
                    "flight_id": s.flight_id,
                    "pairing_id": s.pairing_id,
                    "boarding_start_time": _iso(s.start_utc),
                    "boarding_end_time": _iso(s.end_utc),
                }
                for s in slots
            ]
            return out

        fid = self.resolve_flight(flight_id, flight_no, date)
        mine = next((s for s in self.gates if s.flight_id == fid), None)
        if mine is None:
            raise ToolError(UNRESOLVED_ENTITY, f"{fid} has no boarding-gate record")
        end = mine.end_utc + timedelta(minutes=float(delay_minutes or 0))
        conflict = None
        if delay_minutes:
            later = [s for s in by_gate[mine.gate] if s.start_utc >= mine.end_utc and s is not mine]
            nxt = min(later, key=lambda s: s.start_utc, default=None)
            if nxt is not None and end > nxt.start_utc:
                conflict = {
                    "flight_id": nxt.flight_id,
                    "pairing_id": nxt.pairing_id,
                    "its_boarding_start_time": _iso(nxt.start_utc),
                    "overlap_minutes": round((end - nxt.start_utc).total_seconds() / 60, 1),
                }
        return {
            "flight_id": fid,
            "pairing_id": mine.pairing_id,
            "date": mine.date.isoformat(),
            "actual_boarding_gate_number": mine.gate,
            "claimed_boarding_gate_number": boarding_gate_number,
            "gate_match": boarding_gate_number is None or boarding_gate_number == mine.gate,
            "boarding_start_time": _iso(mine.start_utc),
            "boarding_end_time": _iso(mine.end_utc),
            "delay_minutes": float(delay_minutes or 0),
            "delayed_boarding_end_time": _iso(end) if delay_minutes else None,
            "gate_conflict": conflict,
        }

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    def explain_rule(self, rule_id: str) -> dict[str, Any]:
        self.require("rule", rule_id)
        rule = self.rules[rule_id]
        out: dict[str, Any] = {"rule_id": rule_id, "text": rule.text, "params": dict(rule.params)}
        if rule_id == "RULE-FDP-01":
            book = self.world.book
            out["fdp_limit_by_sectors"] = {str(n): book.fdp_limit(n) for n in range(1, 7)}
        return out

    def search_rules(self, query: str, top_k: int = 3) -> dict[str, Any]:
        """Rules closest to a paraphrased question. Keyword-only if vector search is off."""
        if self.search is not None:
            hits = self.search.search(RULES, query, top_k=top_k)
            return {
                "method": "hybrid",
                "candidates": [
                    {
                        "rule_id": h.key,
                        "text": self.rules[h.key].text if h.key in self.rules else None,
                        "score": h.score,
                        "semantic_similarity": h.semantic,
                    }
                    for h in hits
                ],
            }
        ordered = sorted(self.rules.values(), key=lambda r: r.rule_id)
        scores = bm25(query, [f"{r.rule_id}: {r.text}" for r in ordered])
        ranked = sorted(zip(scores, ordered, strict=True), key=lambda t: (-t[0], t[1].rule_id))
        return {
            "method": "keyword",
            "candidates": [
                {"rule_id": r.rule_id, "text": r.text, "score": round(s, 3)}
                for s, r in ranked[:top_k]
                if s > 0
            ],
        }

    # ------------------------------------------------------------------
    # Did you mean
    # ------------------------------------------------------------------

    def _suggest(self, ids: list[str], near: str, limit: int) -> list[str]:
        ids = sorted(ids)
        out: list[str] = []
        for candidate in (
            ([near] if near in ids else [])
            + sorted(t for t in transpositions(near) if t in ids)
            + digit_prefix_matches(near, ids, limit)
            + difflib.get_close_matches(near, ids, n=limit, cutoff=0.6)
        ):
            if candidate not in out:
                out.append(candidate)
        return out[:limit]

    def suggest_crew_ids(
        self, near: str, limit: int = 3, prefer: Sequence[str] = ()
    ) -> dict[str, Any]:
        # Who the conversation was just about comes first: "C-123" right after asking about
        # C-1042 most likely means C-1042.
        first = [c for c in prefer if c in self.world.crew]
        rest = self._suggest(list(self.world.crew), near.strip().upper(), limit)
        found = (first + [c for c in rest if c not in first])[:limit]
        return {
            "near": near,
            "suggestions": [
                {
                    "crew_id": c,
                    "name": self.world.crew[c].name,
                    "rank": self.world.crew[c].rank.value,
                    "base": self.world.crew[c].base,
                }
                for c in found
            ],
            "note": "Candidates only. Ask which was meant; never act on one unconfirmed.",
        }

    def suggest_pairing_ids(self, near: str, limit: int = 3) -> dict[str, Any]:
        found = self._suggest(list(self.world.pairings), near.strip().upper(), limit)
        return {
            "near": near,
            "suggestions": [
                {
                    "pairing_id": p,
                    "aircraft": self.world.pairings[p].aircraft,
                    "dates": [d.date.isoformat() for d in self.world.pairings[p].days],
                }
                for p in found
            ],
            "note": "Candidates only. Ask which was meant; never act on one unconfirmed.",
        }

    def suggest_flight_nos(self, near: str, limit: int = 3) -> dict[str, Any]:
        legs: dict[str, Any] = {}
        for f in sorted(self.world.flights.values(), key=lambda f: f.date):
            legs.setdefault(f.flight_no, f)
        found = self._suggest(list(legs), near.strip().upper(), limit)
        return {
            "near": near,
            "suggestions": [
                {
                    "flight_no": n,
                    "dep_station": legs[n].dep_station,
                    "arr_station": legs[n].arr_station,
                }
                for n in found
            ],
            "note": "Candidates only. Ask which was meant; never act on one unconfirmed.",
        }

    # ------------------------------------------------------------------
    # Desks
    # ------------------------------------------------------------------

    def list_controllers(self) -> dict[str, Any]:
        return {
            "desks": [{"desk_id": d.desk_id, "controller": d.controller} for d in DESKS],
            "assignment": "each new disruption goes to the controller with the fewest open ones",
            "note": "Controllers dispatch; they are not crew and are not on the roster.",
        }

    def _owners(self, item: Mapping[str, Any]) -> list[Any]:
        """The desks that plan a stored disruption (see ``domain.desks.owning_desks``)."""
        analysis = from_dynamo(item.get("analysis") or {})
        if not analysis:
            return []  # not analysed yet: not assigned yet

        def aircraft(pairing_id: str) -> str | None:
            p = self.world.pairings.get(pairing_id)
            return p.aircraft if p else None

        return owning_desks(analysis, aircraft)

    def controller_issue_counts(self) -> dict[str, Any]:
        """Open disruptions per desk, from the live store; ones not yet analysed (so not yet
        assigned) are listed apart."""
        if self.open_disruptions is None:
            raise ToolError(UNAVAILABLE, "the disruption store is not connected")
        per_desk: dict[str, list[str]] = {d.desk_id: [] for d in DESKS}
        unassigned: list[str] = []
        for item in self.open_disruptions():
            disruption_id = str(item.get("disruption_id") or item.get("pk") or "unknown")
            desks = self._owners(item)
            if not desks:
                unassigned.append(disruption_id)
            for d in desks:
                per_desk[d.desk_id].append(disruption_id)
        return {
            "desks": [
                {
                    "desk_id": d.desk_id,
                    "controller": d.controller,
                    "open_issues": len(per_desk[d.desk_id]),
                    "disruption_ids": sorted(per_desk[d.desk_id]),
                }
                for d in DESKS
            ],
            "unassigned": sorted(unassigned),
        }

    def list_open_disruptions(self, desk: str | None = None) -> dict[str, Any]:
        """The open disruptions, newest first, optionally one desk's: read from the live
        store on every call, so the answer is what is open now."""
        if self.open_disruptions is None:
            raise ToolError(UNAVAILABLE, "the disruption store is not connected")
        rows = []
        for item in self.open_disruptions():
            try:
                report = Report.from_dict(from_dynamo(item["report"]))
            except (DataError, KeyError, TypeError):
                continue
            desks = [d.desk_id for d in self._owners(item)]
            if desk and desks and desk not in desks:
                continue
            rows.append(
                {
                    "disruption_id": str(item.get("disruption_id")),
                    "what": _what(report),
                    "status": str(item.get("status")),
                    "desks": desks,
                    "reported_utc": report.to_dict()["reported_utc"],
                }
            )
        rows.sort(key=lambda r: r["reported_utc"], reverse=True)
        return {"desk": desk, "count": len(rows), "disruptions": rows}


def describe_report(report: Report) -> str:
    """One line on what a report says happened (public: alerts and the feed use it)."""
    return _what(report)


def _what(report: Report) -> str:
    d, kind = report.details, report.report_type
    if kind is ReportType.CREW_UNAVAILABLE:
        span = (
            d["from_date"]
            if d["from_date"] == d["to_date"]
            else f"{d['from_date']} to {d['to_date']}"
        )
        return f"{d['crew_id']} unavailable {span} ({d['reason']})"
    if kind is ReportType.MULTIPLE_CREW_UNAVAILABLE:
        return f"{', '.join(d['crew_ids'])} unavailable from {d['from_date']} ({d['reason']})"
    if kind is ReportType.PLANNED_LEAVE:
        reason = d["reason"].replace("_", " ")
        return f"{d['crew_id']} on planned leave {d['from_date']} to {d['to_date']} ({reason})"
    if kind is ReportType.CERTIFICATION_LAPSE:
        return f"{d['crew_id']} {d['cert_type'].replace('_', ' ')} lapses {d['valid_to']}"
    if kind is ReportType.FLIGHT_DELAY:
        return f"{d['flight_id']} delayed {d['delay_minutes']} min"
    if kind is ReportType.AIRCRAFT_TECHNICAL:
        return f"{d['aircraft']} technical fault"
    if kind is ReportType.STATION_DISRUPTION:
        return f"{d['station']} closed ({d['reason']})"
    if kind is ReportType.GATE_CLOSURE:
        return f"{d['gate']} closed ({d['reason']})"
    return f"{d['flight_id']} moved to gate {d['new_gate']}"


def _verdict(v: Any) -> dict[str, Any]:
    return {
        "rule_id": v.rule_id,
        "status": v.status,
        "detail": v.detail,
        "used": v.used,
        "limit": v.limit,
        "headroom": v.headroom,
        "date": v.date,
    }


def _positioning(p: Any) -> dict[str, Any] | None:
    if p is None:
        return None
    return {
        "flight_id": p.flight_id,
        "from_station": p.from_station,
        "to_station": p.to_station,
        "arrives_utc": p.arrives_utc,
        "delay_hours": p.delay_hours,
    }


def _extras(o: Any) -> dict[str, Any]:
    """Shown next to an option. Seniority and reach rank only through their allowances in
    the cost; the risk score never ranks."""
    return {
        "base": o.base,
        "reachability_minutes": o.reachability_minutes,
        "seniority": o.seniority,
        "risk_score": o.risk_score,
        "resilience": o.resilience,
        "experience_years": o.experience_years,
    }


def _per_station(gates: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for g in gates:
        station = g.split("-", 1)[0]
        out[station] = out.get(station, 0) + 1
    return dict(sorted(out.items()))
