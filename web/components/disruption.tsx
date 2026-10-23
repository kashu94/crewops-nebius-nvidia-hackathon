"use client";

import {
  Building2,
  CalendarDays,
  CheckCheck,
  CheckCircle2,
  Link2,
  Lock,
  MapPin,
  Plane,
  Send,
  ShieldCheck,
  Sparkles,
  Swords,
  Users,
  X,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { tool } from "@/lib/client";
import type { Detail, Plan, Seat } from "@/lib/types";

import { Info } from "./info";
import { useMe } from "./me";
import { seatKey } from "./workspace";
import { FourEyes } from "./four-eyes";
import { Button, clock, cx, day, Mono, Notice, rupees, Status } from "./ui";

const STRATEGY: Record<string, string> = {
  reserve_callout: "reserve callout",
  day_off_callout: "day-off callout",
  positioning: "positioning",
  cancel: "cancellation",
  command_upgrade: "command-qualified first officer",
  right_seat: "captain in the right seat",
  short_crew: "operate one short",
};

/** Other open disruptions needing this very seat: the same person out on another day of
 * the same trip. One cover fills them all, so they get planned together instead of competing. */
function SameSeat({ detail }: { detail: Detail }) {
  const same = detail.same_seat ?? [];
  if (same.length === 0) return null;
  return (
    <p className="flex items-start gap-2 rounded-md border border-[#cfe0ef] bg-accent-soft/70 px-3 py-2 text-sm text-[#173f63]">
      <Link2 size={15} className="mt-0.5 shrink-0" />
      <span>
        <b>The same seat is also reported in </b>
        {same.map((s, i) => (
          <span key={s.disruption_id}>
            {i > 0 && ", "}
            <Link href={`/console/${encodeURIComponent(s.disruption_id)}`} className="font-mono underline">
              {s.disruption_id}
            </Link>{" "}
            ({s.seats.join(", ")}
            {s.desks.length > 0 && ` · ${s.desks.map((d) => d.controller).join(", ")}`})
          </span>
        ))}
        . It is one person out on more days of one trip, so whoever covers flies the whole trip: one cover fills both.
        Plan it once; once that plan is approved, close the other saying it is covered.
      </span>
    </p>
  );
}

/** Whose disruption this is, said plainly for each role, and any other disruption needing
 * the very same seat. */
export function DeskBanner({ detail }: { detail: Detail }) {
  return (
    <>
      <OwnerBanner detail={detail} />
      <SameSeat detail={detail} />
    </>
  );
}

function OwnerBanner({ detail }: { detail: Detail }) {
  const me = useMe();
  const desks = detail.desks ?? [];
  if (desks.length === 0) return null;
  const names = desks.map((d) => `${d.desk_id} (${d.controller})`).join(" and ");
  if (detail.yours) {
    return (
      <p className="flex items-center gap-2 rounded-md border border-[#cfe0ef] bg-accent-soft px-3 py-2 text-sm text-[#173f63]">
        <Building2 size={15} /> On your desk{desks.length > 1 ? `, shared with ${names}` : ""}.
      </p>
    );
  }
  if (me.roles.includes("controller")) {
    return (
      <p className="flex items-start gap-2 rounded-md border border-amber-200 bg-pending-soft px-3 py-2 text-sm text-amber-900">
        <Swords size={15} className="mt-0.5 shrink-0" />
        <span>
          This is <b>{names}</b>&apos;s disruption. You can read and analyse it; planning it is theirs.
          {(detail.seats?.length ?? 0) > 0 &&
            " You may suggest cover for a seat. Where it wants the same crew as yours, the options below say so: agree who takes whom before either plan is submitted."}
        </span>
      </p>
    );
  }
  return (
    <p className="flex items-center gap-2 rounded-md border border-line bg-sunken px-3 py-2 text-sm text-foreground">
      <Building2 size={15} /> Desk: {names}.
    </p>
  );
}

/** The figures a controller reads first: for a re-crew, the uncrewed flights; otherwise
 * the flights it delays or re-gates. Passengers are the seed's bookings. */
export function Coverage({ detail }: { detail: Detail }) {
  let stats: { label: string; value: number; icon: typeof Plane }[];
  if (detail.seats.length > 0) {
    const flights = detail.seats.flatMap((s) => s.context?.flights ?? []);
    const uncrewed = new Map(flights.filter((f) => f.state === "uncrewed").map((f) => [f.flight_id, f]));
    const dates = new Set(detail.seats.flatMap((s) => s.impact?.affected_dates ?? []));
    const booked = [...uncrewed.values()].reduce((n, f) => n + (f.booked ?? 0), 0);
    const stations = new Set([...uncrewed.values()].flatMap((f) => [f.from, f.to]));
    stats = [
      { label: "Duty dates", value: dates.size, icon: CalendarDays },
      { label: "Flights uncrewed", value: uncrewed.size, icon: Plane },
      { label: "Passengers booked", value: booked, icon: Users },
      { label: "Stations", value: stations.size, icon: MapPin },
    ];
  } else {
    const flights = detail.flights ?? [];
    const stands = detail.stands ?? [];
    stats = [
      { label: "Flights affected", value: flights.length, icon: Plane },
      { label: "Leaving late", value: flights.filter((f) => f.late_minutes > 0).length, icon: CalendarDays },
      { label: "Gate moves", value: stands.length, icon: MapPin },
      { label: "Passengers booked", value: flights.reduce((n, f) => n + (f.booked ?? 0), 0), icon: Users },
    ];
  }
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" title="From the roster, the gate plan and the bookings">
      {stats.map(({ label, value, icon: Icon }) => (
        <div key={label} className="rounded-lg border border-line bg-surface p-3">
          <span className="flex items-center gap-1.5 text-[11px] font-semibold text-muted">
            <Icon size={12} /> {label}
          </span>
          <span className="mt-1 block font-mono text-2xl font-semibold tabular-nums">
            {value.toLocaleString("en-IN")}
          </span>
        </div>
      ))}
    </div>
  );
}

