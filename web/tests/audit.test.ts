import { describe, expect, it } from "vitest";

import { mergeAudit } from "@/components/disruption";

describe("audit trail", () => {
  it("shows approving and committing, one step, as one row", () => {
    const at = "2026-10-07T17:26:14Z";
    const rows = mergeAudit([
      { at: "2026-10-07T17:24:51Z", action: "SUBMITTED", actor: "rohit", plan_id: "pl-1" },
      { at, action: "APPROVED", actor: "meera", plan_id: "pl-1" },
      { at, action: "COMMITTED", actor: "meera", plan_id: "pl-1" },
    ]);
    expect(rows.map((r) => r.label)).toEqual(["Submitted", "Approved and committed"]);
  });

  it("keeps separate events apart", () => {
    const rows = mergeAudit([
      { at: "2026-10-07T17:00:00Z", action: "APPROVED", actor: "meera", plan_id: "pl-1" },
      { at: "2026-10-07T17:05:00Z", action: "COMMITTED", actor: "meera", plan_id: "pl-2" },
    ]);
    expect(rows).toHaveLength(2);
  });
});
