"""Tool results as short spoken sentences, with no model involved.

This is the fallback answer. With no model configured, or when a model's draft fails
verification, this is what the user hears. Every id, number, time and name is copied from a
tool result so it always passes the verifier. Plain rather than fluent, and kept short.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from crewops.tools import TraceEntry

SHOW = 8
"""Most rows read out from a lookup. The count we say is still the full count."""


def _n(value: Any) -> str:
    """60.0 -> "60", 18500 -> "18,500"."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return str(value)
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:g}"


def _count(n: Any, singular: str, plural: str | None = None) -> str:
    """ "1 flight", "3 flights"."""
    return f"{_n(n)} {singular if n == 1 else (plural or singular + 's')}"


def _inr(value: Any) -> str:
    return f"₹{_n(value)}"


def _hhmm(iso: str | None) -> str:
    """ "2026-10-13T06:00:00Z" -> "06:00Z"."""
    return f"{iso[11:16]}Z" if iso and len(iso) >= 16 else str(iso)


def _dated(iso: str | None) -> str:
    """ "2026-10-13T06:00:00Z" -> "06:00Z on 2026-10-13"."""
    return f"{_hhmm(iso)} on {iso[:10]}" if iso and len(iso) >= 16 else str(iso)


def _flight(flight_id: str) -> str:
    """ "KS412-2026-10-13" -> "KS412 of 2026-10-13"."""
    number, _, day = flight_id.partition("-")
    return f"{number} of {day}" if day else number


def _who(row: dict[str, Any]) -> str:
    name, crew_id = row.get("name"), row.get("crew_id")
    return f"{name} ({crew_id})" if name and crew_id else str(crew_id or name)


