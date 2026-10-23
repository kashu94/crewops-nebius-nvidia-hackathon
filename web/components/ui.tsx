import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

type Tone = "primary" | "secondary" | "danger" | "ghost" | "accent";

export function Button({
  tone = "primary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: Tone }) {
  const tones: Record<Tone, string> = {
    primary: "bg-brand text-brand-ink hover:bg-[#2a2c2f]",
    accent: "bg-accent text-white hover:bg-[#184d7d]",
    secondary: "bg-surface text-foreground border border-control hover:bg-sunken",
    danger: "bg-critical text-white hover:bg-[#a51e23]",
    ghost: "text-muted hover:text-foreground hover:bg-sunken",
  };
  return (
    <button
      className={cx(
        "inline-flex min-h-8 items-center justify-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium",
        "transition-colors disabled:cursor-not-allowed disabled:opacity-60",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}

export function Panel({
  title,
  icon,
  action,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={cx("flex min-h-0 flex-col rounded-lg border border-line bg-surface", className)}>
      {title && (
        <header className="flex min-h-12 items-center justify-between gap-3 border-b border-rule px-5 py-2.5">
          <h2 className="flex items-center gap-2 text-[15px] font-semibold text-foreground [&_svg]:text-muted">
            {icon}
            {title}
          </h2>
          {action}
        </header>
      )}
      <div className={cx("min-h-0 flex-1 p-5", bodyClassName)}>{children}</div>
    </section>
  );
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={cx("rounded-lg border border-line bg-surface p-5", className)}>{children}</section>;
}

export function Field({
  label,
  hint,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="font-medium text-foreground">{label}</span>
      <input
        className="h-10 rounded-md border border-control bg-surface px-3 outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/20"
        {...props}
      />
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </label>
  );
}

// Pale tint with dark ink of the same hue. Red only where someone must act now, amber where a
// decision waits, green once it is done; everything settled is neutral.
const NEGATIVE = "bg-critical-soft text-[#831c1e] ring-[#f5c2bd]";
const WAITING = "bg-pending-soft text-[#92400e] ring-[#fde68a]";
const READY = "bg-accent-soft text-[#173f63] ring-[#cfe0ef]";
const DONE = "bg-ok-soft text-[#04250e] ring-[#b7e9c8]";
const NEUTRAL = "bg-sunken text-[#353736] ring-line";
const LABEL: Record<string, string> = { RESOLVING: "Awaiting sign-off", DECISION_NEEDED: "Decision needed" };
const STATUS: Record<string, string> = {
  REPORTED: NEGATIVE,
  OPTIONS_READY: READY,
  TO_RESOLVE: READY,
  DECISION_NEEDED: WAITING,
  SUBMITTED: WAITING,
  RESOLVING: WAITING,
  COMMITTED: DONE,
  CLOSED: NEUTRAL,
  RECORDED: NEUTRAL,
  REJECTED: NEGATIVE,
  QUEUED: NEUTRAL,
  WITHDRAWN: NEUTRAL,
  STALE: WAITING,
  REVOKED: NEUTRAL,
};

export function Status({ value }: { value: string }) {
  return (
    <span
      className={cx(
        "inline-flex shrink-0 items-center whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset",
        STATUS[value] ?? NEUTRAL,
      )}
    >
      {LABEL[value] ?? value.charAt(0) + value.slice(1).replace(/_/g, " ").toLowerCase()}
    </span>
  );
}

const SEVERITY = {
  high: { label: "High", tone: "bg-critical-soft text-[#831c1e] ring-[#f5c2bd]", dot: "bg-critical" },
  medium: { label: "Medium", tone: "bg-pending-soft text-[#92400e] ring-[#fde68a]", dot: "bg-pending" },
  low: { label: "Low", tone: "bg-sunken text-[#353736] ring-line", dot: "bg-[#8a8d90]" },
} as const;

/** How serious a disruption is (high, medium or low), with why on hover. */
export function SeverityBadge({ severity }: { severity?: { level: string; reasons?: string[] } | null }) {
  if (!severity) return null;
  const s = SEVERITY[severity.level as keyof typeof SEVERITY];
  if (!s) return null;
  const why = (severity.reasons ?? []).join("; ");
  return (
    <span
      title={why ? `${s.label} severity: ${why}` : `${s.label} severity`}
      className={cx(
        "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset",
        s.tone,
      )}
    >
      <span className={cx("h-1.5 w-1.5 rounded-full", s.dot)} />
      {s.label}
    </span>
  );
}

/** Ids, times and stations in monospace, so C-1042 and P-2291 read at a glance. */
export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cx("font-mono text-[0.92em] tracking-tight", className)}>{children}</span>;
}

export function Notice({ tone = "info", children }: { tone?: "info" | "error" | "ok"; children: ReactNode }) {
  const tones = {
    info: "border-[#cfe0ef] bg-accent-soft text-[#173f63]",
    error: "border-[#f5c2bd] bg-critical-soft text-[#831c1e]",
    ok: "border-[#b7e9c8] bg-ok-soft text-[#04250e]",
  };
  return <p className={cx("rounded-md border px-3 py-2 text-sm", tones[tone])}>{children}</p>;
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-label="Loading"
      className={cx(
        "inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent",
        className,
      )}
    />
  );
}

export function Empty({ icon, children }: { icon?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center text-sm text-muted">
      {icon}
      {children}
    </div>
  );
}

export function rupees(value: unknown): string {
  return typeof value === "number" ? `₹${value.toLocaleString("en-IN")}` : "—";
}

export function clock(iso: string | null | undefined): string {
  return iso ? iso.slice(11, 16) + "Z" : "—";
}

export function day(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
}

/** The day after an ISO date ("2026-10-31" -> "2026-11-01"); "" stays "". */
export function nextDay(iso: string): string {
  if (!iso) return "";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

/** On a form's "from" field: its "until" field may not start before it (the same day, or
 * the same moment for a time, is the earliest), so move its minimum and clear a value that no
 * longer fits. Works for date and datetime-local inputs alike. */
export function untilFrom(from: HTMLInputElement, untilName: string) {
  const until = from.form?.elements.namedItem(untilName);
  if (!(until instanceof HTMLInputElement) || !from.value) return;
  until.min = from.value;
  if (until.value && until.value < from.value) until.value = "";
}
