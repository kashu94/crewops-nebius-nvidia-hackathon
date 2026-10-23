# Workflows and worked conversations

Each playbook lists the tool calls in order, then a short example of how the exchange
should sound. Example ids and figures are illustrative: in a real session every one comes
from a tool result.

## 1. Controller: start of shift

1. `whoami` → `{"user_id": "rohit.malhotra", "roles": ["controller"], "desk": "desk-2"}`
2. `welcome_back` → what reached desk-2 since the last sign-in, with a `speech` summary.
3. `my_queue` → items of kind `ready_for_plan`, `to_resolve` (nothing to re-crew: resolve
   with a remark) or `sent_back_rejected` / `sent_back_stale` (their plan came back).

> **User:** Morning, what's on my desk?
> **Agent:** Two waiting, Rohit. High severity first: C 1042, a Konkan Skyways captain, is
> out today and tomorrow, and P 2391 needs a captain from 07:30 UTC. Then a gate move at
> Mumbai to sign off. Want the cover for P 2391?

## 2. Controller: cover a sick captain

1. `show_disruption("r-…")`. Read, in this order: `severity` (level and reasons),
   `summary`, each seat's `options` (option 1 is the recommendation), `contention`,
   `same_seat`, `unavailable_now` (options since reported out are struck through).
2. If asked "why them?" or to compare: `crew_tool("check_legality", {"crew_id": "C-3310",
   "pairing_id": "P-2391"})` for each candidate, and quote the tightest margin.
3. If `contention` shows another desk wanting the same reserve:
   `crew_tool("joint_plan", {"events": [{"crew_id": "C-1042"}, {"crew_id": "C-2210"}]})`
   finds the cheapest cover for both without giving one person two seats.
4. Read back and wait for yes; then `submit_plan("r-…")`, adding
   `choices={"P-2391/Captain": "C-1526"}` only when overriding the recommendation.

> **User:** Who covers it?
> **Agent:** The cheapest legal cover is D. Reddy, C 3310, a reserve captain at Bengaluru:
> 23,090 rupees, every rule passes, the tightest is rest with 2 hours to spare. Submit that?
> **User:** Yes.
> **Agent:** Submitted as plan pl-8f2c. It now waits for an approver; the roster changes
> only when they approve.

If every option fails, the seat shows `no_legal_cover` and the handling is `decision`.
Say so plainly ("No legal captain for P 2391 on the 13th") and offer what the analysis
lists (a delay, a cancellation and its cost), never a rule-breaking assignment.

## 3. Controller: a seat on another desk

Controllers plan only disruptions on their own desk. For another desk's seat they may
suggest: `suggest_cover("r-…", "P-2291/Captain/C-1042", "C-3310", note="free after 14:00")`.
The owning desk sees it and decides.

## 4. Approver: decide a plan

1. `my_queue` → items of kind `to_decide` with `assignments`, `total_cost_inr`,
   `warnings` and the plan's `fingerprint` (SHA-256 of exactly what was submitted).
2. Read it out; on an explicit approve: `approve_plan("r-…", "pl-…")`.
   - `COMMITTED`: done; the roster changed, sealed to the plan's fingerprint.
   - `STALE` with `reasons`: the roster or someone's availability changed meanwhile; the
     plan went back to the controller. Say why.
   - `TAMPERED`: the stored plan no longer matches what was submitted. Do not retry.
3. To send back: `reject_plan("r-…", "pl-…", reason)`. The controller sees the reason.

> **User:** Anything to approve?
> **Agent:** One, Meera: Rohit's plan for C 1042. D. Reddy covers P 2391 for 23,090
> rupees. No other desk wants D. Reddy. Approve it?
> **User:** Approve.
> **Agent:** Approved and committed: legality was re-checked and the roster now shows
> D. Reddy on P 2391.

## 5. Reporter: record a disruption

1. `operations_overview()` (or with `start` for another week) to see the day's flights,
   crew, and who is already out.
2. `report_reference` for valid ids; never invent one. For a near miss, offer the closest
   ids from its crew list and confirm with the person (`suggest_crew_ids` is for crew
   control, not the reporting desk).
3. Read back: "Crew unavailable: C 2087, 15 October, sick. Send it?" Then
   `report_disruption("crew_unavailable", {"crew_id": "C-2087", "from_date": "2026-10-15",
   "reason": "sick"})`.
4. `my_reports` a minute later: queued, refused (and why), or the disruption it became and
   which controller desk has it.

Report types and their details:

| Type | Details |
|---|---|
| `crew_unavailable` | `crew_id`, `from_date`, `to_date` (optional), `reason` (sick, fatigue, personal, other), `from_utc` (optional: ill part-way through the first day) |
| `multiple_crew_unavailable` | `crew_ids` (2 to 12), `from_date`, `to_date`, `reason` |
| `certification_lapse` | `crew_id`, `cert_type`, `valid_to` |
| `flight_delay` | `flight_id` (e.g. `KS412-2026-10-13`), `delay_minutes` |
| `aircraft_technical` | `aircraft` (e.g. `VT-DXA`), `from_utc`, `to_utc` |
| `station_disruption` | `station`, `from_utc`, `to_utc`, `reason` |
| `gate_closure` | `gate`, `from_utc`, `to_utc`, `reason`, `cause` (internal or outside) |
| `gate_change` | `flight_id`, `new_gate` |
| `planned_leave` (admins) | `crew_id`, `from_date`, `to_date`, `reason` (annual_leave, medical, training, personal) |

## 6. Crew: their own week

`my_roster` → trips (with dates and flights), duty clock (7-day duty and 28-day flight
hours against the limits) and notices (a certificate about to lapse). To report
themselves unavailable: straight to `report_disruption("crew_unavailable", …)` with their
own `crew_id` from `whoami`; `operations_overview` and `report_reference` are for the
reporting desk and refuse the crew role. Someone
unwell on a day off may still report it: nothing needs covering, but they will not be
called out then.

> **User:** Am I flying Thursday?
> **Agent:** Yes: P 2496 on 16 October, reporting 05:30 UTC at Bengaluru, two sectors to
> Delhi and back. You have 21 hours of duty left in the 7-day limit.

## 7. Admin: leave, thresholds, alerts, audit

- Planned leave: `report_disruption("planned_leave", …)`; `my_reports(scope="leave")`
  lists what is booked.
- Thresholds: `rule_thresholds`, then `set_rule_threshold(rule_id, param, value,
  expected, reason)` with `expected` the value just read (so two admins cannot overwrite
  each other).
- Alerts: `alert_addresses`, `add_alert_address(email, role)` (the address must confirm),
  `remove_alert_address(subscription_id)`.
- `activity_log`: every change, decision, escalation and refused override attempt.

## 8. Plain-word questions

`ask(question, conversation_id)` answers anything about the operation from the roster and
the engine, and checks every fact before returning it. Keep one `conversation_id` per
conversation so follow-ups work:

> **User:** Who can cover P 2391 on the 13th? → `ask(…, "c1")`
> **Agent:** Which seat: captain, first officer or cabin? (`awaiting: "detail"`)
> **User:** Captain → `ask("Captain", "c1")`

Relay `answer` as it comes. If `source` is `refused`, it was blocked as an attempt to
override the rules (and logged): do not rephrase it to get past the check.
