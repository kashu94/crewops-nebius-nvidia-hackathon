"use client";

import { BellRing, CalendarRange, Gauge, History, Mail, Save, ScrollText, ShieldAlert, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useMe } from "@/components/me";
import { Button, clock, cx, day, Empty, Field, Mono, Notice, Panel, Spinner, Status } from "@/components/ui";
import { tool } from "@/lib/client";
import { shownStatus } from "@/lib/types";

type Leave = {
  disruption_id: string;
  status: string;
  outcome: string;
  handling?: string | null;
  details: { crew_id?: string; from_date?: string; to_date?: string; reason?: string };
};
type LeaveReference = {
  crew: { crew_id: string; name: string; rank: string }[];
  today: string;
  leave_until: string | null;
};
const LEAVE_REASONS = [
  ["annual_leave", "Annual leave"],
  ["medical", "Medical (planned)"],
  ["training", "Training course"],
  ["personal", "Personal"],
] as const;

function plusDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

type Threshold = {
  rule_id: string;
  param: string;
  label: string;
  unit: string;
  value: number;
  min: number;
  max: number;
  step: number;
  stricter: "lower" | "higher";
  text: string | null;
};

type Event = {
  at: string;
  action: string;
  actor: string;
  disruption_id?: string;
  plan_id?: string;
  rule_id?: string;
  param?: string;
  from?: number;
  to?: number;
  role?: string;
  reason?: string;
};

// Must match crewops.domain.contacts on the server.
const ALERT_TEAMS = [
  ["gate_control", "Gate and stand control"],
  ["station_ops", "Station duty manager"],
  ["flight_info", "Flight information (screens, website, app)"],
  ["ops_monitoring", "Operations monitoring (tells the captain)"],
  ["maintenance_control", "Maintenance control"],
  ["crew_records", "Training and crew records"],
  ["customer_care", "Customer care (passengers)"],
] as const;

type Address = {
  subscription_id: string;
  email: string;
  role: string;
  added_by: string;
  added_utc: string;
  status: string;
};

type Message = { tone: "ok" | "error"; text: string } | null;

