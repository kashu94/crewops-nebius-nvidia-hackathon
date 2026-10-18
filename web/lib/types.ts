// The shapes the MCP server returns, as the web app reads them.

export type FeedItem = {
  disruption_id: string;
  status: string;
  report_type: string | null;
  reported_utc: string | null;
  reported_by: string | null;
  details: Record<string, unknown>;
  summary: string;
  /** recrew: crew control covers seats; decision: no legal cover; internal: handled elsewhere;
   * recorded: an absence on days with nothing to cover, kept on record. */
  handling?: "recrew" | "decision" | "internal" | "recorded" | null;
  severity?: Severity | null;
  desks?: string[];
  airlines?: string[];
};

export type Suggestion = {
  by: string;
  desk: string;
  seat: string;
  crew_id: string;
  name: string | null;
  note: string;
  at: string;
};

/** Someone told about a disruption besides crew control, and what. */
export type TeamNotice = {
  team: string;
  team_title: string;
  scope: string;
  contact: string | null;
  contact_title: string | null;
  channel: "email" | "in_app";
  why: string;
  message: string;
  about: string | null;
};

export type StandMove = {
  flight_id: string;
  flight_no: string;
  from_gate: string;
  to_gate: string;
  boarding_from_utc: string;
  departs_utc: string;
  booked: number | null;
  delay_minutes: number;
  why: string;
  alternatives?: string[];
  emergency?: boolean;
};

export type NextAtGate = {
  flight_id: string;
  flight_no: string;
  gate: string;
  boarding_from_utc: string;
  buffer_minutes: number;
};

export type FlightRow = {
  flight_id: string;
  flight_no: string;
  from: string;
  to: string;
  aircraft: string;
  dep_utc: string;
  new_dep_utc: string | null;
  late_minutes: number;
  gate: string | null;
  seats: number;
  booked: number | null;
};

/** A crew member reported unavailable after the options were worked out. */
export type Unavailable = {
  disruption_id: string;
  reported_utc: string | null;
  reason: string | null;
  from_date: string;
  to_date: string;
};

export type Handling = { kind: "recrew" | "decision" | "internal" | "recorded"; text: string };

/** How serious a disruption is, worked out from its analysis (crewops.analysis.severity). */
export type Severity = { level: "high" | "medium" | "low"; reasons: string[] };

export type MyReport = {
  disruption_id: string;
  reported_by?: string | null;
  narrative?: string;
  status: string;
  outcome: string;
  report_type: string | null;
  reported_utc: string | null;
  details: Record<string, unknown>;
  handled: string | null;
  remark: string | null;
  /** Controller desk it was routed to. Empty until analysed, or if it was refused. */
  desks?: { desk_id: string; controller: string }[];
};

export type Reference = {
  crew: { crew_id: string; name: string; rank: string; base: string; airline: string | null }[];
  airlines: { code: string; name: string; color: string; aircraft: string[] }[];
  flights: {
    flight_id: string;
    flight_no: string;
    date: string;
    from: string;
    to: string;
    dep_utc: string;
    gate: string | null;
  }[];
  aircraft: string[];
  gates: string[];
  stations: string[];
  cert_types: string[];
  week: string[];
};

export type Option = {
  rank: number;
  action: string;
  strategy: string;
  crew_id: string | null;
  name: string;
  crew_rank: string | null;
  cost_inr: number;
  cost_breakdown: Record<string, number>;
  delay_hours: number;
};

export type Seat = {
  /** Set when an approved plan filled this seat after the analysis (e.g. the same seat on
   * another day). Says who flies it now, or that the trip was cancelled. */
  re_crewed?: { cancelled: boolean; holders: { crew_id: string; name: string | null }[] } | null;
  pairing_id: string;
  role: string;
  crew_out: string | null;
  status: string;
  note: string;
  legal_options: number | null;
  excluded: number | null;
  cancellation_cost_inr: number | null;
  cancellation_multiple: number | null;
  delay_hours: number;
  impact: { affected_dates?: string[]; uncovered_flights?: string[]; at_risk_flights?: string[]; passengers?: number };
  options: Option[];
  context?: SeatContext;
  live?: LiveSearch | null;
  reserves?: { base: string; on_call: number }[];
};

export type LiveOption = Option & {
  base: string | null;
  reachability_minutes: number | null;
  risk_score: number | null;
  resilience: number | null;
  positioning_flight: string | null;
  experience_years?: number | null;
};

export type LiveSearch = {
  options: LiveOption[];
  policies: Record<string, LiveOption | null>;
  strategies: LiveOption[];
  cancellation: LiveOption | null;
  short_crew?: LiveOption | null;
  legal_options: number;
  funnel: { stage: string; remaining: number; dropped: number; reason: string }[];
  excluded: { crew_id: string; name: string; stage: string; rule_ids: string[]; reasons: string[] }[];
  excluded_count: number;
};

export type SeatContext = {
  aircraft?: string;
  aircraft_type?: string | null;
  base?: string | null;
  dates?: string[];
  affected_dates?: string[];
  requirement?: string;
  flights?: {
    flight_id: string;
    flight_no: string;
    date: string;
    from: string;
    to: string;
    dep_utc: string;
    arr_utc: string;
    seats: number;
    booked?: number | null;
    state: "uncrewed" | "at_risk" | "crewed";
  }[];
  crew?: { crew_id: string; name: string | null; role: string; state: "out" | "on" }[];
};

export type Rival = {
  disruption_id: string;
  status: string;
  pairing_id: string;
  rank?: number;
  plan_id?: string;
  desks: { desk_id: string; controller: string }[];
  crew_out?: string | null;
  role?: string;
  kind: "option" | "plan";
  report_type?: string | null;
};

