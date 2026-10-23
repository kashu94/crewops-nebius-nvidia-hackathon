"use client";

import {
  Activity,
  AlertOctagon,
  ArrowUpRight,
  BadgeCheck,
  CalendarRange,
  ClipboardCheck,
  Cpu,
  DoorClosed,
  Inbox,
  Plane,
  Radio,
  Users,
  Wrench,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useLiveListener } from "@/components/live";
import { Actions, chosenFor, CommittedPlan, DeskBanner, Recommendation } from "@/components/disruption";
import { AirlineBadge } from "@/components/airline";
import { HandlingBanner } from "@/components/handling";
import { useMe } from "@/components/me";
import { SeatOptions } from "@/components/solutions";
import { clock, cx, day, Empty, Mono, Notice, Panel, SeverityBadge, Spinner, Status } from "@/components/ui";
import { tool } from "@/lib/client";
import { describe, shownStatus, type Detail, type FeedItem, type QueueItem } from "@/lib/types";

const TYPE_ICON: Record<string, typeof Radio> = {
  crew_unavailable: AlertOctagon,
  certification_lapse: BadgeCheck,
  flight_delay: Activity,
  aircraft_technical: Wrench,
  station_disruption: Radio,
  gate_change: Plane,
  gate_closure: DoorClosed,
  multiple_crew_unavailable: Users,
  planned_leave: CalendarRange,
};

function Kpi({
  label,
  value,
  tone,
  icon: Icon,
}: {
  label: string;
  value: number | string;
  tone: "critical" | "pending" | "accent" | "ok";
  icon: typeof Radio;
}) {
  const tones = {
    critical: "text-critical border-red-100",
    pending: "text-pending border-amber-100",
    accent: "text-accent border-[#cfe0ef]",
    ok: "text-ok border-emerald-100",
  };
  // border rather than ring: rings draw outside the box and the workspace clips them at the
  // edges
  return (
    <div className={cx("flex items-center gap-3 rounded-lg border bg-surface p-4", tones[tone])}>
      <span className="grid h-10 w-10 place-items-center rounded-md bg-white">
        <Icon size={18} />
      </span>
      <span>
        <span className="block font-mono text-2xl font-semibold tabular-nums text-foreground">{value}</span>
        <span className="block text-xs font-medium text-muted">{label}</span>
      </span>
    </div>
  );
}

