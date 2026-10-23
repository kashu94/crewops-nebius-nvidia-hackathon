"use client";

import { BellRing, CalendarDays, Clock3, HeartPulse, MoonStar, Plane, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AirlineBadge, AIRLINES } from "@/components/airline";
import { useMe } from "@/components/me";
import { Button, clock, cx, day, Empty, Field, Mono, Notice, Panel, Spinner, Status } from "@/components/ui";
import { tool } from "@/lib/client";
import { shownStatus } from "@/lib/types";

type Day = { date: string; flights: string[]; report_utc: string; release_utc: string };
type Roster = {
  crew_id: string;
  airline?: string | null;
  name: string | null;
  rank?: string | null;
  /** A reserve's standby days, with the base and the window a callout falls in. */
  on_call?: { date: string; base: string; from_utc: string; to_utc: string }[];
  clock: {
    as_of: string;
    duty_hours_7d: number;
    duty_limit_7d: number;
    flight_hours_28d: number;
    flight_limit_28d: number;
    last_rest_ended: string | null;
  };
  pairings: { pairing_id: string; aircraft: string; role: string; days: Day[] }[];
  /** Shown range (a few days back to two months ahead) and the operation's today. */
  window?: { from: string; to: string; today: string };
  notices?: { disruption_id: string; status: string; at: string | null; message: string }[];
  reported_unavailable?: {
    disruption_id: string;
    from_date: string;
    to_date: string;
    reason: string | null;
    status: string;
  }[];
};

type Filed = {
  disruption_id: string;
  status: string;
  outcome: string;
  reported_utc: string | null;
  details: Record<string, unknown>;
  handling?: string | null;
};

const REASONS = [
  ["sick", "Sick"],
  ["fatigue", "Fatigue"],
  ["personal", "Personal"],
  ["other", "Other"],
] as const;

