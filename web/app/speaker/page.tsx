"use client";

import {
  Bell,
  BellRing,
  Check,
  CheckCircle2,
  ClipboardCheck,
  Mic,
  Power,
  Radio,
  Siren,
  Undo2,
  Volume2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { makeRecognition } from "@/lib/speech";

// Desk or ops-room speaker. It belongs to a place, not a person, so it never uses anyone's
// session. The server calls in as the speaker device and only gets alert briefs. Modelled on
// Alexa notifications on an Echo: chime, amber ring, then "what are my notifications?"
// reads them out.

type Alert = {
  id: string;
  disruption_id: string;
  event: string;
  at: string;
  title: string;
  text: string;
  speech: string;
  desk: { desk_id: string; controller: string } | null;
  severity?: "high" | "medium" | "low" | null;
};

/** Coloured by urgency, escalations loudest, so the kind is readable from across the room
 * before the text is. */
const LOOK: Record<string, { tag: string; rail: string; card: string; icon: typeof Bell; iconTone: string }> = {
  ESCALATED: {
    tag: "bg-red-500 text-white",
    rail: "bg-red-500",
    card: "bg-red-950/60 ring-red-500/70 shadow-[0_0_0_1px_rgba(239,68,68,0.35),0_10px_40px_-10px_rgba(239,68,68,0.55)]",
    icon: Siren,
    iconTone: "text-red-300",
  },
  NEW: {
    tag: "bg-amber-400 text-slate-950",
    rail: "bg-amber-400",
    card: "bg-slate-900/80 ring-amber-400/50",
    icon: BellRing,
    iconTone: "text-amber-300",
  },
  APPROVAL: {
    tag: "bg-violet-400 text-slate-950",
    rail: "bg-violet-400",
    card: "bg-slate-900/80 ring-violet-400/40",
    icon: ClipboardCheck,
    iconTone: "text-violet-300",
  },
  BACK: {
    tag: "bg-orange-400 text-slate-950",
    rail: "bg-orange-400",
    card: "bg-slate-900/80 ring-orange-400/40",
    icon: Undo2,
    iconTone: "text-orange-300",
  },
  DONE: {
    tag: "bg-emerald-500/20 text-emerald-200",
    rail: "bg-emerald-500/60",
    card: "bg-slate-900/50 ring-slate-700/60",
    icon: CheckCircle2,
    iconTone: "text-emerald-300",
  },
};

function lookOf(event: string) {
  if (event === "ESCALATED") return LOOK.ESCALATED;
  if (event === "SUBMITTED") return LOOK.APPROVAL;
  if (["REJECTED", "WITHDRAWN", "STALE"].includes(event)) return LOOK.BACK;
  if (["APPROVED", "COMMITTED", "CLOSED"].includes(event)) return LOOK.DONE;
  return LOOK.NEW;
}

const SEVERITY_TONE = {
  high: "bg-red-500/20 text-red-200 ring-red-400/50",
  medium: "bg-amber-400/15 text-amber-200 ring-amber-300/40",
  low: "bg-emerald-400/10 text-emerald-200 ring-emerald-300/30",
} as const;

function useUtcClock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    const tick = () => setNow(new Date());
    const first = setTimeout(tick);
    const t = setInterval(tick, 15_000);
    return () => {
      clearTimeout(first);
      clearInterval(t);
    };
  }, []);
  return now;
}

const SCOPES: { value: string; label: string }[] = [
  { value: "room", label: "Whole ops room" },
  { value: "desk-1", label: "Desk 1 · Ananya Iyer" },
  { value: "desk-2", label: "Desk 2 · Rohit Malhotra" },
  { value: "desk-3", label: "Desk 3 · Divya Rao" },
  { value: "approvals", label: "Approvals" },
];
const POLL_MS = 5_000;
const MAX_ANNOUNCEMENTS = 3; // an alert is spoken unprompted once, then twice more as a reminder
const GAP_MS = 15_000;
// Measured from when an announcement ends. A brief is ~10 s, so a 15 s gap gets all three
// done in about a minute without talking over the room.
const FIRST_DELAY_MS = 1_200; // after the chime
const heardKey = (scope: string) => `crewops-speaker-heard:${scope}`;

