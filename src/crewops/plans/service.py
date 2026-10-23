"""Submit, withdraw, reject and approve plans. The rules live here, in code.

None of this is exposed as a model tool; the advisor and the analysis only prepare options,
and every call takes a signed-in ``Caller``.

The rules, roughly:

- A disruption belongs to the desk it was assigned when analysed (for older records, the
  desks of the aircraft it touches). Only that desk's controller can plan it.
- A seat is a pairing, a role and the crew member being replaced. Seats with no legal cover,
  or that have already started, go in the plan as uncovered and the controller has to
  acknowledge them to submit. If nothing at all is coverable, the disruption is closed with
  a reason instead.
- Cancelling one seat cancels the whole trip. Every seat of that trip in the plan has to be
  cancelled too, the cost is counted once, and the commit releases the whole crew so no
  aircraft is left with one pilot.
- Flying one cabin crew short is checked across the whole plan, counting anyone on the trip
  already reported unavailable, at submit time and again at approval.
- Four eyes: the approver can't be the submitter. Only the submitter can withdraw.
- Each choice has to be legal at submit and is checked again against the roster at
  approval. If anything changed the plan goes STALE and nothing is written.
- Approval is the commit. Roster changes, plan, disruption and audit are one transaction,
  conditioned on each pairing's crew and each moved person's roster version matching what
  the re-check saw, so two approvals (or an approval and a sick report) on the same person
  can't both land.
- If another open plan, or another disruption's proposed cover, wants the same person on
  overlapping dates, the submitter gets a warning and the approver sees it in their queue.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from crewops.analysis.runner import Operation
from crewops.domain import Rank, Role
from crewops.domain.desks import DESKS, Desk, owning_desks
from crewops.engine import AlreadyStarted, TripCancelled, World, find_options
from crewops.engine.options import cabin_minimum, cabin_present
from crewops.plans.fingerprint import fingerprint
from crewops.store import plans as store
from crewops.store.items import from_dynamo
from crewops.store.reports import STATUS_OPTIONS_READY, disruptions_with_status, get_disruption
from crewops.tools import Caller

MAX_REASON = 1000
MAX_SEATS = 20
"""Cap on seats per plan. A commit writes two roster versions per seat, each pairing, and
four more items, and DynamoDB allows 100 items per transaction."""


class PlanError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Outcome:
    status: str
    plan: dict[str, Any]
    warnings: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


def seat_key(seat: Mapping[str, Any]) -> str:
    """ "P-2291/Captain/C-1042": pairing, role and who is replaced, so two seats of one role
    on one pairing are told apart."""
    return f"{seat['pairing_id']}/{seat['role']}/{seat.get('crew_out') or '-'}"


def _desk(desk_id: str | None) -> Desk | None:
    return next((d for d in DESKS if d.desk_id == desk_id), None)


SHORT = "SHORT"
"""A choice for a cabin crew seat: operate one short, when the minimum allows it."""
CANCEL = "CANCEL"
"""A choice for any seat: cancel the trip's flights instead of covering it."""


def _roster(world: World, pairing_id: str) -> list[dict[str, str]]:
    return [{"crew_id": s.crew_id, "role": s.role.value} for s in world.pairings[pairing_id].crew]


def _dates(world: World, pairing_id: str) -> set[date]:
    p = world.pairings.get(pairing_id)
    return {d.date for d in p.days} if p else set()


