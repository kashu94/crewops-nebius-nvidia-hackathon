"use client";

import {
  AlertTriangle,
  ArrowRight,
  Ban,
  CalendarDays,
  Clock3,
  HeartPulse,
  Plane,
  PlaneTakeoff,
  Search,
  Timer,
  Users,
  X,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { AIRLINES, AirlineBadge } from "@/components/airline";
import { useLiveListener } from "@/components/live";
import { clock, cx, day, Empty, Mono, Notice, Panel, SeverityBadge, Spinner, Status } from "@/components/ui";
import { tool } from "@/lib/client";
import { shownStatus } from "@/lib/types";

type Person = { crew_id: string; name: string; out: boolean };
type OnFlight = { disruption_id: string; status: string; report_type: string | null; severity: string | null };
type Flight = {
  flight_id: string;
  flight_no: string;
  date: string;
  from: string;
  to: string;
  dep_utc: string;
  arr_utc: string;
  aircraft: string;
  aircraft_type: string;
  airline: string | null;
  seats: number;
  booked: number | null;
  pairing_id: string | null;
  captain: Person | null;
  first_officer: Person | null;
  cabin: Person[];
  cancelled: boolean;
  disruptions: OnFlight[];
};
type Out = {
  crew_id: string;
  name: string | null;
  rank: string | null;
  airline: string | null;
  from_date: string;
  to_date: string;
  reason: string | null;
  self_reported: boolean;
  disruption_id: string;
  status: string;
  handling: string | null;
};
type Week = {
  week: string[];
  flights: Flight[];
  unavailable: Out[];
  /** Showable range (a few days back to two months ahead) and the operation's today. */
  window?: { from: string; to: string; today: string };
};

function shiftDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

const RANK: Record<string, string> = {
  Captain: "CPT",
  "First Officer": "FO",
  "Senior Cabin Crew": "SCC",
  "Cabin Crew": "CC",
};
const LEVEL_ORDER = { high: 0, medium: 1, low: 2 } as const;

function weekday(date: string, style: "short" | "long" = "short") {
  return new Date(`${date}T00:00:00Z`).toLocaleDateString("en-IN", { weekday: style, timeZone: "UTC" });
}

function spans(o: Out, date: string) {
  return o.from_date <= date && date <= o.to_date;
}

function reportHref(params: Record<string, string>) {
  return `/report?${new URLSearchParams(params).toString()}`;
}

/** Struck through in rose if reported unavailable that day, otherwise a prefilled report link. */
function CrewChip({
  person,
  label,
  date,
  reportable = true,
}: {
  person: Person | null;
  label: string;
  date: string;
  /** false for past days, which can't be reported */
  reportable?: boolean;
}) {
  if (!person) return <span className="text-[11px] text-slate-400">{label} —</span>;
  if (person.out) {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-md bg-rose-50 px-1.5 py-0.5 text-[11px] font-medium text-rose-700 ring-1 ring-inset ring-rose-200"
        title={`${person.name} (${person.crew_id}) is reported unavailable on ${day(date)}`}
      >
        <HeartPulse size={11} />
        <span className="text-rose-500">{label}</span>
        <span className="line-through decoration-rose-400">{person.name}</span>
      </span>
    );
  }
  if (!reportable) {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[11px] text-slate-600">
        <span className="font-semibold text-slate-400">{label}</span>
        {person.name}
      </span>
    );
  }
  return (
    <Link
      href={reportHref({ type: "crew_unavailable", crew_id: person.crew_id, from_date: date })}
      className="group/chip inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-slate-700 transition hover:bg-rose-50 hover:text-rose-700"
      title={`Report ${person.name} (${person.crew_id}) unavailable on ${day(date)}`}
    >
      <span className="font-semibold text-slate-400 group-hover/chip:text-rose-400">{label}</span>
      {person.name}
    </Link>
  );
}

function Kpi({
  icon,
  label,
  value,
  sub,
  tone = "slate",
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "slate" | "rose" | "amber" | "sky";
}) {
  const tones = {
    slate: "from-slate-50 text-slate-600",
    rose: "from-rose-50 text-rose-600",
    amber: "from-amber-50 text-amber-600",
    sky: "from-sky-50 text-sky-600",
  };
  return (
    <div className={cx("animate-rise rounded-2xl border border-line bg-gradient-to-br to-white p-4", tones[tone])}>
      <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.12em]">
        {icon} {label}
      </p>
      <p className="mt-2 text-2xl font-semibold tabular-nums text-slate-900">{value}</p>
      {sub && <div className="mt-1 text-[11px] text-muted">{sub}</div>}
    </div>
  );
}