// Heard alerts per scope, in localStorage. Some private windows block storage, so fall back
// to memory for the life of the page.
const inMemory = new Map<string, Set<string>>();

function loadHeard(scope: string): Set<string> | null {
  try {
    const raw = localStorage.getItem(heardKey(scope));
    return raw ? new Set(JSON.parse(raw) as string[]) : null;
  } catch {
    return inMemory.get(scope) ?? null;
  }
}

function saveHeard(scope: string, heard: Set<string>) {
  inMemory.set(scope, new Set(heard));
  try {
    localStorage.setItem(heardKey(scope), JSON.stringify([...heard].slice(-200)));
  } catch {
    // memory only; worst case an alert is announced again after a reload
  }
}

/** Two-note chime synthesised in WebAudio, so there's no sound file to ship. */
function chime(ctx: AudioContext) {
  [880, 1318.5].forEach((freq, i) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    const start = ctx.currentTime + i * 0.18;
    gain.gain.setValueAtTime(0, start);
    gain.gain.linearRampToValueAtTime(0.18, start + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, start + 0.6);
    osc.connect(gain).connect(ctx.destination);
    osc.start(start);
    osc.stop(start + 0.65);
  });
}

function browserVoice(text: string): Promise<boolean> {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window)) return resolve(false);
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-IN";
    u.onend = () => resolve(true);
    u.onerror = () => resolve(false);
    window.speechSynthesis.speak(u);
  });
}

/** Speaks `text` with Polly, falling back to the browser voice if Polly fails or autoplay is
 * blocked. Resolves false if nothing was spoken. */
async function say(text: string): Promise<boolean> {
  const r = await fetch("/api/speaker/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
    signal: AbortSignal.timeout(15_000),
  }).catch(() => null);
  if (r?.ok) {
    const url = URL.createObjectURL(await r.blob());
    try {
      const played = await new Promise<boolean>((resolve) => {
        const audio = new Audio(url);
        audio.onended = () => resolve(true);
        audio.onerror = () => resolve(false);
        audio.play().catch(() => resolve(false));
      });
      if (played) return true;
    } finally {
      URL.revokeObjectURL(url);
    }
  }
  return browserVoice(text);
}

