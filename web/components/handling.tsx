"use client";

import {
  ArrowRight,
  Bus,
  CheckCircle2,
  DoorOpen,
  Mail,
  Megaphone,
  Monitor,
  Plane,
  Radio,
  ShieldAlert,
  Smartphone,
  UserRound,
  Users,
  Wrench,
} from "lucide-react";

import { Info } from "./info";
import { clock, cx, day, Mono, Panel } from "./ui";
import type { Detail, FlightRow, Handling, NextAtGate, StandMove, TeamNotice } from "@/lib/types";

const TEAM_ICON: Record<string, typeof Mail> = {
  gate_control: DoorOpen,
  station_ops: Users,
  flight_info: Monitor,
  ops_monitoring: Radio,
  maintenance_control: Wrench,
  crew_records: ShieldAlert,
  customer_care: Smartphone,
  crew: UserRound,
};

const KIND: Record<Handling["kind"], { label: string; tone: string; icon: typeof Mail }> = {
  internal: {
    label: "Handled internally",
    tone: "border-emerald-200 bg-emerald-50/70 text-emerald-900",
    icon: CheckCircle2,
  },
  recrew: { label: "Re-crew needed", tone: "border-[#cfe0ef] bg-accent-soft/70 text-[#173f63]", icon: Users },
  decision: { label: "Decision needed", tone: "border-amber-200 bg-amber-50/70 text-amber-900", icon: ShieldAlert },
  recorded: { label: "Recorded", tone: "border-line bg-sunken text-[#353736]", icon: CheckCircle2 },
};

/** How this disruption gets resolved, in one sentence, at the top of the record. */
export function HandlingBanner({ handling }: { handling?: Handling | null }) {
  if (!handling) return null;
  const k = KIND[handling.kind] ?? KIND.internal;
  const Icon = k.icon;
  return (
    <div className={cx("flex items-start gap-3 rounded-lg border px-4 py-3", k.tone)}>
      <Icon size={18} className="mt-0.5 shrink-0" />
      <div className="min-w-0">
        <p className="flex items-center gap-1 text-xs font-semibold">
          {k.label} <Info term="handling" />
        </p>
        <p className="mt-0.5 text-sm leading-relaxed">{handling.text}</p>
      </div>
    </div>
  );
}