export type Plan = {
  status: string;
  plan_id: string;
  desk: string;
  submitted_by: string;
  total_cost_inr: number;
  assignments: string[];
  uncovered: string[];
  reason: string | null;
  picks?: {
    pairing_id: string;
    role: string;
    crew_id: string | null;
    name: string;
    replaces: string | null;
    strategy?: string;
  }[];
  submitted_utc?: string | null;
  decided_by?: string | null;
  decided_utc?: string | null;
  fingerprint?: string | null;
  approval_seal?: string | null;
};

export type Detail = {
  disruption_id: string;
  status: string;
  summary: string;
  severity?: Severity | null;
  report_type: string | null;
  reported_utc: string | null;
  reported_by?: string | null;
  details?: Record<string, unknown>;
  desks?: { desk_id: string; controller: string }[];
  yours?: boolean;
  joint_plan?: { assignments?: Record<string, unknown>[]; total_cost_inr?: number } | null;
  contention?: Record<string, Rival[]>;
  /** Other open disruptions needing this very seat (the same person out on another day). */
  same_seat?: {
    disruption_id: string;
    status: string;
    seats: string[];
    desks: { desk_id: string; controller: string }[];
  }[];
  unavailable_now?: Record<string, Unavailable>;
  suggestions?: Suggestion[];
  airlines?: { code: string; name: string; color: string }[];
  handling?: Handling | null;
  notices?: TeamNotice[];
  stands?: StandMove[];
  next_at_gate?: NextAtGate[];
  flights?: FlightRow[];
  notes?: string[];
  seats: Seat[];
  plans: Plan[];
  audit: { at: string; action: string; actor: string; plan_id?: string; reason?: string }[];
};

export type Verdict = {
  rule_id: string;
  status: string;
  detail: string;
  used: number | null;
  limit: number | null;
  headroom: number | null;
  date: string | null;
};

export type Legality = { legal: boolean; blocking: string[]; verdicts: Verdict[]; name: string };

export type QueueItem = {
  kind: string;
  disruption_id: string;
  plan_id?: string;
  summary?: string;
  desk?: string;
  submitted_by?: string;
  total_cost_inr?: number;
  assignments?: string[];
  uncovered?: string[];
  warnings?: string[];
  reason?: string | null;
  submitted_utc?: string | null;
  /** SHA-256 of exactly what was submitted (crewops.plans.fingerprint). */
  fingerprint?: string | null;
};

const s = (v: unknown) => (typeof v === "string" ? v : v == null ? "" : String(v));

/** One line a controller reads at a glance, built from the report itself. */
export function describe(item: Pick<FeedItem, "report_type" | "details">): string {
  const d = item.details ?? {};
  switch (item.report_type) {
    case "crew_unavailable": {
      const to = s(d.to_date) && s(d.to_date) !== s(d.from_date) ? ` to ${s(d.to_date)}` : "";
      return `${s(d.crew_id)} unavailable ${s(d.from_date)}${to} (${s(d.reason)})`;
    }
    case "multiple_crew_unavailable": {
      const ids = Array.isArray(d.crew_ids) ? (d.crew_ids as unknown[]).map(s) : [];
      const to = s(d.to_date) && s(d.to_date) !== s(d.from_date) ? ` to ${s(d.to_date)}` : "";
      return `${ids.length} crew unavailable ${s(d.from_date)}${to} (${s(d.reason)}): ${ids.join(", ")}`;
    }
    case "planned_leave":
      return `${s(d.crew_id)} on planned leave ${s(d.from_date)} to ${s(d.to_date)} (${s(d.reason).replace(/_/g, " ")})`;
    case "gate_closure":
      return `${s(d.gate)} closed ${s(d.from_utc).slice(11, 16)}–${s(d.to_utc).slice(11, 16)}Z (${s(d.reason)})`;
    case "certification_lapse":
      return `${s(d.crew_id)} ${s(d.cert_type).replace(/_/g, " ")} lapsed`;
    case "flight_delay":
      return `${s(d.flight_id)} delayed ${s(d.delay_minutes)} min`;
    case "aircraft_technical":
      return `${s(d.aircraft)} technical fault`;
    case "station_disruption":
      return `${s(d.station)} closed ${s(d.from_utc).slice(11, 16)}–${s(d.to_utc).slice(11, 16)}Z`;
    case "gate_change":
      return `${s(d.flight_id)} moved to gate ${s(d.new_gate)}`;
    default:
      return (item.report_type ?? "disruption").replace(/_/g, " ");
  }
}

/** One verdict per rule: the tightest margin across the pairing's days. */
export function byRule(verdicts: Verdict[]): Verdict[] {
  const out = new Map<string, Verdict>();
  for (const v of verdicts) {
    const seen = out.get(v.rule_id);
    const worse =
      !seen ||
      (v.status !== "PASS" && seen.status === "PASS") ||
      (v.headroom !== null && (seen.headroom === null || v.headroom < seen.headroom));
    if (worse) out.set(v.rule_id, v);
  }
  return [...out.values()].sort((a, b) => a.rule_id.localeCompare(b.rule_id));
}

/** The status as a desk reads it: a disruption ready but with nothing to re-crew is to be
 * resolved (or decided), not "options ready". */
export function shownStatus(status: string, handling?: string | null): string {
  if (status === "CLOSED" && handling === "recorded") return "RECORDED";
  if (status !== "OPTIONS_READY") return status;
  if (handling === "internal") return "TO_RESOLVE";
  if (handling === "decision") return "DECISION_NEEDED";
  return status;
}
