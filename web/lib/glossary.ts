// What each term on the screens means, in the engine's own definitions. Shown by the
// (i) icons and the walkthrough; kept here so every screen says the same thing.

export const GLOSSARY = {
  delay: {
    title: "Delay",
    text:
      "How late the first departure leaves if this person covers. It is 0 when they can report on time. " +
      "A crew member who must first be flown to the trip's start (positioning) and lands after report " +
      "time delays it by the difference; each delayed hour also costs money.",
  },
  resilience: {
    title: "Reserve cover kept",
    text:
      "How much of the day's reserve pool stays free after this choice, out of 100. A day-off callout uses " +
      "no reserve, so it scores 100. A reserve callout uses one of the n reserves of that rank on call at " +
      "that base that day: 100 × (n − 1) / n. Using the only reserve captain at a station scores 0.",
  },
  reach: {
    title: "Minutes away",
    text:
      "How long this crew member needs from the call to being at the airport ready to report, as held in " +
      "their crew record. It ranks otherwise equal options and tells you who can come in fastest.",
  },
  reserve_callout: {
    title: "Reserve callout",
    text: "Calling in a crew member who is on call (standby) for the day. Cheaper than a day off, and part of their duty.",
  },
  day_off_callout: {
    title: "Day-off callout",
    text: "Calling in a crew member on a day off. Costs more than a reserve, and they may say no.",
  },
  positioning: {
    title: "Positioning",
    text:
      "The crew member is based elsewhere and is first flown, as a passenger, to where the trip starts. " +
      "Adds a fare, and a delay when they land after report time.",
  },
  legal: {
    title: "Legal option",
    text:
      "Passes every legality rule for the whole trip: duty and flight-time limits, rest before and after, " +
      "aircraft rating, certificates valid on every day, on-call window and base.",
  },
  excluded: {
    title: "Excluded candidates",
    text: "Everyone of the right rank who was considered and failed a rule, with the rule and the figures.",
  },
  joint: {
    title: "Joint plan",
    text:
      "When several seats need cover at once, the cheapest set of choices that never uses one person twice. " +
      "Covering each seat on its own can pick the same reserve for two seats.",
  },
  contention: {
    title: "Competing for the same crew",
    text:
      "Another open disruption also ranks this person, or a submitted plan already uses them, on the same or " +
      "an adjacent day. Agree who takes whom before either plan is submitted.",
  },
  cancellation: {
    title: "Cancel instead",
    text:
      "What it costs not to cover the seat: every flight of the trip is cancelled, and approval releases the trip's " +
      "whole crew. Shown as the last resort; it can be chosen even when no crew member is legal.",
  },
  four_eyes: {
    title: "Four-eyes approval",
    text:
      "A controller submits the plan; a different person, an approver, approves it. Approval re-checks every " +
      "rule and changes the roster in the same step, or sends it back if anything changed.",
  },
  handling: {
    title: "How it gets resolved",
    text:
      "Re-crew: a seat has legal cover, so a plan is submitted and approved. Decision: no legal cover, so " +
      "crew control decides with operations. Handled internally: nothing to re-crew (gates moved, a delay " +
      "every crew member absorbs); the desk marks it resolved with a remark. Recorded: someone out on days " +
      "they fly nothing; kept on record so they are not called out, with no desk involved.",
  },
  remote_stand: {
    title: "Remote stand",
    text:
      "Parking away from the terminal when no gate is free: passengers are taken to the aircraft by bus, " +
      "which adds about 15 minutes to boarding.",
  },
  next_at_gate: {
    title: "Next at the same gate",
    text:
      "The departure due at a gate after a late one, within three hours. Its gate agent is told, and it is " +
      "moved to a free gate when the late flight still holds the gate.",
  },
  booked: {
    title: "Passengers booked",
    text: "Passengers booked on the flights concerned, from the booking figures. Seats are the aircraft's capacity.",
  },
  command_upgrade: {
    title: "Command-qualified first officer",
    text:
      "A first officer who has completed command upgrade training may fill a captain's seat. Offered only when fewer " +
      "than three captains of the same airline are legal, after them, most experienced first.",
  },
  right_seat: {
    title: "Captain in the right seat",
    text:
      "A captain current in the right seat may fly as first officer. Offered only when fewer than three first officers " +
      "are legal, after them. An aircraft like the A320 or ATR 72 is never flown by one pilot.",
  },
  short_crew: {
    title: "Operate one cabin crew short",
    text:
      "Regulation needs one cabin crew member for every 50 passenger seats. A trip that still meets that with one fewer " +
      "may operate short; below it, it may not, and the seat must be covered, delayed or cancelled. Anyone else on the " +
      "trip reported unavailable counts as missing too.",
  },
  airline: {
    title: "Airline",
    text:
      "Crew control works for three airlines. Each employs its own crew, so cover for a seat only ever comes from the " +
      "same airline's crew.",
  },
  pairing: {
    title: "Pairing (trip)",
    text: "A crew's trip: one or more duty days of flights on one aircraft, flown by the same crew complement.",
  },
} as const;

export type Term = keyof typeof GLOSSARY;