function minutes(m: number): string {
  return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${m} min`;
}

/** Departures that moved gate, and those that board by bus from a remote stand. */
function StandTable({ stands }: { stands: StandMove[] }) {
  if (stands.length === 0) return null;
  const booked = stands.reduce((n, m) => n + (m.booked ?? 0), 0);
  return (
    <Panel
      title={
        <span className="flex items-center gap-1.5">
          Stand reallocation <Info term="remote_stand" />
        </span>
      }
      icon={<DoorOpen size={14} className="text-accent" />}
      action={<span className="text-xs text-muted">{booked.toLocaleString("en-IN")} booked passengers to move</span>}
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-sm">
          <thead>
            <tr className="text-left text-[11px] text-muted">
              <th className="pb-2 font-medium">Flight</th>
              <th className="pb-2 font-medium">Gate</th>
              <th className="pb-2 font-medium">Boards from</th>
              <th className="pb-2 font-medium">Departs</th>
              <th className="pb-2 text-right font-medium">Booked</th>
              <th className="pb-2 pl-6 font-medium">Why</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {stands.map((m) => {
              const remote = m.to_gate === "remote stand";
              return (
                <tr key={m.flight_id}>
                  <td className="py-2 font-semibold">
                    <Mono>{m.flight_no}</Mono>
                  </td>
                  <td className="py-2">
                    <span className="flex items-center gap-1.5">
                      <Mono className="text-muted line-through decoration-red-300">{m.from_gate}</Mono>
                      <ArrowRight size={12} className="text-muted" />
                      {remote ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-pending-soft px-2 py-0.5 text-xs font-semibold text-amber-800">
                          <Bus size={12} /> remote stand, +{m.delay_minutes} min
                        </span>
                      ) : (
                        <Mono className="rounded-md bg-ok-soft px-1.5 py-0.5 font-semibold text-emerald-800">
                          {m.to_gate}
                        </Mono>
                      )}
                      {m.emergency && (
                        <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-semibold text-[#173f63]">
                          emergency gate
                        </span>
                      )}
                    </span>
                  </td>
                  <td className="py-2">
                    <Mono>
                      {day(m.boarding_from_utc)} {clock(m.boarding_from_utc)}
                    </Mono>
                  </td>
                  <td className="py-2">
                    {remote ? (
                      <span className="flex items-center gap-1.5">
                        <Mono className="text-muted line-through">{clock(m.departs_utc)}</Mono>
                        <Mono className="font-semibold text-amber-800">
                          {clock(new Date(Date.parse(m.departs_utc) + m.delay_minutes * 60_000).toISOString())}
                        </Mono>
                      </span>
                    ) : (
                      <Mono>{clock(m.departs_utc)}</Mono>
                    )}
                  </td>
                  <td className="py-2 text-right font-mono tabular-nums">{m.booked ?? "—"}</td>
                  <td className="py-2 pl-6 text-xs text-muted">
                    {m.why}
                    {(m.alternatives ?? []).length > 0 && (
                      <span className="mt-0.5 block text-[11px]">
                        Also free: <Mono>{(m.alternatives ?? []).join(", ")}</Mono>
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

/** The flights a disruption touches, with new times for the late ones and who is booked. */
function FlightTable({ flights, next }: { flights: FlightRow[]; next: NextAtGate[] }) {
  if (flights.length === 0 && next.length === 0) return null;
  const late = flights.filter((f) => f.late_minutes > 0);
  const booked = flights.reduce((n, f) => n + (f.booked ?? 0), 0);
  return (
    <Panel
      title="Flights affected"
      icon={<Plane size={14} className="text-accent" />}
      action={
        <span className="text-xs text-muted">
          {flights.length} flight{flights.length === 1 ? "" : "s"}
          {late.length > 0 && <>, {late.length} late</>}, {booked.toLocaleString("en-IN")} booked
        </span>
      }
    >
      {flights.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-sm">
            <thead>
              <tr className="text-left text-[11px] text-muted">
                <th className="pb-2 font-medium">Flight</th>
                <th className="pb-2 font-medium">Route</th>
                <th className="pb-2 font-medium">Departs</th>
                <th className="pb-2 font-medium">Gate</th>
                <th className="pb-2 text-right font-medium">Booked / seats</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {flights.map((f) => (
                <tr key={f.flight_id}>
                  <td className="py-2 font-semibold">
                    <Mono>{f.flight_no}</Mono> <span className="text-xs font-normal text-muted">{day(f.dep_utc)}</span>
                  </td>
                  <td className="py-2">
                    <Mono>
                      {f.from} → {f.to}
                    </Mono>
                  </td>
                  <td className="py-2">
                    {f.new_dep_utc ? (
                      <span className="flex flex-wrap items-center gap-1.5">
                        <Mono className="text-muted line-through">{clock(f.dep_utc)}</Mono>
                        <Mono className="font-semibold text-amber-800">{clock(f.new_dep_utc)}</Mono>
                        <span className="rounded-full bg-pending-soft px-1.5 text-[11px] font-semibold text-amber-800">
                          +{minutes(f.late_minutes)}
                        </span>
                      </span>
                    ) : (
                      <Mono>{clock(f.dep_utc)}</Mono>
                    )}
                  </td>
                  <td className="py-2">
                    <Mono>{f.gate ?? "—"}</Mono>
                  </td>
                  <td className="py-2 text-right font-mono tabular-nums">
                    {f.booked ?? "—"} <span className="text-muted">/ {f.seats}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {next.length > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <h3 className="mb-2 flex items-center gap-1 text-xs font-semibold text-muted">
            Next at the same gate <Info term="next_at_gate" />
          </h3>
          <ul className="flex flex-col gap-1.5 text-sm">
            {next.map((n) => (
              <li key={n.flight_id} className="flex flex-wrap items-center gap-2">
                <Mono className="font-semibold">{n.flight_no}</Mono>
                <span className="text-muted">at</span> <Mono>{n.gate}</Mono>
                <span className="text-muted">boards {clock(n.boarding_from_utc)}</span>
                {n.buffer_minutes >= 0 ? (
                  <span className="rounded-full bg-ok-soft px-2 py-0.5 text-xs font-semibold text-emerald-800">
                    gate free {minutes(n.buffer_minutes)} before
                  </span>
                ) : (
                  <span className="rounded-full bg-critical-soft px-2 py-0.5 text-xs font-semibold text-red-800">
                    clash by {minutes(-n.buffer_minutes)}: re-gated
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

/** Everyone told besides crew control: the team, the person on duty, why and what. */
export function NoticeList({ notices }: { notices: TeamNotice[] }) {
  if (notices.length === 0) return null;
  const people = new Set(notices.map((n) => `${n.team}/${n.scope}`)).size;
  return (
    <Panel
      title="Who has been told"
      icon={<Megaphone size={14} className="text-accent" />}
      action={
        <span className="text-xs text-muted">
          {people} {people === 1 ? "person" : "people"}, besides crew control
        </span>
      }
    >
      <ul className="flex flex-col gap-2.5">
        {notices.map((n, i) => {
          const Icon = TEAM_ICON[n.team] ?? Mail;
          return (
            <li key={`${n.team}-${n.scope}-${i}`} className="rounded-md border border-line bg-surface px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="grid h-7 w-7 place-items-center rounded-lg bg-accent-soft text-[#173f63]">
                  <Icon size={14} />
                </span>
                <span className="text-sm font-semibold">{n.team_title}</span>
                <Mono className="rounded bg-sunken px-1.5 text-[11px] text-muted">
                  {n.scope === "NETWORK" ? "network" : n.scope}
                </Mono>
                {n.contact && (
                  <span className="text-xs text-muted">
                    {n.contact}
                    {n.contact_title && <>, {n.contact_title}</>}
                  </span>
                )}
                <span
                  className={cx(
                    "ml-auto inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold",
                    n.channel === "in_app" ? "bg-accent-soft text-[#173f63]" : "bg-sunken text-muted",
                  )}
                  title={
                    n.channel === "in_app" ? "Shown on their CrewOps page" : "Emailed to the team's alert addresses"
                  }
                >
                  {n.channel === "in_app" ? <Smartphone size={11} /> : <Mail size={11} />}
                  {n.channel === "in_app" ? "in CrewOps" : "email"}
                </span>
              </div>
              <p className="mt-1.5 text-xs font-medium text-foreground">To {n.why}.</p>
              <p className="mt-0.5 text-xs leading-relaxed text-muted">{n.message}</p>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-[11px] text-muted">
        Emails go to the addresses an administrator has subscribed to each team&apos;s alerts. They name flights, gates
        and times, never a crew member.
      </p>
    </Panel>
  );
}

/** The record's handling section: what is done beyond re-crewing, and who knows. */
export function HandlingDetail({ detail }: { detail: Detail }) {
  return (
    <>
      <StandTable stands={detail.stands ?? []} />
      <FlightTable flights={detail.flights ?? []} next={detail.next_at_gate ?? []} />
    </>
  );
}
