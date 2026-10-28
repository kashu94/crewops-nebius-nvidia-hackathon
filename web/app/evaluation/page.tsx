"use client";

import {
  BadgeCheck,
  Bot,
  CheckCircle2,
  ChevronDown,
  Clock3,
  FlaskConical,
  Gauge,
  Lock,
  Route,
  Search,
  ShieldCheck,
  X,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

// Held-out eval results for the question pipeline, regenerated with
// `python -m crewops.agent.evaluate --json web/public/evals/<mode>.json`.
// No sign-in needed. Everything on it is synthetic crew data.

type Result = {
  case_id: string;
  kind: string;
  prompt: string;
  passed: boolean;
  source: string;
  intent: string | null;
  ms: number;
  model_calls: number;
  tokens: number;
  checks: Record<string, unknown>;
  answer: string;
};
type Report = {
  summary: {
    mode: string;
    questions: number;
    passed: Record<string, string>;
    routing: string;
    fact_agreement_mean: number;
    unsourced_answers: number;
    personal_data_leaks: number;
    model_calls: number;
    tokens: number;
    ms_p50: number;
    ms_p95: number;
  };
  results: Result[];
};

const KINDS: Record<string, { label: string; what: string }> = {
  rewording: {
    label: "Rewordings",
    what: "Each of the 38 gold questions asked in other words, written after the rules were final",
  },
  off_topic: { label: "Off-topic", what: "Questions outside crew control must run no tool" },
  role: { label: "Role limits", what: "Each role sees only what it may" },
  question_back: { label: "Questions back", what: "A missing seat or date is asked about, not guessed" },
  follow_up: { label: "Follow-ups", what: "A short reply borrows only what it should from the turn before" },
  injection: { label: "Prompt injection", what: "Instructions hidden in a question change nothing" },
};

function ratio(text: string): [number, number] {
  const [a, b] = text.split("/").map(Number);
  return [a || 0, b || 0];
}

function pct(a: number, b: number): string {
  return b ? `${Math.round((a / b) * 100)}%` : "—";
}

function Stat({
  icon,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
}) {
  return (
    <div className="bg-surface px-5 py-4">
      <p className="flex items-center gap-1.5 text-sm text-muted [&_svg]:hidden">
        {icon} {label}
      </p>
      <p className="mt-1 text-[28px] font-normal leading-tight tabular-nums text-foreground">{value}</p>
      {sub && <p className="mt-1 text-xs leading-snug text-muted">{sub}</p>}
    </div>
  );
}

function Bar({ label, what, passed, total }: { label: string; what: string; passed: number; total: number }) {
  const share = total ? passed / total : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="font-medium text-foreground">{label}</span>
        <span className="text-xs tabular-nums text-muted">
          {passed}/{total} · {pct(passed, total)}
        </span>
      </div>
      {/* the pass share in the data hue; what falls short shows as the track */}
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-[#e6e8ea]">
        <div
          className={`h-full rounded-full ${share >= 0.7 ? "bg-accent" : "bg-pending"}`}
          style={{ width: `${share * 100}%` }}
        />
      </div>
      <p className="mt-1 text-xs text-muted">{what}</p>
    </div>
  );
}

function Row({ r }: { r: Result }) {
  const [open, setOpen] = useState(false);
  const agreement = typeof r.checks.agreement === "number" ? (r.checks.agreement as number) : null;
  return (
    <li className="border-b border-rule last:border-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-start gap-3 px-4 py-3 text-left transition hover:bg-sunken"
        aria-expanded={open}
      >
        {r.passed ? (
          <CheckCircle2 size={17} className="mt-0.5 shrink-0 text-ok" />
        ) : (
          <XCircle size={17} className="mt-0.5 shrink-0 text-pending" />
        )}
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-muted">{r.case_id}</span>
            <span className="rounded bg-sunken px-1.5 text-xs font-medium text-[#353736]">
              {KINDS[r.kind]?.label ?? r.kind}
            </span>
            {r.intent && <span className="text-xs text-muted">{r.intent.toLowerCase().replace(/_/g, " ")}</span>}
          </span>
          <span className="mt-0.5 block text-sm text-foreground">{r.prompt}</span>
        </span>
        <span className="hidden shrink-0 text-right text-xs text-muted sm:block">
          {agreement !== null && <span className="block">facts {Math.round(agreement * 100)}%</span>}
          <span className="block font-mono">
            {r.ms} ms{r.model_calls ? ` · ${r.model_calls} model` : ""}
          </span>
        </span>
        <ChevronDown size={15} className={`mt-1 shrink-0 text-muted transition ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="space-y-2 px-11 pb-4 text-sm">
          <p className="rounded-md bg-sunken px-3 py-2 leading-relaxed text-foreground">{r.answer || "(no answer)"}</p>
          <p className="flex flex-wrap gap-1.5">
            <span className="rounded bg-accent-soft px-1.5 py-0.5 text-xs text-[#173f63]">source: {r.source}</span>
            {Object.entries(r.checks).map(([k, v]) => (
              <span key={k} className="rounded-full bg-sunken px-2 py-0.5 font-mono text-xs text-muted">
                {k}: {Array.isArray(v) ? `[${v.join(", ")}]` : String(v)}
              </span>
            ))}
          </p>
        </div>
      )}
    </li>
  );
}

// The live run answers with NVIDIA Nemotron on Nebius Token Factory; the offline run is the
// same questions with no model at all, the baseline the live run is measured against.
const RUN_LABEL: Record<string, string> = {
  live: "With NVIDIA Nemotron (live)",
  offline: "Rules only, no model (baseline)",
};

export default function Evaluation() {
  const [reports, setReports] = useState<Record<string, Report>>({});
  const [mode, setMode] = useState<string | null>(null);
  const [kind, setKind] = useState<string>("all");
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let live = true;
    Promise.all(
      ["live", "offline"].map((m) =>
        fetch(`/evals/${m}.json`, { cache: "no-store" })
          .then((r) => (r.ok ? (r.json() as Promise<Report>) : null))
          .catch(() => null)
          .then((rep) => [m, rep] as const),
      ),
    ).then((pairs) => {
      if (!live) return;
      const found = Object.fromEntries(pairs.filter(([, r]) => r)) as Record<string, Report>;
      setReports(found);
      setMode(Object.keys(found)[0] ?? null);
    });
    return () => {
      live = false;
    };
  }, []);

  const report = mode ? reports[mode] : null;
  const shown = useMemo(() => {
    if (!report) return [];
    const q = query.trim().toLowerCase();
    return report.results.filter(
      (r) =>
        (kind === "all" || r.kind === kind) &&
        (!onlyFailed || !r.passed) &&
        (!q || r.prompt.toLowerCase().includes(q) || r.answer.toLowerCase().includes(q)),
    );
  }, [report, kind, onlyFailed, query]);

  if (!report) {
    return (
      <main className="mx-auto flex min-h-[100dvh] max-w-3xl flex-col items-center justify-center gap-3 px-4 text-center">
        <FlaskConical size={28} className="text-accent" />
        <h1 className="text-xl font-semibold">Evaluation results</h1>
        <p className="text-sm text-muted">
          {Object.keys(reports).length === 0 && mode === null
            ? "Loading, or no report published yet: run python -m crewops.agent.evaluate --json web/public/evals/offline.json."
            : "Loading…"}
        </p>
      </main>
    );
  }

  const s = report.summary;
  const [routed, routable] = ratio(s.routing);
  const answered = report.results.filter((r) => r.answer).length;
  const behaviour = Object.entries(s.passed).filter(([k]) => k !== "rewording");
  const behaviourPassed = behaviour.reduce((n, [, v]) => n + ratio(v)[0], 0);
  const behaviourTotal = behaviour.reduce((n, [, v]) => n + ratio(v)[1], 0);
  const other = Object.keys(reports).find((m) => m !== mode);

  return (
    <main className="min-h-[100dvh] bg-background sm:p-2">
      <div className="mx-auto flex max-w-[1280px] flex-col gap-6 border-line bg-surface px-4 py-8 sm:rounded-lg sm:border sm:px-8">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-sm text-muted">CrewOps evaluation</p>
            <h1 className="mt-0.5 text-[22px] font-semibold tracking-tight text-foreground">
              How well CrewOps answers
            </h1>
            <p className="mt-1 max-w-2xl text-sm text-muted">
              A held-out set written after the routing rules were final and never used to change them: every gold
              question reworded, plus behaviour cases. Scoring is deterministic; no model judges another.
            </p>
          </div>
          {/* A choice only when both runs exist; with one, a plain label says which it is. */}
          {Object.keys(reports).length < 2 ? (
            <p className="rounded-md bg-sunken px-3 py-1.5 text-sm text-muted">
              {RUN_LABEL[mode ?? "live"] ?? mode}
            </p>
          ) : (
            <div className="flex items-center gap-2" role="group" aria-label="Which run">
              {Object.keys(reports).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={`h-8 rounded-md px-3 text-sm font-medium transition-colors ${
                    m === mode
                      ? "bg-brand text-white"
                      : "bg-surface text-foreground ring-1 ring-control hover:bg-sunken"
                  }`}
                >
                  {RUN_LABEL[m] ?? m}
                </button>
              ))}
            </div>
          )}
        </header>

        <section className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line lg:grid-cols-4">
          <Stat
            icon={<ShieldCheck size={13} />}
            label="Unsourced claims"
            value={s.unsourced_answers}
            sub={`of ${answered} answers, re-verified against their own tool results`}
          />
          <Stat
            icon={<Lock size={13} />}
            label="Personal data leaks"
            value={s.personal_data_leaks}
            sub="answers that needed personal data removed"
          />
          <Stat
            icon={<Route size={13} />}
            label="Routing"
            value={pct(routed, routable)}
            sub={`${s.routing} rewordings reach the gold question's intent`}
          />
          <Stat
            icon={<BadgeCheck size={13} />}
            label="Fact agreement"
            value={`${Math.round(s.fact_agreement_mean * 100)}%`}
            sub="mean share of the gold answer's ids, times and figures stated"
          />
          <Stat
            icon={<CheckCircle2 size={13} />}
            label="Behaviour cases"
            value={`${behaviourPassed}/${behaviourTotal}`}
            sub="off-topic, roles, questions back, follow-ups, injection"
          />
          <Stat
            icon={<Clock3 size={13} />}
            label="Latency"
            value={s.ms_p50 < 1 ? "under 1 ms" : `${s.ms_p50} ms`}
            sub={`median; 95th percentile ${s.ms_p95} ms`}
          />
          <Stat
            icon={<Bot size={13} />}
            label="Model calls"
            value={s.model_calls}
            sub={s.model_calls ? `${s.tokens.toLocaleString("en-IN")} tokens in all` : "rules and templates only"}
          />
          <Stat icon={<Gauge size={13} />} label="Questions" value={s.questions} sub={`mode: ${s.mode}`} />
        </section>

        {other && (
          <section className="rounded-lg border border-line bg-surface p-5">
            <h2 className="text-[15px] font-semibold text-foreground">
              {mode === "live" ? "With NVIDIA Nemotron" : "Rules only"} compared with{" "}
              {other === "live" ? "NVIDIA Nemotron" : "the rules-only baseline"}
            </h2>
            <div className="mt-3 grid gap-2 text-sm sm:grid-cols-3">
              {(
                [
                  ["Routing", s.routing, reports[other].summary.routing],
                  [
                    "Fact agreement",
                    `${Math.round(s.fact_agreement_mean * 100)}%`,
                    `${Math.round(reports[other].summary.fact_agreement_mean * 100)}%`,
                  ],
                  ["Median latency", `${s.ms_p50} ms`, `${reports[other].summary.ms_p50} ms`],
                ] as const
              ).map(([label, here, there]) => (
                <p key={label} className="rounded-md bg-sunken px-3 py-2">
                  <span className="block text-xs text-muted">{label}</span>
                  <span className="font-semibold tabular-nums text-foreground">{here}</span>
                  <span className="tabular-nums text-muted"> vs {there}</span>
                </p>
              ))}
            </div>
          </section>
        )}

        <section className="grid gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
          <div className="flex flex-col gap-4 rounded-lg border border-line bg-surface p-5">
            <h2 className="text-[15px] font-semibold text-foreground">By kind</h2>
            {Object.entries(s.passed).map(([k, v]) => {
              const [a, b] = ratio(v);
              return <Bar key={k} label={KINDS[k]?.label ?? k} what={KINDS[k]?.what ?? ""} passed={a} total={b} />;
            })}
            <div className="mt-1 rounded-md bg-sunken p-3 text-xs leading-relaxed text-muted">
              <p className="font-semibold text-foreground">How it is scored</p>A rewording passes when its answer states
              at least 80% of the gold answer&apos;s facts (ids, times, figures), or when the gold question gets no
              factual answer and the rewording reaches the same intent. Every answer is verified again here against its
              own tool results. Rewordings the rules miss are what the semantic search and Nemotron are for: the live run
              measures them.
            </div>
          </div>

          <div className="overflow-hidden rounded-lg border border-line bg-surface">
            <div className="flex flex-wrap items-center gap-2 border-b border-rule px-4 py-3">
              <label className="relative min-w-[200px] flex-1">
                <Search
                  size={13}
                  className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted"
                />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search questions and answers"
                  className="w-full rounded-lg border border-line py-1.5 pl-8 pr-8 text-sm outline-none focus:border-accent"
                />
                {query && (
                  <button
                    type="button"
                    onClick={() => setQuery("")}
                    aria-label="Clear the search"
                    title="Clear"
                    className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-muted hover:bg-sunken hover:text-foreground"
                  >
                    <X size={13} />
                  </button>
                )}
              </label>
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value)}
                className="rounded-lg border border-line px-2 py-1.5 text-sm"
                aria-label="Kind"
              >
                <option value="all">All kinds</option>
                {Object.keys(s.passed).map((k) => (
                  <option key={k} value={k}>
                    {KINDS[k]?.label ?? k}
                  </option>
                ))}
              </select>
              <label className="flex items-center gap-1.5 text-xs text-muted">
                <input type="checkbox" checked={onlyFailed} onChange={(e) => setOnlyFailed(e.target.checked)} />
                Only misses
              </label>
              <span className="ml-auto text-xs text-muted">{shown.length} shown</span>
            </div>
            <ol className="max-h-[70vh] overflow-y-auto">
              {shown.map((r) => (
                <Row key={r.case_id} r={r} />
              ))}
            </ol>
          </div>
        </section>

        <p className="text-center text-xs text-muted">
          All crew, flights and airlines here are synthetic, made up for this demonstration.{" "}
          <Link href="/login" className="text-accent hover:underline">
            Sign in to CrewOps
          </Link>
        </p>
      </div>
    </main>
  );
}
