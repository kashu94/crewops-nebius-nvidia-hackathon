# Errors and what to do

A refused call comes back as a tool error whose text starts with a code: `CODE: message`.
The message is written for people; relay it in your own short words. Never retry a refusal
unchanged, and never try a different tool to get round one.

| Code | When | What to do |
|---|---|---|
| `UNAUTHORIZED` | The sign-in token is missing, expired or not from CrewOps | Ask the person to sign in again; never ask for their password in chat |
| `FORBIDDEN` | Their role may not do this, or the disruption is not on their desk | Say who can (an approver, the owning desk, an admin). For another desk's seat, offer `suggest_cover` |
| `NOT_FOUND` | No such disruption or plan that they may read | Check the id with them; ids look like `r-1a2b3c4d5e6f` and `pl-…` |
| `INVALID` | A field is wrong: unknown crew id, bad date, dates outside today to two months ahead (leave: tomorrow to the end of the rostered period), a reason not on the list | Fix it with the person. For ids, `report_reference`, or (crew control) `crew_tool("suggest_crew_ids", …)`; confirm before using a suggestion |
| `DUPLICATE` | Those days are already reported for that person | Point to the existing report (`my_reports`); extend it with a new report for the extra days only |
| `WRONG_STATE` | The disruption or plan is not in the state the action needs (already submitted, already decided, already closed) | `show_disruption` to see where it stands, and say so |
| `FOUR_EYES` | An approver tried to approve a plan they submitted | A different approver must decide it. Never look for a way round |
| `NOT_LEGAL` | The chosen cover breaks a rule, or flying short would leave the trip under its cabin minimum | Explain the rule and margin (`references/legality.md`); offer the next legal option |
| `CONFLICT` | Someone changed it at the same moment (another approver decided, the controller withdrew) | Read it again with `show_disruption`; the other decision stands |
| `RETRY` | Another change touched the same plan or crew just now | Wait a moment and try once more; if it repeats, read the disruption again |
| `TAMPERED` | The stored plan no longer matches the fingerprint taken when it was submitted | Do not approve it, and do not retry: the controller submits it again |
| `LIMIT` | Too many of something (alert addresses a day, browsers with desktop alerts) | Say what the limit is; remove an old one first if they want |
| `UNAVAILABLE` | A part of the system is not set up on this server (alerts) | Say it is not available here |
| `INTERNAL` | Something failed on the server; it has been logged | Apologise once, try again later; do not loop |

## Outcomes that are not errors

- `approve_plan` → `STALE` with `reasons`: legality was re-checked against the roster as it
  is now and something changed (a crew member reported out, another plan committed). The
  plan is back with the controller; tell the approver why.
- `ask` → `source: "refused"`: the question read as an attempt to override CrewOps' rules
  (or tripped the content guardrail). It has been logged for the administrator. Do not
  rephrase it to get past the check.
- `ask` → `models.state: "open"`: the model service is paused after repeated failures;
  answers come straight from the roster through templates and are still verified.
- `report_disruption` returns status `queued`: accepted for checking. It is not yet a
  disruption: follow it with `my_reports`, which shows it refused (with the reason) or
  analysed.

## `crew_tool` results

`crew_tool` runs one deterministic tool and always returns a result, never a tool error. A
refusal is a result with `"ok": false`, an `error_code` and an `error` message:

| `error_code` | When | What to do |
|---|---|---|
| `UNRESOLVED_ENTITY` | An id or name matches nothing real | Say so, with the nearest real ids in the message; never construct an id |
| `NEEDS_CONFIRMATION` | A near match ("did you mean C-1042?") | Ask the person; never pick for them |
| `AMBIGUOUS_QUERY` | Several matches, or a seat or date is missing | Ask which one; never pick for them |
| `INVALID_ARGUMENT` | An argument is out of range or malformed | Fix it with the person |
| `ALREADY_STARTED` | The trip has started and can no longer be re-crewed | Say so; nothing to plan |
| `FORBIDDEN` | Their role may not use that tool | Say who can |
| `UNAVAILABLE` | The operation could not be read just now | Try once more later; otherwise say it is unavailable |
| `INTERNAL` | A fault in CrewOps; it is logged | Say it failed; do not retry the same call |