class Plans:
    def __init__(
        self,
        table: Any,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        load: Callable[[Any], Operation] = Operation.load,
    ) -> None:
        self.table = table
        self.clock = clock
        self.load = load

    # ------------------------------------------------------------------
    # Controller actions
    # ------------------------------------------------------------------

    def submit(
        self,
        caller: Caller,
        disruption_id: str,
        choices: Mapping[str, str] | None = None,
        note: str = "",
        accept_uncovered: bool = False,
    ) -> Outcome:
        """A plan from the disruption's analysis: the proposed cover (the joint plan when
        there is one), with any seat overridden by ``choices`` (seat key, or "pairing/role"
        when that names one seat, -> crew id)."""
        desk = self._controller_desk(caller)
        item = self._disruption(disruption_id)
        if item["status"] != STATUS_OPTIONS_READY:
            raise PlanError(
                "WRONG_STATE", f"{disruption_id} is {item['status']}, not ready for a plan"
            )
        analysis = from_dynamo(item.get("analysis") or {})
        world = self.load(self.table).world
        seats = analysis.get("seats", [])
        self._check_desk(desk, world, analysis, disruption_id)

        coverable, uncovered = [], []
        for seat in seats:
            reason = self._why_uncoverable(world, seat)
            if reason and self._special(seat, choices or {}):
                reason = None  # no crew is legal, but it may be cancelled or flown short
            (uncovered if reason else coverable).append((seat, reason))
        if not coverable:
            raise PlanError(
                "NOTHING_TO_COVER",
                f"{disruption_id} has no seat that can be covered; close it with a reason "
                "once it is handled",
            )
        if uncovered and not accept_uncovered:
            listing = "; ".join(f"{seat_key(s)}: {r}" for s, r in uncovered)
            raise PlanError(
                "UNCOVERED",
                f"these seats cannot be covered: {listing}. Submit with them acknowledged, "
                "or handle them first",
            )

        if len(coverable) > MAX_SEATS:
            raise PlanError(
                "TOO_LARGE",
                f"{len(coverable)} seats is more than one plan can commit at once ({MAX_SEATS})",
            )
        picks = self._picks([s for s, _ in coverable], analysis, choices or {})
        assignments = [
            self._legal_choice(world, seat, picks[seat_key(seat)]) for seat, _ in coverable
        ]
        chosen = [a["crew_id"] for a in assignments if a["crew_id"]]
        if len(set(chosen)) != len(chosen):
            raise PlanError("INVALID", "one crew member is chosen for two seats")
        assignments = self._one_cancellation_per_trip(assignments)
        rosters_before = {
            pid: _roster(world, pid) for pid in sorted({a["pairing_id"] for a in assignments})
        }
        if below := self._below_cabin_minimum(
            world, {"assignments": assignments, "rosters_before": rosters_before}
        ):
            raise PlanError("NOT_LEGAL", "; ".join(below))

        plan = {
            "plan_id": f"pl-{uuid.uuid4().hex[:12]}",
            "disruption_id": disruption_id,
            "status": store.PLAN_SUBMITTED,
            "desk": desk.desk_id,
            "submitted_by": caller.user_id,
            "submitted_utc": self._now(),
            "note": note.strip()[:MAX_REASON],
            "assignments": assignments,
            "uncovered": [{"seat": seat_key(s), "reason": r} for s, r in uncovered],
            "total_cost_inr": sum(a["cost_inr"] for a in assignments),
            "rosters_before": rosters_before,
        }
        # shown to the approver and recomputed at approval
        plan["fingerprint"] = fingerprint(plan)
        warnings = self.contention(plan, world)
        plan["warnings"] = list(warnings)
        try:
            store.submit(self.table, plan, self.clock())
        except store.Conflict:
            raise PlanError("CONFLICT", f"{disruption_id} changed meanwhile; look again") from None
        return Outcome(store.PLAN_SUBMITTED, plan, warnings)

    def withdraw(
        self, caller: Caller, disruption_id: str, plan_id: str, reason: str = ""
    ) -> Outcome:
        plan = self._submitted(disruption_id, plan_id)
        if plan["submitted_by"] != caller.user_id:
            raise PlanError("FORBIDDEN", "only the controller who submitted a plan can withdraw it")
        return self._close(plan, store.PLAN_WITHDRAWN, caller, reason or "withdrawn")

    def close(self, caller: Caller, disruption_id: str, reason: str) -> Outcome:
        """End a disruption that has no coverable seat, recording what was done (for example,
        the flights were cancelled). This is also how one handled outside crew control is
        marked resolved, with a remark: a gate closure re-gated by gate control, a delay every
        crew member absorbs. It goes to RESOLVING for an approver other than this controller to
        sign off (CLOSED) or send back. Nothing on the roster changes, and a crew member it
        took out stays out on its dates."""
        desk = self._controller_desk(caller)
        item = self._disruption(disruption_id)
        if item["status"] != STATUS_OPTIONS_READY:
            raise PlanError("WRONG_STATE", f"{disruption_id} is {item['status']}")
        if not reason.strip():
            raise PlanError("INVALID", "say what was done instead")
        analysis = from_dynamo(item.get("analysis") or {})
        world = self.load(self.table).world
        self._check_desk(desk, world, analysis, disruption_id)
        coverable = [s for s in analysis.get("seats", []) if not self._why_uncoverable(world, s)]
        if coverable:
            raise PlanError(
                "WRONG_STATE",
                f"{disruption_id} still has legal cover for "
                f"{', '.join(seat_key(s) for s in coverable)}: submit a plan instead",
            )
        try:
            store.submit_resolution(
                self.table, disruption_id, caller.user_id, self.clock(), reason[:MAX_REASON]
            )
        except store.Conflict:
            raise PlanError("CONFLICT", f"{disruption_id} changed meanwhile") from None
        # Four eyes here too: an approver who is not this controller signs it off.
        return Outcome(store.STATUS_RESOLVING, {"disruption_id": disruption_id, "reason": reason})

    def _resolution(self, caller: Caller, disruption_id: str) -> dict[str, Any]:
        """The submitted resolution an approver may decide: the controller's remark and who
        sent it, checked for four eyes."""
        if Role.APPROVER not in caller.roles:
            raise PlanError("FORBIDDEN", "only an approver signs off a resolution")
        item = self._disruption(disruption_id)
        if item["status"] != store.STATUS_RESOLVING:
            raise PlanError("WRONG_STATE", f"{disruption_id} is {item['status']}")
        sent = [
            e
            for e in store.audit_trail(self.table, disruption_id)
            if e.get("action") == "RESOLUTION_SUBMITTED"
        ]
        if not sent:
            raise PlanError("WRONG_STATE", f"{disruption_id} has no resolution to sign off")
        latest = sent[-1]
        if latest.get("actor") == caller.user_id:
            raise PlanError(
                "FOUR_EYES", "a resolution is signed off by someone other than its sender"
            )
        return latest

    def confirm_resolution(self, caller: Caller, disruption_id: str) -> Outcome:
        sent = self._resolution(caller, disruption_id)
        try:
            store.confirm_resolution(
                self.table,
                disruption_id,
                caller.user_id,
                self.clock(),
                str(sent.get("reason") or ""),
                str(sent.get("actor") or ""),
            )
        except store.Conflict:
            raise PlanError("CONFLICT", f"{disruption_id} changed meanwhile") from None
        return Outcome(store.STATUS_CLOSED, {"disruption_id": disruption_id})

    def return_resolution(self, caller: Caller, disruption_id: str, reason: str) -> Outcome:
        self._resolution(caller, disruption_id)
        if not reason.strip():
            raise PlanError("INVALID", "say why it goes back to the desk")
        try:
            store.return_resolution(
                self.table, disruption_id, caller.user_id, self.clock(), reason[:MAX_REASON]
            )
        except store.Conflict:
            raise PlanError("CONFLICT", f"{disruption_id} changed meanwhile") from None
        return Outcome(STATUS_OPTIONS_READY, {"disruption_id": disruption_id, "reason": reason})

    # ------------------------------------------------------------------
    # Approver actions
    # ------------------------------------------------------------------

    def reject(self, caller: Caller, disruption_id: str, plan_id: str, reason: str) -> Outcome:
        plan = self._submitted(disruption_id, plan_id)
        self._check_approver(caller, plan)
        if not reason.strip():
            raise PlanError("INVALID", "say why the plan is rejected")
        return self._close(plan, store.PLAN_REJECTED, caller, reason)

    def approve(self, caller: Caller, disruption_id: str, plan_id: str) -> Outcome:
        """Re-check against the roster as it is now; commit if nothing changed, else STALE."""
        plan = self._submitted(disruption_id, plan_id)
        self._check_approver(caller, plan)
        # The SUBMITTED audit event (same transaction as the plan) has the fingerprint too.
        # If someone strips or edits the plan's copy, the audit one still catches it.
        recorded = next(
            (
                e.get("fingerprint")
                for e in store.audit_trail(self.table, disruption_id)
                if e.get("action") == "SUBMITTED" and e.get("plan_id") == plan_id
            ),
            None,
        )
        expected = recorded or plan.get("fingerprint")
        if expected and (plan.get("fingerprint") != expected or fingerprint(plan) != expected):
            raise PlanError(
                "TAMPERED",
                "this plan changed after it was submitted (its fingerprint no longer "
                "matches); it cannot be approved. Ask the controller to submit it again.",
            )
        moved = {a["crew_id"] for a in plan["assignments"] if a["crew_id"]} | {
            a["replaces"] for a in plan["assignments"] if a.get("replaces")
        }
        # read versions before the re-check, so a change in between fails the commit
        versions = store.read_versions(self.table, moved)
        world = self.load(self.table).world
        reasons = self._still_valid(world, plan)
        rosters = {} if reasons else self._rosters_after(plan, reasons)
        if reasons:
            return self._close(plan, store.PLAN_STALE, caller, "; ".join(reasons), reasons)
        try:
            store.commit(self.table, plan, rosters, versions, caller.user_id, self.clock())
        except store.Conflict as exc:
            if exc.transient:
                raise PlanError(
                    "RETRY", "another change touched the same plan or crew just now; try again"
                ) from None
            current = store.get_plan(self.table, disruption_id, plan_id)
            if current and current["status"] == store.PLAN_SUBMITTED:
                why = ["the roster or a crew member's availability changed during approval"]
                return self._close(current, store.PLAN_STALE, caller, why[0], why)
            raise PlanError("CONFLICT", "the plan was decided meanwhile") from None
        return Outcome(store.PLAN_COMMITTED, plan | {"status": store.PLAN_COMMITTED})

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def queue(self, caller: Caller) -> list[dict[str, Any]]:
        """What is waiting for this person: plans to decide (approver), disruptions on their
        desk ready for a plan, and their plans sent back while still unresolved
        (controller)."""
        out: list[dict[str, Any]] = []
        world: World | None = None
        if Role.APPROVER in caller.roles:
            world = self.load(self.table).world
            for plan in store.plans_with_status(self.table, store.PLAN_SUBMITTED):
                if plan["submitted_by"] != caller.user_id:
                    warnings = self.contention(plan, world)
                    out.append({"kind": "to_decide", **summary(plan), "warnings": list(warnings)})
            for item in disruptions_with_status(self.table, store.STATUS_RESOLVING):
                sent = [
                    e
                    for e in store.audit_trail(self.table, item["disruption_id"])
                    if e.get("action") == "RESOLUTION_SUBMITTED"
                ]
                if sent and sent[-1].get("actor") != caller.user_id:
                    analysis = from_dynamo(item.get("analysis") or {})
                    out.append(
                        {
                            "kind": "to_confirm",
                            "disruption_id": item["disruption_id"],
                            "summary": analysis.get("summary", ""),
                            "remark": sent[-1].get("reason", ""),
                            "submitted_by": sent[-1].get("actor"),
                            "submitted_utc": sent[-1].get("at"),
                        }
                    )
        desk = _desk(caller.desk) if Role.CONTROLLER in caller.roles else None
        if desk is not None:
            world = world or self.load(self.table).world
            ready = {}
            for item in disruptions_with_status(self.table, STATUS_OPTIONS_READY):
                analysis = from_dynamo(item.get("analysis") or {})
                if self._touches(desk, world, analysis):
                    ready[item["disruption_id"]] = item
                    out.append(
                        {
                            "kind": "ready_for_plan" if analysis.get("seats") else "to_resolve",
                            "disruption_id": item["disruption_id"],
                            "summary": analysis.get("summary", ""),
                        }
                    )
            for status in (store.PLAN_REJECTED, store.PLAN_STALE):
                for plan in store.plans_with_status(self.table, status):
                    if (
                        plan["submitted_by"] == caller.user_id
                        and plan["disruption_id"] in ready
                        and self._latest(plan)
                    ):
                        out.append({"kind": f"sent_back_{status.lower()}", **summary(plan)})
        return out

    def contention(self, plan: Mapping[str, Any], world: World) -> tuple[str, ...]:
        """Other open plans, and other disruptions' proposed cover, wanting the same crew on
        dates that overlap."""
        wanted: dict[str, set[date]] = defaultdict(set)
        for a in plan["assignments"]:
            if a["crew_id"]:
                wanted[a["crew_id"]] |= _dates(world, a["pairing_id"])

        def clash(crew_id: str, pairing_id: str) -> bool:
            return crew_id in wanted and bool(wanted[crew_id] & _dates(world, pairing_id))

        warnings = []
        for other in store.plans_with_status(self.table, store.PLAN_SUBMITTED):
            if other["disruption_id"] == plan["disruption_id"]:
                continue
            for a in other["assignments"]:
                if a["crew_id"] and clash(a["crew_id"], a["pairing_id"]):
                    warnings.append(
                        f"{a['crew_id']} is also in plan {other['plan_id']} for "
                        f"{other['disruption_id']} ({other['desk']}), awaiting approval"
                    )
        for item in disruptions_with_status(self.table, STATUS_OPTIONS_READY):
            if item["disruption_id"] == plan["disruption_id"]:
                continue
            analysis = from_dynamo(item.get("analysis") or {})
            proposed = [
                (a["crew_id"], a["pairing_id"])
                for a in (analysis.get("joint_plan") or {}).get("assignments", [])
            ] or [
                (s["recommended"]["crew_id"], s["pairing_id"])
                for s in analysis.get("seats", [])
                if s.get("recommended")
            ]
            for crew_id, pairing_id in proposed:
                if clash(crew_id, pairing_id):
                    warnings.append(
                        f"{crew_id} is also the proposed cover for {item['disruption_id']} "
                        f"({pairing_id})"
                    )
        return tuple(dict.fromkeys(warnings))

    # ------------------------------------------------------------------

    def _now(self) -> str:
        return self.clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _disruption(self, disruption_id: str) -> dict[str, Any]:
        item = get_disruption(self.table, disruption_id)
        if item is None:
            raise PlanError("NOT_FOUND", f"no disruption {disruption_id}")
        return item

    def _submitted(self, disruption_id: str, plan_id: str) -> dict[str, Any]:
        plan = store.get_plan(self.table, disruption_id, plan_id)
        if plan is None:
            raise PlanError("NOT_FOUND", f"no plan {plan_id} for {disruption_id}")
        if plan["status"] != store.PLAN_SUBMITTED:
            raise PlanError(
                "WRONG_STATE", f"plan {plan_id} is {plan['status']}, not awaiting a decision"
            )
        return plan

    def _latest(self, plan: Mapping[str, Any]) -> bool:
        plans = store.plans_for(self.table, plan["disruption_id"])
        latest = max(plans, key=lambda p: (p["submitted_utc"], p["plan_id"]))
        return latest["plan_id"] == plan["plan_id"]

    @staticmethod
    def _controller_desk(caller: Caller) -> Desk:
        desk = _desk(caller.desk) if Role.CONTROLLER in caller.roles else None
        if desk is None:
            raise PlanError("FORBIDDEN", "only a controller with a desk can do this")
        return desk

    @staticmethod
    def _check_approver(caller: Caller, plan: Mapping[str, Any]) -> None:
        if Role.APPROVER not in caller.roles:
            raise PlanError("FORBIDDEN", "only an approver can decide on a plan")
        if plan["submitted_by"] == caller.user_id:
            raise PlanError("FOUR_EYES", "a plan is decided by someone other than its submitter")

    @staticmethod
    def _touches(desk: Desk, world: World, analysis: Mapping[str, Any]) -> bool:
        """Is this the desk the disruption was assigned to at analysis (the least-loaded
        controller)?"""

        def aircraft(pairing_id: str) -> str | None:
            p = world.pairings.get(pairing_id)
            return p.aircraft if p else None

        return desk.desk_id in {d.desk_id for d in owning_desks(analysis, aircraft)}

    def _check_desk(
        self, desk: Desk, world: World, analysis: Mapping[str, Any], disruption_id: str
    ) -> None:
        if not self._touches(desk, world, analysis):
            raise PlanError("FORBIDDEN", f"{disruption_id} touches no aircraft on {desk.desk_id}")

    @staticmethod
    def _special(seat: Mapping[str, Any], choices: Mapping[str, str]) -> bool:
        """A seat with no legal crew whose choice is to cancel or operate short."""
        if seat.get("status") != "no_legal_cover":
            return False
        pick = choices.get(seat_key(seat)) or choices.get(f"{seat['pairing_id']}/{seat['role']}")
        return pick in (CANCEL, SHORT)

    @staticmethod
    def _one_cancellation_per_trip(assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Cancelling a seat cancels its whole trip: no other seat of that trip may be
        covered, and the trip's cancellation is costed once."""
        cancelled = {a["pairing_id"] for a in assignments if a["strategy"] == "cancel"}
        mixed = sorted(
            cancelled & {a["pairing_id"] for a in assignments if a["strategy"] != "cancel"}
        )
        if mixed:
            raise PlanError(
                "INVALID",
                f"cancelling cancels every flight of {', '.join(mixed)}: choose cancel for "
                "every seat of it in this plan, or cover each seat instead",
            )
        seen: set[str] = set()
        out = []
        for a in assignments:
            if a["strategy"] == "cancel":
                a = a | ({"cost_inr": 0} if a["pairing_id"] in seen else {})
                seen.add(a["pairing_id"])
            out.append(a)
        return out

    @classmethod
    def _below_cabin_minimum(cls, world: World, plan: Mapping[str, Any]) -> list[str]:
        """Trips this plan would leave below one cabin crew per 50 seats: the cabin crew
        after the plan, less anyone on the trip reported unavailable on its days."""
        short = {a["pairing_id"] for a in plan["assignments"] if a["strategy"] == "short_crew"}
        if not short:
            return []
        rosters = cls._rosters_after(plan, [])
        reasons = []
        for pid in sorted(short):
            if pid not in world.pairings:
                continue  # the roster check reports a trip that is gone
            cabin = [
                s["crew_id"]
                for s in rosters[pid][1]
                if s["role"] in (Rank.CABIN_CREW.value, Rank.SENIOR_CABIN_CREW.value)
            ]
            present, minimum = cabin_present(world, pid, cabin), cabin_minimum(world, pid)
            if present < minimum:
                reasons.append(
                    f"{pid} would fly with {present} cabin crew, below the minimum of "
                    f"{minimum} (one per 50 seats)"
                )
        return reasons

    @staticmethod
    def _why_uncoverable(world: World, seat: Mapping[str, Any]) -> str | None:
        if seat.get("status") != "options":
            return {
                "no_legal_cover": "no legal cover",
                "already_started": "already started",
                "cancelled": "the trip is cancelled",
            }.get(seat.get("status", ""), str(seat.get("status")))
        out, pid = seat.get("crew_out"), seat["pairing_id"]
        if not out:
            return "no rostered crew member to replace"
        if pid not in world.pairings or {"crew_id": out, "role": seat["role"]} not in _roster(
            world, pid
        ):
            now = (
                [s["crew_id"] for s in _roster(world, pid) if s["role"] == seat["role"]]
                if pid in world.pairings
                else []
            )
            held = f" ({', '.join(now)} holds it now)" if now else ""
            return f"already re-crewed: {out} is no longer on {pid} as {seat['role']}{held}"
        return None

    @staticmethod
    def _picks(
        seats: list[dict[str, Any]], analysis: Mapping[str, Any], choices: Mapping[str, str]
    ) -> dict[str, str]:
        """The crew for each seat: an explicit choice, else the joint plan's (matched to
        seats of the same pairing and role in order), else the seat's recommendation."""
        keys = {seat_key(s): s for s in seats}
        short: dict[str, list[str]] = defaultdict(list)
        for key, s in keys.items():
            short[f"{s['pairing_id']}/{s['role']}"].append(key)
        resolved: dict[str, str] = {}
        for given, crew_id in choices.items():
            if given in keys:
                resolved[given] = crew_id
            elif len(short.get(given, [])) == 1:
                resolved[short[given][0]] = crew_id
            elif given in short:
                raise PlanError(
                    "INVALID", f"{given} names several seats; use one of {', '.join(short[given])}"
                )
            else:
                raise PlanError("INVALID", f"no such seat: {given}")
        joint: dict[str, list[str]] = defaultdict(list)
        for a in (analysis.get("joint_plan") or {}).get("assignments", []):
            joint[f"{a['pairing_id']}/{a['role']}"].append(a["crew_id"])
        picks = {}
        for key, s in keys.items():
            group = f"{s['pairing_id']}/{s['role']}"
            fallback = joint[group].pop(0) if joint.get(group) else None
            crew_id = resolved.get(key) or fallback or (s.get("recommended") or {}).get("crew_id")
            if not crew_id:
                raise PlanError("INVALID", f"no cover chosen for {key}")
            picks[key] = crew_id
        return picks

    @staticmethod
    def _delay(seat: Mapping[str, Any]) -> tuple[float, date | None]:
        on = seat.get("delay_date")
        return float(seat.get("delay_hours") or 0), date.fromisoformat(on) if on else None

    def _legal_choice(self, world: World, seat: dict[str, Any], crew_id: str) -> dict[str, Any]:
        """The chosen crew member as a legal option for the seat now, with its cost; or the
        choice to operate one cabin crew short, or to cancel the trip's flights."""
        hours, on = self._delay(seat)
        try:
            result = find_options(world, seat["pairing_id"], Rank(seat["role"]), hours, on)
        except AlreadyStarted as exc:
            raise PlanError("NOT_LEGAL", str(exc)) from None
        base = {
            "pairing_id": seat["pairing_id"],
            "role": seat["role"],
            "replaces": seat["crew_out"],
            "delay_hours": hours,
            "delay_date": on.isoformat() if on else None,
        }
        if crew_id == SHORT:
            if result.short_crew is None:
                raise PlanError(
                    "NOT_LEGAL",
                    f"{seat_key(seat)} cannot operate one short: it would fall below the "
                    "minimum cabin crew",
                )
            return base | {
                "crew_id": None,
                "name": "Operate one cabin crew short",
                "strategy": "short_crew",
                "cost_inr": 0,
            }
        if crew_id == CANCEL:
            flights = list(world.pairing(seat["pairing_id"]).flight_ids)
            return base | {
                "crew_id": None,
                "name": f"Cancel {len(flights)} flight(s)",
                "strategy": "cancel",
                "cost_inr": result.cancellation.cost_inr,
                "flights": flights,
            }
        option = next((o for o in result.crew_options if o.crew_id == crew_id), None)
        if option is None:
            why = next((e.reasons for e in result.excluded if e.crew_id == crew_id), ())
            detail = f": {'; '.join(why)}" if why else ""
            raise PlanError(
                "NOT_LEGAL",
                f"{crew_id} is not a legal option for {seat_key(seat)}{detail}",
            )
        return {
            "pairing_id": seat["pairing_id"],
            "role": seat["role"],
            "crew_id": crew_id,
            "name": option.name,
            "replaces": seat["crew_out"],
            "strategy": option.strategy.value,
            "cost_inr": option.cost_inr,
            "delay_hours": hours,
            "delay_date": on.isoformat() if on else None,
        }

    @staticmethod
    def _still_valid(world: World, plan: Mapping[str, Any]) -> list[str]:
        reasons = []
        for pid, before in plan["rosters_before"].items():
            if pid not in world.pairings or _roster(world, pid) != before:
                reasons.append(f"the crew of {pid} changed after the plan was submitted")
        reasons += Plans._below_cabin_minimum(world, plan)
        for a in plan["assignments"]:
            # The same search as at submission, so a trip that has started since, or cover
            # who can no longer reach the airport in time, makes the plan stale too.
            on = date.fromisoformat(a["delay_date"]) if a.get("delay_date") else None
            try:
                result = find_options(
                    world, a["pairing_id"], Rank(a["role"]), float(a["delay_hours"]), on
                )
            except (AlreadyStarted, TripCancelled) as exc:
                reasons.append(str(exc))
                continue
            except Exception as exc:  # the crew member or pairing is gone
                reasons.append(f"{a['crew_id'] or a['pairing_id']}: {exc}")
                continue
            if not a["crew_id"]:
                continue  # operating short (checked above) or cancelling: nobody to re-check
            option = next((o for o in result.crew_options if o.crew_id == a["crew_id"]), None)
            if option is None:
                why = next((e.reasons for e in result.excluded if e.crew_id == a["crew_id"]), ())
                details = "; ".join(why) or "no longer among the legal options"
                reasons.append(
                    f"{a['crew_id']} is no longer legal for {a['pairing_id']}: {details}"
                )
            elif option.delay_hours > 0 and option.cost_inr != a["cost_inr"]:
                reasons.append(
                    f"{a['crew_id']} can no longer reach {a['pairing_id']} by report time: "
                    f"the departure would slip {option.delay_hours:g} h"
                )
        return reasons

    @staticmethod
    def _rosters_after(
        plan: Mapping[str, Any], reasons: list[str]
    ) -> dict[str, tuple[list[dict[str, str]], list[dict[str, str]]]]:
        """Each pairing's crew with every replaced seat taken by its cover, a seat flown
        short left empty, and a cancelled trip's whole crew released. Never adds a seat: a
        replaced crew member no longer there is a reason the plan is stale."""
        rosters = {}
        cancelled = {a["pairing_id"] for a in plan["assignments"] if a["strategy"] == "cancel"}
        for pid, before in plan["rosters_before"].items():
            after = [dict(seat) for seat in before]
            emptied = 0
            for a in (a for a in plan["assignments"] if a["pairing_id"] == pid):
                seat = next(
                    (s for s in after if s["role"] == a["role"] and s["crew_id"] == a["replaces"]),
                    None,
                )
                if seat is None:
                    reasons.append(f"{a['replaces']} is no longer on {pid} as {a['role']}")
                    continue
                if a["crew_id"]:
                    seat["crew_id"] = a["crew_id"]
                else:
                    # flying short: drop the seat
                    after.remove(seat)
                    emptied += 1
            if pid in cancelled:
                rosters[pid] = (before, [])  # the flights are cancelled: everyone released
                continue
            ids = [s["crew_id"] for s in after]
            if len(after) != len(before) - emptied or len(set(ids)) != len(ids):
                reasons.append(f"covering {pid} would leave it with a duplicate or missing seat")
            rosters[pid] = (before, after)
        return rosters

    def _close(
        self,
        plan: dict[str, Any],
        status: str,
        caller: Caller,
        reason: str,
        reasons: list[str] | None = None,
    ) -> Outcome:
        try:
            store.close(self.table, plan, status, caller.user_id, self.clock(), reason[:MAX_REASON])
        except store.Conflict:
            raise PlanError("CONFLICT", "the plan was decided meanwhile") from None
        return Outcome(status, plan | {"status": status}, (), tuple(reasons or ()))


def summary(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "disruption_id": plan["disruption_id"],
        "plan_id": plan["plan_id"],
        "desk": plan["desk"],
        "submitted_by": plan["submitted_by"],
        "total_cost_inr": plan["total_cost_inr"],
        # the controller's note to the approver, if any
        "note": plan.get("note") or "",
        "assignments": [
            f"{a['pairing_id']} {a['role']}: {a['name']} ({a['crew_id']}) replacing "
            f"{a['replaces']}, ₹{a['cost_inr']:,}"
            if a["crew_id"]
            else f"{a['pairing_id']} {a['role']}: {a['name']} instead of {a['replaces']}, "
            f"₹{a['cost_inr']:,}"
            for a in plan["assignments"]
        ],
        "uncovered": [f"{u['seat']}: {u['reason']}" for u in plan.get("uncovered", [])],
        "reason": plan.get("reason"),
        # The same choices as data, so a decided plan can be shown against its options.
        "picks": [
            {k: a.get(k) for k in ("pairing_id", "role", "crew_id", "name", "replaces", "strategy")}
            for a in plan["assignments"]
        ],
        "submitted_utc": plan.get("submitted_utc"),
        "decided_by": plan.get("decided_by"),
        "decided_utc": plan.get("decided_utc"),
        # four-eyes evidence, see plans.fingerprint
        "fingerprint": plan.get("fingerprint"),
        "approval_seal": plan.get("approval_seal"),
    }
