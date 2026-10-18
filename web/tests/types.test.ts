import { describe, expect, it } from "vitest";

import { byRule, describe as line, type Verdict } from "@/lib/types";

describe("feed lines", () => {
  it("reads each report type at a glance", () => {
    expect(
      line({ report_type: "crew_unavailable", details: { crew_id: "C-1042", from_date: "2026-10-13", to_date: "2026-10-14", reason: "sick" } }),
    ).toBe("C-1042 unavailable 2026-10-13 to 2026-10-14 (sick)");
    expect(line({ report_type: "flight_delay", details: { flight_id: "KS412-2026-10-13", delay_minutes: 90 } })).toBe(
      "KS412-2026-10-13 delayed 90 min",
    );
    expect(
      line({ report_type: "station_disruption", details: { station: "BLR", from_utc: "2026-10-15T08:00:00Z", to_utc: "2026-10-15T14:00:00Z" } }),
    ).toBe("BLR closed 08:00–14:00Z");
  });
});

describe("legality by rule", () => {
  const v = (rule_id: string, status: string, headroom: number | null): Verdict => ({
    rule_id,
    status,
    headroom,
    detail: "",
    used: null,
    limit: null,
    date: null,
  });

  it("keeps one verdict per rule: a failure, else the tightest margin", () => {
    const out = byRule([v("RULE-REST-04", "PASS", 4), v("RULE-REST-04", "PASS", 1.5), v("RULE-FDP-01", "PASS", 3), v("RULE-FDP-01", "FAIL", 5)]);
    expect(out.map((x) => [x.rule_id, x.status, x.headroom])).toEqual([
      ["RULE-FDP-01", "FAIL", 5],
      ["RULE-REST-04", "PASS", 1.5],
    ]);
  });
});