/** The engine's recommendation for a seat, in one readable card. */
export function Recommendation({ seat }: { seat: Seat }) {
  const [best, next] = seat.options;
  if (!best || seat.re_crewed) return null;
  return (
    <div className="relative overflow-hidden rounded-lg border border-[#cfe0ef] bg-surface p-4">
      <span className="flex items-center gap-1.5 text-[11px] font-semibold text-[#173f63]">
        <Sparkles size={13} /> Recommended for <Mono>{seat.pairing_id}</Mono> · {seat.role}
      </span>
      <div className="mt-2 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xl font-semibold">
            {best.name} <Mono className="text-base text-[#173f63]">{best.crew_id}</Mono>
          </p>
          <p className="text-sm text-muted">
            {STRATEGY[best.strategy] ?? best.strategy}
            {seat.crew_out && (
              <>
                {" "}
                replacing <Mono>{seat.crew_out}</Mono>
              </>
            )}
            {best.delay_hours > 0 ? `, first departure ~${best.delay_hours}h late` : ""}
          </p>
        </div>
        <div className="text-right">
          <p className="font-mono text-2xl font-semibold">{rupees(best.cost_inr)}</p>
          <p className="text-xs text-muted">
            {next ? `next best ${rupees(next.cost_inr)}` : "the only legal option"}
            {seat.cancellation_multiple ? ` · cancelling costs ${seat.cancellation_multiple}×` : ""}
          </p>
        </div>
      </div>
    </div>
  );
}

