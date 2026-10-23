# CrewOps MCP tools

Generated from the server by `python -m crewops.mcp.skill_docs`; do not edit by hand.

Every call runs as the signed-in person: the server reads who they are from the access
token, never from the arguments, and refuses what their role may not do (`FORBIDDEN: ...`).
**Changes** marks tools that change something (a report, a plan, the roster, a setting);
**commits** marks the ones that cannot be undone by the same person (approving a plan
rewrites the roster). Ask the person before calling either.

## Server tools

### `activity_log`: reads only

Admin: the latest audit events across the system, newest first.

Parameters: `limit` (integer, optional, default `50`).

### `add_alert_address`: **changes**

Admin: send one role's alerts (controller: options ready or a plan sent back; approver: a plan waiting), or one operational team's (gate_control, station_ops, flight_info, ops_monitoring, maintenance_control, crew_records, customer_care: the disruptions that team must act on), to an email address. AWS asks the address to confirm first. Limited to a few new addresses a day.

Parameters: `email` (string, required); `role` (string, required).

### `alert_addresses`: reads only

Admin: the email addresses receiving alerts, the role each receives, and whether it has been confirmed.

Parameters: none.

### `approve_plan`: **commits**

Approver: approve a plan you did not submit. Legality is checked again, and the roster is changed in one step; a plan whose roster changed meanwhile comes back STALE instead.

Parameters: `disruption_id` (string, required); `plan_id` (string, required).

### `ask`: reads only

Ask a crew-control question in plain words. The answer is built from the live roster and the legality engine, and every fact in it is checked before it is given. `awaiting` says when the answer is a question back (which id, which date). Pass the same `conversation_id` (letters, digits, - and _) on every question of one conversation, so a short reply ("Captain") or "is she legal for P-2291?" is read with the previous question.

Parameters: `question` (string, required); `conversation_id` (string, optional, default `''`).

### `close_disruption`: **changes**

Controller: resolve a disruption that has no coverable seat, saying what was done (for example, the flights were cancelled), or one handled outside crew control (a gate closure re-gated, a delay every crew member absorbs). It then waits for an approver to sign it off.

Parameters: `disruption_id` (string, required); `reason` (string, required).

### `confirm_resolution`: **changes**

Approver: sign off a disruption a controller resolved with a remark (it is then closed). Four eyes: never one you sent yourself.

Parameters: `disruption_id` (string, required).

### `crew_tool`: reads only

Call one deterministic tool by name (see `list_crew_tools`) with JSON arguments, e.g. name="find_options", arguments={"crew_id": "C-1042"}. Structured data, no model involved.

Parameters: `name` (string, required); `arguments` (null/object, optional, default `None`).

### `disruptions`: reads only

Recent disruptions in every status, newest report first, with their analysis summary: the desk's chronological feed.

Parameters: `limit` (integer, optional, default `40`).

### `list_crew_tools`: reads only

The deterministic crew-operations tools the signed-in person may call directly with `crew_tool`, with their input schemas.

Parameters: none.

### `my_queue`: reads only

What is waiting for the signed-in person: plans to decide (approver), disruptions on their desk ready for a plan, and their plans sent back (controller).

Parameters: none.

### `my_reports`: reads only

Reports and how each stands, newest first: still being checked, refused (and why), or the disruption it became. scope "mine" (what you filed), "crew" (what crew filed about themselves, for the reporting desk and crew control) or "leave" (planned leave booked by admins).

Parameters: `scope` (string, optional, default `'mine'`); `limit` (integer, optional, default `30`).

### `my_roster`: reads only

Crew: your own pairings, duty days and duty clock.

Parameters: none.

### `operations_overview`: reads only

A week of the operation at a glance, from ``start`` (YYYY-MM-DD; today if empty): every flight with its airline, aircraft and crew, who is reported unavailable when, and the open disruptions on each flight. For the reporting desk and crew control.

Parameters: `start` (string, optional, default `''`).

### `ops_board`: reads only

The ops-room wall display: every open disruption (most serious and escalated first) with its desk, waiting time and recommended cover; each desk's load; and today's flights, crew out and reserves on call. Speaker devices and crew control.