export default function Speaker() {
  const [scope, setScope] = useState<string | null>(null);
  const [paired, setPaired] = useState<boolean | null>(null);
  const [on, setOn] = useState(false);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [heard, setHeard] = useState<Set<string>>(new Set());
  const [speaking, setSpeaking] = useState(false);
  const [listening, setListening] = useState(false);
  const [announce, setAnnounce] = useState(true);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const audio = useRef<AudioContext | null>(null);
  const announced = useRef<Set<string>>(new Set());
  // per unheard alert: times announced, and when the next one is due
  const schedule = useRef(new Map<string, { count: number; nextAt: number }>());
  const busy = useRef(false);
  const unheardNow = useRef<Alert[]>([]);
  const recognition = useRef<ReturnType<typeof makeRecognition>>(null);

  // ?for=desk-2 wins, else whatever desk was set last time
  useEffect(() => {
    const t = setTimeout(() => {
      const fromUrl = new URLSearchParams(window.location.search).get("for");
      let saved: string | null = null;
      try {
        saved = localStorage.getItem("crewops-speaker-for");
      } catch {
        saved = null;
      }
      const pick = [fromUrl, saved, "room"].find((s) => s && SCOPES.some((o) => o.value === s)) ?? "room";
      setScope(pick);
    });
    fetch("/api/speaker/pair")
      .then((r) => r.json())
      .then((d: { paired?: boolean }) => setPaired(Boolean(d.paired)))
      .catch(() => setPaired(false));
    return () => clearTimeout(t);
  }, []);

  const unheard = alerts.filter((a) => !heard.has(a.id));
  useEffect(() => {
    unheardNow.current = unheard;
  });

  const markHeard = useCallback(
    (ids: string[]) => {
      if (!scope) return;
      ids.forEach((id) => schedule.current.delete(id)); // heard, stop announcing
      setHeard((prev) => {
        const next = new Set(prev);
        ids.forEach((id) => next.add(id));
        saveHeard(scope, next);
        return next;
      });
    },
    [scope],
  );

  /** "What are my notifications?" Reads new alerts oldest first and marks them heard. */
  const read = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    setSpeaking(true);
    try {
      const queue = [...unheardNow.current].reverse();
      if (queue.length === 0) {
        await say("You have no new CrewOps notifications.");
      } else {
        await say(`You have ${queue.length} new CrewOps notification${queue.length === 1 ? "" : "s"}.`);
        for (const a of queue) {
          // autoplay can block speech; only mark heard if it was actually spoken
          if (await say(a.speech)) markHeard([a.id]);
        }
      }
    } finally {
      busy.current = false;
      setSpeaking(false);
    }
  }, [markHeard]);

  useEffect(() => {
    if (!on || !paired || !scope) return;
    let stop = false;
    let inFlight = false;
    const tick = async () => {
      if (inFlight) return; // stops a slow response landing after a newer one
      inFlight = true;
      const r = await fetch(`/api/speaker/feed?for=${encodeURIComponent(scope)}`, {
        signal: AbortSignal.timeout(8_000),
      })
        .catch(() => null)
        .finally(() => {
          inFlight = false;
        });
      if (stop) return;
      const data = (await r?.json().catch(() => null)) as { result?: { alerts?: Alert[] }; error?: string } | null;
      if (!r?.ok || !data?.result) {
        setError(data?.error ?? "CrewOps is unreachable; trying again.");
        return;
      }
      setError(null);
      const list = data.result.alerts ?? [];
      setAlerts(list);
      const known = loadHeard(scope);
      if (known === null) {
        // fresh speaker: treat the existing backlog as heard, like a new Echo
        const baseline = new Set(list.map((a) => a.id));
        saveHeard(scope, baseline);
        setHeard(baseline);
        list.forEach((a) => announced.current.add(a.id));
        return;
      }
      setHeard(known);
      // Stop announcing once the disruption has left this desk's list (plan submitted,
      // resolved, closed) or someone has heard it.
      const pending = new Set(list.filter((a) => !known.has(a.id)).map((a) => a.id));
      for (const id of [...schedule.current.keys()]) if (!pending.has(id)) schedule.current.delete(id);
      const fresh = list.filter((a) => !known.has(a.id) && !announced.current.has(a.id));
      if (fresh.length > 0 && audio.current) {
        fresh.forEach((a) => {
          announced.current.add(a.id);
          schedule.current.set(a.id, { count: 0, nextAt: Date.now() + FIRST_DELAY_MS });
        });
        chime(audio.current);
      }
    };
    tick();
    const timer = setInterval(tick, POLL_MS);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [on, paired, scope]);

  // Each new alert is spoken up to three times, oldest first, one at a time, until it's dealt
  // with or acknowledged.
  useEffect(() => {
    if (!on || !announce || !scope) return;
    const forWhom =
      scope === "room" ? "" : scope === "approvals" ? " for approvals" : ` for ${scope.replace("-", " ")}`;
    const timer = setInterval(async () => {
      if (busy.current) return;
      const now = Date.now();
      const due = [...unheardNow.current].reverse().find((a) => {
        const s = schedule.current.get(a.id);
        return s && s.count < MAX_ANNOUNCEMENTS && s.nextAt <= now;
      });
      if (!due) return;
      const entry = schedule.current.get(due.id)!;
      busy.current = true;
      setSpeaking(true);
      try {
        const lead = entry.count === 0 ? `New CrewOps alert${forWhom}.` : "Reminder:";
        await say(`${lead} ${due.speech}`);
        entry.count += 1;
        entry.nextAt = Date.now() + GAP_MS;
        setCounts((c) => ({ ...c, [due.id]: entry.count }));
      } finally {
        busy.current = false;
        setSpeaking(false);
      }
    }, 1_000);
    return () => clearInterval(timer);
  }, [on, announce, scope]);

  useEffect(
    () => () => {
      recognition.current?.stop();
      void audio.current?.close().catch(() => undefined);
    },
    [],
  );

  function switchOn() {
    audio.current ??= new AudioContext();
    void audio.current.resume();
    setOn(true);
  }

  function listen() {
    const r = makeRecognition();
    if (!r) {
      void read();
      return;
    }
    recognition.current = r;
    setListening(true);
    r.onresult = (e) => {
      const last = e.results[e.results.length - 1];
      if (!last?.isFinal) return;
      const said = last[0].transcript.toLowerCase();
      if (/notification|what('s| is) new|anything|messages?/.test(said)) void read();
      else if (/clear|dismiss/.test(said)) markHeard(unheard.map((a) => a.id));
      else void say("Ask: what are my notifications?");
    };
    r.onerror = () => setListening(false);
    r.onend = () => setListening(false);
    r.start();
  }

  function choose(value: string) {
    setScope(value);
    setAlerts([]);
    announced.current = new Set();
    try {
      localStorage.setItem("crewops-speaker-for", value);
    } catch {
      // not remembered across reloads
    }
    const url = new URL(window.location.href);
    url.searchParams.set("for", value);
    window.history.replaceState(null, "", url);
  }

  async function submitCode() {
    const r = await fetch("/api/speaker/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    }).catch(() => null);
    const d = (await r?.json().catch(() => null)) as { error?: string } | null;
    if (r?.ok) setPaired(true);
    else setError(d?.error ?? "Pairing failed.");
  }

  const escalated = unheard.some((a) => a.event === "ESCALATED");
  const ring = !on
    ? "off"
    : speaking
      ? "speaking"
      : listening
        ? "listening"
        : escalated
          ? "escalated"
          : unheard.length
            ? "notify"
            : "idle";
  const scopeLabel = SCOPES.find((s) => s.value === scope)?.label ?? "";
  const now = useUtcClock();
  // Escalations first, then the newest.
  const shown = [...alerts]
    .sort(
      (a, b) =>
        Number(b.event === "ESCALATED" && !heard.has(b.id)) - Number(a.event === "ESCALATED" && !heard.has(a.id)),
    )
    .slice(0, 8);

  return (
    <main className="flex min-h-[100dvh] flex-1 flex-col bg-[#060b16] text-slate-100 lg:h-[100dvh] lg:overflow-hidden">
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 bg-[radial-gradient(ellipse_at_20%_10%,rgba(56,189,248,0.10),transparent_55%),radial-gradient(ellipse_at_90%_90%,rgba(99,102,241,0.08),transparent_50%)]"
      />
      <header className="relative flex flex-wrap items-center gap-3 border-b border-white/5 px-5 py-3">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-white/5 ring-1 ring-white/10">
          <Radio size={15} className="text-sky-300" />
        </span>
        <div className="leading-tight">
          <p className="text-[10px] font-semibold uppercase tracking-[0.28em] text-sky-300/80">CrewOps desk speaker</p>
          <h1 className="text-base font-semibold tracking-tight">{scopeLabel || " "}</h1>
        </div>
        {scope && (
          <select
            value={scope}
            onChange={(e) => choose(e.target.value)}
            aria-label="Which desk this speaker serves"
            className="ml-2 rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-slate-200 outline-none focus:border-sky-400"
          >
            {SCOPES.map((s) => (
              <option key={s.value} value={s.value} className="bg-slate-900">
                Serves: {s.label}
              </option>
            ))}
          </select>
        )}
        <div className="ml-auto flex items-center gap-4 text-xs text-slate-400">
          {now && (
            <span className="font-mono tabular-nums">
              {now.toISOString().slice(11, 16)}Z{" "}
              <span className="text-slate-500">
                · {now.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" })}{" "}
                IST
              </span>
            </span>
          )}
          <span className="flex items-center gap-1.5">
            <span
              className={`h-2 w-2 rounded-full ${!on ? "bg-slate-600" : error ? "bg-red-500" : "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]"}`}
            />
            {!on ? "Off" : error ? "Reconnecting" : "Live"}
          </span>
        </div>
      </header>

      {paired === false ? (
        <section className="relative m-auto w-full max-w-sm rounded-3xl bg-slate-900/80 p-6 text-center ring-1 ring-white/10">
          <p className="text-sm font-semibold">Pair this screen as a speaker</p>
          <p className="mt-1 text-xs text-slate-400">
            Enter the pairing code the administrator set up, once per screen.
          </p>
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            className="mt-4 w-full rounded-xl border border-white/10 bg-slate-950 px-3 py-2 text-center font-mono tracking-widest outline-none focus:border-sky-400"
            placeholder="Pairing code"
            aria-label="Pairing code"
          />
          <button
            type="button"
            onClick={submitCode}
            className="mt-3 w-full rounded-xl bg-sky-500 py-2 text-sm font-medium text-slate-950 hover:bg-sky-400"
          >
            Pair
          </button>
          {error && <p className="mt-2 text-xs text-red-300">{error}</p>}
        </section>
      ) : (
        <div className="relative grid min-h-0 flex-1 gap-6 px-5 py-6 lg:grid-cols-[minmax(340px,0.9fr)_minmax(0,1.3fr)] lg:gap-10 lg:px-10">
          {/* Echo from above; the ring shows state */}
          <section className="flex min-h-0 flex-col items-center justify-center gap-6">
            <div
              className={`speaker-ring speaker-${ring} grid h-56 w-56 place-items-center rounded-full p-[10px] xl:h-64 xl:w-64`}
              aria-live="polite"
              aria-label={
                ring === "notify" || ring === "escalated"
                  ? `${unheard.length} new notification${unheard.length === 1 ? "" : "s"}`
                  : `Speaker ${ring}`
              }
            >
              <div className="grid h-full w-full place-items-center rounded-full bg-gradient-to-b from-slate-800 to-slate-950 shadow-[inset_0_6px_24px_rgba(0,0,0,0.6)]">
                <div className="grid h-[86%] w-[86%] place-items-center rounded-full bg-[radial-gradient(circle,#1e293b_1px,transparent_1.4px)] bg-[length:9px_9px]">
                  {!on ? (
                    <button
                      type="button"
                      onClick={switchOn}
                      className="flex flex-col items-center gap-2 rounded-full px-6 py-4 text-slate-200 hover:text-white"
                    >
                      <Power size={34} />
                      <span className="text-xs">Turn on the speaker</span>
                    </button>
                  ) : ring === "notify" || ring === "escalated" ? (
                    <div
                      className={`flex flex-col items-center gap-1 ${escalated ? "text-red-200" : "text-amber-200"}`}
                    >
                      {escalated ? (
                        <Siren size={30} className="animate-pulse" />
                      ) : (
                        <BellRing size={30} className="animate-pulse" />
                      )}
                      <span className="font-mono text-3xl font-semibold">{unheard.length}</span>
                      <span className="text-[11px] uppercase tracking-widest">new</span>
                      {escalated && (
                        <span className="rounded-full bg-red-500 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-white">
                          {unheard.filter((a) => a.event === "ESCALATED").length} escalated
                        </span>
                      )}
                    </div>
                  ) : ring === "speaking" ? (
                    <div className="flex items-end gap-1" aria-hidden>
                      {[0, 1, 2, 3, 4].map((i) => (
                        <span
                          key={i}
                          className="wave-bar h-10 w-1.5 rounded-full bg-cyan-300"
                          style={{ animationDelay: `${i * 0.12}s` }}
                        />
                      ))}
                    </div>
                  ) : (
                    <Bell size={28} className="text-slate-500" />
                  )}
                </div>
              </div>
            </div>

            {on && (
              <div className="flex flex-wrap items-center justify-center gap-2">
                <button
                  type="button"
                  onClick={() => void read()}
                  disabled={speaking}
                  className="flex items-center gap-2 rounded-full bg-sky-500 px-5 py-2.5 text-sm font-semibold text-slate-950 shadow-lg shadow-sky-500/20 hover:bg-sky-400 disabled:opacity-60"
                >
                  <Volume2 size={16} /> What are my notifications?
                </button>
                <button
                  type="button"
                  onClick={listen}
                  disabled={speaking || listening}
                  className="flex items-center gap-2 rounded-full bg-white/5 px-4 py-2.5 text-sm font-medium text-slate-100 ring-1 ring-white/15 hover:bg-white/10 disabled:opacity-60"
                  title="Say: what are my notifications?"
                >
                  <Mic size={16} className={listening ? "text-sky-300" : ""} /> {listening ? "Listening…" : "Ask"}
                </button>
                {unheard.length > 0 && (
                  <button
                    type="button"
                    onClick={() => markHeard(unheard.map((a) => a.id))}
                    className="flex items-center gap-2 rounded-full bg-amber-400 px-4 py-2.5 text-sm font-semibold text-slate-950 shadow hover:bg-amber-300"
                    title="Acknowledge: stop announcing these"
                  >
                    <Check size={16} /> Got it
                  </button>
                )}
              </div>
            )}
            {on && (
              <label className="flex items-center gap-2 text-xs text-slate-400">
                <input
                  type="checkbox"
                  checked={announce}
                  onChange={(e) => setAnnounce(e.target.checked)}
                  className="accent-sky-400"
                />
                Announce new alerts (up to 3 times)
              </label>
            )}
            {error && <p className="text-xs text-red-300">{error}</p>}
            <p className="max-w-sm text-center text-[11px] text-slate-500">
              No sign-in: this speaker belongs to the desk, not a person. A real Echo, or the Alexa app, gets the same
              alert from the CrewOps skill.
            </p>
          </section>

          {/* only the alert list scrolls */}
          <section className="flex min-h-0 flex-col">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-400">Alert stream</h2>
              <span className="flex gap-3 text-[10px] text-slate-500">
                <span className="flex items-center gap-1">
                  <span className="h-2 w-2 rounded-full bg-red-500" /> Escalated
                </span>
                <span className="flex items-center gap-1">
                  <span className="h-2 w-2 rounded-full bg-amber-400" /> New
                </span>
                <span className="flex items-center gap-1">
                  <span className="h-2 w-2 rounded-full bg-violet-400" /> Approval
                </span>
                <span className="flex items-center gap-1">
                  <span className="h-2 w-2 rounded-full bg-emerald-500" /> Done
                </span>
              </span>
            </div>
            {alerts.length === 0 ? (
              <p className="rounded-2xl bg-white/[0.03] px-4 py-6 text-center text-sm text-slate-400 ring-1 ring-white/10">
                {on ? "All quiet. New alerts for this desk chime here." : "Turn the speaker on to hear alerts."}
              </p>
            ) : (
              <ol className="flex min-h-0 flex-col gap-2.5 overflow-y-auto pr-1 [scrollbar-width:thin]">
                {shown.map((a) => {
                  const look = lookOf(a.event);
                  const fresh = !heard.has(a.id);
                  const Icon = look.icon;
                  return (
                    <li
                      key={a.id}
                      className={`relative overflow-hidden rounded-2xl py-3 pl-5 pr-4 text-sm ring-1 transition ${look.card} ${
                        fresh ? "" : "opacity-60"
                      } ${fresh && a.event === "ESCALATED" ? "escalated-card" : ""}`}
                    >
                      <span className={`absolute inset-y-0 left-0 w-1.5 ${look.rail}`} aria-hidden />
                      <div className="flex items-start gap-3">
                        <Icon size={18} className={`mt-0.5 shrink-0 ${look.iconTone}`} />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span
                              className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${look.tag}`}
                            >
                              {a.title}
                            </span>
                            {a.severity && (
                              <span
                                className={`rounded-full px-2 py-0.5 text-[10px] font-semibold capitalize ring-1 ${SEVERITY_TONE[a.severity]}`}
                              >
                                {a.severity}
                              </span>
                            )}
                            {fresh && counts[a.id] ? (
                              <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] font-medium text-slate-200">
                                Announced {counts[a.id]} of {MAX_ANNOUNCEMENTS}
                              </span>
                            ) : null}
                            <span className="ml-auto font-mono text-[11px] text-slate-400">
                              {a.at.slice(5, 10)} {a.at.slice(11, 16)}Z
                            </span>
                          </div>
                          <p
                            className={`mt-1.5 leading-relaxed ${a.event === "ESCALATED" ? "text-red-50" : "text-slate-200"}`}
                          >
                            {/* severity is in the badge; this text is what gets spoken */}
                            {a.text.replace(/^(High|Medium|Low) severity\. /, "")}
                          </p>
                          <p className="mt-1 font-mono text-[10px] text-slate-500">{a.disruption_id}</p>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
