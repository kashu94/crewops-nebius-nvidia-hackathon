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
    primary: "bg-brand text-brand-ink shadow-sm hover:bg-[#123259]",
    accent: "bg-accent text-white shadow-sm hover:bg-sky-700",
    secondary: "bg-surface text-foreground border border-line hover:border-sky-300 hover:bg-sunken",
    danger: "bg-critical text-white shadow-sm hover:bg-red-700",
    ghost: "text-muted hover:text-foreground hover:bg-sunken",
  };
  return (
    <button
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium",
        "transition active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50",
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
    <section
      className={cx(
        "flex min-h-0 flex-col rounded-2xl border border-line bg-surface shadow-[0_1px_2px_rgb(15_23_42/0.04)]",
        "animate-rise",
        className,
      )}
    >
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-[0.08em] text-slate-600">
            {icon}
            {title}
          </h2>
          {action}
        </header>
      )}
      <div className={cx("min-h-0 flex-1 p-4", bodyClassName)}>{children}</div>
    </section>
  );
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <section className={cx("animate-rise rounded-2xl border border-line bg-surface p-5 shadow-sm", className)}>
      {children}
    </section>
  );
}

export function Field({
  label,
  hint,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="font-medium text-slate-700">{label}</span>
      <input
        className="rounded-lg border border-line bg-surface px-3 py-2 outline-none transition focus:border-accent focus:ring-4 focus:ring-sky-100"
        {...props}
      />
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </label>
  );
}

const STATUS: Record<string, string> = {
  REPORTED: "bg-critical-soft text-red-800 ring-red-200",
  OPTIONS_READY: "bg-accent-soft text-sky-800 ring-sky-200",
  TO_RESOLVE: "bg-ok-soft text-emerald-800 ring-emerald-200",
  DECISION_NEEDED: "bg-pending-soft text-amber-800 ring-amber-200",
  SUBMITTED: "bg-pending-soft text-amber-800 ring-amber-200",
  COMMITTED: "bg-ok-soft text-emerald-800 ring-emerald-200",
  CLOSED: "bg-slate-100 text-slate-700 ring-slate-200",
  RECORDED: "bg-violet-50 text-violet-800 ring-violet-200",
  REJECTED: "bg-critical-soft text-red-800 ring-red-200",
  QUEUED: "bg-slate-100 text-slate-700 ring-slate-200",
  WITHDRAWN: "bg-slate-100 text-slate-700 ring-slate-200",
  STALE: "bg-orange-100 text-orange-800 ring-orange-200",
};

export function Status({ value }: { value: string }) {
  return (
    <span
      className={cx(
        "inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold tracking-wide ring-1 ring-inset",
        STATUS[value] ?? "bg-slate-100 text-slate-700 ring-slate-200",
      )}
    >
      {value.replace(/_/g, " ")}
    </span>
  );
}

const SEVERITY = {
  high: { label: "High", tone: "bg-red-600 text-white ring-red-600", dot: "bg-white" },
  medium: { label: "Medium", tone: "bg-amber-100 text-amber-900 ring-amber-300", dot: "bg-amber-500" },
  low: { label: "Low", tone: "bg-emerald-50 text-emerald-800 ring-emerald-200", dot: "bg-emerald-500" },
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
        "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset",
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
    info: "border-sky-200 bg-accent-soft text-sky-900",
    error: "border-red-200 bg-critical-soft text-red-900",
    ok: "border-emerald-200 bg-ok-soft text-emerald-900",
  };
  return <p className={cx("animate-rise rounded-lg border px-3 py-2 text-sm", tones[tone])}>{children}</p>;
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
