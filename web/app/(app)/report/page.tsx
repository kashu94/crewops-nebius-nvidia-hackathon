"use client";

import {
  Activity,
  AlertOctagon,
  BadgeCheck,
  Building2,
  ChevronRight,
  DoorClosed,
  DoorOpen,
  Inbox,
  Plus,
  RefreshCw,
  Send,
  TowerControl,
  Users,
  Wrench,
  X,
} from "lucide-react";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";

import { AIRLINES, AirlineBadge, airlineOf } from "@/components/airline";
import { Modal } from "@/components/modal";
import { Button, clock, cx, day, Empty, Mono, Notice, Panel, Spinner, Status, untilFrom } from "@/components/ui";
import { tool } from "@/lib/client";
import { describe, shownStatus, type MyReport, type Reference } from "@/lib/types";

type Kind =
  | "crew_unavailable"
  | "multiple_crew_unavailable"
  | "certification_lapse"
  | "flight_delay"
  | "gate_closure"
  | "station_disruption"
  | "gate_change"
  | "aircraft_technical";

const KINDS: { kind: Kind; title: string; hint: string; icon: typeof Users }[] = [
  { kind: "crew_unavailable", title: "Sick crew", hint: "One crew member can't fly", icon: AlertOctagon },
  {
    kind: "multiple_crew_unavailable",
    title: "Multiple sick crew",
    hint: "Several out together, covered as one",
    icon: Users,
  },
  {
    kind: "certification_lapse",
    title: "Certification expiry",
    hint: "A licence, medical or training lapses",
    icon: BadgeCheck,
  },
  { kind: "flight_delay", title: "Delay", hint: "A departure leaves late", icon: Activity },
  { kind: "gate_closure", title: "Gate closure", hint: "A gate is out of use for a while", icon: DoorClosed },
  {
    kind: "station_disruption",
    title: "Station closure",
    hint: "An airport closes (weather, runway)",
    icon: TowerControl,
  },
  { kind: "gate_change", title: "Gate change", hint: "Move one departure to another gate", icon: DoorOpen },
  { kind: "aircraft_technical", title: "Aircraft fault", hint: "An aircraft is unavailable", icon: Wrench },
];

const REASONS = ["sick", "fatigue", "personal", "other"];

const input =
  "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none transition focus:border-accent focus:ring-4 focus:ring-[#cfe0ef]";

function Label({ text, hint, children }: { text: string; hint?: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="font-medium text-foreground">{text}</span>
      {children}
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </label>
  );
}

/** datetime-local has no zone; the report API wants UTC. */
const utc = (v: FormDataEntryValue | null) => (v ? `${String(v)}:00Z` : "");
const text = (v: FormDataEntryValue | null) => String(v ?? "").trim();

