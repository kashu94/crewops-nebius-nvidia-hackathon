"use client";

import { ArrowUpRight, Inbox, ListFilter } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AirlineBadge } from "@/components/airline";
import { useLiveListener } from "@/components/live";
import { useMe } from "@/components/me";
import { clock, cx, day, Empty, Mono, Notice, Panel, SeverityBadge, Spinner, Status } from "@/components/ui";
import { tool } from "@/lib/client";
import { describe, shownStatus, type FeedItem } from "@/lib/types";

type Scope = "mine" | "others" | "network";
type State = "open" | "resolved" | "all";

const RESOLVED = new Set(["COMMITTED", "CLOSED"]);

const TYPE: Record<string, string> = {
  crew_unavailable: "Sick crew",
  multiple_crew_unavailable: "Multiple sick crew",
  certification_lapse: "Certification expiry",
  flight_delay: "Delay",
  aircraft_technical: "Aircraft fault",
  station_disruption: "Station closure",
  gate_closure: "Gate closure",
  gate_change: "Gate change",
  planned_leave: "Planned leave",
};

function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string; count?: number; disabled?: boolean }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-xl border border-line bg-sunken p-1">
      {options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={value === o.value}
          disabled={o.disabled}
          onClick={() => onChange(o.value)}
          className={cx(
            "rounded-lg px-3 py-1.5 text-sm transition disabled:cursor-not-allowed disabled:opacity-40",
            value === o.value ? "bg-surface font-semibold shadow-sm" : "text-muted hover:text-foreground",
          )}
        >
          {o.label}
          {o.count !== undefined && <span className="ml-1.5 font-mono text-xs text-muted">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

export default function Disruptions() {
  const me = useMe();
  const [items, setItems] = useState<FeedItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<Scope>(me.desk ? "mine" : "network");
  const [state, setState] = useState<State>("open");
  const [type, setType] = useState<string>("all");

  const load = useCallback(() => {
    tool<FeedItem[]>("disruptions", { limit: 100 }).then(setItems, (err: Error) => setError(err.message));
  }, []);
  useEffect(load, [load]);
  useLiveListener(useCallback(() => load(), [load]));

  const inScope = useCallback(
    (d: FeedItem, s: Scope) => {
      const desks = d.desks ?? [];
      if (s === "network" || !me.desk) return true;
      // not analysed yet, so no desk owns it. Only the whole-network view shows it.
      if (d.status === "REPORTED") return false;
      // older records with no aircraft have no desk; show them to every desk
      const mine = desks.length === 0 || desks.includes(me.desk);
      return s === "mine" ? mine : !mine;
    },
    [me.desk],
  );
  const inState = (d: FeedItem, s: State) => s === "all" || (s === "resolved") === RESOLVED.has(d.status);

  const shown = useMemo(
    () =>
      (items ?? []).filter((d) => inScope(d, scope) && inState(d, state) && (type === "all" || d.report_type === type)),
    [items, scope, state, type, inScope],
  );
  const count = (s: Scope) => (items ?? []).filter((d) => inScope(d, s) && inState(d, state)).length;
  const types = useMemo(
    () => [...new Set((items ?? []).map((d) => d.report_type ?? ""))].filter(Boolean).sort(),
    [items],
  );

  if (error) return <Notice tone="error">{error}</Notice>;

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Disruptions</h1>
        <p className="text-sm text-muted">
          Every disruption reported, by desk and state. Open one for its full record.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        {me.desk ? (
          <Segmented
            label="Desk"
            value={scope}
            onChange={setScope}
            options={[
              { value: "mine", label: "My desk", count: count("mine") },
              { value: "others", label: "Other desks", count: count("others") },
              { value: "network", label: "Whole network", count: count("network") },
            ]}
          />
        ) : (
          <span
            className="rounded-xl border border-line bg-sunken px-3 py-2 text-sm text-muted"
            title="Only controllers hold a desk"
          >
            Whole network <span className="font-mono text-xs">{count("network")}</span>
            <span className="ml-2 text-xs">· you work across every desk</span>
          </span>
        )}
        <Segmented
          label="State"
          value={state}
          onChange={setState}
          options={[
            { value: "open", label: "Open" },
            { value: "resolved", label: "Resolved" },
            { value: "all", label: "All" },
          ]}
        />
        <label className="flex items-center gap-2 text-sm text-muted">
          <ListFilter size={14} />
          <select
            value={type}
            onChange={(e) => setType(e.target.value)}
            className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm"
          >
            <option value="all">Every type</option>
            {types.map((t) => (
              <option key={t} value={t}>
                {TYPE[t] ?? t.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
      </div>

      <Panel bodyClassName="p-0">
        {!items ? (
          <div className="p-4 text-muted">
            <Spinner />
          </div>
        ) : shown.length === 0 ? (
          <Empty icon={<Inbox size={22} />}>Nothing here with these filters.</Empty>
        ) : (
          <ol className="divide-y divide-line">
            {shown.map((d) => {
              return (
                <li key={d.disruption_id}>
                  <Link
                    href={`/console/${encodeURIComponent(d.disruption_id)}`}
                    className="group flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 transition hover:bg-sunken"
                  >
                    <span className="w-28 shrink-0 text-xs text-muted">
                      {day(d.reported_utc)} <Mono>{clock(d.reported_utc)}</Mono>
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5 text-sm font-medium text-slate-800">
                        {(d.airlines ?? []).map((a) => (
                          <AirlineBadge key={a} code={a} compact />
                        ))}
                        <span className="truncate">{describe(d)}</span>
                      </span>
                      <span className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-muted">
                        <span>{TYPE[d.report_type ?? ""] ?? d.report_type}</span>
                        <Mono>{d.disruption_id}</Mono>
                        {(d.desks ?? []).map((k) => (
                          <span
                            key={k}
                            className={cx(
                              "rounded px-1.5 font-semibold",
                              k === me.desk ? "bg-pending-soft text-amber-800" : "bg-slate-100 text-slate-600",
                            )}
                          >
                            {k}
                          </span>
                        ))}
                      </span>
                    </span>
                    <SeverityBadge severity={d.severity} />
                    <Status value={shownStatus(d.status, d.handling)} />
                    <ArrowUpRight size={15} className="text-muted transition group-hover:text-accent" />
                  </Link>
                </li>
              );
            })}
          </ol>
        )}
      </Panel>
    </div>
  );
}