/** Daily passengers stacked by airline, with crew out and open disruptions marked. The
 * schedule repeats daily, the load doesn't. */
function WeekChart({
  week,
  flights,
  out,
  selected,
  onSelect,
}: {
  week: string[];
  flights: Flight[];
  out: Out[];
  selected: string;
  onSelect: (d: string) => void;
}) {
  const perDay = week.map((date) => {
    const today = flights.filter((f) => f.date === date);
    const byAirline = Object.keys(AIRLINES).map((code) => ({
      code,
      n: today.filter((f) => f.airline === code && !f.cancelled).reduce((s, f) => s + (f.booked ?? 0), 0),
    }));
    const pax = byAirline.reduce((s, a) => s + a.n, 0);
    const away = new Set(out.filter((o) => spans(o, date)).map((o) => o.crew_id)).size;
    const hit = new Set(today.flatMap((f) => f.disruptions.map((d) => d.disruption_id))).size;
    return { date, total: today.length, pax, byAirline, away, hit };
  });
  const max = Math.max(1, ...perDay.map((d) => d.pax));
  return (
    <div className="grid grid-cols-7 gap-2 sm:gap-3" role="tablist" aria-label="Days of the week">
      {perDay.map((d) => {
        const on = d.date === selected;
        return (
          <button
            key={d.date}
            role="tab"
            aria-selected={on}
            onClick={() => onSelect(d.date)}
            className={cx(
              "group flex flex-col items-stretch rounded-xl border p-2 text-left transition",
              on
                ? "border-accent bg-accent-soft/60 ring-1 ring-sky-200"
                : "border-transparent hover:border-line hover:bg-sunken",
            )}
          >
            <div className="flex h-28 items-end justify-center">
              <div
                className="flex w-7 flex-col-reverse overflow-hidden rounded-md sm:w-9"
                style={{ height: `${Math.max(6, (d.pax / max) * 100)}%` }}
                title={d.byAirline.map((a) => `${AIRLINES[a.code].name}: ${a.n} passengers`).join(" · ")}
              >
                {d.byAirline.map((a) =>
                  a.n ? (
                    <span
                      key={a.code}
                      className="block w-full transition-opacity group-hover:opacity-90"
                      style={{ height: `${(a.n / Math.max(1, d.pax)) * 100}%`, background: AIRLINES[a.code].color }}
                    />
                  ) : null,
                )}
              </div>
            </div>
            <span className="mt-2 text-center text-[11px] uppercase tracking-wide text-muted">{weekday(d.date)}</span>
            <span
              className={cx("text-center text-lg font-semibold leading-tight", on ? "text-sky-900" : "text-slate-800")}
            >
              {Number(d.date.slice(8, 10))}
            </span>
            <span className="text-center text-[11px] tabular-nums text-slate-600">
              {d.pax.toLocaleString("en-IN")} pax
            </span>
            <span className="text-center text-[10px] tabular-nums text-muted">{d.total} flights</span>
            <span className="mt-1.5 flex min-h-[18px] flex-wrap justify-center gap-1">
              {d.away > 0 && (
                <span
                  className="inline-flex items-center gap-0.5 rounded-full bg-rose-100 px-1.5 text-[10px] font-semibold text-rose-700"
                  title="Crew reported unavailable"
                >
                  <HeartPulse size={9} /> {d.away}
                </span>
              )}
              {d.hit > 0 && (
                <span
                  className="inline-flex items-center gap-0.5 rounded-full bg-amber-100 px-1.5 text-[10px] font-semibold text-amber-800"
                  title="Open disruptions"
                >
                  <AlertTriangle size={9} /> {d.hit}
                </span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function Load({ booked, seats }: { booked: number | null; seats: number }) {
  if (booked == null) return <span className="text-[11px] text-slate-400">—</span>;
  const pct = Math.min(100, Math.round((booked / Math.max(1, seats)) * 100));
  return (
    <div className="w-20" title={`${booked} of ${seats} seats booked`}>
      <div className="flex justify-between text-[10px] tabular-nums text-muted">
        <span>{booked}</span>
        <span>{pct}%</span>
      </div>
      <div className="mt-0.5 h-1.5 overflow-hidden rounded-full bg-slate-100">
        <div
          className={cx("h-full rounded-full", pct >= 90 ? "bg-indigo-500" : "bg-sky-400")}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function FlightRow({ f, today }: { f: Flight; today: string }) {
  const ahead = f.date >= today;
  const worst = [...f.disruptions].sort(
    (a, b) =>
      (LEVEL_ORDER[a.severity as keyof typeof LEVEL_ORDER] ?? 3) -
      (LEVEL_ORDER[b.severity as keyof typeof LEVEL_ORDER] ?? 3),
  )[0];
  const crewOut = [f.captain, f.first_officer, ...f.cabin].some((p) => p?.out);
  return (
    <li
      className={cx(
        "grid items-center gap-x-4 gap-y-2 border-l-[3px] px-4 py-2.5 sm:grid-cols-[80px_minmax(0,1.1fr)_minmax(0,1.6fr)_auto]",
        f.cancelled
          ? "border-l-slate-300 bg-slate-50 opacity-70"
          : worst?.severity === "high"
            ? "border-l-red-500 bg-red-50/30"
            : worst || crewOut
              ? "border-l-amber-400 bg-amber-50/20"
              : "border-l-transparent",
      )}
    >
      <div>
        <Mono className="text-sm font-semibold text-slate-900">{clock(f.dep_utc)}</Mono>
        <p className="text-[11px] text-muted">
          arr <Mono>{clock(f.arr_utc)}</Mono>
        </p>
      </div>
      <div className="min-w-0">
        <p className="flex items-center gap-2">
          <AirlineBadge code={f.airline} compact />
          <Mono className="text-sm font-semibold">{f.flight_no}</Mono>
          {f.cancelled && (
            <span className="inline-flex items-center gap-1 rounded bg-slate-200 px-1.5 text-[10px] font-semibold text-slate-700">
              <Ban size={10} /> Cancelled
            </span>
          )}
        </p>
        <p className="mt-0.5 flex items-center gap-1.5 text-xs text-slate-600">
          <Mono>{f.from}</Mono> <ArrowRight size={11} className="text-slate-400" /> <Mono>{f.to}</Mono>
          <span className="text-slate-300">·</span>
          <Mono className="text-muted">
            {f.aircraft} {f.aircraft_type}
          </Mono>
        </p>
      </div>
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex flex-wrap gap-x-1 gap-y-0.5">
          <CrewChip person={f.captain} label="CPT" date={f.date} reportable={ahead} />
          <CrewChip person={f.first_officer} label="FO" date={f.date} reportable={ahead} />
        </div>
        <div className="flex flex-wrap items-center gap-x-1 gap-y-0.5">
          <span className="flex items-center gap-1 text-[11px] text-muted">
            <Users size={11} /> {f.cabin.length} cabin
          </span>
          {f.cabin
            .filter((p) => p.out)
            .map((p) => (
              <CrewChip key={p.crew_id} person={p} label="CC" date={f.date} reportable={ahead} />
            ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Load booked={f.booked} seats={f.seats} />
        <div className="flex flex-wrap items-center justify-end gap-1">
          {f.disruptions.map((d) => (
            <span key={d.disruption_id} className="inline-flex items-center gap-1">
              <SeverityBadge severity={d.severity ? { level: d.severity } : null} />
              <Mono className="text-[10px] text-muted">{d.disruption_id}</Mono>
            </span>
          ))}
          {!f.cancelled && ahead && (
            <Link
              href={reportHref({ type: "flight_delay", flight_id: f.flight_id })}
              className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-muted transition hover:bg-amber-50 hover:text-amber-800"
              title={`Report ${f.flight_no} delayed`}
            >
              <Timer size={11} /> Delay
            </Link>
          )}
        </div>
      </div>
    </li>
  );
}

export default function Operations() {
  const [data, setData] = useState<Week | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [airline, setAirline] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [onlyIssues, setOnlyIssues] = useState(false);

  const [start, setStart] = useState("");
  const load = useMemo(
    () => () =>
      tool<Week>("operations_overview", start ? { start } : {}).then(
        (w) => {
          setData(w);
          // keep the selected day if it's in this week, else today, else the week's first day
          const today = w.window?.today ?? "";
          setSelected((s) => (s && w.week.includes(s) ? s : w.week.includes(today) ? today : (w.week[0] ?? null)));
        },
        (err: Error) => setError(err.message),
      ),
    [start],
  );
  useEffect(() => {
    load();
  }, [load]);
  // any status change (analysed, approved, ...) refreshes the board
  useLiveListener(load);

  const date = selected ?? data?.week[0] ?? "";
  const shown = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    return data.flights.filter((f) => {
      if (f.date !== date) return false;
      if (airline && f.airline !== airline) return false;
      if (
        onlyIssues &&
        !f.disruptions.length &&
        !f.cancelled &&
        ![f.captain, f.first_officer, ...f.cabin].some((p) => p?.out)
      ) {
        return false;
      }
      if (!q) return true;
      const people = [f.captain, f.first_officer, ...f.cabin].filter(Boolean) as Person[];
      return [f.flight_no, f.from, f.to, f.aircraft, ...people.flatMap((p) => [p.name, p.crew_id])].some((v) =>
        v.toLowerCase().includes(q),
      );
    });
  }, [data, date, airline, query, onlyIssues]);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) {
    return (
      <div className="flex flex-1 items-center justify-center text-muted">
        <Spinner />
      </div>
    );
  }
  if (data.week.length === 0) {
    return <Notice>The rostered calendar has ended: there are no flights to show from here.</Notice>;
  }

  const disruptions = new Map<string, OnFlight>();
  data.flights.forEach((f) => f.disruptions.forEach((d) => disruptions.set(d.disruption_id, d)));
  const levels = { high: 0, medium: 0, low: 0 };
  disruptions.forEach((d) => {
    if (d.severity && d.severity in levels) levels[d.severity as keyof typeof levels] += 1;
  });
  const booked = data.flights.reduce((n, f) => n + (f.cancelled ? 0 : (f.booked ?? 0)), 0);
  const awayWeek = new Set(data.unavailable.map((o) => o.crew_id)).size;
  const awayToday = data.unavailable.filter((o) => spans(o, date));
  const cancelled = data.flights.filter((f) => f.cancelled).length;

  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-accent">Operations</p>
          <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
            Week of {day(data.week[0])} – {day(data.week[data.week.length - 1])}
            {data.window && (
              <span className="ml-1 inline-flex items-center gap-1 align-middle text-sm font-normal">
                <button
                  type="button"
                  onClick={() => setStart(shiftDays(data.week[0], -7))}
                  disabled={data.week[0] <= data.window.from}
                  className="rounded-lg border border-line px-2 py-1 text-slate-600 transition hover:bg-sunken disabled:opacity-40"
                  aria-label="Previous week"
                >
                  ←
                </button>
                <button
                  type="button"
                  onClick={() => setStart(data.window!.today)}
                  className="rounded-lg border border-line px-2.5 py-1 text-slate-600 transition hover:bg-sunken"
                >
                  Today
                </button>
                <button
                  type="button"
                  onClick={() => setStart(shiftDays(data.week[0], 7))}
                  disabled={data.week[data.week.length - 1] >= data.window.to}
                  className="rounded-lg border border-line px-2 py-1 text-slate-600 transition hover:bg-sunken disabled:opacity-40"
                  aria-label="Next week"
                >
                  →
                </button>
              </span>
            )}
          </h1>
          <p className="text-sm text-muted">
            Every flight, who flies it and who is out, from 5 days back to 2 months ahead. Click a crew name to report
            them unavailable.
          </p>
        </div>
        <Link
          href="/report"
          className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:brightness-110"
        >
          <AlertTriangle size={15} /> Record a disruption
        </Link>
      </header>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi
          icon={<PlaneTakeoff size={13} />}
          label="Flights"
          value={data.flights.length - cancelled}
          tone="sky"
          sub={
            <span className="flex flex-wrap gap-2">
              {Object.keys(AIRLINES).map((code) => (
                <span key={code} className="flex items-center gap-1">
                  <span className="h-2 w-2 rounded-full" style={{ background: AIRLINES[code].color }} />
                  {data.flights.filter((f) => f.airline === code && !f.cancelled).length}
                </span>
              ))}
              {cancelled > 0 && <span>· {cancelled} cancelled</span>}
            </span>
          }
        />
        <Kpi
          icon={<Users size={13} />}
          label="Passengers booked"
          value={booked.toLocaleString("en-IN")}
          sub="across the week"
        />
        <Kpi
          icon={<HeartPulse size={13} />}
          label="Crew out"
          value={awayWeek}
          tone="rose"
          sub={`${new Set(awayToday.map((o) => o.crew_id)).size} on ${day(date)}`}
        />
        <Kpi
          icon={<AlertTriangle size={13} />}
          label="Open disruptions"
          value={disruptions.size}
          tone="amber"
          sub={
            <span className="flex gap-2">
              <span className="font-semibold text-red-700">{levels.high} high</span>
              <span className="text-amber-700">{levels.medium} medium</span>
              <span className="text-emerald-700">{levels.low} low</span>
            </span>
          }
        />
      </div>

      <Panel
        title="Passengers by day"
        icon={<CalendarDays size={14} className="text-accent" />}
        action={
          <span className="hidden items-center gap-3 text-[11px] text-muted sm:flex">
            {Object.entries(AIRLINES).map(([code, a]) => (
              <span key={code} className="flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-sm" style={{ background: a.color }} /> {a.name}
              </span>
            ))}
          </span>
        }
      >
        <WeekChart
          week={data.week}
          flights={data.flights}
          out={data.unavailable}
          selected={date}
          onSelect={setSelected}
        />
      </Panel>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Panel
          title={
            <span>
              {weekday(date, "long")} {day(date)}{" "}
              <span className="font-normal text-muted">· {shown.length} flights</span>
            </span>
          }
          icon={<Plane size={14} className="text-accent" />}
          bodyClassName="p-0"
        >
          <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
            <label className="relative min-w-[180px] flex-1">
              <Search
                size={13}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Flight, station, aircraft or crew"
                className="w-full rounded-lg border border-line bg-surface py-1.5 pl-8 pr-8 text-sm outline-none focus:border-accent focus:ring-4 focus:ring-sky-100"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  aria-label="Clear the search"
                  title="Clear"
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-slate-400 hover:bg-sunken hover:text-slate-700"
                >
                  <X size={13} />
                </button>
              )}
            </label>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setAirline(null)}
                className={cx(
                  "rounded-lg px-2.5 py-1 text-xs font-medium",
                  !airline ? "bg-slate-900 text-white" : "text-muted hover:bg-sunken",
                )}
              >
                All
              </button>
              {Object.entries(AIRLINES).map(([code, a]) => (
                <button
                  key={code}
                  onClick={() => setAirline(airline === code ? null : code)}
                  className={cx(
                    "flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-medium transition",
                    airline === code ? "bg-slate-900 text-white" : "text-muted hover:bg-sunken",
                  )}
                  title={a.name}
                >
                  <span className="h-2 w-2 rounded-full" style={{ background: a.color }} /> {code}
                </button>
              ))}
            </div>
            <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted">
              <input
                type="checkbox"
                checked={onlyIssues}
                onChange={(e) => setOnlyIssues(e.target.checked)}
                className="accent-sky-600"
              />
              Only affected
            </label>
          </div>
          {shown.length === 0 ? (
            <div className="p-6">
              <Empty icon={<Plane size={22} />}>No flights match.</Empty>
            </div>
          ) : (
            <ol className="divide-y divide-line">
              {shown.map((f) => (
                <FlightRow key={f.flight_id} f={f} today={data.window?.today ?? ""} />
              ))}
            </ol>
          )}
        </Panel>

        <Panel title="Crew out" icon={<HeartPulse size={14} className="text-rose-500" />} className="self-start">
          {data.unavailable.length === 0 ? (
            <Empty icon={<HeartPulse size={22} />}>Nobody is reported unavailable this week.</Empty>
          ) : (
            <ul className="flex flex-col gap-2">
              {data.unavailable.map((o) => (
                <li
                  key={`${o.disruption_id}-${o.crew_id}`}
                  className={cx(
                    "rounded-xl border p-3 transition",
                    spans(o, date) ? "border-rose-200 bg-rose-50/60" : "border-line",
                  )}
                >
                  <div className="flex items-start gap-2.5">
                    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-rose-100 text-[11px] font-semibold text-rose-700">
                      {(o.name ?? o.crew_id)
                        .split(/[\s.]+/)
                        .filter(Boolean)
                        .map((w) => w[0])
                        .join("")
                        .slice(0, 2)
                        .toUpperCase()}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-1.5 text-sm font-semibold">
                        <span className="truncate">{o.name ?? o.crew_id}</span>
                        <AirlineBadge code={o.airline} compact />
                      </p>
                      <p className="text-[11px] text-muted">
                        <Mono>{o.crew_id}</Mono> · {RANK[o.rank ?? ""] ?? o.rank} · {o.reason ?? "unavailable"}
                      </p>
                    </div>
                    {o.status && <Status value={shownStatus(o.status, o.handling)} />}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1">
                    {data.week.map((d) => (
                      <span
                        key={d}
                        title={day(d)}
                        className={cx(
                          "grid h-6 w-6 place-items-center rounded-md text-[10px] font-semibold",
                          spans(o, d) ? "bg-rose-500 text-white" : "bg-slate-100 text-slate-400",
                          d === date && "ring-2 ring-sky-400 ring-offset-1",
                        )}
                      >
                        {Number(d.slice(8, 10))}
                      </span>
                    ))}
                  </div>
                  {o.disruption_id && (
                    <p className="mt-1.5 flex items-center gap-1 text-[10px] text-muted">
                      <Clock3 size={10} />
                      {o.self_reported ? "Reported by themselves" : "Reported by the desk"} ·{" "}
                      <Mono>{o.disruption_id}</Mono>
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