def _list(items: Sequence[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _flight_nos(flight_ids: Sequence[str]) -> list[str]:
    return [f.split("-")[0] for f in flight_ids]


# --------------------------------------------------------------------------
# lookup rows
# --------------------------------------------------------------------------

ROW: dict[str, Callable[[dict[str, Any]], str]] = {
    "crew": lambda r: (
        f"{_who(r)}, {r['rank']}, based {r['base']}, rated {'/'.join(r['ratings'])}, "
        f"reachable in {_n(r['reachability_minutes'])} minutes"
    ),
    "flights": lambda r: (
        f"{r['flight_no']} {r['dep_station']} to {r['arr_station']} on {r['date']}, "
        f"departs {_hhmm(r['dep_utc'])}, {r['aircraft']}, {_n(r['seats'])} seats, "
        f"{_n(r['block_hours'])} block hours" + (", cancelled" if r.get("cancelled") else "")
    ),
    "pairings": lambda r: f"{r['pairing_id']} on {r['aircraft']}, {', '.join(r['dates'])}",
    "pairing_crew": lambda r: f"{_who(r)}, {r['role']} on {r['pairing_id']} ({r['aircraft']})",
    "pairing_days": lambda r: (
        f"{r['pairing_id']} on {r['date']}: {', '.join(_flight_nos(r['flights']))}, report "
        f"{_hhmm(r['report_utc'])}, release {_hhmm(r['release_utc'])}"
    ),
    "reserves": lambda r: (
        f"{_who(r)}, {r['rank']}, {r['base']}, on call {r['oncall_window_utc']['start']}Z to "
        f"{r['oncall_window_utc']['end']}Z"
    ),
    "certifications": lambda r: f"{r['crew_id']} {r['cert_type']} valid to {r['valid_to']}",
    "risk_signals": lambda r: (
        f"{r['crew_id']} has a risk score of {_n(r['disruption_risk_score'])}"
        + (f": {'; '.join(r['drivers'])}" if r.get("drivers") else "")
    ),
    "duty_clocks": lambda r: (
        f"{r['crew_id']}: {_n(r['duty_hours_7d'])} duty hours in the last week and "
        f"{_n(r['flight_hours_28d'])} flight hours in the last four weeks, as of "
        f"{r['as_of_utc'][:10]}"
    ),
    "roster_exceptions": lambda r: f"{r['crew_id']} on {r['date']}: {r['rule']}, {r['note']}",
    "costs": lambda r: "; ".join(f"{k.replace('_', ' ')} {_n(v)}" for k, v in r.items()),
}

NOUN = {
    "crew": "crew members",
    "flights": "flights",
    "pairings": "pairings",
    "pairing_crew": "crew assignments",
    "pairing_days": "duty days",
    "reserves": "reserve duties",
    "certifications": "certifications",
    "risk_signals": "risk scores",
    "duty_clocks": "duty clocks",
    "roster_exceptions": "roster exceptions",
    "costs": "cost tables",
}


def _lookup(r: dict[str, Any], args: dict[str, Any]) -> str:
    entity = r["entity"]
    if "groups" in r:
        parts = []
        for g in r["groups"][:12]:
            label = ", ".join(str(v) for k, v in g.items() if k != "count")
            parts.append(f"{label}: {_n(g['count'])}")
        more = " (more groups not read out)" if len(r["groups"]) > 12 else ""
        return f"{_n(r['count'])} {NOUN[entity]} in all. " + "; ".join(parts) + more + "."
    rows = r["rows"]
    if not rows:
        return f"No {NOUN[entity]} match that."
    if entity == "costs":
        return "Costs in rupees: " + ROW["costs"](rows[0]) + "."
    if r["count"] == 1:
        return ROW[entity](rows[0]) + "."
    lines = [ROW[entity](row) for row in rows[:SHOW]]
    if args.get("sort_by"):
        order = "highest" if args.get("sort_desc") else "lowest"
        field = str(args["sort_by"]).replace("_", " ")
        return f"By {field}, {order} first: " + "; ".join(lines) + "."
    tail = " Those are the first few; ask to narrow it down." if r["count"] > len(lines) else ""
    return f"{_n(r['count'])} {NOUN[entity]}: " + "; ".join(lines) + "." + tail


# --------------------------------------------------------------------------
# Other tools
# --------------------------------------------------------------------------


def _duty_clock(r: dict[str, Any], args: dict[str, Any]) -> str:
    d, f = r["duty_window_days"], r["flight_window_days"]
    return (
        f"{_who(r)} as of {r['as_of']}: {_n(r[f'duty_hours_{d}d'])} duty hours over the {d} days "
        f"{r['duty_window']}, leaving {_n(r[f'duty_headroom_{d}d'])} under the "
        f"{_n(r[f'duty_limit_{d}d'])}-hour limit. {_n(r[f'flight_hours_{f}d'])} flight hours "
        f"over the {f} days {r['flight_window']}, leaving {_n(r[f'flight_headroom_{f}d'])} "
        f"under {_n(r[f'flight_limit_{f}d'])}. That day alone: "
        f"{_n(r['duty_hours_this_date'])} duty hours."
    )


def _brief(r: dict[str, Any], args: dict[str, Any]) -> str:
    lines = [
        f"Callout for {_who(r)}: you are called out to operate {r['pairing_id']} on "
        f"{r['aircraft']} as {r['role']}."
    ]
    for i, day in enumerate(r["days"], start=1):
        line = (
            f"Day {i}, {day['date']}: report {_hhmm(day['report_utc'])} at "
            f"{day['report_station']}; {'; '.join(day['flights'])}; release "
            f"{_hhmm(day['release_utc'])}"
        )
        if day.get("overnight_station"):
            line += f"; overnight {day['overnight_station']}"
        lines.append(line + ".")
    if r.get("acknowledge_by_utc"):
        lines.append(
            f"Please acknowledge by {_hhmm(r['acknowledge_by_utc'])} on "
            f"{r['acknowledge_by_utc'][:10]}, {_n(r['reachability_minutes'])} minutes before "
            "report, so cover can still be arranged if you cannot accept."
        )
    return " ".join(lines)


def _legality(r: dict[str, Any], args: dict[str, Any]) -> str:
    who = _who(r)
    delay = f" with a {_n(r['delay_hours'])}-hour delay" if r.get("delay_hours") else ""
    if r["legal"]:
        extra = ""
        if r.get("positioning"):
            p = r["positioning"]
            extra = (
                f" They position on {p['flight_id']} from {p['from_station']}, arriving "
                f"{_hhmm(p['arrives_utc'])}"
                + (
                    f", which delays the first departure {_n(p['delay_hours'])} hours."
                    if p["delay_hours"]
                    else "."
                )
            )
        return f"{who} is legal for {r['pairing_id']}{delay}: every rule passes.{extra}"
    reasons = "; ".join(f"{v['rule_id']} fails: {v['detail']}" for v in r["blocking"])
    return f"{who} is not legal for {r['pairing_id']}{delay}. {reasons}."


def _option(o: dict[str, Any], lead: str = "") -> str:
    """Lower-cases the engine's "Assign ..." label so it fits mid-sentence ("is to assign
    Captain ..." or "assign ..." in a list)."""
    action = o["action"]
    if action.startswith("Assign "):
        action = f"{lead}assign {action[len('Assign ') :]}"
    return f"{action}, {_inr(o['cost_inr'])}"


def _options(r: dict[str, Any], args: dict[str, Any]) -> str:
    seat = f"the {r['role']} seat on {r['pairing_id']}"
    out = f" with {r['crew_out']} out" if r.get("crew_out") else ""
    rec = r.get("recommended")
    cancel = r["cancellation"]
    if not rec:
        return (
            f"No legal cover for {seat}{out}; {r['excluded_count']} candidates were ruled out. "
            f"Cancelling costs {_inr(cancel['cost_inr'])}: {cancel['action']}."
        )
    text = f"For {seat}{out}, the cheapest legal choice is {_option(rec, 'to ')}."
    others = [o for o in r["options"][1:3]]
    if others:
        text += " Next: " + "; ".join(_option(o) for o in others) + "."
    if r.get("equal_cost_alternatives"):
        text += f" {_count(r['equal_cost_alternatives'], 'other option')} cost the same."
    text += (
        f" {_count(r['legal_options'], 'legal option')} in all. Cancelling instead costs "
        f"{_inr(cancel['cost_inr'])}"
    )
    if r.get("cancellation_multiple"):
        text += f", {_n(r['cancellation_multiple'])} times as much"
    return text + "."


def _same(r: dict[str, Any], args: dict[str, Any]) -> str:
    a, b = _who(r["crew_a"]), _who(r["crew_b"])
    if r["same_pairing"]:
        return f"Yes, {a} and {b} fly {_list(r['shared_pairings'])} together."
    return f"No, {a} and {b} do not fly a pairing together this week."


def _ripple(r: dict[str, Any], args: dict[str, Any]) -> str:
    if not r["affected_pairings"]:
        return r.get("note", "No flights are affected.")
    parts = []
    for p in r["affected_pairings"]:
        hit = _list(_flight_nos(p["directly_affected_flights"]))
        line = f"{p['pairing_id']} on {p['aircraft']}: {hit}"
        if p["at_risk_flights"]:
            line += f", with {_list(_flight_nos(p['at_risk_flights']))} at risk after"
        parts.append(line)
    text = (
        f"{_count(r['directly_affected_flights'], 'flight')} directly affected and "
        f"{_n(r['at_risk_flights'])} more at risk, {_count(r['passengers'], 'passenger')}. "
        + "; ".join(parts)
        + "."
    )
    if r.get("already_departed"):
        text += " Flights that already departed are not counted."
    return text


def _simulate(r: dict[str, Any], args: dict[str, Any]) -> str:
    what = r["from_flight"].split("-")[0] if r.get("from_flight") else r["pairing_id"]
    text = (
        f"A {_n(r['delay_hours'])}-hour delay to {what} on {r['date']} stretches the duty from "
        f"{_n(r['fdp_hours_before'])} to {_n(r['fdp_hours_after'])} hours against a "
        f"{_n(r['fdp_limit'])}-hour limit."
    )
    if r["duty_breaches_limit"]:
        legs = _list(_flight_nos(r["legs_to_drop"]))
        text += (
            f" No crew can legally fly the whole day: {legs} must come off this crew, and "
            f"cancelling {'it' if len(r['legs_to_drop']) == 1 else 'them'} costs "
            f"{_inr(r['cancellation_cost_inr'])}."
        )
    if r["changed"]:
        names = _list([f"{c['crew_id']} ({c['role']})" for c in r["changed"]])
        text += f" Rostered crew who stop being legal: {names}."
    elif not r["duty_breaches_limit"]:
        text += " The rostered crew all stay legal."
    return text


def _joint(r: dict[str, Any], args: dict[str, Any]) -> str:
    if not r["assignments"]:
        text = "No seat can be covered legally."
    else:
        text = (
            "Cheapest joint plan: "
            + "; ".join(
                f"{a['pairing_id']} {a['role']}: {_who(a)}, {_inr(a['cost_inr'])}"
                for a in r["assignments"]
            )
            + f". Total {_inr(r['total_cost_inr'])}."
        )
    if r.get("equal_cost_alternatives"):
        text += f" {_count(r['equal_cost_alternatives'], 'other plan')} cost the same."
    if r.get("unfilled"):
        text += (
            " No legal cover for "
            + _list([f"{u['pairing_id']} {u['role']}" for u in r["unfilled"]])
            + "."
        )
    if r.get("already_started"):
        text += " Already started: " + _list([s["pairing_id"] for s in r["already_started"]]) + "."
    return text


def _gate(r: dict[str, Any], args: dict[str, Any]) -> str:
    if "actual_boarding_gate_number" in r:
        f = r["flight_id"].split("-")[0]
        text = f"{f} on {r['date']} boards at {r['actual_boarding_gate_number']}"
        if r.get("claimed_boarding_gate_number") and not r["gate_match"]:
            text += f", not {r['claimed_boarding_gate_number']}"
        text += f", from {_hhmm(r['boarding_start_time'])} to {_hhmm(r['boarding_end_time'])}."
        if c := r.get("gate_conflict"):
            text += (
                f" Delayed, it overlaps {c['flight_id'].split('-')[0]} at that gate by "
                f"{_n(c['overlap_minutes'])} minutes."
            )
        elif r.get("delay_minutes"):
            text += " Delayed, it still clears the gate before the next boarding."
        return text
    if "boarding_gate_number" in r:
        text = f"{r['boarding_gate_number']}"
        if "occupied" in r:
            if r["occupied"]:
                text += (
                    f" is in use as of {_dated(r['checked_at_utc'])}: "
                    f"{_flight(r['occupying_flight_id'])} holds it until "
                    f"{_dated(r['occupied_until'])}."
                )
            else:
                text += f" is free as of {_dated(r['checked_at_utc'])}."
        else:
            text += f" on {r['date']}:"
        now = r.get("checked_at_utc") if "occupied" in r else None
        slots = [
            f"{_flight(s['flight_id'])} from {_dated(s['boarding_start_time'])}"
            for s in r["schedule"]
            if s["flight_id"] != r.get("occupying_flight_id")
            and (now is None or s["boarding_end_time"] > now)
        ][: 3 if now else SHOW]
        if slots:
            text += (" Next boardings: " if now else " Boardings: ") + "; ".join(slots) + "."
        elif now is None:
            text += " no boardings."
        return text
    if "occupied_boarding_gates" in r:
        return (
            f"{_n(r['occupied_boarding_gates'])} of {_n(r['of_boarding_gates'])} boarding gates "
            f"are in use on {r['date']}: {', '.join(r['occupied_gate_numbers'])}."
        )
    return (
        f"{_n(r['total_boarding_gates'])} boarding gates: "
        + "; ".join(f"{s} {_n(c)}" for s, c in r["gates_by_station"].items())
        + "."
    )


def _rule(r: dict[str, Any], args: dict[str, Any]) -> str:
    return f"{r['rule_id']}: {r['text']}"


def _search_rules(r: dict[str, Any], args: dict[str, Any]) -> str:
    if not r["candidates"]:
        return "No rule matches that question."
    best = r["candidates"][0]
    return f"The closest rule is {best['rule_id']}: {best['text']}"


def _suggest(r: dict[str, Any], args: dict[str, Any]) -> str:
    if not r["suggestions"]:
        return f"Nothing real is close to {r['near']}."
    names = []
    for s in r["suggestions"]:
        key = s.get("crew_id") or s.get("pairing_id") or s.get("flight_no")
        names.append(_who(s) if "name" in s else key)
    return f"{r['near']} doesn't exist. Did you mean {_list(names)}?"


def _controllers(r: dict[str, Any], args: dict[str, Any]) -> str:
    return (
        "Controller desks: "
        + "; ".join(f"{d['controller']} ({d['desk_id']})" for d in r["desks"])
        + ". No desk owns an aircraft: each new disruption goes to the controller with the "
        "fewest open ones."
    )


def _issues(r: dict[str, Any], args: dict[str, Any]) -> str:
    text = (
        "Open disruptions: "
        + "; ".join(f"{d['controller']} {_n(d['open_issues'])}" for d in r["desks"])
        + "."
    )
    if r.get("unassigned"):
        text += f" Not tied to any desk: {', '.join(r['unassigned'])}."
    return text


def _open(r: dict[str, Any], args: dict[str, Any]) -> str:
    where = f" on {r['desk']}" if r.get("desk") else ""
    rows = r["disruptions"]
    if not rows:
        return f"No open disruptions{where} right now."
    head = f"{_n(r['count'])} open disruption{'s' if r['count'] != 1 else ''}{where}: "
    shown = rows[:8]
    text = head + "; ".join(
        f"{d['disruption_id']}, {d['what']} ({d['status'].replace('_', ' ').lower()})"
        for d in shown
    )
    if len(rows) > len(shown):
        text += f"; and {_n(len(rows) - len(shown))} more"
    return text + "."


RENDERERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], str]] = {
    "lookup": _lookup,
    "duty_clock": _duty_clock,
    "notification_brief": _brief,
    "check_legality": _legality,
    "find_options": _options,
    "same_pairing": _same,
    "ripple": _ripple,
    "simulate": _simulate,
    "joint_plan": _joint,
    "check_gate": _gate,
    "explain_rule": _rule,
    "search_rules": _search_rules,
    "suggest_crew_ids": _suggest,
    "suggest_pairing_ids": _suggest,
    "suggest_flight_nos": _suggest,
    "list_controllers": _controllers,
    "controller_issue_counts": _issues,
    "list_open_disruptions": _open,
}


def render_entry(entry: TraceEntry) -> str:
    if not entry.ok:
        if entry.error_code == "FORBIDDEN":
            return "That isn't available to your role."
        return str(entry.error)
    try:
        return RENDERERS[entry.tool](
            entry.result, entry.args if isinstance(entry.args, dict) else {}
        )
    except (KeyError, TypeError, IndexError, StopIteration):
        return f"{entry.tool.replace('_', ' ')} returned a result I can't read out."


def render(trace: Sequence[TraceEntry]) -> str:
    """Results in call order. Identical repeats are only said once."""
    seen: set[str] = set()
    parts = []
    for entry in trace:
        text = render_entry(entry)
        if text not in seen:
            seen.add(text)
            parts.append(text)
    return "\n".join(parts)