function CrewChips({
  reference,
  value,
  onChange,
}: {
  reference: Reference;
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const [typed, setTyped] = useState("");
  const add = () => {
    const id = typed.trim().toUpperCase();
    if (id && !value.includes(id) && value.length < 12) onChange([...value, id]);
    setTyped("");
  };
  const name = (id: string) => reference.crew.find((c) => c.crew_id === id);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-2">
        <input
          list="crew-list"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          placeholder="C-1042"
          className={input}
        />
        <Button type="button" tone="secondary" onClick={add} disabled={!typed.trim()}>
          <Plus size={14} /> Add
        </Button>
      </div>
      {value.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {value.map((id) => (
            <li
              key={id}
              className="inline-flex items-center gap-1.5 rounded-full bg-accent-soft py-1 pl-2.5 pr-1 text-xs text-[#173f63]"
            >
              <Mono className="font-semibold">{id}</Mono>
              {name(id) && (
                <span className="text-[#173f63]">
                  {name(id)!.name}, {name(id)!.rank}
                </span>
              )}
              <button
                type="button"
                aria-label={`Remove ${id}`}
                onClick={() => onChange(value.filter((x) => x !== id))}
                className="rounded-full p-0.5 hover:bg-white"
              >
                <X size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Prefill from an Operations board link, e.g. `/report?type=…&crew_id=…`. */
type Prefill = { crew_id?: string; from_date?: string; flight_id?: string };

function Fields({
  kind,
  reference,
  crewIds,
  setCrewIds,
  prefill = {},
}: {
  kind: Kind;
  reference: Reference;
  crewIds: string[];
  setCrewIds: (v: string[]) => void;
  prefill?: Prefill;
}) {
  const first = reference.week[0] ?? "";
  const last = reference.week[reference.week.length - 1] ?? "";
  const dates = (
    <div className="grid grid-cols-2 gap-3">
      <Label text="From">
        <input
          name="from_date"
          type="date"
          required
          min={first}
          max={last}
          defaultValue={prefill.from_date ?? first}
          onChange={(e) => untilFrom(e.currentTarget, "to_date")}
          className={input}
        />
      </Label>
      <Label text="Until" hint="Optional: the same day if empty">
        <input name="to_date" type="date" min={prefill.from_date ?? first} max={last} className={input} />
      </Label>
    </div>
  );
  const reason = (
    <fieldset className="flex flex-col gap-1.5 text-sm">
      <legend className="mb-1 font-medium text-foreground">Reason</legend>
      <div className="grid grid-cols-4 gap-2">
        {REASONS.map((r, i) => (
          <label key={r} className="cursor-pointer">
            <input type="radio" name="reason" value={r} defaultChecked={i === 0} className="peer sr-only" />
            <span className="block rounded-lg border border-line px-2 py-2 text-center text-xs capitalize transition peer-checked:border-accent peer-checked:bg-accent-soft peer-checked:font-semibold">
              {r}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
  const span = (
    <div className="grid grid-cols-2 gap-3">
      <Label text="From (UTC)">
        <input
          name="from_utc"
          type="datetime-local"
          required
          min={`${first}T00:00`}
          max={`${last}T23:59`}
          onChange={(e) => untilFrom(e.currentTarget, "to_utc")}
          className={input}
        />
      </Label>
      <Label text="Until (UTC)">
        <input
          name="to_utc"
          type="datetime-local"
          required
          min={`${first}T00:00`}
          max={`${last}T23:59`}
          className={input}
        />
      </Label>
    </div>
  );
  switch (kind) {
    case "crew_unavailable":
      return (
        <>
          <Label text="Crew member">
            <input
              name="crew_id"
              list="crew-list"
              required
              placeholder="C-1042"
              defaultValue={prefill.crew_id}
              className={input}
            />
          </Label>
          {dates}
          <Label
            text="Unavailable from (UTC, optional)"
            hint="Taken ill part-way through a day: flights before this time are still flown by them"
          >
            <input name="from_time" type="time" className={input} />
          </Label>
          {reason}
        </>
      );
    case "multiple_crew_unavailable":
      return (
        <>
          <Label
            text="Crew members"
            hint="Two to twelve. They are covered together, so none is offered to cover another."
          >
            <CrewChips reference={reference} value={crewIds} onChange={setCrewIds} />
          </Label>
          {dates}
          {reason}
        </>
      );
    case "certification_lapse":
      return (
        <>
          <Label text="Crew member">
            <input name="crew_id" list="crew-list" required placeholder="C-5417" className={input} />
          </Label>
          <div className="grid grid-cols-2 gap-3">
            <Label text="Certificate">
              <select name="cert_type" required className={input}>
                {reference.cert_types.map((c) => (
                  <option key={c} value={c}>
                    {c.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </Label>
            <Label text="Valid until">
              <input name="valid_to" type="date" required min={first} max={last} className={input} />
            </Label>
          </div>
          <p className="text-xs text-muted">
            This reports the date the certificate stops being valid (failed medical, missed training). It never edits
            the crew member&apos;s records: crew control takes them off trips after that date, the crew member sees a
            notice on their page, and training and crew records are emailed to renew it.
          </p>
        </>
      );
    case "flight_delay":
      return (
        <>
          <Label text="Flight" hint="Number and date, e.g. KS412-2026-10-13">
            <input
              name="flight_id"
              list="flight-list"
              required
              placeholder="KS412-2026-10-13"
              defaultValue={prefill.flight_id}
              className={input}
            />
          </Label>
          <Label text="Delay (minutes)">
            <input name="delay_minutes" type="number" min={1} max={1440} required defaultValue={60} className={input} />
          </Label>
          <p className="text-xs text-muted">
            Flight information, operations monitoring (who tells the captain), the station, gate control and the next
            departure at the same gate are told.
          </p>
        </>
      );
    case "gate_closure":
      return (
        <>
          <Label text="Gate">
            <select name="gate" required className={input}>
              {reference.gates.map((g) => (
                <option key={g}>{g}</option>
              ))}
            </select>
          </Label>
          {span}
          <Label text="Reason">
            <input name="reason" required maxLength={200} placeholder="Jet bridge fault" className={input} />
          </Label>
          <fieldset className="flex flex-col gap-1.5 text-sm">
            <legend className="mb-1 font-medium text-foreground">Cause</legend>
            <div className="grid grid-cols-2 gap-2">
              {[
                ["internal", "Internal", "Equipment or staff at the gate: the station fixes it"],
                ["external", "External", "Weather or a natural event: more gates may close"],
              ].map(([value, label, hint], i) => (
                <label key={value} className="cursor-pointer">
                  <input type="radio" name="cause" value={value} defaultChecked={i === 0} className="peer sr-only" />
                  <span className="block rounded-lg border border-line px-3 py-2 text-xs transition peer-checked:border-accent peer-checked:bg-accent-soft">
                    <b className="block">{label}</b>
                    <span className="text-muted">{hint}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        </>
      );
    case "station_disruption":
      return (
        <>
          <Label text="Station">
            <select name="station" required className={input}>
              {reference.stations.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </Label>
          {span}
          <Label text="Reason">
            <input name="reason" required maxLength={200} placeholder="Fog below landing minima" className={input} />
          </Label>
        </>
      );
    case "gate_change":
      return <GateChange reference={reference} />;
    case "aircraft_technical":
      return (
        <>
          <Label text="Aircraft">
            <select name="aircraft" required className={input}>
              {reference.aircraft.map((a) => (
                <option key={a}>{a}</option>
              ))}
            </select>
          </Label>
          {span}
        </>
      );
  }
}

/** A gate change offers only the other gates at the flight's departure station: never the
 * gate it already boards at (the server refuses that too). */
function GateChange({ reference }: { reference: Reference }) {
  const [flightId, setFlightId] = useState("");
  const flight = reference.flights.find((f) => f.flight_id === flightId.trim().toUpperCase());
  const gates = reference.gates.filter((g) => !flight || (g.startsWith(`${flight.from}-`) && g !== flight.gate));
  return (
    <>
      <Label text="Flight" hint={flight?.gate ? `Boards at ${flight.gate} now` : undefined}>
        <input
          name="flight_id"
          list="flight-list"
          required
          placeholder="DX401-2026-10-13"
          value={flightId}
          onChange={(e) => setFlightId(e.target.value)}
          className={input}
        />
      </Label>
      <Label text="New gate">
        <select name="new_gate" required className={input} key={flight?.flight_id ?? "any"}>
          {gates.map((g) => (
            <option key={g}>{g}</option>
          ))}
        </select>
      </Label>
    </>
  );
}

function detailsOf(kind: Kind, f: FormData, crewIds: string[]): Record<string, unknown> {
  const dates = () => ({
    from_date: text(f.get("from_date")),
    to_date: text(f.get("to_date")) || text(f.get("from_date")),
  });
  switch (kind) {
    case "crew_unavailable":
      return {
        crew_id: text(f.get("crew_id")).toUpperCase(),
        ...dates(),
        reason: text(f.get("reason")),
        // fell ill mid-day; earlier legs still count as flown
        ...(text(f.get("from_time"))
          ? { from_utc: `${text(f.get("from_date"))}T${text(f.get("from_time"))}:00Z` }
          : {}),
      };
    case "multiple_crew_unavailable":
      return { crew_ids: crewIds, ...dates(), reason: text(f.get("reason")) };
    case "certification_lapse":
      return {
        crew_id: text(f.get("crew_id")).toUpperCase(),
        cert_type: text(f.get("cert_type")),
        valid_to: text(f.get("valid_to")),
      };
    case "flight_delay":
      return { flight_id: text(f.get("flight_id")).toUpperCase(), delay_minutes: Number(f.get("delay_minutes")) };
    case "gate_closure":
      return {
        gate: text(f.get("gate")),
        from_utc: utc(f.get("from_utc")),
        to_utc: utc(f.get("to_utc")),
        reason: text(f.get("reason")),
        cause: text(f.get("cause")) || "internal",
      };
    case "station_disruption":
      return {
        station: text(f.get("station")),
        from_utc: utc(f.get("from_utc")),
        to_utc: utc(f.get("to_utc")),
        reason: text(f.get("reason")),
      };
    case "gate_change":
      return { flight_id: text(f.get("flight_id")).toUpperCase(), new_gate: text(f.get("new_gate")) };
    case "aircraft_technical":
      return { aircraft: text(f.get("aircraft")), from_utc: utc(f.get("from_utc")), to_utc: utc(f.get("to_utc")) };
  }
}

/** The controller desk a report went to, or why it hasn't gone to one yet. */
function deskLine(r: MyReport): string {
  if (r.status === "REJECTED") return "Not sent to crew control: it was refused";
  const desks = r.desks ?? [];
  if (desks.length > 0) return desks.map((d) => `${d.controller} (${d.desk_id})`).join(", ");
  if (r.status === "QUEUED" || r.status === "REPORTED") return "Being assigned: within a minute of the analysis";
  return "No controller desk";
}

const DETAIL_LABEL: Record<string, string> = {
  crew_id: "Crew member",
  crew_ids: "Crew members",
  from_date: "From",
  to_date: "Until",
  from_utc: "Unavailable from (UTC)",
  to_utc: "Until (UTC)",
  reason: "Reason",
  flight_id: "Flight",
  delay_minutes: "Delay (minutes)",
  gate: "Gate",
  new_gate: "New gate",
  station: "Station",
  cert_type: "Certificate",
  valid_to: "Valid until",
  aircraft: "Aircraft",
  cause: "Cause",
};

function shown(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "string") return value.replace(/_/g, " ");
  return String(value);
}

function ReportDetail({ r, scope, onClose }: { r: MyReport; scope: "mine" | "crew"; onClose: () => void }) {
  const details = Object.entries(r.details ?? {}).filter(([, v]) => v !== null && v !== undefined && v !== "");
  return (
    <Modal
      onClose={onClose}
      title={
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <AirlineBadge compact code={airlineOf(String(r.details.flight_id ?? ""))} />
            <Status value={shownStatus(r.status, r.handled ? "internal" : null)} />
            <Mono className="text-xs text-muted">{r.disruption_id}</Mono>
          </div>
          <h2 className="text-base font-semibold leading-snug">{describe(r)}</h2>
        </div>
      }
    >
      <dl className="grid grid-cols-[minmax(0,10rem)_1fr] gap-x-4 gap-y-2 text-sm">
        <dt className="text-muted">Reported</dt>
        <dd>
          {day(r.reported_utc)} <Mono>{clock(r.reported_utc)}</Mono>
          {scope === "crew" && r.reported_by && <span className="text-muted"> · by {r.reported_by}</span>}
        </dd>
        <dt className="flex items-center gap-1 text-muted">
          <Building2 size={13} /> Controller desk
        </dt>
        <dd className={cx((r.desks ?? []).length > 0 ? "font-medium" : "text-muted")}>{deskLine(r)}</dd>
        {details.map(([k, v]) => (
          <Fragment key={k}>
            <dt className="text-muted">{DETAIL_LABEL[k] ?? shown(k)}</dt>
            <dd className={k.endsWith("_id") || k.endsWith("_ids") || k === "gate" ? "font-mono" : ""}>
              {k.endsWith("_utc") && typeof v === "string" ? `${day(v)} ${clock(v)}` : shown(v)}
            </dd>
          </Fragment>
        ))}
      </dl>
      {r.narrative && (
        <p className="mt-4 rounded-md bg-sunken px-3 py-2 text-sm italic text-foreground">“{r.narrative}”</p>
      )}
      <div
        className={cx(
          "mt-4 rounded-md px-3 py-2 text-sm",
          r.status === "REJECTED" ? "bg-critical-soft text-red-900" : "bg-accent-soft/60 text-[#173f63]",
        )}
      >
        {r.handled ?? r.outcome}
      </div>
      {r.remark && (
        <p className="mt-2 rounded-md bg-ok-soft px-3 py-2 text-sm text-emerald-900">Crew control: {r.remark}</p>
      )}
    </Modal>
  );
}

function MyReports({
  reports,
  onRefresh,
  scope,
  onScope,
}: {
  reports: MyReport[] | null;
  onRefresh: () => void;
  scope: "mine" | "crew";
  onScope: (s: "mine" | "crew") => void;
}) {
  const [open, setOpen] = useState<MyReport | null>(null);
  const close = useCallback(() => setOpen(null), []);
  return (
    <Panel
      title={
        <span className="flex items-center gap-1">
          {(["mine", "crew"] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => onScope(k)}
              className={cx(
                "rounded-md px-2 py-0.5 normal-case tracking-normal",
                scope === k ? "bg-accent-soft text-[#173f63]" : "text-muted hover:text-foreground",
              )}
            >
              {k === "mine" ? "My reports" : "From crew"}
            </button>
          ))}
        </span>
      }
      icon={<Inbox size={14} className="text-accent" />}
      action={
        <button
          onClick={onRefresh}
          className="rounded-md p-1 text-muted hover:bg-sunken hover:text-foreground"
          title="Refresh"
        >
          <RefreshCw size={14} />
        </button>
      }
    >
      {!reports ? (
        <Spinner />
      ) : reports.length === 0 ? (
        <Empty icon={<Inbox size={22} />}>Nothing reported yet.</Empty>
      ) : (
        <ol className="flex flex-col gap-2">
          {reports.map((r) => (
            <li key={r.disruption_id}>
              <button
                type="button"
                onClick={() => setOpen(r)}
                className="group w-full rounded-md border border-line px-3 py-2.5 text-left transition hover:border-[#cfe0ef] hover:bg-accent-soft/40"
                title="Show the whole report"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <AirlineBadge compact code={airlineOf(String(r.details.flight_id ?? ""))} />
                    <span className="truncate text-sm font-medium">{describe(r)}</span>
                  </span>
                  <Status value={shownStatus(r.status, r.handled ? "internal" : null)} />
                </div>
                <p className="mt-0.5 truncate text-[11px] text-muted">
                  <Mono>{r.disruption_id}</Mono> · {day(r.reported_utc)} <Mono>{clock(r.reported_utc)}</Mono>
                  {scope === "crew" && r.reported_by && <> · by {r.reported_by}</>}
                </p>
                <p className="mt-1 flex items-center gap-1 truncate text-xs text-muted">
                  <Building2 size={12} className="shrink-0" /> {deskLine(r)}
                </p>
                <span className="mt-1.5 flex items-center gap-0.5 text-[11px] font-medium text-accent opacity-80 group-hover:opacity-100">
                  View report <ChevronRight size={12} />
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
      {open && <ReportDetail r={open} scope={scope} onClose={close} />}
    </Panel>
  );
}

export default function ReportPage() {
  const [reference, setReference] = useState<Reference | null>(null);
  const [reports, setReports] = useState<MyReport[] | null>(null);
  const [kind, setKind] = useState<Kind>("crew_unavailable");
  const [crewIds, setCrewIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [scope, setScope] = useState<"mine" | "crew">("mine");
  const [prefill, setPrefill] = useState<Prefill>({});
  // coming from the Operations board, type and crew/flight are already picked
  useEffect(() => {
    const t = setTimeout(() => {
      const q = new URLSearchParams(window.location.search);
      const type = q.get("type");
      if (type && KINDS.some((k) => k.kind === type)) setKind(type as Kind);
      const pick = (name: string) => {
        const v = q.get(name);
        return v && /^[A-Za-z0-9-]{1,40}$/.test(v) ? v : undefined;
      };
      setPrefill({ crew_id: pick("crew_id"), from_date: pick("from_date"), flight_id: pick("flight_id") });
    });
    return () => clearTimeout(t);
  }, []);
  // Only the latest request may fill the list: switching tabs while one is in flight must
  // not let the slower, older reply show the other tab's reports.
  const latestLoad = useRef(0);
  const loadReports = useCallback(() => {
    const n = ++latestLoad.current;
    tool<MyReport[]>("my_reports", { scope }).then(
      (r) => latestLoad.current === n && setReports(r),
      () => latestLoad.current === n && setReports([]),
    );
  }, [scope]);

  useEffect(() => {
    tool<Reference>("report_reference").then(setReference, (err: Error) => setError(err.message));
    loadReports();
  }, [loadReports]);

  // analysis takes up to a minute, so keep polling while any report is pending
  const waiting = useMemo(
    () => (reports ?? []).some((r) => r.status === "REPORTED" || r.status === "QUEUED"),
    [reports],
  );
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(loadReports, 10_000);
    return () => clearInterval(t);
  }, [waiting, loadReports]);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    if (kind === "multiple_crew_unavailable" && crewIds.length < 2) {
      setMessage({ tone: "error", text: "Add at least two crew members." });
      return;
    }
    const f0 = new FormData(form);
    const toDate = text(f0.get("to_date"));
    if (toDate && toDate < text(f0.get("from_date"))) {
      setMessage({ tone: "error", text: "'Until' can't be before 'From'." });
      return;
    }
    if (f0.get("from_utc") && f0.get("to_utc") && text(f0.get("to_utc")) <= text(f0.get("from_utc"))) {
      setMessage({ tone: "error", text: "'Until' must be after 'From'." });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const f = new FormData(form);
      const out = await tool<{ report_id: string; note: string }>("report_disruption", {
        report_type: kind,
        details: detailsOf(kind, f, crewIds),
        narrative: text(f.get("narrative")),
      });
      setMessage({ tone: "ok", text: `Recorded as ${out.report_id}. ${out.note}` });
      form.reset();
      setCrewIds([]);
      setTimeout(loadReports, 1500);
    } catch (err) {
      setMessage({ tone: "error", text: err instanceof Error ? err.message : "Failed." });
    } finally {
      setBusy(false);
    }
  }

  if (error) return <Notice tone="error">{error}</Notice>;

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Record a disruption</h1>
        <p className="text-sm text-muted">
          CrewOps works out what it breaks, who must be told and how it gets resolved. Crew control takes it from there.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <Panel>
          {!reference ? (
            <Spinner />
          ) : (
            <form onSubmit={submit} className="flex flex-col gap-4">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" role="radiogroup" aria-label="Type">
                {KINDS.map(({ kind: k, title, hint, icon: Icon }) => (
                  <button
                    key={k}
                    type="button"
                    role="radio"
                    aria-checked={kind === k}
                    onClick={() => {
                      setKind(k);
                      setMessage(null);
                    }}
                    className={cx(
                      "lift flex flex-col items-start gap-1 rounded-md border p-3 text-left transition",
                      kind === k ? "border-accent bg-accent-soft ring-1 ring-[#cfe0ef]" : "border-line bg-surface",
                    )}
                  >
                    <Icon size={16} className={kind === k ? "text-accent" : "text-muted"} />
                    <span className="text-sm font-semibold">{title}</span>
                    <span className="text-[11px] leading-snug text-muted">{hint}</span>
                  </button>
                ))}
              </div>

              <div key={`${kind}:${JSON.stringify(prefill)}`} className="flex flex-col gap-3">
                <Fields kind={kind} reference={reference} crewIds={crewIds} setCrewIds={setCrewIds} prefill={prefill} />
                <Label text="Anything crew controller should know" hint="Optional">
                  <input name="narrative" maxLength={500} className={input} />
                </Label>
              </div>

              {message && <Notice tone={message.tone}>{message.text}</Notice>}
              <Button type="submit" tone="accent" disabled={busy} className="self-start">
                {busy ? <Spinner /> : <Send size={14} />} Record disruption
              </Button>

              <datalist id="crew-list">
                {reference.crew.map((c) => (
                  <option key={c.crew_id} value={c.crew_id}>
                    {c.name}, {c.rank}, {c.base}
                    {c.airline ? `, ${AIRLINES[c.airline]?.name ?? c.airline}` : ""}
                  </option>
                ))}
              </datalist>
              <datalist id="flight-list">
                {reference.flights.map((f) => (
                  <option key={f.flight_id} value={f.flight_id}>
                    {f.from}→{f.to} {f.dep_utc.slice(11, 16)}Z{f.gate ? ` · ${f.gate}` : ""}
                  </option>
                ))}
              </datalist>
            </form>
          )}
        </Panel>
        <MyReports
          reports={reports}
          onRefresh={loadReports}
          scope={scope}
          onScope={(k) => {
            setReports(null);
            setScope(k);
          }}
        />
      </div>
    </div>
  );
}