Parameters: none.

### `push_subscribe`: **changes**

Controllers and approvers: turn on desktop alerts in this browser with the push subscription its service worker made (endpoint and keys).

Parameters: `endpoint` (string, required); `p256dh` (string, required); `auth` (string, required).

### `push_unsubscribe`: **changes**

Turn desktop alerts off for one browser.

Parameters: `endpoint` (string, required).

### `reject_plan`: **changes**

Approver: send a plan back to its controller, saying why.

Parameters: `disruption_id` (string, required); `plan_id` (string, required); `reason` (string, required).

### `remove_alert_address`: **commits**

Admin: stop sending alerts to an address (its id from `alert_addresses`). An address never confirmed lapses after three days but stays listed, and counts toward the limit, until it is removed here.

Parameters: `subscription_id` (string, required).

### `report_disruption`: **changes**

Report a disruption (today to two months ahead), or, as an admin, book planned leave (planned_leave: crew_id, from_date, to_date, reason annual_leave, medical, training or personal; up to the end of the rostered period). report_type is one of crew_unavailable, multiple_crew_unavailable, certification_lapse, flight_delay, aircraft_technical, station_disruption, gate_closure, gate_change. details depend on the type, e.g. crew_unavailable: {"crew_id": "C-1042", "from_date": "2026-10-13", "to_date": "2026-10-14", "reason": "sick"}; multiple_crew_unavailable: the same with "crew_ids" (2 to 12) instead of "crew_id"; gate_closure: {"gate": "BLR-G2", "from_utc", "to_utc", "reason"}. Crew may report only their own unavailability. It is analysed automatically.

Parameters: `report_type` (string, required); `details` (object, required); `narrative` (string, optional, default `''`).

### `report_reference`: reads only

Reporters, controllers and approvers: the crew, flights, aircraft, gates and stations a report can name.

Parameters: none.

### `reset_password`: **commits**

Admin: give a team member a new random password, returned once. An admin resets crew, reporters, controllers, approvers and auditors; only a super admin resets an admin. Nobody resets their own here, nor the speaker device's.

Parameters: `username` (string, required).

### `return_resolution`: **changes**

Approver: send a resolved disruption back to its desk, saying why.

Parameters: `disruption_id` (string, required); `reason` (string, required).

### `revoke_report`: **commits**

Admin: withdraw a report filed in error (a reporter's report, a crew member's own unavailability or planned leave), saying why. Only while nothing has been done about it: reported, being planned, or a plan waiting for approval (that plan is withdrawn too). Once cover is approved, crew control resolves it instead. The crew it named count as available again.

Parameters: `disruption_id` (string, required); `reason` (string, required).

### `rule_thresholds`: reads only

The legality rules' adjustable thresholds: current value, allowed range (never looser than regulation) and the rule's text.

Parameters: none.

### `set_rule_threshold`: **commits**

Admin: change one threshold from the value you saw (`expected`) to `value`, saying why. Only stricter values, or the regulatory one, are accepted; the change is audited and the engine uses it within a minute.

Parameters: `rule_id` (string, required); `param` (string, required); `value` (number, required); `expected` (number, required); `reason` (string, required).

### `show_disruption`: reads only

A disruption's status, analysis summary, plans and audit trail.

Parameters: `disruption_id` (string, required).

### `speaker_feed`: reads only

Speaker devices only: the spoken alerts for a desk speaker (scope desk-1, desk-2, desk-3), the approvals corner ("approvals") or the whole ops room ("room"): the latest event of each disruption, newest first, each with an id to remember it by.

Parameters: `scope` (string, optional, default `'room'`); `limit` (integer, optional, default `20`).

### `still_here`: **changes**

Controllers and approvers: record that your console is open now, so the next "welcome back" covers only what came after.

Parameters: none.

### `submit_plan`: **changes**

Controller: submit cover for a disruption on your desk. By default the analysis's recommendation; choices overrides a seat, e.g. {"P-2291/Captain": "C-1526"}. A different approver must approve it.

