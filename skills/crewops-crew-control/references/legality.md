# Legality, cover and severity

How CrewOps decides who may fly, how cover is ranked, and how serious a disruption is.
Everything here is computed by the engine; this page is for explaining its answers, not
for working them out yourself. Quote the numbers the tools return.

All times are UTC. Duty windows count calendar days (UTC dates), including the duty date.

## The seven rules

| Rule | Plain words | Default limit |
|---|---|---|
| `RULE-FDP-01` | Longest flight duty period in one day | 13 h, less 0.5 h for each sector after the 2nd |
| `RULE-DUTY-02` | Duty hours in any 7 calendar days | 60 h |
| `RULE-FLT-03` | Flight (block) hours in any 28 calendar days | 100 h |
| `RULE-REST-04` | Rest between release and the next report | 12 h |
| `RULE-QUAL-05` | Rated on the aircraft type; works for the airline that flies it; not reported unavailable; status active | Pass or fail |
| `RULE-CERT-06` | Every certificate (licence, medical, recurrent training, dangerous goods) valid on the duty date | Pass or fail |
| `RULE-BASE-07` | Reserves are called out from their own base; elsewhere needs positioning (a flight there, which costs and may delay) | Pass or fail |

Admins may tighten a threshold (`rule_thresholds`, `set_rule_threshold`); never looser
than regulation, and only a super admin may relax one back. The engine uses a change
within a minute, so quote the current value from the tool, not the table above.

### Reading a verdict (`crew_tool("check_legality", ...)`)

Each rule comes back `PASS` or `FAIL` with `used`, `limit` and `headroom` where it has
numbers, and the date it applies to. Explain a failure by the rule and the margin:

> "Not legal: only 11 h 45 rest after P-2295 on 18 October, 15 minutes short of the 12 h minimum."

The engine checks every day of the whole trip, the rest before, between and after its
days, and every later 7- and 28-day window the new duty would push over (a duty today can
breach the limit on a trip three days later). A crew member reported unavailable in
another disruption fails `RULE-QUAL-05` on those dates.

## Ways to cover a seat (strategies)

Options come ranked cheapest first, with cancellation as the baseline every option beats.

| Strategy | When | Cost basis (₹, defaults) |
|---|---|---|
| `reserve_callout` | An on-call reserve of the right rank at the base, inside their call window | 18,500 pilot / 9,500 cabin, plus seniority and travel allowances |
| `day_off_callout` | Someone off that day who agrees to come in (days off are protected rest: their consent) | 24,000 pilot / 12,500 cabin, plus allowances |
| `positioning` | Cover from another base, flown in on a same-day flight | callout + 6,500 positioning; may delay the first departure (5,400 per hour of delay) |
| `command_upgrade` | A command-qualified first officer fills a captain's seat | callout costs |
| `right_seat` | A captain current in the right seat fills a first officer's seat | callout costs |
| `short_crew` | Fly one cabin crew short, only if the trip keeps its cabin minimum (1 per 50 seats) | none; chosen only after a warning |
| `cancel` | No legal cover, or the cheapest outcome | 250,000 per flight leg |

Allowances: 450 (pilot) or 150 (cabin) per year of seniority, and 180 per 10 minutes the
person needs to reach the airport. Cover only ever comes from the same airline.

A recommendation also says how much of today's reserve cover at that base it uses up:
taking the only reserve captain at a station matters more than one of twelve.

## Severity

Worked out from the analysis, never by a model; every rating comes with its reasons.

- High means a flight is at risk however it is handled. Any one of these makes it high: no
  legal crew for a seat, the best way out is to cancel or fly short, a joint plan leaves a
  seat unfilled, a delay breaks a duty limit. So do three or more of the medium factors
  together.
- Medium means it needs a real decision. The factors are cover only from outside today's
  reserves (day-off callout, positioning, upgrade), flying today or tomorrow, 300 or more
  passengers, three or more seats, and departures leaving late.
- Low means a reserve covers it with time to spare, or there is nothing to cover.

## Handling kinds (how each disruption gets resolved)

- `recrew`: seats have legal cover. A plan, then four-eyes approval.
- `decision`: no legal cover. Crew control decides with operations (delay, swap, cancel)
  and closes it with what was done.
- `internal`: nothing to re-crew (gates moved, a delay every crew member absorbs). The desk
  marks it resolved with a remark.
- `recorded`: someone out on days they fly nothing. Closed at once; they are not called out
  on those days.

## Windows

- Disruptions are reported for today up to two months ahead; nothing for the past.
- Planned leave (admins) runs from tomorrow to the end of the rostered period.
- Crew see their own trips from five days back to two months ahead.
