import { CheckCircle2 } from "lucide-react";

import type { Seat } from "@/lib/types";

/** A seat filled since the analysis by an approved plan: who flies it now. */
export function ReCrewed({ seat }: { seat: Seat }) {
  const r = seat.re_crewed;
  if (!r) return null;
  const who = r.holders.map((h) => `${h.name ?? h.crew_id} (${h.crew_id})`).join(", ");
  return (
    <p className="flex items-start gap-2 rounded-xl border border-emerald-200 bg-ok-soft px-3 py-2 text-sm text-emerald-950">
      <CheckCircle2 size={15} className="mt-0.5 shrink-0" />
      <span>
        <b>Already re-crewed.</b>{" "}
        {r.cancelled
          ? `${seat.pairing_id} was cancelled by an approved plan.`
          : `${who} now flies ${seat.pairing_id} as ${seat.role}, through an approved plan (checked against every rule for the whole trip).`}{" "}
        {seat.crew_out} is off it, so this seat needs no cover: close this disruption saying it is covered.
      </span>
    </p>
  );
}
