// Sign-in backdrop: a night runway seen on final approach. Plain SVG and CSS, slightly
// blurred so the form on top stays readable. Animation stops under prefers-reduced-motion.

const HORIZON = 430;
const NEAR = 830;
const LIGHTS = 72;
/** The runway's vanishing point sits right of centre, so the text has open sky. */
const CX = 470;
const SPREAD = 250;

/** Perspective: 1 at the near end of the runway, shrinking towards the horizon. */
const depth = Array.from({ length: LIGHTS }, (_, i) => 1 / (1 + i * 0.3));

const y = (s: number) => HORIZON + (NEAR - HORIZON) * s;

/** Lights dim into the haze with distance, so the far end dissolves instead of bunching up. */
const fade = (s: number) => 0.95 * Math.min(1, Math.max(0, (s - 0.12) / 0.33));

/** Distant lights along the horizon: fixed, so the server and browser draw the same. */
const CITY = [
  [408, 1],
  [424, 2],
  [441, 1],
  [452, 3],
  [497, 1],
  [512, 2],
  [529, 1],
  [548, 3],
  [566, 1],
  [583, 2],
] as const;

/** Out-of-focus lights: left %, top %, size px, colour, drift seconds, delay seconds. */
const BOKEH = [
  [8, 18, 90, "lime", 26, 0],
  [22, 62, 54, "mint", 31, 4],
  [34, 30, 120, "pale", 34, 9],
  [47, 74, 70, "lime", 28, 2],
  [58, 14, 64, "mint", 36, 6],
  [66, 48, 110, "lime", 30, 12],
  [78, 26, 46, "pale", 24, 3],
  [86, 66, 96, "mint", 33, 8],
  [14, 84, 60, "pale", 29, 14],
  [92, 10, 72, "lime", 35, 5],
  [40, 90, 84, "mint", 27, 11],
  [72, 86, 52, "pale", 32, 1],
] as const;

// a hint of green, for the NVIDIA and Nebius models behind the answers
const TINT: Record<string, string> = {
  lime: "rgba(118, 185, 0, 0.34)",
  mint: "rgba(52, 168, 96, 0.3)",
  pale: "rgba(190, 236, 170, 0.24)",
};

export function NightApproach() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="approach-drift absolute -inset-6 blur-[1.5px]">
        {/* Sky: deep night, warming towards the horizon. */}
        <div className="absolute inset-0 bg-[linear-gradient(180deg,#040a18_0%,#0a1d3a_45%,#13315a_62%,#1d3b5e_70%,#0b1730_100%)]" />
        <div className="absolute inset-x-0 top-[44%] h-40 bg-[radial-gradient(ellipse_at_center,rgba(251,191,36,0.18),transparent_70%)]" />

        {/* Aurora: three slow light fields. */}
        <div className="aurora aurora-a absolute -left-1/4 -top-1/4 h-[70%] w-[90%] rounded-full bg-sky-500/25 blur-[90px]" />
        <div className="aurora aurora-b absolute -right-1/4 top-[5%] h-[60%] w-[80%] rounded-full bg-indigo-500/25 blur-[100px]" />
        <div className="aurora aurora-c absolute left-[10%] top-[20%] h-[45%] w-[70%] rounded-full bg-emerald-500/20 blur-[110px]" />

        {/* The runway on final approach. */}
        <svg viewBox="0 0 600 800" preserveAspectRatio="xMidYMax slice" className="absolute inset-0 h-full w-full">
          <defs>
            <filter id="glow" x="-200%" y="-200%" width="500%" height="500%">
              <feGaussianBlur stdDeviation="3.2" result="soft" />
              <feMerge>
                <feMergeNode in="soft" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <linearGradient id="horizon" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0.45" stopColor="#94c5ff" stopOpacity="0" />
              <stop offset="1" stopColor="#94c5ff" stopOpacity="0.28" />
            </linearGradient>
            {/* the road fades out well before the horizon, so its far end never reads as a point */}
            <linearGradient id="tarmac" gradientUnits="userSpaceOnUse" x1="0" y1={HORIZON} x2="0" y2={NEAR}>
              <stop offset="0" stopColor="#0b1a33" stopOpacity="0" />
              <stop offset="0.3" stopColor="#0b1a33" stopOpacity="0" />
              <stop offset="1" stopColor="#020611" stopOpacity="0.85" />
            </linearGradient>
            <linearGradient id="edge" gradientUnits="userSpaceOnUse" x1="0" y1={HORIZON} x2="0" y2={NEAR}>
              <stop offset="0" stopColor="#d4f0b0" stopOpacity="0" />
              <stop offset="0.16" stopColor="#d4f0b0" stopOpacity="0" />
              <stop offset="1" stopColor="#d4f0b0" stopOpacity="0.18" />
            </linearGradient>
          </defs>
          <polygon
            points={`${CX - 3},${HORIZON} ${CX + 3},${HORIZON} ${CX + SPREAD + 40},${NEAR} ${CX - SPREAD - 40},${NEAR}`}
            fill="url(#tarmac)"
          />
          <line x1="0" x2="600" y1={HORIZON} y2={HORIZON} stroke="url(#horizon)" strokeWidth="1" />
          {/* faint runway edges, fading with distance like the lights */}
          {[-1, 1].map((side) => (
            <line
              key={side}
              x1={CX}
              y1={HORIZON}
              x2={CX + side * SPREAD * 1.12}
              y2={y(1.12)}
              stroke="url(#edge)"
              strokeWidth="1"
            />
          ))}
          {CITY.map(([x, r]) => (
            <circle key={x} cx={x} cy={HORIZON - 3} r={r} className="city-light" fill="#fde68a" filter="url(#glow)" />
          ))}
          <g filter="url(#glow)">
            {depth.map((s, i) => (
              <g key={i}>
                <circle cx={CX - SPREAD * s} cy={y(s)} r={0.4 + 4.2 * s} fill="#e2f5c4" opacity={fade(s)} />
                <circle cx={CX + SPREAD * s} cy={y(s)} r={0.4 + 4.2 * s} fill="#e2f5c4" opacity={fade(s)} />
                {/* the group carries the fade: the flashing animation sets the light's own opacity */}
                <g opacity={fade(s)}>
                  <circle
                    cx={CX}
                    cy={y(s)}
                    r={0.3 + 3.1 * s}
                    fill="#e3fbe8"
                    className="lead-in"
                    style={{ animationDelay: `${i * 0.03}s` }}
                  />
                </g>
              </g>
            ))}
          </g>
        </svg>

        {/* Out-of-focus airfield lights drifting past. */}
        {BOKEH.map(([left, top, size, tint, seconds, delay]) => (
          <span
            key={`${left}-${top}`}
            className="bokeh absolute rounded-full"
            style={{
              left: `${left}%`,
              top: `${top}%`,
              width: size,
              height: size,
              background: `radial-gradient(circle, ${TINT[tint]} 0%, transparent 68%)`,
              animationDuration: `${seconds}s`,
              animationDelay: `-${delay}s`,
            }}
          />
        ))}
      </div>

      {/* Legibility: darker where the text sits, and a soft vignette. */}
      <div className="absolute inset-0 bg-[linear-gradient(100deg,rgba(3,8,20,0.78)_0%,rgba(3,8,20,0.45)_45%,rgba(3,8,20,0.1)_100%)]" />
      <div className="absolute inset-0 shadow-[inset_0_0_160px_rgba(0,0,0,0.55)]" />
    </div>
  );
}