function Feed({
  items,
  selected,
  onDesk,
  onSelect,
}: {
  items: FeedItem[] | null;
  selected: string | null;
  onDesk: Set<string>;
  onSelect: (id: string) => void;
}) {
  if (!items)
    return (
      <div className="p-4 text-muted">
        <Spinner />
      </div>
    );
  if (items.length === 0) return <Empty icon={<Inbox size={22} />}>No disruptions reported.</Empty>;
  return (
    <ol className="relative flex flex-col">
      <span aria-hidden className="absolute bottom-3 left-[18px] top-3 w-px bg-line" />
      {items.map((d, i) => {
        const Icon = TYPE_ICON[d.report_type ?? ""] ?? Radio;
        const critical = d.status === "REPORTED" || d.status === "OPTIONS_READY";
        return (
          <li key={d.disruption_id} className="" style={{ animationDelay: `${Math.min(i, 10) * 30}ms` }}>
            <button
              onClick={() => onSelect(d.disruption_id)}
              className={cx(
                "relative flex w-full gap-3 rounded-md px-2 py-2.5 text-left transition",
                selected === d.disruption_id ? "bg-accent-soft ring-1 ring-[#cfe0ef]" : "hover:bg-sunken",
              )}
            >
              <span
                className={cx(
                  "relative z-10 grid h-[22px] w-[22px] shrink-0 place-items-center rounded-full ring-4 ring-white",
                  critical
                    ? "bg-critical text-white"
                    : d.status === "SUBMITTED"
                      ? "bg-pending text-white"
                      : "bg-slate-300 text-white",
                )}
              >
                <Icon size={12} strokeWidth={2.5} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center justify-between gap-2">
                  <Mono className="text-[11px] text-muted">
                    {day(d.reported_utc)} {clock(d.reported_utc)}
                  </Mono>
                  <span className="flex items-center gap-1">
                    <SeverityBadge severity={d.severity} />
                    <Status value={shownStatus(d.status, d.handling)} />
                  </span>
                </span>
                <span className="mt-1 flex items-center gap-1.5 text-sm font-medium text-foreground">
                  {(d.airlines ?? []).map((a) => (
                    <AirlineBadge key={a} code={a} compact />
                  ))}
                  <span className="truncate">{describe(d)}</span>
                </span>
                <span className="mt-0.5 flex items-center gap-2 text-[11px] text-muted">
                  <Mono>{d.disruption_id}</Mono>
                  {onDesk.has(d.disruption_id) && (
                    <span className="rounded bg-pending-soft px-1.5 font-semibold text-amber-800">on your desk</span>
                  )}
                </span>
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function Solutions({ id, onChanged }: { id: string; onChanged: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    tool<Detail>("show_disruption", { disruption_id: id }).then(
      (d) => {
        setDetail(d);
        setError(null);
      },
      (err: Error) => setError(err.message),
    );
  }, [id]);

  useEffect(load, [load]); // keyed by id in the parent, so each disruption starts fresh
  useLiveListener(useCallback((e) => e.disruption_id === id && load(), [id, load]));

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!detail)
    return (
      <div className="text-muted">
        <Spinner />
      </div>
    );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Mono className="text-base font-semibold">{detail.disruption_id}</Mono>
        <SeverityBadge severity={detail.severity} />
        <Status value={shownStatus(detail.status, detail.handling?.kind)} />
        <Link
          href={`/console/${encodeURIComponent(id)}`}
          className="ml-auto flex items-center gap-1 text-xs text-accent hover:underline"
        >
          Full record <ArrowUpRight size={12} />
        </Link>
      </div>
      <DeskBanner detail={detail} />
      <HandlingBanner handling={detail.handling} />
      <CommittedPlan detail={detail} />
      {detail.seats.length === 0 ? (
        <p className="text-sm leading-relaxed text-foreground">
          {detail.summary || "No seats to cover for this disruption."}
        </p>
      ) : (
        detail.seats.map((s) => (
          <div key={`${s.pairing_id}-${s.role}-${s.crew_out}`} className="flex flex-col gap-3">
            {detail.status !== "COMMITTED" && <Recommendation seat={s} />}
            <SeatOptions
              seat={s}
              contention={detail.contention}
              chosen={chosenFor(detail, s)}
              unavailable={detail.unavailable_now ?? {}}
            />
          </div>
        ))
      )}
      <div className="rounded-md border border-line bg-sunken/60 p-3">
        <Actions
          detail={detail}
          onChanged={() => {
            load();
            onChanged();
          }}
        />
      </div>
    </div>
  );
}

export default function ControllerWorkspace() {
  const me = useMe();
  const [feed, setFeed] = useState<FeedItem[] | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    tool<FeedItem[]>("disruptions", { limit: 40 }).then(
      (items) => {
        setFeed(items);
        setError(null);
        setSelected(
          (s) => s ?? items.find((d) => d.status === "OPTIONS_READY")?.disruption_id ?? items[0]?.disruption_id ?? null,
        );
      },
      (err: Error) => setError(err.message),
    );
    tool<QueueItem[]>("my_queue").then(setQueue, () => undefined);
  }, []);

  useEffect(load, [load]);
  useLiveListener(load);

  const onDesk = useMemo(() => new Set(queue.map((q) => q.disruption_id)), [queue]);
  // a rejected plan leaves the disruption both ready and sent back, so don't count it twice
  const waiting = onDesk.size;
  const open = feed?.filter((d) => ["REPORTED", "OPTIONS_READY", "SUBMITTED"].includes(d.status)) ?? [];
  const awaiting = feed?.filter((d) => d.status === "SUBMITTED").length ?? 0;
  const resolved = feed?.filter((d) => ["COMMITTED", "CLOSED"].includes(d.status)).length ?? 0;
  const readyAll = feed?.filter((d) => d.status === "OPTIONS_READY").length ?? 0;
  const isController = me.roles.includes("controller");
  const ready = queue.filter((q) => q.kind === "ready_for_plan" || q.kind === "to_resolve").length;
  const sentBack = queue.filter((q) => q.kind.startsWith("sent_back")).length;

  return (
    // wide screens: workspace fills the window and only the panels scroll
    <div className="flex flex-col gap-4 xl:h-[calc(100vh-6.75rem)] xl:overflow-hidden">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Crew control</h1>
          <p className="text-sm text-muted">
            Disruptions as they arrive and legal cover ranked by cost. Ask Alexa+ from the button in the corner.
          </p>
        </div>
        {isController && waiting > 0 && (
          <span className="flex items-center gap-1.5 rounded-full bg-pending-soft px-3 py-1 text-xs font-semibold text-amber-800">
            <ClipboardCheck size={13} /> {waiting} waiting for you
          </span>
        )}
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Open disruptions" value={feed ? open.length : "–"} tone="critical" icon={AlertOctagon} />
        {isController ? (
          <>
            <Kpi label="Waiting on your desk" value={ready} tone="accent" icon={Inbox} />
            <Kpi label="Sent back to you" value={sentBack} tone="pending" icon={ClipboardCheck} />
          </>
        ) : (
          <>
            <Kpi label="Ready for a plan" value={feed ? readyAll : "–"} tone="accent" icon={Inbox} />
            <Kpi label="Awaiting approval" value={feed ? awaiting : "–"} tone="pending" icon={ClipboardCheck} />
          </>
        )}
        <Kpi label="Resolved" value={feed ? resolved : "–"} tone="ok" icon={BadgeCheck} />
      </div>
      <div className="grid gap-4 xl:min-h-0 xl:flex-1 xl:grid-cols-[minmax(320px,2fr)_minmax(460px,3fr)]">
        <Panel
          title="Ingestion feed"
          icon={<Radio size={14} className="text-critical" />}
          className="xl:h-full xl:min-h-0"
          bodyClassName="overflow-y-auto p-2"
        >
          <Feed items={feed} selected={selected} onDesk={onDesk} onSelect={setSelected} />
        </Panel>
        <Panel
          title="Solution engine"
          icon={<Cpu size={14} className="text-accent" />}
          className="xl:h-full xl:min-h-0"
          bodyClassName="overflow-y-auto"
        >
          {selected ? (
            <Solutions key={selected} id={selected} onChanged={load} />
          ) : (
            <Empty icon={<Cpu size={22} />}>Select a disruption to see legal cover, ranked by cost.</Empty>
          )}
        </Panel>
      </div>
    </div>
  );
}
