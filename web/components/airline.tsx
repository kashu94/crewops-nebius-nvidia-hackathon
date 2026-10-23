// The three fictional airlines' marks. Drawn here as small SVGs so no image files are
// needed; the names and colours match data/seed/airlines.json.

import { cx } from "./ui";

export const AIRLINES: Record<string, { name: string; color: string }> = {
  DX: { name: "Deccan Express", color: "#0284c7" },
  KS: { name: "Konkan Skyways", color: "#ea580c" },
  NG: { name: "Nilgiri Connect", color: "#059669" },
};

/** The airline that flies a flight number or flight id (its first two letters). */
export function airlineOf(flight: string | null | undefined): string | null {
  const code = (flight ?? "").slice(0, 2).toUpperCase();
  return code in AIRLINES ? code : null;
}

function Mark({ code, size }: { code: string; size: number }) {
  const color = AIRLINES[code]?.color ?? "#64748b";
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <rect width="24" height="24" rx="7" fill={color} />
      {code === "DX" && (
        // A rising sun under a wing.
        <>
          <circle cx="12" cy="14" r="4.2" fill="white" fillOpacity="0.9" />
          <path d="M3.5 11.5 Q12 6 20.5 11.5 L20.5 12.8 Q12 8.6 3.5 12.8 Z" fill="white" />
        </>
      )}
      {code === "KS" && (
        // A coastal wave.
        <path
          d="M3 15 C6 10, 9 10, 12 15 S18 20, 21 13"
          fill="none"
          stroke="white"
          strokeWidth="2.6"
          strokeLinecap="round"
        />
      )}
      {code === "NG" && (
        // A hill with a winding road.
        <>
          <path d="M3 19 L10 8 L14 13 L16.5 10 L21 19 Z" fill="white" fillOpacity="0.92" />
          <path d="M10.5 19 C12 17, 9.5 15.5, 12 14" stroke={color} strokeWidth="1.4" fill="none" />
        </>
      )}
    </svg>
  );
}

/** An airline's mark, with its name unless `compact`. The compact mark names the airline
 * the moment it is hovered or focused, not after the browser's tooltip delay. */
export function AirlineBadge({
  code,
  compact = false,
  className,
}: {
  code: string | null | undefined;
  compact?: boolean;
  className?: string;
}) {
  if (!code || !AIRLINES[code]) return null;
  const a = AIRLINES[code];
  return (
    <span
      className={cx("group/airline relative inline-flex shrink-0 items-center gap-1.5 align-middle", className)}
      aria-label={a.name}
      tabIndex={compact ? 0 : undefined}
    >
      <Mark code={code} size={compact ? 16 : 18} />
      {compact ? (
        <span
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-1/2 z-30 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-md bg-brand px-2 py-1 text-[11px] font-medium text-white opacity-0 transition-opacity duration-100 group-hover/airline:opacity-100 group-focus/airline:opacity-100"
        >
          {a.name} · {code}
        </span>
      ) : (
        <span className="text-xs font-semibold" style={{ color: a.color }}>
          {a.name}
        </span>
      )}
    </span>
  );
}
