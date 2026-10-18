"use client";

import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  ChevronDown,
  Clock,
  Plane,
  ShieldCheck,
  Swords,
  Users,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { tool } from "@/lib/client";
import { byRule, type Legality, type Option, type Rival, type Seat, type Unavailable } from "@/lib/types";

import { Info } from "./info";
import { ReCrewed } from "./re-crewed";
import { clock, cx, day, Mono, rupees, Spinner } from "./ui";

const STRATEGY: Record<string, string> = {
  reserve_callout: "Reserve callout",
  day_off_callout: "Day-off callout",
  positioning: "Positioning",
  cancel: "Cancellation",
  command_upgrade: "command-qualified first officer",
  right_seat: "captain in the right seat",
  short_crew: "operate one short",
};

const RULE_NAME: Record<string, string> = {
  "RULE-FDP-01": "Duty period",
  "RULE-DUTY-02": "7-day duty",
  "RULE-FLT-03": "28-day flying",
  "RULE-REST-04": "Rest",
  "RULE-QUAL-05": "Type rating",
  "RULE-CERT-06": "Certificates",
  "RULE-BASE-07": "Base",
};

/** Every rule checked live for one person on one trip, with each rule's margin. */
export function LegalityCheck({
  crewId,
  pairingId,
  strategy,
  delay,
}: {
  crewId: string;
  pairingId: string;
  strategy: string;
  delay: number;
}) {
  const [legality, setLegality] = useState<Legality | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    tool<{ ok: boolean; result?: Legality; error?: string }>("crew_tool", {
      name: "check_legality",
      arguments: { crew_id: crewId, pairing_id: pairingId },
    }).then(
      (out) => (out.ok && out.result ? setLegality(out.result) : setError(out.error ?? "The legality check failed.")),
      (err: Error) => setError(err.message),
    );
  }, [crewId, pairingId]);
  if (error) return <p className="text-xs text-critical">{error}</p>;
  if (!legality)
    return (
      <p className="flex items-center gap-2 text-xs text-muted">
        <Spinner className="h-3 w-3" /> Checking every rule against the live roster…
      </p>
    );
  return (
    <>
      {(strategy === "positioning" || delay > 0) && (
        <p className="mb-2 text-[11px] text-muted">
          Checked against the trip as rostered; the ranking already allows for the positioning flight or the later
          departure.
        </p>
      )}
      <p
        className={cx(
          "mb-2 flex items-center gap-1.5 text-xs font-semibold",
          legality.legal ? "text-emerald-700" : "text-critical",
        )}
      >
        {legality.legal ? <ShieldCheck size={14} /> : <XCircle size={14} />}
        {legality.legal ? "Legal now: every rule passes" : `Not legal now: ${legality.blocking.join(", ")}`}
      </p>
      <ul className="grid gap-1.5 sm:grid-cols-2">
        {byRule(legality.verdicts).map((v) => (
          <li
            key={v.rule_id}
            className={cx(
              "flex items-start gap-1.5 rounded-lg px-2 py-1.5 text-[11px]",
              v.status === "PASS" ? "bg-ok-soft/60 text-emerald-900" : "bg-critical-soft text-red-900",
            )}
            title={v.rule_id}
          >
            {v.status === "PASS" ? (
              <CheckCircle2 size={12} className="mt-0.5 shrink-0" />
            ) : (
              <XCircle size={12} className="mt-0.5 shrink-0" />
            )}
            <span>
              <span className="font-semibold">{RULE_NAME[v.rule_id] ?? v.rule_id}</span>{" "}
              {v.headroom !== null ? `${v.headroom}h margin · ` : ""}
              <span className="opacity-80">{v.detail}</span>
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}

/** Other open disruptions, possibly on other desks, that want the same person. */
function Rivals({ rivals }: { rivals: Rival[] }) {
  return (
    <span className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-amber-800">
      <Swords size={12} />
      Also wanted by
      {rivals.map((r) => (
        <Link
          key={`${r.disruption_id}-${r.pairing_id}-${r.kind}-${r.rank ?? r.plan_id ?? ""}`}
          href={`/console/${encodeURIComponent(r.disruption_id)}`}
          onClick={(e) => e.stopPropagation()}
          className="rounded bg-pending-soft px-1.5 py-0.5 font-mono hover:underline"
          title={r.kind === "plan" ? `In plan ${r.plan_id}, awaiting approval` : `Ranked #${r.rank} there`}
        >
          {r.disruption_id} · {r.desks.map((d) => d.desk_id).join("/")}
          {r.kind === "plan" ? " · in a plan" : ""}
        </Link>
      ))}
    </span>
  );
}

function OptionCard({
  option,
  best,
  pairingId,
  rivals,
  chosen = false,
  gone,
}: {
  option: Option;
  best: number;
  pairingId: string;
  rivals: Rival[];
  /** The option the committed plan used. */
  chosen?: boolean;
  /** Reported unavailable since the options were worked out. */
  gone?: Unavailable;
}) {
  const [open, setOpen] = useState(false);
  const extra = option.cost_inr - best;
  return (
    <li
      className={cx(
        "lift animate-rise rounded-xl border bg-surface",
        gone
          ? "border-red-200 bg-critical-soft/40"
          : chosen
            ? "border-emerald-300 bg-emerald-50/60 ring-2 ring-emerald-100"
            : option.rank === 1
              ? "border-sky-300 ring-1 ring-sky-100"
              : "border-line",
      )}
      style={{ animationDelay: `${option.rank * 50}ms` }}
    >
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-start gap-3 p-3 text-left"
      >
        <span
          className={cx(
            "grid h-7 w-7 shrink-0 place-items-center rounded-lg text-xs font-bold",
            option.rank === 1 ? "bg-accent text-white" : "bg-slate-100 text-slate-600",
          )}
        >
          {option.rank}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className={cx("font-semibold text-slate-900", gone && "text-red-800 line-through")}>
              {option.name}
            </span>
            {gone && (
              <span className="rounded-full bg-critical px-2 py-0.5 text-[10px] font-semibold text-white">
                reported {gone.reason ?? "unavailable"} {day(gone.reported_utc)} {clock(gone.reported_utc)}
              </span>
            )}
            {option.crew_id && <Mono className="text-sky-700">{option.crew_id}</Mono>}
            {option.crew_rank && <span className="text-xs text-muted">{option.crew_rank}</span>}
            {chosen && (
              <span className="rounded-full bg-emerald-600 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white">
                chosen
              </span>
            )}
          </span>
          <span className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
            <span>{STRATEGY[option.strategy] ?? option.strategy.replace(/_/g, " ")}</span>
            {option.delay_hours > 0 && (
              <span className="flex items-center gap-1 text-amber-700">
                <Clock size={12} /> departs ~{option.delay_hours}h late
              </span>
            )}
          </span>
          {rivals.length > 0 && <Rivals rivals={rivals} />}
        </span>
        <span className="text-right">
          <span className="block font-mono text-sm font-semibold">{rupees(option.cost_inr)}</span>
          <span className={cx("block text-[11px]", extra === 0 ? "text-emerald-600" : "text-muted")}>
            {extra !== 0 ? `+${rupees(extra)}` : option.rank === 1 ? "cheapest" : "same cost"}
          </span>
        </span>
        <ChevronDown size={16} className={cx("mt-1 shrink-0 text-muted transition", open && "rotate-180")} />
      </button>
      {open && (
        <div className="animate-rise border-t border-line px-3 pb-3 pt-2">
          {Object.keys(option.cost_breakdown).length > 0 && (
            <p className="mb-2 text-xs text-muted">
              {Object.entries(option.cost_breakdown)
                .map(([k, v]) => `${k.replace(/_/g, " ")} ${rupees(v)}`)
                .join(" · ")}
            </p>
          )}
          {option.crew_id && (
            <LegalityCheck
              crewId={option.crew_id}
              pairingId={pairingId}
              strategy={option.strategy}
              delay={option.delay_hours}
            />
          )}
        </div>
      )}
    </li>
  );
}

function CancelCard({ seat }: { seat: Seat }) {
  const flights = seat.impact?.uncovered_flights ?? [];
  return (
    <li className="lift animate-rise flex items-start gap-3 rounded-xl border border-red-200 bg-critical-soft/40 p-3">
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-critical text-white">
        <Ban size={14} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1 font-semibold text-red-900">
          Cancel instead <Info term="cancellation" />
        </span>
        <span className="mt-1 block text-xs text-red-800/80">
          {flights.length > 0
            ? `${flights.map((f) => f.split("-")[0]).join(", ")} would not fly`
            : "The affected flights would not fly"}
          {seat.impact?.passengers !== undefined ? `, ${seat.impact.passengers} passengers` : ""}
        </span>
      </span>
      <span className="text-right">
        <span className="block font-mono text-sm font-semibold text-red-900">{rupees(seat.cancellation_cost_inr)}</span>
        {seat.cancellation_multiple ? (
          <span className="block text-[11px] text-red-800/80">{seat.cancellation_multiple}× the best cover</span>
        ) : null}
      </span>
    </li>
  );
}

export function SeatOptions({
  seat,
  contention = {},
  chosen = null,
  unavailable = {},
}: {
  seat: Seat;
  contention?: Record<string, Rival[]>;
  unavailable?: Record<string, Unavailable>;
  /** Once decided, the crew id the committed plan used. Options then show as analysed
   * rather than being searched again against today's roster. */
  chosen?: string | null;
}) {
  const decided = chosen !== null;
  const options: Option[] = decided ? seat.options : (seat.live?.options ?? seat.options);
  const best = options[0]?.cost_inr ?? 0;
  const impact = seat.impact ?? {};
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Plane size={15} className="text-accent" />
          <Mono>{seat.pairing_id}</Mono> · {seat.role}
          {seat.crew_out && (
            <span className="font-normal text-muted">
              for <Mono>{seat.crew_out}</Mono>
            </span>
          )}
        </h3>
        <span className="flex items-center gap-3 text-xs text-muted">
          {impact.passengers !== undefined && (
            <span className="flex items-center gap-1">
              <Users size={12} /> {impact.passengers} pax
            </span>
          )}
          {seat.legal_options !== null && <span>{seat.legal_options} legal</span>}
        </span>
      </div>
      {(impact.uncovered_flights?.length ?? 0) > 0 && (
        <p className="flex flex-wrap items-center gap-1.5 text-xs text-red-800">
          <AlertTriangle size={12} /> Uncrewed:
          {impact.uncovered_flights!.map((f) => (
            <Mono key={f} className="rounded bg-critical-soft px-1.5 py-0.5">
              {f.split("-")[0]}
            </Mono>
          ))}
        </p>
      )}
      {seat.re_crewed ? (
        <ReCrewed seat={seat} />
      ) : seat.status === "options" ? (
        <ol className="flex flex-col gap-2">
          {options.map((o) => (
            <OptionCard
              key={`${o.rank}-${o.crew_id}`}
              option={o}
              best={best}
              pairingId={seat.pairing_id}
              rivals={decided ? [] : (o.crew_id && contention[o.crew_id]) || []}
              chosen={decided && o.crew_id === chosen}
              gone={!decided && o.crew_id ? unavailable[o.crew_id] : undefined}
            />
          ))}
          {decided && !options.some((o) => o.crew_id === chosen) && (
            <li className="rounded-xl border border-emerald-200 bg-emerald-50/60 px-3 py-2 text-sm text-emerald-900">
              Chosen: <Mono>{chosen}</Mono>, from beyond the top options shown here.
            </li>
          )}
          {seat.cancellation_cost_inr !== null && <CancelCard seat={seat} />}
        </ol>
      ) : (
        <p className="rounded-lg bg-critical-soft px-3 py-2 text-xs text-red-900">
          {seat.status === "already_started"
            ? "This trip has already started."
            : seat.status === "cancelled"
              ? "This trip is cancelled: nothing to re-crew."
              : "No legal cover."}{" "}
          {seat.note}
        </p>
      )}
    </div>
  );
}
