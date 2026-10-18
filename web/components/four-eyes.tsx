"use client";

import { Check, Fingerprint, Hourglass } from "lucide-react";

import { clock, cx, day } from "./ui";

/** Four-eyes evidence for a plan: the SHA-256 fingerprint of exactly what was submitted, and
 * once approved, the approver's seal over that fingerprint (crewops.plans.fingerprint). Approval
 * recomputes the fingerprint and refuses a plan changed after submission. */
export function FourEyes({
  fingerprint,
  seal,
  submittedBy,
  submittedAt,
  approvedBy,
  approvedAt,
  className,
}: {
  fingerprint?: string | null;
  seal?: string | null;
  submittedBy?: string | null;
  submittedAt?: string | null;
  approvedBy?: string | null;
  approvedAt?: string | null;
  className?: string;
}) {
  if (!fingerprint) return null;
  const row = (
    label: string,
    value: string | null | undefined,
    ok: boolean,
    who?: string | null,
    at?: string | null,
  ) => (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
      <span
        className={cx(
          "grid h-4 w-4 place-items-center rounded-full",
          ok ? "bg-emerald-400 text-slate-950" : "bg-slate-700 text-slate-300",
        )}
      >
        {ok ? <Check size={10} strokeWidth={3} /> : <Hourglass size={9} />}
      </span>
      <span className="w-20 shrink-0 text-slate-400">{label}</span>
      {value ? (
        <span title={value} className="break-all text-emerald-300">
          {value.slice(0, 16)}
          <span className="text-emerald-300/40">{value.slice(16, 24)}…</span>
        </span>
      ) : (
        <span className="text-slate-500">awaiting a second person</span>
      )}
      {who && (
        <span className="text-slate-400">
          · {who}
          {at && (
            <>
              {" "}
              {day(at)} {clock(at)}
            </>
          )}
        </span>
      )}
    </div>
  );
  return (
    <div
      className={cx(
        "rounded-xl bg-slate-950 px-3 py-2.5 font-mono text-[11px] leading-relaxed shadow-inner",
        className,
      )}
    >
      <p className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">
        <Fingerprint size={12} className="text-emerald-400" /> Four-eyes record · SHA-256
      </p>
      {row("Submitted", fingerprint, true, submittedBy, submittedAt)}
      {row("Approved", seal, Boolean(seal), approvedBy, approvedAt)}
    </div>
  );
}