/** What the trip needs, its flights and its crew. */
export function TripFacts({ seat }: { seat: Seat }) {
  const c = seat.context;
  if (!c) return null;
  return (
    <div className="flex flex-col gap-3 text-sm">
      {c.requirement && <p className="rounded-lg bg-sunken px-3 py-2 text-xs text-foreground">{c.requirement}</p>}
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
        <dt className="text-muted">Aircraft</dt>
        <dd>
          <Mono>{c.aircraft}</Mono> · {c.aircraft_type}
        </dd>
        {c.base && (
          <>
            <dt className="text-muted">Base</dt>
            <dd>
              <Mono>{c.base}</Mono>
            </dd>
          </>
        )}
        <dt className="text-muted">Duty dates</dt>
        <dd>{(c.affected_dates ?? []).map(day).join(", ")}</dd>
      </dl>
      <ol className="flex flex-col gap-1">
        {c.flights?.map((f) => (
          <li key={f.flight_id} className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs hover:bg-sunken">
            <Mono className="w-14 font-semibold">{f.flight_no}</Mono>
            <Mono className="text-muted">
              {f.from}→{f.to}
            </Mono>
            <Mono className="text-muted">
              {day(f.date)} {clock(f.dep_utc)}
            </Mono>
            <span
              className={cx(
                "ml-auto rounded-full px-2 py-0.5 text-[11px] font-semibold",
                f.state === "uncrewed"
                  ? "bg-critical-soft text-red-800"
                  : f.state === "at_risk"
                    ? "bg-pending-soft text-amber-800"
                    : "bg-ok-soft text-emerald-800",
              )}
            >
              {f.state.replace("_", " ")}
            </span>
          </li>
        ))}
      </ol>
      <ul className="flex flex-wrap gap-1.5">
        {c.crew?.map((m) => (
          <li
            key={`${m.crew_id}-${m.role}`}
            className={cx(
              "rounded-lg px-2 py-1 text-[11px]",
              m.state === "out" ? "bg-critical-soft text-red-900" : "bg-sunken text-foreground",
            )}
            title={m.state === "out" ? "Unavailable" : "Rostered"}
          >
            <b>{m.role}</b> {m.name} <Mono>{m.crew_id}</Mono>
            {m.state === "out" && " · out"}
          </li>
        ))}
      </ul>
    </div>
  );
}

type AuditRow = Detail["audit"][number];

/** Approve and commit happen in one step but are logged as two events; show them as one row. */
export function mergeAudit(audit: AuditRow[]): (AuditRow & { label: string })[] {
  const out: (AuditRow & { label: string })[] = [];
  for (const a of audit) {
    const prev = out[out.length - 1];
    if (a.action === "COMMITTED" && prev?.action === "APPROVED" && prev.plan_id === a.plan_id && prev.at === a.at) {
      prev.label = "Approved and committed";
      continue;
    }
    out.push({
      ...a,
      label: a.action
        .replace(/_/g, " ")
        .toLowerCase()
        .replace(/^\w/, (c) => c.toUpperCase()),
    });
  }
  return out;
}

