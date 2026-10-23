import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { chosenFor, CommittedPlan } from "@/components/disruption";
import { SeatOptions } from "@/components/solutions";
import type { Detail, Plan, Seat } from "@/lib/types";

const seat: Seat = {
  pairing_id: "P-2291",
  role: "Captain",
  crew_out: "C-1042",
  status: "options",
  note: "",
  legal_options: 1,
  excluded: 0,
  cancellation_cost_inr: 900000,
  cancellation_multiple: 6,
  delay_hours: 0,
  impact: {},
  options: [
    {
      rank: 1,
      action: "assign",
      strategy: "reserve_callout",
      crew_id: "C-2001",
      name: "Asha Rao",
      crew_rank: "Captain",
      cost_inr: 150000,
      cost_breakdown: {},
      delay_hours: 0,
    },
  ],
};

const cancelled: Plan = {
  status: "COMMITTED",
  plan_id: "PLAN-1",
  desk: "desk-1",
  submitted_by: "ananya",
  total_cost_inr: 900000,
  assignments: [],
  uncovered: [],
  reason: null,
  picks: [
    {
      pairing_id: "P-2291",
      role: "Captain",
      crew_id: null,
      name: "Cancel 3 flight(s)",
      replaces: "C-1042",
      strategy: "cancel",
    },
  ],
  decided_by: "meera",
};

const detail = {
  disruption_id: "D-1",
  status: "COMMITTED",
  summary: "",
  seats: [seat],
  plans: [cancelled],
} as unknown as Detail;

const text = (html: string) => html.replace(/<[^>]+>/g, "");

describe("a committed plan that cancelled the seat", () => {
  it("does not say the cancellation flies the trip", () => {
    const shown = text(renderToStaticMarkup(createElement(CommittedPlan, { detail })));
    expect(shown).toContain("Cancel 3 flight(s) for Captain on P-2291 instead of C-1042");
    expect(shown).not.toContain("flies");
  });

  it("shows no blank 'Chosen' line among the options", () => {
    const chosen = chosenFor(detail, seat);
    expect(chosen).toBe("");
    const shown = text(renderToStaticMarkup(createElement(SeatOptions, { seat, chosen })));
    expect(shown).not.toContain("Chosen:");
  });

  it("still names a crew member the plan picked from beyond the options shown", () => {
    const shown = text(renderToStaticMarkup(createElement(SeatOptions, { seat, chosen: "C-3003" })));
    expect(shown).toContain("Chosen: C-3003, from beyond the top options shown here.");
  });
});