Parameters: `disruption_id` (string, required); `choices` (null/object, optional, default `None`); `note` (string, optional, default `''`); `accept_uncovered` (boolean, optional, default `False`).

### `suggest_cover`: **changes**

Controller: suggest cover for a seat on another desk's disruption (seat as "P-2291/Captain/C-1042"). The owning desk sees it and may adopt it; it plans nothing.

Parameters: `disruption_id` (string, required); `seat` (string, required); `crew_id` (string, required); `note` (string, optional, default `''`).

### `team_accounts`: reads only

Admin: the team's sign-ins, roles, and whether you may reset each password.

Parameters: none.

### `welcome_back`: **changes**

Controllers and approvers: what came to your desk (or to approvals) since you last signed in, with a spoken summary. Records this sign-in.

Parameters: none.

### `whoami`: reads only

The signed-in person as the server sees them: username, roles, and their crew id or desk where they have one.

Parameters: none.

### `withdraw_plan`: **changes**

Controller: withdraw a plan you submitted that is still awaiting a decision.

Parameters: `disruption_id` (string, required); `plan_id` (string, required); `reason` (string, optional, default `''`).

## Crew tools (through `crew_tool`)

Deterministic: structured rows and verdicts from the roster and the legality
engine, no model involved. Call `list_crew_tools` for the exact input schemas the
signed-in person may use; `crew_tool(name, arguments)` calls one.

| Tool | Who may call it | What it does |
|---|---|---|
| `lookup` | controller, approver, auditor | Rows from the operation (the rostered calendar): crew, flights, pairings, reserves, certifications, risk signals, duty clocks, roster exceptions, costs. |
| `duty_clock` | crew, controller, approver, auditor | A crew member's hours as of a date (default: now): that day's own duty and flight hours, the 7-day duty total and 28-day flight total with limits and headroom. |
| `notification_brief` | crew, controller, approver, auditor | Every fact a callout message needs for one crew member on one pairing: report time and station, legs per day, overnights, and an acknowledge-by time from their reachability. |
| `check_legality` | crew, controller, approver, auditor | All seven legality rules for one crew member on one pairing, with the numbers behind each verdict. |
| `find_options` | controller, approver, auditor | Every legal way to fill a seat, cheapest first, with cancellation as the baseline, and why everyone else is out (the funnel). |
| `same_pairing` | controller, approver, auditor | Whether two crew members fly a pairing together this week. |
| `ripple` | controller, approver, auditor | The blast radius of a disruption: uncovered flights, later flights at risk, passengers, aircraft and desks. |
| `simulate` | controller, approver, auditor | What a delay does to one duty day: the duty period against its limit, which rostered crew stop being legal and why, and, if no crew could legally fly the whole day, the fewest trailing legs to drop and what cancelling them costs. |
| `joint_plan` | controller, approver, auditor | Cheapest cover for several seats at once, never giving one person two seats. |
| `check_gate` | reporter, controller, approver, auditor | Boarding gates. |
| `explain_rule` | crew, reporter, controller, approver, auditor, admin, super_admin | The text and parameters of one legality rule, by id. |
| `search_rules` | crew, reporter, controller, approver, auditor, admin, super_admin | The rules closest to a legality question that names no rule id ('how much sleep does a pilot need between duties'). |
| `suggest_crew_ids` | controller, approver, auditor | Real crew ids near one that does not exist or has the wrong shape ('C-10'). |
| `suggest_pairing_ids` | controller, approver, auditor | Real pairing ids near one that does not exist or has the wrong shape ('P-22'). |
| `suggest_flight_nos` | reporter, controller, approver, auditor | Real flight numbers near one that does not exist or has the wrong shape ('DX9999'). |
| `list_controllers` | reporter, controller, approver, auditor, admin, super_admin | The controller desks and the aircraft each covers. |
| `list_open_disruptions` | controller, approver, auditor, admin, super_admin | The disruptions being worked right now, from the live store: each one's id, what happened, its status and its desk. |
| `controller_issue_counts` | controller, approver, auditor, admin, super_admin | Open disruptions per controller desk, from the live store. |