export function AuditTrail({ audit }: { audit: AuditRow[] }) {
  return (
    <ol className="relative flex flex-col gap-3">
      <span aria-hidden className="absolute bottom-1 left-[5px] top-1 w-px bg-line" />
      {mergeAudit(audit).map((a, i) => (
        <li key={i} className="relative flex gap-3 text-sm">
          <span className="relative z-10 mt-1.5 h-[11px] w-[11px] shrink-0 rounded-full border-2 border-white bg-accent ring-1 ring-[#cfe0ef]" />
          <span className="min-w-0">
            <span className="block font-medium">{a.label}</span>
            <span className="block text-xs text-muted">
              {a.actor} ·{" "}
              <Mono>
                {day(a.at)} {clock(a.at)}
              </Mono>
              {a.plan_id && (
                <>
                  {" "}
                  · <Mono>{a.plan_id}</Mono>
                </>
              )}
            </span>
            {a.reason && <span className="block text-xs text-muted">“{a.reason}”</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}

export function PlanList({ plans }: { plans: Plan[] }) {
  if (plans.length === 0) return <p className="text-sm text-muted">No plan yet.</p>;
  return (
    <ul className="flex flex-col gap-3">
      {plans.map((p) => (
        <li key={p.plan_id} className="rounded-md border border-line p-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Mono className="font-semibold">{p.plan_id}</Mono>
            <Status value={p.status} />
            <span className="ml-auto font-mono text-sm">{rupees(p.total_cost_inr)}</span>
          </div>
          <p className="mt-1 text-xs text-muted">
            {p.desk} · submitted by {p.submitted_by}
          </p>
          <FourEyes
            className="mt-2"
            fingerprint={p.fingerprint}
            seal={p.approval_seal}
            submittedBy={p.submitted_by}
            submittedAt={p.submitted_utc}
            approvedBy={p.approval_seal ? p.decided_by : null}
            approvedAt={p.approval_seal ? p.decided_utc : null}
          />
          <ul className="mt-2 flex flex-col gap-1 text-xs">
            {p.assignments.map((a) => (
              <li key={a} className="rounded bg-sunken px-2 py-1 font-mono">
                {a}
              </li>
            ))}
            {p.uncovered.map((u) => (
              <li key={u} className="rounded bg-critical-soft px-2 py-1 text-red-900">
                Uncovered: {u}
              </li>
            ))}
          </ul>
          {p.reason && <p className="mt-1 text-xs text-muted">Reason: {p.reason}</p>}
        </li>
      ))}
    </ul>
  );
}

type Message = { tone: "ok" | "error"; text: string } | null;

/** Who plans this disruption, said by name. */
function owner(detail: Detail): string {
  const desks = detail.desks ?? [];
  return desks.length ? desks.map((d) => `${d.controller} (${d.desk_id})`).join(" or ") : "its desk's controller";
}

/** For a committed disruption, the crew id its plan put in this seat; null otherwise. */
export function chosenFor(detail: Detail, seat: Seat): string | null {
  const plan = detail.plans.find((p) => p.status === "COMMITTED");
  if (!plan) return null;
  const pick = (plan.picks ?? []).find(
    (p) => p.pairing_id === seat.pairing_id && p.role === seat.role && (!seat.crew_out || p.replaces === seat.crew_out),
  );
  return pick?.crew_id ?? "";
}

/** The plan that was approved and committed: who replaced whom, decided by whom, when. */
export function CommittedPlan({ detail }: { detail: Detail }) {
  const plan = detail.plans.find((p) => p.status === "COMMITTED");
  if (!plan) return null;
  return (
    <section className="rounded-lg border border-emerald-200 bg-surface p-4">
      <p className="flex items-center gap-1.5 text-[11px] font-semibold text-emerald-800">
        <ShieldCheck size={13} /> Committed plan <Mono className="font-normal normal-case">{plan.plan_id}</Mono>
      </p>
      <ul className="mt-2 flex flex-col gap-1 text-sm">
        {(plan.picks ?? []).map((p) => (
          <li key={`${p.pairing_id}-${p.role}-${p.crew_id}`}>
            {p.crew_id ? (
              <>
                <b>{p.name}</b> <Mono className="text-[#173f63]">{p.crew_id}</Mono> flies {p.role} on{" "}
              </>
            ) : (
              // cancelled, or operated one short: nobody flies the seat
              <>
                <b>{p.name}</b> for {p.role} on{" "}
              </>
            )}
            <Mono>{p.pairing_id}</Mono>
            {p.replaces && (
              <>
                {" "}
                instead of <Mono>{p.replaces}</Mono>
              </>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-emerald-900/80">
        {rupees(plan.total_cost_inr)} · submitted by {plan.submitted_by}
        {plan.decided_by && (
          <>
            {" "}
            · approved by <b>{plan.decided_by}</b>
            {plan.decided_utc && (
              <>
                {" "}
                on {day(plan.decided_utc)} <Mono>{clock(plan.decided_utc)}</Mono>
              </>
            )}
          </>
        )}
      </p>
    </section>
  );
}

/** Only the actions the signed-in role may take on this disruption, as it stands. */
export type Choice = { seat: string; crewId: string; name: string; overrides: boolean; stale?: boolean };

export function Actions({
  detail,
  onChanged,
  choices = [],
}: {
  detail: Detail;
  onChanged: () => void;
  choices?: Choice[];
}) {
  const me = useMe();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const pending = detail.plans.find((p) => p.status === "SUBMITTED");
  const isController = me.roles.includes("controller");
  const isApprover = me.roles.includes("approver");
  const canPlan = isController && detail.yours && detail.status === "OPTIONS_READY";
  const canDecide = isApprover && pending && pending.submitted_by !== me.user_id;
  const canWithdraw = isController && pending && pending.submitted_by === me.user_id;
  // Uncovered seats can still go in a plan if they're acknowledged. If nothing can be
  // covered the server only allows closing it. A seat with no legal crew counts as
  // handled once someone picks cancel or fly-short for it.
  const decided = new Set(choices.filter((c) => c.crewId === "CANCEL" || c.crewId === "SHORT").map((c) => c.seat));
  const open = (s: Seat) => (s.status !== "options" || Boolean(s.re_crewed)) && !decided.has(seatKey(s));
  const someUncovered = detail.seats.some(open);
  const overriding = choices.some((c) => c.overrides);
  const cancelling = choices.some((c) => c.crewId === "CANCEL");
  const short = choices.some((c) => c.crewId === "SHORT");
  // Only changed seats go: the server keeps the joint plan for the rest.
  const picked = Object.fromEntries(choices.filter((c) => c.overrides).map((c) => [c.seat, c.crewId]));
  const stale = choices.some((c) => c.stale);
  // nothing coverable: close it, or resolve with a remark if it was handled outside crew control
  const noneCoverable = detail.seats.every(open);
  const internal = detail.handling?.kind === "internal";

  async function act(name: string, args: Record<string, unknown>, done: string) {
    setBusy(true);
    setMessage(null);
    try {
      const out = await tool<{ status: string; reasons?: string[]; contention?: string[] }>(name, {
        disruption_id: detail.disruption_id,
        ...args,
      });
      const extra = [...(out.reasons ?? []), ...(out.contention ?? [])].join(" · ");
      setMessage({
        tone: out.status === "STALE" ? "error" : "ok",
        text: `${done}: ${out.status}${extra ? ` (${extra})` : ""}`,
      });
      setReason("");
      setNote("");
      onChanged();
    } catch (err) {
      setMessage({ tone: "error", text: err instanceof Error ? err.message : "Failed." });
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  if (!canPlan && !canDecide && !canWithdraw) {
    // After an action the reload can leave nothing for this role to do (an approval found
    // stale, a plan sent back), so the outcome is kept above the status line.
    return (
      <div className="flex flex-col gap-3">
        {message && <Notice tone={message.tone}>{message.text}</Notice>}
        <p className="flex items-center gap-2 text-xs text-muted">
          <Lock size={13} />
          {detail.status === "COMMITTED"
            ? "Committed: the roster has been changed."
            : detail.status === "CLOSED"
              ? "Resolved: see the remark on the audit trail."
              : pending
                ? pending.submitted_by === me.user_id
                  ? "Waiting for a different approver."
                  : "A plan is waiting for an approver."
                : isController && !detail.yours
                  ? `Only ${owner(detail)} can plan this one; you can read it.`
                  : "Nothing for your role to do here."}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      {canPlan && (
        <>
          {choices.length > 0 && (
            <ul className="flex flex-col gap-1 rounded-lg bg-sunken p-2 text-xs">
              {choices.map((c) => (
                <li key={c.seat} className="flex flex-wrap items-center gap-1.5">
                  <span className="text-muted">Submitting</span> <b>{c.name}</b>
                  {c.crewId !== "CANCEL" && c.crewId !== "SHORT" && <Mono>{c.crewId}</Mono>}
                  <span className="text-muted">for {c.seat.split("/").slice(0, 2).join(" ")}</span>
                  {c.stale ? (
                    <span className="rounded-full bg-critical-soft px-2 py-0.5 font-semibold text-red-800">
                      no longer a legal option: choose again
                    </span>
                  ) : (
                    c.overrides && (
                      <span className="rounded-full bg-pending-soft px-2 py-0.5 font-semibold text-amber-800">
                        overrides the recommendation
                      </span>
                    )
                  )}
                </li>
              ))}
            </ul>
          )}
          {cancelling && (
            <Notice tone="error">
              This plan cancels flights. Passengers are rebooked and operations and customer care are told. Say why in
              the note: the approver decides.
            </Notice>
          )}
          {short && !cancelling && (
            <Notice tone="info">
              This plan operates a trip one cabin crew short, at the legal minimum. Say why in the note.
            </Notice>
          )}
          {!noneCoverable && (
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && e.preventDefault()}
              maxLength={500}
              placeholder={
                overriding ? "Why this option over the recommended one? (required)" : "Note for the approver (optional)"
              }
              className="rounded-lg border border-line px-3 py-2 text-sm outline-none focus:border-accent"
            />
          )}
          {!noneCoverable && (
            <Button
              type="button"
              tone={cancelling ? "danger" : "accent"}
              disabled={busy || stale || (overriding && !note.trim())}
              onClick={() => act("submit_plan", { note, accept_uncovered: false, choices: picked }, "Submitted")}
            >
              <Send size={14} />{" "}
              {cancelling
                ? "Submit the cancellation for approval"
                : overriding
                  ? "Submit my choice for approval"
                  : "Submit the recommended plan"}
            </Button>
          )}
          {someUncovered && !noneCoverable && (
            <Button
              type="button"
              tone="secondary"
              disabled={busy || stale || (overriding && !note.trim())}
              onClick={() => act("submit_plan", { note, accept_uncovered: true, choices: picked }, "Submitted")}
            >
              Submit, accepting uncovered seats
            </Button>
          )}
          {noneCoverable && (
            <div className={cx("flex flex-col gap-2", detail.seats.length > 0 && "mt-2 border-t border-line pt-3")}>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                maxLength={300}
                rows={2}
                placeholder={
                  internal
                    ? "Remark before resolving, e.g. gates re-allocated, screens updated, captains told"
                    : "What was done instead, e.g. DX404 cancelled and passengers rebooked"
                }
                className="rounded-lg border border-line px-3 py-2 text-sm outline-none focus:border-accent"
              />
              <Button
                type="button"
                tone={internal ? "accent" : "secondary"}
                disabled={busy || !reason.trim()}
                onClick={() => act("close_disruption", { reason }, "Sent to an approver for sign-off")}
              >
                {internal ? <CheckCircle2 size={14} /> : null}
                {internal ? "Mark resolved" : "Close the disruption"}
              </Button>
              <p className="text-[11px] text-muted">
                {internal
                  ? "No crew change is needed. An approver signs it off; the remark goes on the audit trail and to whoever reported it."
                  : "When it has been dealt with another way, for example the flights were cancelled. An approver signs it off."}
              </p>
            </div>
          )}
        </>
      )}
      {canDecide && pending && (
        <>
          <p className="text-xs text-muted">
            Plan <Mono>{pending.plan_id}</Mono> by {pending.submitted_by}, {rupees(pending.total_cost_inr)}. Approving
            re-checks every rule and changes the roster in the same step.
          </p>
          {confirming ? (
            <div className="flex gap-2">
              <Button
                type="button"
                tone="accent"
                disabled={busy}
                onClick={() => act("approve_plan", { plan_id: pending.plan_id }, "Approved")}
              >
                <CheckCheck size={14} /> Confirm and commit
              </Button>
              <Button type="button" tone="ghost" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </div>
          ) : (
            <Button type="button" tone="accent" disabled={busy} onClick={() => setConfirming(true)}>
              <ShieldCheck size={14} /> Approve and commit…
            </Button>
          )}
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && e.preventDefault()}
            maxLength={300}
            placeholder="Reason to send it back"
            className="rounded-lg border border-line px-3 py-2 text-sm outline-none focus:border-critical"
          />
          <Button
            type="button"
            tone="danger"
            disabled={busy || !reason.trim()}
            onClick={() => act("reject_plan", { plan_id: pending.plan_id, reason }, "Rejected")}
          >
            <X size={14} /> Send back
          </Button>
        </>
      )}
      {canWithdraw && pending && (
        <Button
          type="button"
          tone="secondary"
          disabled={busy}
          onClick={() => act("withdraw_plan", { plan_id: pending.plan_id, reason }, "Withdrawn")}
        >
          Withdraw my plan
        </Button>
      )}
    </div>
  );
}

export function JointPlan({ detail }: { detail: Detail }) {
  const joint = detail.joint_plan;
  // Only meaningful when several seats are covered together.
  if (detail.seats.length < 2 || !joint?.assignments?.length) return null;
  return (
    <div className="rounded-md border border-[#cfe0ef] bg-accent-soft/50 p-3 text-xs">
      <p className="mb-1 flex items-center gap-1 font-semibold text-[#173f63]">
        Joint plan for every seat together <Info term="joint" />
      </p>
      <ul className="flex flex-col gap-1">
        {joint.assignments.map((a, i) => (
          <li key={i} className="font-mono text-[#173f63]">
            {String(a.pairing_id)} {String(a.role)}: {String(a.name ?? a.crew_id)} ({String(a.crew_id)})
          </li>
        ))}
      </ul>
      {joint.total_cost_inr !== undefined && (
        <p className="mt-1 text-[#173f63]">Total {rupees(joint.total_cost_inr)}</p>
      )}
    </div>
  );
}
