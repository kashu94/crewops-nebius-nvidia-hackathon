"""What the MCP server can do for a signed-in caller, without the MCP plumbing.

Every operation takes the ``Caller`` built from the request's verified token, never what the
user typed about themselves. Questions run the full answer pipeline, plans go through
``crewops.plans`` (four-eyes), and reports are queued with the sender's identity stamped
here. Crew tools use the same role-checked dispatch as the advisor.

With a conversation id, the previous turn of that conversation is read from AgentCore Memory
and passed in alongside the question (``agent.context``).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any

from crewops import alerts as alert_briefs
from crewops.agent.breaker import MODEL_BREAKER
from crewops.agent.context import Turn
from crewops.agent.memory import ConversationMemory, valid_conversation
from crewops.analysis.runner import (
    ALL_STATUSES,
    OPEN_STATUSES,
    UNAVAILABLE_STATUSES,
    absences_from,
)
from crewops.analysis.severity import severity
from crewops.auth.accounts import AccountError, Accounts, may_reset, new_password
from crewops.domain import DataError, Report, Role, authorize
from crewops.domain.desks import owning_desks
from crewops.domain.reports import (
    ABSENCE_TYPES,
    LEAVE_FILERS,
    OPERATIONAL_REPORTERS,
    ReportType,
    crew_out_of,
)
from crewops.mcp import views
from crewops.notify.subscriptions import AlertError, AlertSubscriptions
from crewops.plans.service import PlanError, Plans
from crewops.plans.service import seat_key as _seat_key
from crewops.plans.service import summary as plan_summary
from crewops.store import plans as plan_store
from crewops.store import rules as rule_store
from crewops.store import security as security_store
from crewops.store.items import from_dynamo
from crewops.store.reports import (
    add_suggestion,
    disruptions_with_status,
    filed_by,
    filed_recently,
    get_disruption,
    last_seen,
    mark_seen,
    record_filed,
    rejections_for,
    suggestions_for,
)
from crewops.store.users import get_profile, list_profiles
from crewops.tools import TOOL_SCHEMAS, Caller, allowed_tools, dispatch
from crewops.tools.errors import INTERNAL

CACHE_SECONDS = 60.0
"""How long one loaded operation answers questions before it is read again. Plan actions
on this server reload it at once; changes made elsewhere show within this time."""
FEED_CACHE_SECONDS = 4.0
"""How long one speaker-feed read serves every screen that polls."""
log = logging.getLogger(__name__)

REPORT_AHEAD_DAYS = 60
"""How far ahead a disruption may be reported. Anything later is leave planning."""
CREW_LOOK_BACK_DAYS = 5
"""How far back crew see their own flights."""
READERS = frozenset({Role.CONTROLLER, Role.APPROVER, Role.AUDITOR, Role.ADMIN, Role.SUPER_ADMIN})
ADMINS = frozenset({Role.ADMIN, Role.SUPER_ADMIN})
FEED_STATUSES = ("REPORTED", "OPTIONS_READY", "SUBMITTED", "RESOLVING", "COMMITTED", "CLOSED")
OUTCOME = {
    "REPORTED": "Received; crew control is analysing it.",
    "OPTIONS_READY": "Crew control is arranging cover.",
    "SUBMITTED": "A plan is waiting for approval.",
    "RESOLVING": "Handled by crew control; waiting for an approver to sign it off.",
    "COMMITTED": "Cover has been arranged.",
    "CLOSED": "Resolved by crew control.",
    "REVOKED": "Revoked by an admin: filed in error.",
}
RESOLVED = ("COMMITTED", "CLOSED")


def _remark(table: Any, disruption_id: str, status: str) -> str | None:
    """What crew control said when it resolved or closed the disruption, or the admin's
    reason for revoking it."""
    if status not in ("CLOSED", "REVOKED"):
        return None
    closed = [e for e in plan_store.audit_trail(table, disruption_id) if e.get("action") == status]
    return closed[-1].get("reason") if closed else None


def _airline(tools: Any, code: str) -> dict[str, str]:
    a = next((a for a in tools.seed.airlines if a.code == code), None)
    return {"code": code, "name": a.name if a else code, "color": a.color if a else "#64748b"}


def _iso_z(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _impact(analysis: Mapping[str, Any]) -> dict[str, Any]:
    """What a disruption touches: flights uncovered or at risk, and their passengers (each
    trip counted once, as the severity counts them)."""
    flights: set[str] = set(analysis.get("affected_flights") or [])
    per_trip: dict[str, int] = {}
    for seat in analysis.get("seats") or []:
        impact = seat.get("impact") or {}
        flights |= set(impact.get("uncovered_flights") or [])
        flights |= set(impact.get("at_risk_flights") or [])
        trip = str(seat.get("pairing_id"))
        per_trip[trip] = max(per_trip.get(trip, 0), int(impact.get("passengers") or 0))
    flights |= {leg["flight_id"] for leg in analysis.get("late_departures") or []}
    return {
        "flights": len(flights),
        "passengers": sum(per_trip.values()),
        "flight_ids": sorted(flights),
    }


def _crew_name(tools: Any, crew_id: str | None) -> str | None:
    crew = tools.world.crew.get(crew_id) if crew_id else None
    return crew.name if crew else None


def _outcome(status: str, handling: Mapping[str, Any]) -> str:
    """How a disruption stands, for whoever reported it."""
    if status == "OPTIONS_READY" and handling.get("kind") == "internal":
        return "Handled; crew control will mark it resolved."
    if status == "OPTIONS_READY" and handling.get("kind") == "decision":
        return "Crew control is deciding what to do with operations."
    if status == "CLOSED" and handling.get("kind") == "recorded":
        return "Recorded: no flights to cover on those days, and not called out for cover then."
    return OUTCOME.get(status, status)


def _analysis_view(tools: Any, analysis: Mapping[str, Any]) -> dict[str, Any]:
    """How a disruption is handled beyond its seats: the one-line handling, who is told,
    the flights it moves or delays and the gates that change."""
    late = {leg["flight_id"]: leg["minutes"] for leg in analysis.get("late_departures") or []}
    flights = [*analysis.get("affected_flights", []), *late]
    return {
        "handling": analysis.get("handling") or None,
        "airlines": [_airline(tools, code) for code in analysis.get("airlines") or []],
        "notices": analysis.get("notices") or [],
        "stands": analysis.get("stands") or [],
        "next_at_gate": analysis.get("next_at_gate") or [],
        "flights": views.flight_rows(tools, flights, late),
        "delays": analysis.get("delays") or [],
        "notes": analysis.get("notes") or [],
    }


def _seat(s: Mapping[str, Any]) -> dict[str, Any]:
    """A seat as the console shows it: who is out, the impact and the ranked options."""
    keep = (
        "pairing_id",
        "role",
        "crew_out",
        "status",
        "note",
        "legal_options",
        "excluded",
        "cancellation_cost_inr",
        "cancellation_multiple",
        "delay_hours",
        "delay_date",
    )
    out = {k: s.get(k) for k in keep}
    out["impact"] = s.get("impact", {})
    out["options"] = [
        {
            k: o.get(k)
            for k in (
                "rank",
                "action",
                "strategy",
                "crew_id",
                "name",
                "crew_rank",
                "cost_inr",
                "cost_breakdown",
                "delay_hours",
            )
        }
        for o in s.get("options", [])
    ]
    return out


class ApiError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class CrewOpsApi:
    def __init__(
        self,
        table: Any,
        live_factory: Callable[[], Any],
        send: Callable[[dict[str, Any]], None],
        *,
        plans: Plans | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        cache_seconds: float = CACHE_SECONDS,
        memory: ConversationMemory | None = None,
        alerts: AlertSubscriptions | None = None,
        accounts: Accounts | None = None,
    ) -> None:
        """``live_factory`` builds the answer pipeline over the current operation
        (``agent.service.build``); ``send`` puts a report on the ingest queue. Without
        ``memory`` every question stands on its own; without ``alerts`` the alert tools
        say alerts are not set up."""
        self.table = table
        self.accounts = accounts
        self._feed_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}
        self.live_factory = live_factory
        self.send = send
        self.plans = plans or Plans(table)
        self.clock = clock
        self.cache_seconds = cache_seconds
        self._live: Any = None
        self._loaded = 0.0
        self._refreshing = False
        # Bumped by ``invalidate``: a reload that began before it read the old roster.
        self._generation = 0
        self._loading = threading.Lock()
        # The pipeline keeps per-question state (model usage, the advisor's last run), so
        # one question at a time uses it; tool calls run on other threads meanwhile.
        self._asking = threading.Lock()
        self.memory = memory
        self.alerts = alerts
        # A turn is remembered after its answer is returned, never delaying it.
        self._remembering = ThreadPoolExecutor(max_workers=2, thread_name_prefix="memory")

    # ------------------------------------------------------------------
    # Questions and tools
    # ------------------------------------------------------------------

    def live(self) -> Any:
        """The current operation. Loaded once; after that, a stale copy keeps answering
        while a fresh one loads in the background, so no question waits on the reload."""
        with self._loading:
            if self._live is None:
                self._live = self.live_factory()
                self._loaded = time.monotonic()
                return self._live
            stale = time.monotonic() - self._loaded > self.cache_seconds
            if stale and not self._refreshing:
                self._refreshing = True
                threading.Thread(
                    target=self._refresh, args=(self._generation,), daemon=True
                ).start()
            return self._live

    def _refresh(self, generation: int = 0) -> None:
        try:
            fresh = self.live_factory()
            with self._loading:
                if generation == self._generation:
                    self._live, self._loaded = fresh, time.monotonic()
        except Exception:
            log.exception("reloading the operation failed; the previous copy stays")
        finally:
            with self._loading:
                self._refreshing = False

    def invalidate(self) -> None:
        with self._loading:
            self._live = None
            self._generation += 1
        self._feed_cache.clear()  # an action here is heard at once, not after the cache

    def ask(self, caller: Caller, question: str, conversation_id: str = "") -> dict[str, Any]:
        """With a ``conversation_id`` (letters, digits, - and _), the previous turn can fill
        in what this question leaves out."""
        if caller.roles <= {Role.SPEAKER}:
            # A speaker device hears alert briefs; questions are for people.
            raise ApiError("FORBIDDEN", "a speaker device does not ask questions")
        if conversation_id and not valid_conversation(conversation_id):
            raise ApiError(
                "INVALID",
                "conversation_id: up to 100 letters, digits, - or _, not starting with - or _",
            )
        remembered = self.memory is not None and bool(conversation_id)
        previous = None
        if remembered:
            previous = self.memory.last_turn(caller.user_id, conversation_id, self.clock())
        live = self.live()
        with self._asking:
            answer, usage = live.ask(question, caller, previous)
        record = answer.to_dict()
        blocked = next((n for n in record["notes"] if str(n).startswith("blocked:")), None)
        if record["source"] == "refused" and blocked:
            # Who tried what, for the admin's activity log (the question already redacted).
            security_store.log_blocked(
                self.table, caller.user_id, str(blocked), str(record["question"]), self.clock()
            )
        if remembered:
            turn = Turn.of(answer, self.clock())
            self._remembering.submit(
                self.memory.remember, caller.user_id, conversation_id, turn, record["text"]
            )
        return {
            "answer": record["text"],
            "verified": record["verified"],
            "source": record["source"],
            "awaiting": record["awaiting"],
            "intent": (record["route"] or {}).get("intent"),
            "notes": record["notes"],
            "model_calls": usage.get("calls", 0),
            # Whether the model service is in use or paused (crewops.agent.breaker).
            "models": MODEL_BREAKER.status(),
        }

    def whoami(self, caller: Caller) -> dict[str, Any]:
        return {
            "user_id": caller.user_id,
            "roles": sorted(r.value for r in caller.roles),
            "crew_id": caller.crew_id,
            "desk": caller.desk,
        }

    def list_tools(self, caller: Caller) -> list[dict[str, Any]]:
        allowed = allowed_tools(caller)
        return [
            {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
            for t in TOOL_SCHEMAS
            if t["name"] in allowed
        ]

    def run_tool(self, caller: Caller, name: str, arguments: Mapping[str, Any] | None) -> dict:
        entry = dispatch(self.live().pipeline.tools, caller, name, dict(arguments or {}))
        out = entry.to_dict()
        if entry.error_code == INTERNAL:  # logged by dispatch; the caller sees no internals
            out["error"] = "the tool failed unexpectedly; it has been logged"
        return out

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    def report(
        self,
        caller: Caller,
        report_type: str,
        details: Mapping[str, Any],
        narrative: str = "",
    ) -> dict[str, Any]:
        """Check a report now (so the reporter hears about a mistake at once), then queue it.
        Who sent it comes from the token, never from the request."""
        doc = {
            "report_id": f"r-{uuid.uuid4().hex[:12]}",
            "type": report_type,
            "reported_by": {
                "user_id": caller.user_id,
                "roles": sorted(r.value for r in caller.roles),
                "crew_id": caller.crew_id,
            },
            "reported_utc": self.clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "details": dict(details),
            "narrative": narrative,
        }
        try:
            report = Report.from_dict(doc)
        except DataError as exc:
            raise ApiError("INVALID", str(exc)) from None
        if reason := authorize(report):
            raise ApiError("FORBIDDEN", reason)
        if outside := self._outside_window(report):
            raise ApiError("INVALID", outside)
        if overlap := self._already_out(report.details, report.report_type.value, report.report_id):
            raise ApiError("DUPLICATE", overlap)
        self.send(report.to_dict())
        record_filed(self.table, report)
        return {
            "report_id": report.report_id,
            "status": "queued",
            "note": "It is checked against the roster and analysed within a minute.",
        }

    def _outside_window(self, report: Report) -> str | None:
        """Why a report's dates are out of bounds, or None. A disruption is about today
        and the next two months; further ahead is leave, booked through rostering, which
        may run to the end of the rostered period. Nothing is reported for the past."""
        tools = self.live().pipeline.tools
        today = tools.world.snapshot_utc.date()
        last = tools.week[-1] if tools.week else today
        leave = report.report_type is ReportType.PLANNED_LEAVE
        earliest = today + timedelta(days=1) if leave else today
        latest = last if leave else min(last, today + timedelta(days=REPORT_AHEAD_DAYS))
        d = report.details
        days: list[date] = [date.fromisoformat(d[k]) for k in ("from_date", "to_date") if k in d]
        days += [date.fromisoformat(d[k][:10]) for k in ("from_utc", "to_utc") if d.get(k)]
        if d.get("flight_id"):
            try:
                days.append(date.fromisoformat(str(d["flight_id"])[-10:]))
            except ValueError:
                return f"flight_id: a flight id with its date, like DX401-{today}"
        # A certificate may already have lapsed; one lapsing beyond the window is not a
        # disruption yet.
        if d.get("valid_to") and date.fromisoformat(d["valid_to"]) > latest:
            return f"valid_to: up to {latest} (two months ahead)"
        if report.report_type is ReportType.GATE_CHANGE:
            current = next((g.gate for g in tools.gates if g.flight_id == d.get("flight_id")), None)
            if current and current == d.get("new_gate"):
                return f"new_gate: {d['flight_id']} already boards at {current}; pick another gate"
        bad = [x for x in days if not earliest <= x <= latest]
        if not bad:
            return None
        what = "leave" if leave else "a disruption"
        return f"dates for {what} run from {earliest} to {latest}; {bad[0]} is outside that"

    # ------------------------------------------------------------------
    # Plans
    # ------------------------------------------------------------------

    def queue(self, caller: Caller) -> list[dict[str, Any]]:
        return self.plans.queue(caller)

    def disruptions(self, caller: Caller, limit: int = 40) -> list[dict[str, Any]]:
        """Recent disruptions in every status, newest report first: the desk's feed."""
        if not caller.roles & READERS:
            raise ApiError("FORBIDDEN", "your role may not read disruptions")
        items = [i for status in FEED_STATUSES for i in disruptions_with_status(self.table, status)]
        tools = self.live().pipeline.tools
        today = self.clock().date()  # as the record rates it: how soon it flies from now
        out = []
        for item in items:
            report = from_dynamo(item.get("report") or {})
            analysis = from_dynamo(item.get("analysis") or {})
            out.append(
                {
                    "disruption_id": item["disruption_id"],
                    "status": item["status"],
                    "report_type": report.get("type"),
                    "reported_utc": report.get("reported_utc"),
                    "reported_by": (report.get("reported_by") or {}).get("user_id"),
                    "details": report.get("details", {}),
                    "summary": analysis.get("summary", ""),
                    "severity": severity(analysis, report.get("reported_utc"), today)
                    if analysis
                    else None,
                    "handling": (analysis.get("handling") or {}).get("kind"),
                    "airlines": analysis.get("airlines") or [],
                    "desks": [
                        d["desk_id"]
                        for d in views.desks_of_disruption(
                            tools,
                            analysis,
                            [seat["pairing_id"] for seat in analysis.get("seats", [])],
                        )
                    ],
                }
            )
        out.sort(key=lambda d: d.get("reported_utc") or "", reverse=True)
        return out[: max(1, min(int(limit), 100))]

    def show(self, caller: Caller, disruption_id: str) -> dict[str, Any]:
        item = get_disruption(self.table, disruption_id)
        report = from_dynamo((item or {}).get("report") or {})
        own = (report.get("reported_by") or {}).get("user_id") == caller.user_id
        # Someone who may not read it is told the same as when it does not exist, so ids
        # cannot be probed.
        if item is None or not (caller.roles & READERS or own):
            raise ApiError("NOT_FOUND", f"no disruption {disruption_id} that you may read")
        analysis = from_dynamo(item.get("analysis") or {})
        handling = analysis.get("handling") or {}
        if not caller.roles & READERS:
            # The person who filed it hears how it stands, nothing about other crew,
            # replacements, costs or other desks.
            return {
                "disruption_id": disruption_id,
                "status": item["status"],
                "outcome": _outcome(item["status"], handling),
                "report_type": report.get("type"),
                "reported_utc": report.get("reported_utc"),
                "reported_by": caller.user_id,
                "details": report.get("details", {}),
                # Handled outside crew control (gates moved, a delay absorbed): what was
                # done is about flights and gates, never about crew, so they may read it.
                "handled": handling.get("text") if handling.get("kind") == "internal" else None,
                # Crew control's remark on resolving it, which is about what was done.
                "remark": _remark(self.table, disruption_id, item["status"]),
            }
        tools = self.live().pipeline.tools
        seats = [_seat(s) for s in analysis.get("seats", [])]
        still_open = item["status"] in ("OPTIONS_READY", "SUBMITTED")
        for seat in seats:
            seat["context"] = views.seat_context(tools, seat)
            # Filled since by an approved plan (for this seat on another day, say): no cover
            # is needed, so no options are offered for it.
            seat["re_crewed"] = views.re_crewed(tools, seat) if still_open else None
            seat["live"] = None if seat["re_crewed"] else views.live_options(tools, seat)
            seat["reserves"] = views.reserve_capacity(
                tools, seat["role"], seat["impact"].get("affected_dates") or []
            )
        desks = views.desks_of_disruption(tools, analysis, [s["pairing_id"] for s in seats])
        joint = analysis.get("joint_plan")
        mine = {(s["pairing_id"], s["role"], s["crew_out"]) for s in seats if s.get("crew_out")}
        return {
            "disruption_id": disruption_id,
            "status": item["status"],
            "summary": analysis.get("summary", ""),
            "severity": severity(analysis, report.get("reported_utc"), self.clock().date())
            if analysis
            else None,
            "report_type": report.get("type"),
            "reported_utc": report.get("reported_utc"),
            "reported_by": (report.get("reported_by") or {}).get("user_id"),
            "details": report.get("details", {}),
            # What the reporter (or the crew member) added for crew control, in their words.
            "narrative": report.get("narrative") or "",
            "desks": desks,
            # Touching no aircraft at all (a closed gate nobody was using): any desk's.
            "yours": bool(caller.desk)
            and Role.CONTROLLER in caller.roles
            and (not desks or any(d["desk_id"] == caller.desk for d in desks)),
            **_analysis_view(tools, analysis),
            # The ranked options each seat had when it was analysed; approval re-checks.
            "seats": seats,
            "joint_plan": joint if joint and joint.get("assignments") else None,
            # Who else wants these crew: other open disruptions and submitted plans.
            "contention": views.contention(
                tools,
                self.table,
                disruption_id,
                [
                    (
                        {
                            o["crew_id"]
                            for o in ((s["live"] or {}).get("options") or s["options"])
                            if o.get("crew_id")
                        },
                        set(s["impact"].get("affected_dates") or []),
                    )
                    for s in seats
                ],
                mine,
            ),
            # Other open disruptions needing this very seat (the same person out on another
            # day of the same trip): one cover fills them all.
            "same_seat": views.same_seat(tools, self.table, disruption_id, mine),
            # Crew among the options who have since been reported unavailable on these dates:
            # shown struck out, with when, rather than silently dropped.
            "unavailable_now": self._unavailable_now(disruption_id, seats),
            # Other desks' suggestions of cover: advice for the owning desk, which alone
            # may act on it; other readers see it as part of the record.
            "suggestions": [
                {k: s.get(k) for k in ("by", "desk", "seat", "crew_id", "note", "at")}
                | {"name": _crew_name(tools, s.get("crew_id"))}
                for s in suggestions_for(self.table, disruption_id)
            ],
            "plans": [
                {"status": p["status"], **plan_summary(p)}
                for p in plan_store.plans_for(self.table, disruption_id)
            ],
            "audit": [
                {k: e.get(k) for k in ("at", "action", "actor", "plan_id", "reason") if e.get(k)}
                for e in plan_store.audit_trail(self.table, disruption_id)
            ],
        }

    def suggest(
        self, caller: Caller, disruption_id: str, seat: str, crew_id: str, note: str = ""
    ) -> dict[str, Any]:
        """A controller on another desk suggests cover for a seat. It is shown to the owning
        desk; it plans nothing and changes nothing."""
        if Role.CONTROLLER not in caller.roles or not caller.desk:
            raise ApiError("FORBIDDEN", "only a controller with a desk can suggest cover")
        item = get_disruption(self.table, disruption_id)
        if item is None:
            raise ApiError("NOT_FOUND", f"no disruption {disruption_id}")
        if item["status"] != "OPTIONS_READY":
            raise ApiError("WRONG_STATE", f"{disruption_id} is {item['status']}")
        analysis = from_dynamo(item.get("analysis") or {})
        tools = self.live().pipeline.tools
        owners = views.desks_of_disruption(tools, analysis)
        if any(d["desk_id"] == caller.desk for d in owners):
            raise ApiError(
                "INVALID", "this is your own desk's disruption: choose the option instead"
            )
        if seat not in {_seat_key(s) for s in analysis.get("seats", [])}:
            raise ApiError("INVALID", f"no such seat: {seat}")
        if crew_id not in tools.world.crew:
            raise ApiError("INVALID", f"no crew member {crew_id}")
        note = (note or "").strip()[:300]
        add_suggestion(
            self.table,
            disruption_id,
            caller.user_id,
            caller.desk,
            seat,
            crew_id,
            note,
            self.clock(),
        )
        return {
            "status": "suggested",
            "disruption_id": disruption_id,
            "seat": seat,
            "crew_id": crew_id,
        }

    # ------------------------------------------------------------------
    # The crew member's own roster
    # ------------------------------------------------------------------

    def my_roster(self, caller: Caller) -> dict[str, Any]:
        """The signed-in crew member's pairings, duty days and duty clock. Only ever their
        own, since the crew id comes from the verified profile and not the request."""
        if not caller.crew_id:
            raise ApiError("FORBIDDEN", "only crew have a roster")
        tools = self.live().pipeline.tools
        mine = tools.lookup("pairing_crew", {"crew_id": caller.crew_id})["rows"]
        today = tools.world.snapshot_utc.date()
        first = today - timedelta(days=CREW_LOOK_BACK_DAYS)
        last = today + timedelta(days=REPORT_AHEAD_DAYS)
        if tools.week:
            last = min(last, tools.week[-1])  # nothing is rostered after the calendar ends
        pairings = []
        for row in mine:
            (p,) = tools.lookup("pairings", {"pairing_id": row["pairing_id"]})["rows"]
            days = [d for d in p["days"] if first.isoformat() <= d["date"] <= last.isoformat()]
            if not days:
                continue  # outside the days a crew member sees
            pairings.append(
                {
                    "pairing_id": p["pairing_id"],
                    "aircraft": p["aircraft"],
                    "role": row["role"],
                    "days": days,
                }
            )
        crew = tools.world.crew.get(caller.crew_id)
        # A reserve's duty is standby: the days on call and the window a callout falls in.
        on_call = [
            {
                "date": d.isoformat(),
                "base": duty.base,
                "from_utc": duty.window_start.strftime("%H:%M"),
                "to_utc": duty.window_end.strftime("%H:%M"),
            }
            for duty in tools.world.reserves.get(caller.crew_id, ())
            for d in duty.dates
            if first <= d <= last
        ]
        return {
            "crew_id": caller.crew_id,
            "name": mine[0]["name"] if mine else (crew.name if crew else None),
            "rank": crew.rank.value if crew else None,
            "on_call": sorted(on_call, key=lambda o: o["date"]),
            "airline": tools.world.airline_of_crew.get(caller.crew_id),
            "notices": self._crew_notices(caller.crew_id),
            "reported_unavailable": self._reported_out(caller.crew_id),
            "clock": tools.duty_clock(caller.crew_id),
            # The days shown: a few back, two months ahead; reports only from today.
            "window": {
                "from": first.isoformat(),
                "to": last.isoformat(),
                "today": today.isoformat(),
            },
            "pairings": sorted(pairings, key=lambda p: p["days"][0]["date"] if p["days"] else ""),
        }

    def _unavailable_now(
        self, disruption_id: str, seats: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        wanted: dict[str, set[str]] = {}
        for seat in seats:
            dates = set(seat["impact"].get("affected_dates") or [])
            options = [*seat.get("options", []), *((seat.get("live") or {}).get("options") or [])]
            for o in options:
                if o.get("crew_id"):
                    wanted.setdefault(o["crew_id"], set()).update(dates)
        if not wanted:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for status in UNAVAILABLE_STATUSES:
            for item in disruptions_with_status(self.table, status):
                if item["disruption_id"] == disruption_id:
                    continue
                report = from_dynamo(item.get("report") or {})
                kind, d = report.get("type", ""), report.get("details") or {}
                if kind not in ABSENCE_TYPES:
                    continue
                days = {
                    (date.fromisoformat(d["from_date"]) + timedelta(days=i)).isoformat()
                    for i in range(
                        (date.fromisoformat(d["to_date"]) - date.fromisoformat(d["from_date"])).days
                        + 1
                    )
                }
                for crew_id in crew_out_of(kind, d):
                    if crew_id in wanted and wanted[crew_id] & days:
                        out[crew_id] = {
                            "disruption_id": item["disruption_id"],
                            "reported_utc": report.get("reported_utc"),
                            "reason": d.get("reason"),
                            "from_date": d["from_date"],
                            "to_date": d["to_date"],
                        }
        return out

    def _reported_out(self, crew_id: str) -> list[dict[str, Any]]:
        """The periods this crew member is already reported unavailable, so the same days
        are not reported twice."""
        out = []
        for status in UNAVAILABLE_STATUSES:
            for item in disruptions_with_status(self.table, status):
                report = from_dynamo(item.get("report") or {})
                kind, d = report.get("type", ""), report.get("details") or {}
                if kind in ABSENCE_TYPES and crew_id in (crew_out_of(kind, d)):
                    out.append(
                        {
                            "disruption_id": item["disruption_id"],
                            "from_date": d["from_date"],
                            "to_date": d["to_date"],
                            "reason": d.get("reason"),
                            "status": item["status"],
                        }
                    )
        return sorted(out, key=lambda r: r["from_date"])

    def _crew_notices(self, crew_id: str) -> list[dict[str, Any]]:
        """In-app notices addressed to this crew member (a certificate about to lapse),
        newest first, with how the disruption behind each stands."""
        out = []
        for status in FEED_STATUSES:
            for item in disruptions_with_status(self.table, status):
                analysis = from_dynamo(item.get("analysis") or {})
                for n in analysis.get("notices") or []:
                    if n.get("channel") == "in_app" and n.get("about") == crew_id:
                        report = from_dynamo(item.get("report") or {})
                        out.append(
                            {
                                "disruption_id": item["disruption_id"],
                                "status": item["status"],
                                "at": report.get("reported_utc"),
                                "message": n["message"],
                            }
                        )
        return sorted(out, key=lambda n: n["at"] or "", reverse=True)

    # ------------------------------------------------------------------
    # Reporting desk
    # ------------------------------------------------------------------

    def my_reports(self, caller: Caller, scope: str = "mine", limit: int = 30) -> list[dict]:
        """Reports and how each stands, newest first: still being checked, refused (and
        why), or the disruption it became. ``scope`` "mine" is what the caller filed;
        "crew" is what crew filed about themselves, for the reporting desk; "leave" is the
        planned leave admins booked."""
        limit = max(1, min(int(limit), 100))
        if scope == "crew":
            if not caller.roles & (OPERATIONAL_REPORTERS | READERS):
                raise ApiError("FORBIDDEN", "only the reporting desk and crew control see these")
            filed = [
                f for f in filed_recently(self.table, 300) if "crew" in (f.get("roles") or [])
            ][:limit]
            states = [self._filed_state(f) for f in filed]
            if not caller.roles & READERS:
                # The reporting desk follows who is out and how it stands, never why (sick,
                # fatigue and the like are crew control's business), as on the operations view.
                for s in states:
                    s["details"] = {k: v for k, v in s["details"].items() if k != "reason"}
                    s["narrative"] = ""
            return states
        elif scope == "leave":
            # Leave booked by rostering: the admins who book it, and crew control.
            if not caller.roles & (LEAVE_FILERS | READERS):
                raise ApiError("FORBIDDEN", "only admins and crew control see planned leave")
            filed = [
                f
                for f in filed_recently(self.table, 5000)
                if from_dynamo(f.get("report") or {}).get("type") == "planned_leave"
            ][:limit]
        elif scope == "mine":
            filed = filed_by(self.table, caller.user_id, limit)
        else:
            raise ApiError("INVALID", "scope is mine, crew or leave")
        return [self._filed_state(f) for f in filed]

    def _filed_state(self, filed: Mapping[str, Any]) -> dict[str, Any]:
        report = from_dynamo(filed.get("report") or {})
        out = {
            "disruption_id": filed["report_id"],
            "report_type": report.get("type"),
            "reported_utc": report.get("reported_utc"),
            "reported_by": filed.get("user_id"),
            "details": report.get("details", {}),
            "narrative": report.get("narrative") or "",
            "handled": None,
            "remark": None,
            "desks": [],
        }
        item = get_disruption(self.table, filed["report_id"])
        if item is not None:
            analysis = from_dynamo(item.get("analysis") or {})
            handling = analysis.get("handling") or {}
            # The controller desk it went to: set when it is analysed, so none before.
            desks = owning_desks(analysis, lambda _pairing: None) if analysis else []
            return out | {
                "status": item["status"],
                "desks": [{"desk_id": d.desk_id, "controller": d.controller} for d in desks],
                "outcome": _outcome(item["status"], handling),
                "handling": handling.get("kind"),
                "handled": handling.get("text") if handling.get("kind") == "internal" else None,
                "remark": _remark(self.table, filed["report_id"], item["status"]),
            }
        refused = rejections_for(self.table, filed["report_id"])
        if refused:
            reason = refused[-1].get("reason", "")
            return out | {"status": "REJECTED", "outcome": f"Not accepted: {reason}"}
        return out | {"status": "QUEUED", "outcome": "Received; being checked against the roster."}

    def _already_out(
        self, details: Mapping[str, Any], report_type: str, report_id: str
    ) -> str | None:
        """The days a crew member in this report is already reported unavailable, said as
        the reason to refuse it, or None. Counts open disruptions and reports still in the
        queue (filed, neither stored nor refused yet); a resend of the same report is left
        to the worker, which stores it once."""
        if report_type not in ABSENCE_TYPES:
            return None
        wanted = set(crew_out_of(report_type, details))
        start, end = (
            date.fromisoformat(details["from_date"]),
            date.fromisoformat(details["to_date"]),
        )

        def overlap(other_id: str, other: Mapping[str, Any]) -> str | None:
            if other_id == report_id:
                return None
            d = other.get("details") or {}
            kind = other.get("type", "")
            if kind not in ABSENCE_TYPES:
                return None
            clash = wanted & set(crew_out_of(kind, d))
            if not clash:
                return None
            o_start, o_end = date.fromisoformat(d["from_date"]), date.fromisoformat(d["to_date"])
            if start <= o_end and o_start <= end:
                return (
                    f"{', '.join(sorted(clash))} is already reported unavailable "
                    f"{d['from_date']} to {d['to_date']} ({other_id})"
                )
            return None

        for status in UNAVAILABLE_STATUSES:
            for item in disruptions_with_status(self.table, status):
                report = from_dynamo(item.get("report") or {})
                if why := overlap(str(item["disruption_id"]), report):
                    return why
        # Reports still queued: only a clashing one is looked up, to see whether the worker
        # has stored it (counted above, or closed since) or refused it (no longer counts).
        for row in filed_recently(self.table):
            rid = str(row["report_id"])
            why = overlap(rid, from_dynamo(row.get("report") or {}))
            if (
                why
                and get_disruption(self.table, rid) is None
                and not rejections_for(self.table, rid)
            ):
                return why
        return None

    # ------------------------------------------------------------------
    # Spoken alerts: the desk speaker and "welcome back"
    # ------------------------------------------------------------------

    RECENT_RESOLVED = 10
    """Only the latest few resolved disruptions can still raise an alert, otherwise every
    speaker poll gets slower with each disruption ever handled."""

    def _alert_items(self) -> list[dict]:
        def reported(item: Mapping[str, Any]) -> str:
            return str((from_dynamo(item.get("report") or {})).get("reported_utc") or "")

        items: list[dict] = []
        for status in ALL_STATUSES:
            found = disruptions_with_status(self.table, status)
            if status in RESOLVED:
                found = sorted(found, key=reported, reverse=True)[: self.RECENT_RESOLVED]
            items += found
        return items

    def _spoken_alerts(self, scope: str, since: str | None = None, limit: int = 20) -> list[dict]:
        tools = self.live().pipeline.tools

        def aircraft(pairing_id: str) -> str | None:
            p = tools.world.pairings.get(pairing_id)
            return p.aircraft if p else None

        from crewops.tools.port import describe_report

        items = self._alert_items()
        try:
            return alert_briefs.alerts(
                items,
                lambda d: plan_store.audit_trail(self.table, d),
                aircraft,
                describe_report,
                scope,
                since,
                max(1, min(int(limit), 50)),
            )
        except ValueError as exc:
            raise ApiError("INVALID", str(exc)) from None

    def speaker_feed(self, caller: Caller, scope: str = "room", limit: int = 20) -> dict:
        """What a desk or ops-room speaker announces: the latest event of each disruption
        for its desk, the approvals corner, or the whole room. Only a speaker device hears
        it; a person reads the console instead."""
        if Role.SPEAKER not in caller.roles:
            raise ApiError("FORBIDDEN", "only a speaker device hears the alert feed")
        # Several screens poll every few seconds: one read serves them all for a moment.
        limit = max(1, min(int(limit), 50))  # also bounds the cache to one entry per scope
        key = (scope, limit)
        now = time.monotonic()
        cached = self._feed_cache.get(key)
        if cached is None or now - cached[0] > FEED_CACHE_SECONDS:
            cached = (now, self._spoken_alerts(scope, limit=limit))
            self._feed_cache[key] = cached
        return {"scope": scope, "alerts": cached[1]}

    def ops_board(self, caller: Caller) -> dict[str, Any]:
        """The ops-room wall display (the Fire TV app). Cached like the speaker feed, since
        every screen in the room polls it."""
        if not caller.roles & (READERS | {Role.SPEAKER}):
            raise ApiError("FORBIDDEN", "only the ops room and crew control see the board")
        now = time.monotonic()
        cached = self._feed_cache.get(("board",))
        if cached is None or now - cached[0] > FEED_CACHE_SECONDS:
            cached = (now, self._board())
            self._feed_cache[("board",)] = cached
        return cached[1]

    def _board(self) -> dict[str, Any]:
        from crewops.domain.desks import DESKS
        from crewops.engine.options import Strategy
        from crewops.tools.port import describe_report

        tools = self.live().pipeline.tools
        world = tools.world
        today = world.snapshot_utc.date()
        clock = self.clock()
        level_rank = {"high": 0, "medium": 1, "low": 2}
        cards = []
        for status in OPEN_STATUSES:
            for item in disruptions_with_status(self.table, status):
                try:
                    report = Report.from_dict(from_dynamo(item["report"]))
                except (DataError, KeyError, TypeError):
                    continue
                analysis = from_dynamo(item.get("analysis") or {})
                rated = severity(analysis, report.reported_utc.isoformat(), today)
                desks = views.desks_of_disruption(
                    tools, analysis, [s["pairing_id"] for s in analysis.get("seats", [])]
                )
                trail = plan_store.audit_trail(self.table, item["disruption_id"])
                latest = max(trail, key=lambda e: str(e.get("at") or "")) if trail else {}
                since = str(item.get("analysed_utc") or report.reported_utc.isoformat())
                try:
                    waited = (
                        clock - datetime.fromisoformat(since.replace("Z", "+00:00"))
                    ).total_seconds()
                except ValueError:
                    waited = 0
                cover = []
                for seat in analysis.get("seats") or []:
                    rec = seat.get("recommended") or {}
                    if rec.get("strategy"):
                        try:
                            how = Strategy(rec["strategy"]).label
                        except ValueError:
                            how = str(rec["strategy"])
                        who = (
                            f"{rec.get('name')} ({rec.get('crew_id')})"
                            if rec.get("crew_id")
                            else ""
                        )
                        cover.append(
                            {
                                "seat": f"{seat.get('pairing_id')} {seat.get('role')}",
                                "who": who,
                                "how": how,
                                "cost_inr": rec.get("cost_inr"),
                            }
                        )
                cards.append(
                    {
                        "disruption_id": item["disruption_id"],
                        "status": item["status"],
                        "handling": (analysis.get("handling") or {}).get("kind"),
                        "what": describe_report(report),
                        "report_type": report.report_type.value,
                        "severity": rated["level"] if analysis else None,
                        "reasons": rated["reasons"] if analysis else [],
                        "desk": desks[0] if desks else None,
                        "escalated": latest.get("action") == "ESCALATED",
                        "waiting_minutes": max(0, int(waited // 60)),
                        "airlines": analysis.get("airlines") or [],
                        "cover": cover[:3],
                        "impact": _impact(analysis),
                    }
                )
        cards.sort(
            key=lambda c: (
                not c["escalated"],
                level_rank.get(c["severity"] or "low", 3),
                -c["waiting_minutes"],
            )
        )
        desks = []
        for d in DESKS:
            mine = [c for c in cards if c["desk"] and c["desk"]["desk_id"] == d.desk_id]
            desks.append(
                {
                    "desk_id": d.desk_id,
                    "controller": d.controller,
                    "open": len(mine),
                    "high": sum(1 for c in mine if c["severity"] == "high"),
                    "escalated": sum(1 for c in mine if c["escalated"]),
                    "submitted": sum(1 for c in mine if c["status"] == "SUBMITTED"),
                }
            )
        flights_today = [
            f
            for f in world.flights.values()
            if f.date == today and f.flight_id not in world.cancelled
        ]
        out_today = {c for c, days in world.unavailable.items() if any(d == today for d, _ in days)}
        reserves = {
            r
            for r, duties in world.reserves.items()
            if any(today in d.dates for d in duties) and r not in out_today
        }
        affected = {f for c in cards for f in c["impact"]["flight_ids"]}
        upcoming = sorted(
            (f for f in world.flights.values() if f.dep_utc >= world.snapshot_utc),
            key=lambda f: f.dep_utc,
        )[:8]
        # keyed by airline too: cover only ever comes from the disrupted airline's own crew
        cover_today: dict[tuple[str, str, str], int] = {}
        for crew_id, duties in world.reserves.items():
            if not any(today in d.dates for d in duties) or crew_id in out_today:
                continue
            crew = world.crew.get(crew_id)
            if crew is not None:
                key = (world.airline_of_crew.get(crew_id) or "", crew.base, crew.rank.value)
                cover_today[key] = cover_today.get(key, 0) + 1
        return {
            "as_of": clock.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "today": today.isoformat(),
            # What just happened, as the desk speaker announces it.
            "latest": [
                {k: a.get(k) for k in ("at", "event", "title", "text", "disruption_id", "severity")}
                for a in self._spoken_alerts("room", limit=5)
            ],
            "departures": [
                {
                    "flight_no": f.flight_no,
                    "from": f.dep_station,
                    "to": f.arr_station,
                    "dep_utc": f.dep_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "aircraft": f.aircraft,
                    "airline": world.airline_of_aircraft.get(f.aircraft),
                    "state": "cancelled"
                    if f.flight_id in world.cancelled
                    else "affected"
                    if f.flight_id in affected
                    else "on plan",
                }
                for f in upcoming
            ],
            "reserve_cover": [
                {"airline": airline, "base": base, "rank": rank, "on_call": n}
                for (airline, base, rank), n in sorted(cover_today.items())
            ],
            "system": {"models": MODEL_BREAKER.status()},
            "counts": {
                level: sum(1 for c in cards if c["severity"] == level)
                for level in ("high", "medium", "low")
            }
            | {"escalated": sum(1 for c in cards if c["escalated"]), "open": len(cards)},
            "desks": desks,
            "disruptions": cards[:30],
            "day": {
                "flights": len(flights_today),
                "passengers": sum(f.booked or 0 for f in flights_today),
                "crew_out": len(out_today),
                "reserves_on_call": len(reserves),
            },
        }

    @staticmethod
    def _scope_of(caller: Caller) -> str | None:
        """The alerts a person follows: their desk, or approvals; None for other roles."""
        if Role.CONTROLLER in caller.roles and caller.desk:
            return caller.desk
        if Role.APPROVER in caller.roles:
            return alert_briefs.APPROVALS
        return None

    def my_alerts(self, caller: Caller, limit: int = 5) -> list[dict]:
        """The latest updates on this controller's desk, or for approvers, newest first:
        what Alexa reads when asked what is waiting. Marks nothing seen."""
        scope = self._scope_of(caller)
        if scope is None:
            raise ApiError("FORBIDDEN", "only controllers and approvers have alerts")
        return self._spoken_alerts(scope, limit=limit)

    def push_subscribe(self, caller: Caller, endpoint: str, p256dh: str, auth: str) -> dict:
        """Desktop alerts in this browser: the push subscription its service worker made.
        Controllers and approvers only; only browser push services are accepted."""
        import re

        from crewops.notify.webpush import allowed_endpoint, unb64u
        from crewops.store import push

        if self._scope_of(caller) is None:
            raise ApiError("FORBIDDEN", "desktop alerts are for controllers and approvers")
        if not isinstance(endpoint, str) or len(endpoint) > 1000 or not allowed_endpoint(endpoint):
            raise ApiError("INVALID", "endpoint: not a browser push service")
        b64 = re.compile(r"^[A-Za-z0-9_-]+$")
        try:
            if not (b64.match(p256dh) and b64.match(auth)):
                raise ValueError
            if len(unb64u(p256dh)) != 65 or len(unb64u(auth)) != 16:
                raise ValueError
        except (ValueError, TypeError):
            raise ApiError("INVALID", "keys: not a browser's push keys") from None
        mine = push.subscriptions(self.table, caller.user_id)
        if not any(s["endpoint"] == endpoint for s in mine) and len(mine) >= push.MAX_PER_PERSON:
            raise ApiError(
                "LIMIT", f"desktop alerts are on in {push.MAX_PER_PERSON} browsers already"
            )
        push.save(self.table, caller.user_id, endpoint, p256dh, auth, self.clock())
        return {"status": "subscribed"}

    def push_unsubscribe(self, caller: Caller, endpoint: str) -> dict:
        from crewops.store import push

        push.forget(self.table, caller.user_id, str(endpoint))
        return {"status": "unsubscribed"}

    def still_here(self, caller: Caller) -> dict:
        """A controller's or approver's open console says so now and then, so "while you
        were away" starts when they last had CrewOps open, not when they last signed in."""
        if self._scope_of(caller) is not None:
            mark_seen(self.table, caller.user_id, self.clock())
        return {"ok": True}

    def welcome_back(self, caller: Caller) -> dict:
        """What came to this controller's desk, or to approvals, since they last signed
        in; then it marks them seen. Nothing on a first sign-in."""
        scope = self._scope_of(caller)
        if scope is None:
            return {"since": None, "alerts": [], "speech": ""}
        now = self.clock()
        since = last_seen(self.table, caller.user_id)
        mark_seen(self.table, caller.user_id, now)
        if since is None:
            return {"since": None, "alerts": [], "speech": ""}
        found = self._spoken_alerts(scope, since=since)
        name = caller.user_id.split(".")[0].title()
        when = datetime.fromisoformat(since.replace("Z", "+00:00"))
        return {
            "since": since,
            "alerts": found,
            "speech": alert_briefs.welcome_speech(name, found, when),
        }

    def reference(self, caller: Caller) -> dict[str, Any]:
        """The crew, flights, aircraft, gates and stations a report can name."""
        if not caller.roles & (OPERATIONAL_REPORTERS | LEAVE_FILERS):
            raise ApiError("FORBIDDEN", "only reporters, crew control and admins file reports")
        tools = self.live().pipeline.tools
        today = tools.world.snapshot_utc.date()
        return views.reference(tools, today, today + timedelta(days=REPORT_AHEAD_DAYS))

    def operations(self, caller: Caller, start: str = "") -> dict[str, Any]:
        """The operating week at a glance, for the reporting desk and crew control: every
        flight with its airline, aircraft and crew, who is reported unavailable when, and
        the open disruptions touching each flight (with how serious they are).

        A reporter sees who is out and when, never why (sick, fatigue and the like are
        crew control's business), nor the disruption records behind it."""
        if not caller.roles & (OPERATIONAL_REPORTERS | READERS):
            raise ApiError("FORBIDDEN", "your role does not see the operation")
        tools = self.live().pipeline.tools
        today = tools.world.snapshot_utc.date()
        # A week at a time, from ``start``, inside what crew and reporters may see.
        lo = today - timedelta(days=CREW_LOOK_BACK_DAYS)
        hi = today + timedelta(days=REPORT_AHEAD_DAYS)
        if tools.week:
            lo, hi = max(lo, tools.week[0]), min(hi, tools.week[-1])
        try:
            first = date.fromisoformat(start) if start else today
        except ValueError:
            raise ApiError("INVALID", "start: a date (YYYY-MM-DD)") from None
        first = min(max(first, lo), max(lo, hi - timedelta(days=6)))
        week = [
            (first + timedelta(days=i)).isoformat()
            for i in range(7)
            if first + timedelta(days=i) <= hi
        ]
        # one read per status, shared by the flights and the absences below
        by_status = {s: disruptions_with_status(self.table, s) for s in UNAVAILABLE_STATUSES}
        open_on: dict[str, list[dict[str, Any]]] = {}
        for status in OPEN_STATUSES:
            for item in by_status[status]:
                analysis = from_dynamo(item.get("analysis") or {})
                report = from_dynamo(item.get("report") or {})
                flights = set(analysis.get("affected_flights") or [])
                for s in analysis.get("seats") or []:
                    impact = s.get("impact") or {}
                    flights |= set(impact.get("uncovered_flights") or [])
                    flights |= set(impact.get("at_risk_flights") or [])
                flights |= {leg["flight_id"] for leg in analysis.get("late_departures") or []}
                rated = severity(analysis, report.get("reported_utc"), today) if analysis else None
                for f in flights:
                    open_on.setdefault(f, []).append(
                        {
                            "disruption_id": item["disruption_id"],
                            "status": item["status"],
                            "report_type": report.get("type"),
                            "severity": rated["level"] if rated else None,
                        }
                    )
        items = [i for status in UNAVAILABLE_STATUSES for i in by_status[status]]
        unavailable = [
            u
            for u in self._unavailable_list(tools, items)
            if week and u["from_date"] <= week[-1] and u["to_date"] >= week[0]
        ]
        out_days: dict[str, set[str]] = {}
        for u in unavailable:
            first, last = date.fromisoformat(u["from_date"]), date.fromisoformat(u["to_date"])
            out_days.setdefault(u["crew_id"], set()).update(
                (first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)
            )
        partial = {
            (crew_id, on.isoformat()): _iso_z(at)
            for (crew_id, on), at in absences_from(items).items()
        }
        if not caller.roles & READERS:
            private = ("reason", "self_reported", "disruption_id", "status", "handling")
            unavailable = [{k: v for k, v in u.items() if k not in private} for u in unavailable]
        out = views.operations(tools, open_on, out_days, unavailable, partial, week)
        out["window"] = {"from": lo.isoformat(), "to": hi.isoformat(), "today": today.isoformat()}
        return out

    def _unavailable_list(self, tools: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Everyone reported unavailable, by whom and for which days."""
        out = []
        for item in items:
            report = from_dynamo(item.get("report") or {})
            kind, d = report.get("type", ""), report.get("details") or {}
            if kind not in ABSENCE_TYPES:
                continue
            by = report.get("reported_by") or {}
            for crew_id in crew_out_of(kind, d):
                crew = tools.world.crew.get(crew_id)
                out.append(
                    {
                        "crew_id": crew_id,
                        "name": _crew_name(tools, crew_id),
                        "rank": crew.rank.value if crew else None,
                        "airline": tools.world.airline_of_crew.get(crew_id),
                        "from_date": d["from_date"],
                        "to_date": d["to_date"],
                        "reason": d.get("reason"),
                        "self_reported": by.get("crew_id") == crew_id,
                        "disruption_id": item["disruption_id"],
                        "status": item["status"],
                        "handling": (
                            from_dynamo(item.get("analysis") or {}).get("handling") or {}
                        ).get("kind"),
                    }
                )
        return sorted(out, key=lambda r: (r["from_date"], r["crew_id"]))

    # ------------------------------------------------------------------
    # Rule thresholds and system activity
    # ------------------------------------------------------------------

    def thresholds(self, caller: Caller) -> list[dict[str, Any]]:
        if not caller.roles & READERS:
            raise ApiError("FORBIDDEN", "your role may not read the rule settings")
        return rule_store.thresholds(self.table)

    def set_threshold(
        self,
        caller: Caller,
        rule_id: str,
        param: str,
        value: float,
        expected: float,
        reason: str,
    ) -> dict[str, Any]:
        if not caller.roles & ADMINS:
            raise ApiError("FORBIDDEN", "only an admin changes rule thresholds")
        try:
            out = rule_store.set_threshold(
                self.table,
                rule_id,
                param,
                value,
                expected,
                caller.user_id,
                reason,
                self.clock(),
                may_relax=Role.SUPER_ADMIN in caller.roles,
            )
        except rule_store.RuleError as exc:
            raise ApiError(exc.code, exc.message) from None
        self.invalidate()  # answer from the new rules at once on this server
        return out

    def activity(self, caller: Caller, limit: int = 50) -> list[dict[str, Any]]:
        if not caller.roles & ADMINS:
            raise ApiError("FORBIDDEN", "only an admin reads the activity log")
        return rule_store.activity(self.table, max(1, min(int(limit), 200)))

    # ------------------------------------------------------------------
    # Admin: revoke a report filed in error, reset a password
    # ------------------------------------------------------------------

    def revoke_report(self, caller: Caller, disruption_id: str, reason: str) -> dict[str, Any]:
        """Withdraw a report filed in error (a reporter's, a crew member's own absence, or
        planned leave) while nothing has been done about it. A plan waiting for approval is
        withdrawn with it. Once cover is approved the roster has changed, so crew control
        resolves it instead."""
        if not caller.roles & ADMINS:
            raise ApiError("FORBIDDEN", "only an admin revokes a report")
        if not reason.strip():
            raise ApiError("INVALID", "say why the report is revoked")
        item = get_disruption(self.table, disruption_id)
        if item is None:
            raise ApiError("NOT_FOUND", f"no disruption {disruption_id}")
        status = item["status"]
        handling = from_dynamo(item.get("analysis") or {}).get("handling") or {}
        recorded = status == "CLOSED" and handling.get("kind") == "recorded"
        if status not in ("REPORTED", "OPTIONS_READY", "SUBMITTED", "RESOLVING") and not recorded:
            why = {
                "COMMITTED": "cover was approved and the roster changed; crew control resolves it",
                "CLOSED": "crew control already resolved it",
                "REVOKED": "it is already revoked",
            }.get(status, f"it is {status}")
            raise ApiError("WRONG_STATE", f"{disruption_id} can't be revoked: {why}")
        plan = None
        if status == "SUBMITTED" and item.get("plan_id"):
            plan = plan_store.get_plan(self.table, disruption_id, item["plan_id"])
        try:
            plan_store.revoke(
                self.table,
                disruption_id,
                status,
                caller.user_id,
                self.clock(),
                reason.strip()[:300],
                plan,
            )
        except plan_store.Conflict:
            raise ApiError("CONFLICT", f"{disruption_id} changed meanwhile; look again") from None
        self.invalidate()  # the crew it named are available again at once
        return {"disruption_id": disruption_id, "status": plan_store.STATUS_REVOKED, "was": status}

    def team_accounts(self, caller: Caller) -> list[dict[str, Any]]:
        """The team's sign-ins and roles, with whether this admin may reset each password."""
        if not caller.roles & ADMINS:
            raise ApiError("FORBIDDEN", "only an admin sees the team's accounts")
        out = []
        for p in sorted(list_profiles(self.table), key=lambda p: p.get("username", "")):
            roles = [Role(r) for r in p.get("roles") or [] if r in Role.__members__.values()]
            try:
                may_reset(caller.user_id, caller.roles, p["username"], roles)
                resettable = True
            except AccountError:
                resettable = False
            out.append(
                {
                    "username": p["username"],
                    "name": p.get("name"),
                    "roles": [r.value for r in roles],
                    "crew_id": p.get("crew_id"),
                    "may_reset": resettable,
                }
            )
        return out

    def reset_password(self, caller: Caller, username: str) -> dict[str, Any]:
        """Give one team member a new random password, shown once to the admin."""
        if self.accounts is None:
            raise ApiError("UNAVAILABLE", "password resets are not set up on this server")
        username = username.strip().lower()
        profile = get_profile(self.table, username)
        if profile is None:
            raise ApiError("NOT_FOUND", f"no team member {username}")
        roles = [Role(r) for r in profile.get("roles") or [] if r in Role.__members__.values()]
        try:
            may_reset(caller.user_id, caller.roles, username, roles)
            password = new_password()
            self.accounts.set_password(username, password)
        except AccountError as exc:
            raise ApiError(exc.code, exc.message) from None
        security_store.log_password_reset(self.table, caller.user_id, username, self.clock())
        return {
            "username": username,
            "password": password,
            "note": "Shown once. Pass it on in person.",
        }

    # ------------------------------------------------------------------
    # Alert addresses (admins only)
    # ------------------------------------------------------------------

    def _alerts(self, call: Callable[[AlertSubscriptions], Any]) -> Any:
        if self.alerts is None:
            raise ApiError("UNAVAILABLE", "alerts are not set up on this server")
        try:
            return call(self.alerts)
        except AlertError as exc:
            raise ApiError(exc.code, exc.message) from None

    def alert_addresses(self, caller: Caller) -> list[dict[str, Any]]:
        return self._alerts(lambda a: a.list(caller))

    def add_alert_address(self, caller: Caller, email: str, role: str) -> dict[str, Any]:
        return self._alerts(lambda a: a.add(caller, email, role))

    def remove_alert_address(self, caller: Caller, subscription_id: str) -> dict[str, Any]:
        return self._alerts(lambda a: a.remove(caller, subscription_id))

    # ------------------------------------------------------------------

    def _plan_action(self, action: Callable[..., Any], *args: Any) -> dict[str, Any]:
        try:
            out = action(*args)
        except PlanError as exc:
            raise ApiError(exc.code, exc.message) from None
        self.invalidate()  # the roster may have changed: answer from the new one
        result = {"status": out.status}
        if "plan_id" in out.plan:
            result |= plan_summary(out.plan)
        if out.warnings:
            result["contention"] = list(out.warnings)
        if out.reasons:
            result["reasons"] = list(out.reasons)
        return result

    def submit(
        self,
        caller: Caller,
        disruption_id: str,
        choices: Mapping[str, str] | None = None,
        note: str = "",
        accept_uncovered: bool = False,
    ) -> dict[str, Any]:
        return self._plan_action(
            self.plans.submit, caller, disruption_id, dict(choices or {}), note, accept_uncovered
        )

    def approve(self, caller: Caller, disruption_id: str, plan_id: str) -> dict[str, Any]:
        return self._plan_action(self.plans.approve, caller, disruption_id, plan_id)

    def reject(self, caller: Caller, disruption_id: str, plan_id: str, reason: str) -> dict:
        return self._plan_action(self.plans.reject, caller, disruption_id, plan_id, reason)

    def withdraw(self, caller: Caller, disruption_id: str, plan_id: str, reason: str = "") -> dict:
        return self._plan_action(self.plans.withdraw, caller, disruption_id, plan_id, reason)

    def close(self, caller: Caller, disruption_id: str, reason: str) -> dict[str, Any]:
        return self._plan_action(self.plans.close, caller, disruption_id, reason)

    def confirm_resolution(self, caller: Caller, disruption_id: str) -> dict[str, Any]:
        return self._plan_action(self.plans.confirm_resolution, caller, disruption_id)

    def return_resolution(self, caller: Caller, disruption_id: str, reason: str) -> dict[str, Any]:
        return self._plan_action(self.plans.return_resolution, caller, disruption_id, reason)