function ThresholdRow({
  t,
  mayRelax,
  onSaved,
}: {
  t: Threshold;
  /** super admin: may relax a threshold, not just tighten it */
  mayRelax: boolean;
  onSaved: (m: Message) => void;
}) {
  const [value, setValue] = useState(t.value);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const changed = value !== t.value;
  // relaxing = higher limit or shorter minimum rest
  const relaxing = t.stricter === "lower" ? value > t.value : value < t.value;
  const blocked = relaxing && !mayRelax;
  const regulatory = t.stricter === "lower" ? t.max : t.min;

  async function save() {
    setBusy(true);
    try {
      await tool("set_rule_threshold", { rule_id: t.rule_id, param: t.param, value, expected: t.value, reason });
      onSaved({
        tone: "ok",
        text: `${t.label} is now ${value}${t.unit}. New answers and every approval use it within a minute; existing analyses update when re-analysed.`,
      });
      setReason("");
    } catch (err) {
      onSaved({ tone: "error", text: err instanceof Error ? err.message : "Failed." });
      setValue(t.value);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-xl border border-line p-4 transition hover:border-sky-200">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <p className="text-sm font-semibold">{t.label}</p>
          <Mono className="text-[11px] text-muted">{t.rule_id}</Mono>
        </div>
        <span
          className={cx("font-mono text-2xl font-semibold tabular-nums", changed ? "text-accent" : "text-slate-900")}
        >
          {value}
          <span className="text-sm text-muted">{t.unit}</span>
        </span>
      </div>
      <div className="relative mt-3">
        <input
          type="range"
          min={t.min}
          max={t.max}
          step={t.step}
          value={value}
          onChange={(e) => setValue(Number(e.target.value))}
          aria-label={t.label}
          className="w-full"
        />
        <div className="mt-1 flex justify-between text-[11px] text-muted">
          <span>
            {t.min}
            {t.unit}
            {t.stricter === "lower" && " · strictest"}
            {t.stricter === "higher" && " · regulatory"}
          </span>
          <span>
            {t.max}
            {t.unit}
            {t.stricter === "lower" && " · regulatory"}
            {t.stricter === "higher" && " · strictest"}
          </span>
        </div>
      </div>
      {t.text && <p className="mt-2 text-xs text-muted">{t.text}</p>}
      {changed && (
        <div className="animate-rise mt-3 flex flex-col gap-2 rounded-lg bg-accent-soft/60 p-3 sm:flex-row">
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={300}
            placeholder={`Why ${t.label.toLowerCase()} changes from ${t.value}${t.unit}`}
            className="min-w-0 flex-1 rounded-lg border border-line bg-white px-3 py-2 text-sm outline-none focus:border-accent"
          />
          <Button tone="accent" disabled={busy || !reason.trim() || blocked} onClick={save}>
            {busy ? <Spinner /> : <Save size={14} />} Save
          </Button>
          <Button tone="ghost" onClick={() => setValue(t.value)}>
            Undo
          </Button>
        </div>
      )}
      {blocked && (
        <p className="mt-2 text-[11px] text-amber-800">
          This relaxes the rule. Only a super admin may relax a threshold; an admin may only tighten it.
        </p>
      )}
      {!blocked && relaxing && (
        <p className="mt-2 text-[11px] text-muted">Relaxes the rule: recorded as a super admin&apos;s change.</p>
      )}
      {value === regulatory && t.value !== regulatory && (
        <p className="mt-2 text-[11px] text-muted">Restores the regulatory value.</p>
      )}
    </li>
  );
}

function describeEvent(e: Event): string {
  if (e.action === "RULE_CHANGED") return `${e.rule_id} ${e.param?.replace(/_/g, " ")} ${e.from} → ${e.to}`;
  if (e.action === "BLOCKED_PROMPT") return `Security: question refused (${e.reason ?? "override attempt"})`;
  if (e.action.startsWith("ALERT"))
    return `${e.action === "ALERT_ADDED" ? "Alert address added" : "Alert address removed"} (${e.role})`;
  return `${e.action.replace(/_/g, " ").toLowerCase()} ${e.disruption_id ?? ""}${e.plan_id ? ` · ${e.plan_id}` : ""}`;
}

export default function Admin() {
  const me = useMe();
  const isAdmin = me.roles.some((r) => r === "admin" || r === "super_admin");
  const [thresholds, setThresholds] = useState<Threshold[] | null>(null);
  const [events, setEvents] = useState<Event[] | null>(null);
  const [addresses, setAddresses] = useState<Address[] | null>(null);
  const [leave, setLeave] = useState<Leave[] | null>(null);
  const [leaveRef, setLeaveRef] = useState<LeaveReference | null>(null);
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    const fail = (err: Error) => setMessage({ tone: "error", text: err.message });
    tool<Threshold[]>("rule_thresholds").then(setThresholds, fail);
    tool<Event[]>("activity_log", { limit: 40 }).then(setEvents, fail);
    tool<Address[]>("alert_addresses").then(setAddresses, (err: Error) => {
      setAddresses([]);
      fail(err);
    });
    tool<Leave[]>("my_reports", { scope: "leave" }).then(setLeave, () => setLeave([]));
    tool<LeaveReference>("report_reference").then(setLeaveRef, () => undefined);
  }, []);

  useEffect(() => {
    if (isAdmin) load();
  }, [isAdmin, load]);

  if (!isAdmin) return <Notice>Only an admin manages rule thresholds and alerts.</Notice>;

  async function run(call: () => Promise<unknown>, done: string, then?: () => void) {
    setBusy(true);
    setMessage(null);
    try {
      await call();
      setMessage({ tone: "ok", text: done });
      then?.();
      load();
    } catch (err) {
      setMessage({ tone: "error", text: err instanceof Error ? err.message : "Failed." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Administration</h1>
        <p className="text-sm text-muted">
          Rule thresholds, system activity, alert addresses and planned leave. Plan decisions stay with controllers and
          approvers.
        </p>
      </div>
      {message && <Notice tone={message.tone}>{message.text}</Notice>}

      <div className="grid gap-4 xl:grid-cols-[1.2fr_1fr]">
        <Panel
          title="Rule thresholds"
          icon={<Gauge size={14} className="text-accent" />}
          action={
            <span className="flex items-center gap-1 text-[11px] text-muted">
              <ShieldAlert size={12} /> Admins tighten; only a super admin relaxes. Never looser than regulation
            </span>
          }
        >
          {!thresholds ? (
            <Spinner />
          ) : (
            <ul className="grid gap-3 md:grid-cols-2">
              {thresholds.map((t) => (
                <ThresholdRow
                  key={`${t.rule_id}-${t.param}-${t.value}`}
                  t={t}
                  mayRelax={me.roles.includes("super_admin")}
                  onSaved={(m) => {
                    setMessage(m);
                    load();
                  }}
                />
              ))}
            </ul>
          )}
        </Panel>

        <Panel
          title="System activity"
          icon={<History size={14} className="text-accent" />}
          className="xl:max-h-[640px]"
          bodyClassName="overflow-y-auto p-2"
        >
          {!events ? (
            <div className="p-2">
              <Spinner />
            </div>
          ) : events.length === 0 ? (
            <Empty icon={<ScrollText size={22} />}>No activity yet.</Empty>
          ) : (
            <ol className="flex flex-col">
              {events.map((e, i) => (
                <li
                  key={`${e.at}-${i}`}
                  className={cx(
                    "flex gap-3 rounded-lg px-2 py-2 text-sm",
                    e.action === "BLOCKED_PROMPT" ? "bg-red-50 ring-1 ring-inset ring-red-200" : "hover:bg-sunken",
                  )}
                >
                  <Mono className="w-[86px] shrink-0 text-[11px] text-muted">
                    {day(e.at)} {clock(e.at)}
                  </Mono>
                  <span className="min-w-0 flex-1">
                    <span
                      className={cx(
                        "flex items-center gap-1.5 truncate",
                        e.action === "BLOCKED_PROMPT" ? "font-medium text-red-800" : "text-slate-800",
                      )}
                    >
                      {e.action === "BLOCKED_PROMPT" && <ShieldAlert size={13} className="shrink-0" />}
                      {e.action === "BLOCKED_PROMPT" ? "Security: question refused" : describeEvent(e)}
                    </span>
                    <span className="block truncate text-[11px] text-muted" title={e.reason}>
                      {e.actor}
                      {e.reason ? ` — ${e.reason}` : ""}
                    </span>
                  </span>
                </li>
              ))}
            </ol>
          )}
        </Panel>
      </div>

      <Panel title="Alert addresses" icon={<BellRing size={14} className="text-accent" />}>
        <form
          className="grid gap-3 sm:grid-cols-[1fr_180px_auto] sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            const form = e.currentTarget;
            const f = new FormData(form);
            const email = String(f.get("email") ?? "");
            const role = String(f.get("role") ?? "");
            run(
              () => tool("add_alert_address", { email, role }),
              `AWS has emailed ${email} a confirmation link.`,
              () => form.reset(),
            );
          }}
        >
          <Field label="Email address" name="email" type="email" required maxLength={254} />
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-slate-700">Role</span>
            <select name="role" className="rounded-lg border border-line bg-surface px-3 py-2">
              <optgroup label="Crew control">
                <option value="controller">Controller</option>
                <option value="approver">Approver</option>
                <option value="duty_supervisor">Duty supervisor (untouched disruptions)</option>
              </optgroup>
              <optgroup label="Operational teams">
                {ALERT_TEAMS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </optgroup>
            </select>
          </label>
          <Button disabled={busy}>
            <Mail size={14} /> Add
          </Button>
        </form>
        <p className="mt-2 text-[11px] text-muted">
          One role per address; it receives alerts once it confirms AWS&apos;s email. At most five new addresses a day.
        </p>
        {addresses && addresses.length > 0 && (
          <ul className="mt-4 divide-y divide-line rounded-xl border border-line">
            {addresses.map((a) => (
              <li key={a.subscription_id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
                <span className="min-w-0 flex-1 truncate">{a.email}</span>
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs capitalize">
                  {a.role.replace(/_/g, " ")}
                </span>
                <span className="text-xs text-muted">{a.status}</span>
                <button
                  className="rounded-lg p-1.5 text-muted transition hover:bg-critical-soft hover:text-critical"
                  title="Remove"
                  disabled={busy}
                  onClick={() =>
                    run(
                      () => tool("remove_alert_address", { subscription_id: a.subscription_id }),
                      `Removed ${a.email}.`,
                    )
                  }
                >
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="Planned leave" icon={<CalendarRange size={14} className="text-accent" />}>
        <p className="mb-3 text-xs leading-relaxed text-muted">
          Leave booked ahead (annual leave, a planned operation, a course), as rostering would. The person is never
          offered as cover in that period, and any trips they are already rostered on then go to a controller to re-crew
          in advance, with four-eyes approval as usual. No ten-minute escalation: nothing about it is urgent.
          Short-notice absences are reported as disruptions instead.
        </p>
        {leaveRef && (
          <form
            className="grid gap-3 sm:grid-cols-[1.4fr_1fr_1fr_1fr_auto] sm:items-end"
            onSubmit={(e) => {
              e.preventDefault();
              const form = e.currentTarget;
              const f = new FormData(form);
              const from = String(f.get("from") ?? "");
              run(
                () =>
                  tool("report_disruption", {
                    report_type: "planned_leave",
                    details: {
                      crew_id: String(f.get("crew") ?? "").trim(),
                      from_date: from,
                      to_date: String(f.get("to") ?? "") || from,
                      reason: f.get("reason"),
                    },
                  }),
                "Leave recorded; its trips go to a controller to re-crew within a minute.",
                () => form.reset(),
              );
            }}
          >
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-slate-700">Crew member</span>
              <input
                name="crew"
                list="leave-crew"
                required
                placeholder="C-1042"
                className="rounded-lg border border-line bg-surface px-3 py-2 outline-none focus:border-accent focus:ring-4 focus:ring-sky-100"
              />
              <datalist id="leave-crew">
                {leaveRef.crew.map((c) => (
                  <option key={c.crew_id} value={c.crew_id}>
                    {c.name}, {c.rank}
                  </option>
                ))}
              </datalist>
            </label>
            <Field
              label="From"
              name="from"
              type="date"
              required
              min={plusDays(leaveRef.today, 1)}
              max={leaveRef.leave_until ?? undefined}
            />
            <Field
              label="Until"
              name="to"
              type="date"
              min={plusDays(leaveRef.today, 1)}
              max={leaveRef.leave_until ?? undefined}
            />
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-slate-700">Reason</span>
              <select
                name="reason"
                className="rounded-lg border border-line bg-surface px-3 py-2 outline-none focus:border-accent"
              >
                {LEAVE_REASONS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <Button type="submit" tone="accent" disabled={busy}>
              {busy ? <Spinner /> : <CalendarRange size={14} />} Book leave
            </Button>
          </form>
        )}
        <div className="mt-4">
          {leave === null ? (
            <Spinner />
          ) : leave.length === 0 ? (
            <Empty icon={<CalendarRange size={22} />}>No leave booked.</Empty>
          ) : (
            <ul className="flex flex-col gap-2">
              {leave.map((l) => (
                <li key={l.disruption_id} className="rounded-xl border border-line px-3 py-2 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium">
                      <Mono>{l.details.crew_id}</Mono> · {day(l.details.from_date)} – {day(l.details.to_date)} ·{" "}
                      <span className="text-muted">{(l.details.reason ?? "").replace(/_/g, " ")}</span>
                    </span>
                    <Status value={shownStatus(l.status, l.handling)} />
                  </div>
                  <p className="mt-1 text-xs text-muted">{l.outcome}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Panel>
    </div>
  );
}