function Gauge({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = Math.min(100, Math.round((used / limit) * 100));
  const tone = pct >= 90 ? "bg-critical" : pct >= 75 ? "bg-pending" : "bg-accent";
  return (
    <div>
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-medium text-foreground">{label}</span>
        <Mono className="text-muted">
          {used}h / {limit}h
        </Mono>
      </div>
      <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-sunken">
        <div className={cx("h-full rounded-full transition-[width] duration-700", tone)} style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-1 text-[11px] text-muted">{Math.max(0, +(limit - used).toFixed(2))}h left before the limit</p>
    </div>
  );
}

function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Monday-first month grids from `from` to `to`. Leading padding cells are null. */
function months(from: string, to: string): { label: string; cells: (string | null)[] }[] {
  const out: { label: string; cells: (string | null)[] }[] = [];
  let cursor = `${from.slice(0, 7)}-01`;
  while (cursor <= to) {
    const first = new Date(`${cursor}T00:00:00Z`);
    const label = first.toLocaleDateString("en-IN", { month: "long", year: "numeric", timeZone: "UTC" });
    const cells: (string | null)[] = Array((first.getUTCDay() + 6) % 7).fill(null);
    for (let d = cursor; d.slice(0, 7) === cursor.slice(0, 7); d = addDays(d, 1)) cells.push(d);
    out.push({ label, cells });
    const next = new Date(first);
    next.setUTCMonth(first.getUTCMonth() + 1);
    cursor = next.toISOString().slice(0, 10);
  }
  return out;
}

function ReportSheet({
  crewId,
  taken,
  flying,
  earliest,
  latest,
  onClose,
}: {
  crewId: string;
  taken: NonNullable<Roster["reported_unavailable"]>;
  /** duty dates */
  flying: Set<string>;
  /** today to two months out; the server rejects anything outside */
  earliest: string;
  latest: string;
  onClose: (text?: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  // Only days off in range. Still allowed so crew control knows, but there's nothing to cover.
  const offOnly = useMemo(() => {
    if (!from) return false;
    const end = to && to >= from ? to : from;
    // string compare, not a day-by-day walk, so a typo'd year 9999 doesn't hang the tab
    return ![...flying].some((d) => d >= from && d <= end);
  }, [from, to, flying]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function send(form: FormData) {
    setBusy(true);
    setError(null);
    const from = String(form.get("from") ?? "");
    const to = String(form.get("to") ?? "") || from;
    if (to < from) {
      setError("'Until' must be on or after 'From'.");
      setBusy(false);
      return;
    }
    const clash = taken.find((t) => from <= t.to_date && t.from_date <= to);
    if (clash) {
      setError(`You're already reported unavailable ${clash.from_date} to ${clash.to_date}.`);
      setBusy(false);
      return;
    }
    try {
      const out = await tool<{ report_id: string; note: string }>("report_disruption", {
        report_type: "crew_unavailable",
        details: {
          crew_id: crewId,
          from_date: from,
          to_date: to,
          reason: form.get("reason"),
        },
        narrative: String(form.get("narrative") ?? ""),
      });
      onClose(`Reported (${out.report_id}). ${out.note}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed.");
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-brand/30 sm:items-center"
      onClick={() => onClose()}
    >
      <div
        role="dialog"
        aria-modal
        aria-label="Report unavailable"
        className="w-full max-w-md rounded-t-3xl bg-surface p-5 shadow-2xl sm:rounded-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            <HeartPulse size={18} className="text-critical" /> Report unavailable
          </h2>
          <button className="rounded-lg p-1.5 text-muted hover:bg-sunken" onClick={() => onClose()} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            send(new FormData(e.currentTarget));
          }}
        >
          <div className="grid grid-cols-2 gap-3">
            <Field
              label="From"
              name="from"
              type="date"
              required
              autoFocus
              min={earliest}
              max={latest}
              value={from}
              onChange={(e) => {
                setFrom(e.target.value);
                if (to && to < e.target.value) setTo("");
              }}
            />
            <Field
              label="Until"
              name="to"
              type="date"
              hint="Optional: the same day if empty"
              min={from || earliest}
              max={latest}
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          </div>
          {offOnly && (
            <p className="rounded-lg bg-sunken px-3 py-2 text-xs leading-relaxed text-[#353736]">
              You have no flights on these days, so nothing needs covering. Report it anyway if you&apos;re unwell:
              it&apos;s recorded, and you won&apos;t be asked to cover anyone then.
            </p>
          )}
          {taken.length > 0 && (
            <p className="rounded-lg bg-sunken px-3 py-2 text-xs text-muted">
              Already reported:{" "}
              {taken
                .map((t) => (t.from_date === t.to_date ? t.from_date : `${t.from_date} to ${t.to_date}`))
                .join("; ")}
            </p>
          )}
          <fieldset className="flex flex-col gap-1.5 text-sm">
            <legend className="mb-1 font-medium text-foreground">Reason</legend>
            <div className="grid grid-cols-4 gap-2">
              {REASONS.map(([value, label], i) => (
                <label key={value} className="cursor-pointer">
                  <input type="radio" name="reason" value={value} defaultChecked={i === 0} className="peer sr-only" />
                  <span className="block rounded-lg border border-line px-2 py-2 text-center text-xs transition peer-checked:border-accent peer-checked:bg-accent-soft peer-checked:font-semibold">
                    {label}
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <Field label="Anything crew control should know?" name="narrative" maxLength={500} hint="Optional" />
          {error && <Notice tone="error">{error}</Notice>}
          <Button type="submit" tone="danger" disabled={busy}>
            {busy && <Spinner />} Tell crew control
          </Button>
          <p className="text-center text-[11px] text-muted">
            Cover is worked out automatically; a controller and an approver decide it.
          </p>
        </form>
      </div>
    </div>
  );
}

export default function Crew() {
  const me = useMe();
  const [roster, setRoster] = useState<Roster | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sheet, setSheet] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [filed, setFiled] = useState<Filed[]>([]);

  const reload = useCallback(() => {
    if (!me.crew_id) return;
    tool<Roster>("my_roster").then(setRoster, (err: Error) => setError(err.message));
    tool<Filed[]>("my_reports").then(setFiled, () => undefined);
  }, [me.crew_id]);
  useEffect(reload, [reload]);

  // keep polling while a report is still being checked (takes up to a minute)
  const checking = filed.some((f) => f.status === "QUEUED" || f.status === "REPORTED");
  useEffect(() => {
    if (!checking) return;
    const t = setInterval(reload, 10_000);
    return () => clearInterval(t);
  }, [checking, reload]);

  // reported-unavailable days (self or by a reporter) with the reason, in their own colour
  const out = useMemo(() => {
    const map = new Map<string, { reason: string | null; disruption_id: string }>();
    (roster?.reported_unavailable ?? []).forEach((r) => {
      for (
        let d = new Date(`${r.from_date}T00:00:00Z`);
        d.toISOString().slice(0, 10) <= r.to_date;
        d.setUTCDate(d.getUTCDate() + 1)
      ) {
        map.set(d.toISOString().slice(0, 10), { reason: r.reason, disruption_id: r.disruption_id });
      }
    });
    return map;
  }, [roster]);

  const standby = useMemo(() => new Map((roster?.on_call ?? []).map((o) => [o.date, o])), [roster]);

  const duties = useMemo(() => {
    const map = new Map<string, { pairing: string; aircraft: string; day: Day }>();
    roster?.pairings.forEach((p) =>
      p.days.forEach((d) => map.set(d.date, { pairing: p.pairing_id, aircraft: p.aircraft, day: d })),
    );
    return map;
  }, [roster]);

  if (!me.crew_id) return <Notice>This view is for crew members. Your account has no crew record.</Notice>;
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!roster)
    return (
      <div className="text-muted">
        <Spinner />
      </div>
    );

  // older servers don't send a window; fall back to the duty clock's week
  const today = roster.window?.today ?? roster.clock.as_of.slice(0, 10);
  const win = roster.window ?? { from: today, to: addDays(today, 6), today };

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-muted">Good to see you</p>
          <h1 className="mt-0.5 text-[22px] font-semibold tracking-tight">{roster.name ?? me.user_id}</h1>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted">
            {roster.airline && (
              <span
                className="inline-flex items-center gap-1.5 font-medium"
                style={{ color: AIRLINES[roster.airline]?.color }}
              >
                <AirlineBadge code={roster.airline} compact />
                {AIRLINES[roster.airline]?.name ?? roster.airline}
              </span>
            )}
            <span>
              <Mono>{roster.crew_id}</Mono> · {roster.rank ?? roster.pairings[0]?.role ?? "Crew"}
              {standby.size > 0 && " (reserve)"}
            </span>
          </p>
        </div>
        <Button tone="danger" className="h-10 px-4" onClick={() => setSheet(true)}>
          <HeartPulse size={16} /> I can&apos;t fly: report unavailable
        </Button>
      </header>

      {message && <Notice tone="ok">{message}</Notice>}

      {(roster.notices ?? []).length > 0 && (
        <section aria-label="Notices for you" className="flex flex-col gap-2">
          {(roster.notices ?? []).map((n) => (
            <div key={n.disruption_id} className="flex gap-3 rounded-lg border border-amber-200 bg-surface p-4">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-white text-amber-600">
                <BellRing size={17} />
              </span>
              <div className="min-w-0">
                <p className="text-sm font-semibold text-amber-900">Action needed</p>
                <p className="mt-0.5 text-sm leading-relaxed text-amber-950/90">{n.message}</p>
                <p className="mt-1 text-[11px] text-amber-900/60">
                  {day(n.at)} · <Mono>{n.disruption_id}</Mono>
                </p>
              </div>
            </div>
          ))}
        </section>
      )}

      <div className="grid items-start gap-6 lg:grid-cols-2 2xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,0.85fr)]">
        <Panel
          title="My calendar"
          icon={<CalendarDays size={14} className="text-accent" />}
          action={
            <span className="text-[11px] text-muted">
              {day(win.from)} – {day(win.to)}
            </span>
          }
        >
          <div className="flex max-h-[560px] flex-col gap-4 overflow-y-auto pr-1">
            {months(win.from, win.to).map((m) => (
              <section key={m.label}>
                <h3 className="mb-1.5 text-xs font-semibold text-foreground">{m.label}</h3>
                <div className="grid grid-cols-7 gap-1 text-center text-[11px] text-muted">
                  {["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"].map((w) => (
                    <span key={w}>{w}</span>
                  ))}
                </div>
                <ol className="mt-1 grid grid-cols-7 gap-1 text-center">
                  {m.cells.map((date, i) => {
                    if (!date) return <li key={`pad-${i}`} />;
                    const shown = date >= win.from && date <= win.to;
                    const duty = duties.get(date);
                    const onCall = standby.get(date);
                    const away = out.get(date);
                    const past = date < win.today;
                    return (
                      <li
                        key={date}
                        className={cx(
                          "relative flex h-10 flex-col items-center justify-center rounded-lg text-xs",
                          !shown
                            ? "text-[#b4b7ba]"
                            : away
                              ? "bg-rose-50 text-rose-700 ring-2 ring-inset ring-rose-300"
                              : duty
                                ? "bg-accent text-white"
                                : onCall
                                  ? "bg-accent-soft text-[#173f63] ring-1 ring-inset ring-[#9dbcd8]"
                                  : "bg-sunken text-muted",
                          shown && past && "opacity-55",
                          date === win.today && "outline outline-2 outline-offset-1 outline-foreground",
                        )}
                        title={
                          !shown
                            ? undefined
                            : away
                              ? `Reported unavailable${away.reason ? ` (${away.reason.replace(/_/g, " ")})` : ""}${duty ? `: ${duty.pairing} being covered` : ""}`
                              : duty
                                ? `${duty.pairing} on ${duty.aircraft}: ${duty.day.flights.map((f) => f.split("-")[0]).join(", ")}`
                                : onCall
                                  ? `On call at ${onCall.base}, ${onCall.from_utc}–${onCall.to_utc}Z`
                                  : "Off"
                        }
                      >
                        <span className={cx("font-semibold", away && duty && "line-through decoration-2")}>
                          {Number(date.slice(8, 10))}
                        </span>
                        {shown && (away ? <HeartPulse size={10} /> : duty ? <Plane size={10} /> : null)}
                      </li>
                    );
                  })}
                </ol>
              </section>
            ))}
          </div>
          <p className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted">
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm bg-accent" /> Flying
            </span>
            {standby.size > 0 && (
              <span className="flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-sm bg-accent-soft ring-1 ring-[#9dbcd8]" /> On call
              </span>
            )}
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm bg-sunken ring-1 ring-line" /> Off
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm bg-rose-50 ring-2 ring-rose-300" /> Unavailable or on leave
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm outline outline-2 outline-foreground" /> Today
            </span>
          </p>
        </Panel>

        <Panel title="My flights" icon={<Plane size={14} className="text-accent" />}>
          {roster.pairings.length === 0 && standby.size > 0 ? (
            <div className="text-sm">
              <p className="font-medium">On standby: no trips rostered yet</p>
              <p className="mt-1 text-muted">
                You&apos;re a reserve at {roster.on_call?.[0]?.base}, on call {roster.on_call?.[0]?.from_utc}–
                {roster.on_call?.[0]?.to_utc}Z on {standby.size} days from {day(roster.on_call?.[0]?.date)} to{" "}
                {day(roster.on_call?.[roster.on_call.length - 1]?.date)}. When crew control calls you out to cover a
                trip, it appears here and on your calendar.
              </p>
            </div>
          ) : roster.pairings.length === 0 ? (
            <Empty icon={<MoonStar size={22} />}>No trips rostered.</Empty>
          ) : (
            <ol className="flex max-h-[560px] flex-col gap-3 overflow-y-auto pr-1">
              {roster.pairings
                .flatMap((p) => p.days.map((d) => ({ p, d })))
                .sort((a, b) => a.d.report_utc.localeCompare(b.d.report_utc))
                .map(({ p, d }) => (
                  <li
                    key={`${p.pairing_id}-${d.date}`}
                    className={cx(
                      "lift rounded-xl border p-3",
                      out.has(d.date) ? "border-rose-300 bg-rose-50/60" : "border-line",
                      d.date < win.today && "opacity-60",
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-2 text-sm font-semibold">
                        {day(d.date)}
                        {d.date < win.today && (
                          <span className="rounded-full bg-sunken px-2 py-0.5 text-[11px] font-medium text-muted">
                            Flown
                          </span>
                        )}
                        {d.date === win.today && (
                          <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-semibold text-[#173f63]">
                            Today
                          </span>
                        )}
                        {out.has(d.date) && (
                          <span className="flex items-center gap-1 rounded-full bg-rose-100 px-2 py-0.5 text-[11px] font-semibold text-rose-700">
                            <HeartPulse size={10} /> Unavailable
                          </span>
                        )}
                      </span>
                      <span className="text-xs text-muted">
                        <Mono>{p.pairing_id}</Mono> · <Mono>{p.aircraft}</Mono>
                      </span>
                    </div>
                    <p className="mt-1 flex items-center gap-1.5 text-xs text-muted">
                      <Clock3 size={12} /> Report <Mono>{clock(d.report_utc)}</Mono> · Release{" "}
                      <Mono>{clock(d.release_utc)}</Mono>
                    </p>
                    <p className="mt-2 flex flex-wrap gap-1.5">
                      {d.flights.map((f) => (
                        <Mono key={f} className="rounded-md bg-accent-soft px-1.5 py-0.5 text-[11px] text-[#173f63]">
                          {f.split("-")[0]}
                        </Mono>
                      ))}
                    </p>
                  </li>
                ))}
            </ol>
          )}
        </Panel>

        <div className="flex flex-col gap-6 lg:col-span-2 2xl:col-span-1">
          <Panel title="Duty limits" icon={<Clock3 size={14} className="text-accent" />}>
            <div className="flex flex-col gap-4">
              <Gauge label="Duty, last 7 days" used={roster.clock.duty_hours_7d} limit={roster.clock.duty_limit_7d} />
              <Gauge
                label="Flying, last 28 days"
                used={roster.clock.flight_hours_28d}
                limit={roster.clock.flight_limit_28d}
              />
              <p className="text-[11px] text-muted">
                As of {day(roster.clock.as_of)}; last rest ended {day(roster.clock.last_rest_ended)}{" "}
                <Mono>{clock(roster.clock.last_rest_ended)}</Mono>.
              </p>
            </div>
          </Panel>
          {filed.length > 0 && (
            <Panel title="My reports" icon={<HeartPulse size={14} className="text-accent" />}>
              <ul className="flex flex-col gap-2">
                {filed.slice(0, 5).map((f) => (
                  <li key={f.disruption_id} className="rounded-md border border-line px-3 py-2 text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">
                        {String(f.details.from_date ?? "")}
                        {f.details.to_date && f.details.to_date !== f.details.from_date
                          ? ` to ${String(f.details.to_date)}`
                          : ""}
                      </span>
                      <Status value={shownStatus(f.status, f.handling)} />
                    </div>
                    <p className={cx("mt-1 text-xs", f.status === "REJECTED" ? "text-critical" : "text-muted")}>
                      {f.outcome}
                    </p>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
      </div>

      {sheet && (
        <ReportSheet
          crewId={roster.crew_id}
          taken={roster.reported_unavailable ?? []}
          flying={new Set(duties.keys())}
          earliest={win.today}
          latest={win.to}
          onClose={(text) => {
            setSheet(false);
            if (text) {
              setMessage(text);
              setTimeout(reload, 1500);
            }
          }}
        />
      )}
    </div>
  );
}
