"use client";

import { BadgeCheck, Mic, Pause, Play, RotateCcw, Send, Sparkles, Square, Volume2, VolumeX } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { speak, tool } from "@/lib/client";
import { makeRecognition, type Recognition } from "@/lib/speech";

import { AnswerText } from "./answer-text";
import { cx } from "./ui";

type Answer = {
  answer: string;
  verified: boolean;
  source: string;
  awaiting: string | null;
  intent: string | null;
  notes: string[];
  model_calls: number;
  /** The model service's circuit breaker: "open" while it is paused after failures. */
  models?: { state: string; retry_in_seconds: number };
};

type Turn = { id: number; question: string; answer?: Answer; error?: string };

const SOURCE: Record<string, string> = {
  template: "From the engine",
  explained: "Reworded, every fact kept",
  model: "Model answer, verified",
  clarify: "Question back",
  refused: "Refused",
  smalltalk: "Conversation",
};

type Playback = "idle" | "playing" | "paused";

const VOICE_ERRORS: Record<string, string> = {
  "not-allowed": "The microphone is blocked for this site. Allow it in the browser's address bar, or type instead.",
  "service-not-allowed": "This browser doesn't allow speech recognition here. Use Chrome or Edge, or type instead.",
  network: "The browser's speech service couldn't be reached. Check the connection, or type instead.",
  "no-speech": "I didn't hear anything. Tap the microphone and speak, or type instead.",
  "audio-capture": "No microphone was found. Type the question instead.",
  aborted: "Listening stopped.",
};

function newConversation() {
  return `web-${crypto.randomUUID()}`;
}

/** Fourteen bars that breathe while CrewOps listens or thinks. */
function Waveform({ active, tone }: { active: boolean; tone: "listen" | "think" | "speak" }) {
  return (
    <div className="flex h-10 items-center justify-center gap-[5px]" aria-hidden>
      {Array.from({ length: 14 }).map((_, i) => (
        <span
          key={i}
          className={cx(
            "h-full w-[4px] rounded-full transition-colors",
            active ? "wave-bar" : "scale-y-[0.18]",
            tone === "listen" ? "bg-sky-400" : tone === "speak" ? "bg-emerald-400" : "bg-indigo-400",
          )}
          style={{
            animationDelay: `${(i % 7) * 90}ms`,
            animationDuration: `${780 + (i % 4) * 120}ms`,
          }}
        />
      ))}
    </div>
  );
}

export function AlexaModule({
  suggestions,
  className,
  wide = false,
}: {
  suggestions: string[];
  className?: string;
  /** The assistant page: the speaker on the left, the conversation on the right. */
  wide?: boolean;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [typed, setTyped] = useState("");
  const [heard, setHeard] = useState("");
  const [listening, setListening] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [voice, setVoice] = useState(true);
  const [conversation, setConversation] = useState(newConversation);
  const conversationRef = useRef(conversation);
  const recognition = useRef<Recognition | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const clipUrl = useRef<string | null>(null);
  const [canListen, setCanListen] = useState(false);
  const [playback, setPlayback] = useState<Playback>("idle");
  const [spokenId, setSpokenId] = useState<number | null>(null);

  function stop() {
    audio.current?.pause();
    if (audio.current) audio.current.currentTime = 0;
    setPlayback("idle");
  }

  useEffect(() => {
    conversationRef.current = conversation;
  }, [conversation]);

  useEffect(() => {
    recognition.current = makeRecognition();
    setCanListen(recognition.current !== null);
    return () => {
      audio.current?.pause();
      if (clipUrl.current) URL.revokeObjectURL(clipUrl.current);
    };
  }, []);

  async function ask(question: string) {
    const text = question.trim();
    if (!text || thinking) return;
    const id = Date.now();
    stop();
    setTurns((t) => [...t, { id, question: text }]);
    setThinking(true);
    try {
      const answer = await tool<Answer>("ask", {
        question: text,
        conversation_id: conversationRef.current,
      });
      setTurns((t) => t.map((x) => (x.id === id ? { ...x, answer } : x)));
      if (voice) {
        const clip = await speak(answer.answer);
        if (clip) {
          audio.current?.pause();
          if (clipUrl.current) URL.revokeObjectURL(clipUrl.current);
          clipUrl.current = URL.createObjectURL(clip);
          const player = new Audio(clipUrl.current);
          player.onended = () => setPlayback("idle");
          audio.current = player;
          setSpokenId(id);
          player.play().then(
            () => setPlayback("playing"),
            () => setPlayback("idle"),
          );
        }
      }
    } catch (err) {
      const error = err instanceof Error ? err.message : "Something went wrong.";
      setTurns((t) => t.map((x) => (x.id === id ? { ...x, error } : x)));
    } finally {
      setThinking(false);
    }
  }

  const silence = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [voiceError, setVoiceError] = useState<string | null>(null);

  function listen() {
    const r = recognition.current;
    if (!r) return;
    if (listening) {
      // A second tap ends listening; whatever was heard so far is asked.
      r.stop();
      return;
    }
    stop();
    setVoiceError(null);
    let finalText = "";
    let interimText = "";
    const quiet = () => {
      if (silence.current) clearTimeout(silence.current);
      // Nothing heard for 10 seconds: stop rather than listen forever.
      silence.current = setTimeout(() => r.stop(), 10_000);
    };
    r.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) finalText += result[0].transcript;
        else interim += result[0].transcript;
      }
      interimText = interim;
      setHeard(finalText || interim);
      quiet();
    };
    r.onerror = (event) => {
      setVoiceError(VOICE_ERRORS[event.error] ?? `Voice input stopped (${event.error}). Type the question instead.`);
    };
    r.onend = () => {
      if (silence.current) clearTimeout(silence.current);
      setListening(false);
      setHeard("");
      const said = (finalText || interimText).trim();
      if (said) ask(said);
    };
    try {
      r.start();
      setListening(true);
      quiet();
    } catch {
      // Already running in this tab (a double tap): end it, the next tap starts again.
      r.stop();
      setListening(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    ask(typed);
    setTyped("");
  }

  const last = [...turns].reverse().find((t) => t.answer || t.error);
  const busy = listening || thinking || playback === "playing";
  const latestId = turns.length ? turns[turns.length - 1].id : null;

  return (
    <div
      className={cx(
        wide ? "grid min-h-0 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" : "flex min-h-0 flex-col gap-4",
        className,
      )}
    >
      <div className="flex min-w-0 flex-col gap-4">
        {/* The speaker */}
        <div className="relative overflow-hidden rounded-2xl border border-sky-100 bg-gradient-to-b from-white via-sky-50/60 to-white p-5">
          <div className="flex items-start gap-4">
            <button
              onClick={listen}
              disabled={!canListen || thinking}
              title={canListen ? "Talk to CrewOps" : "Voice input needs Chrome or Edge; type below instead"}
              className="group relative grid h-20 w-20 shrink-0 place-items-center rounded-full disabled:cursor-not-allowed"
            >
              {busy && (
                <>
                  <span className="animate-ring absolute inset-0 rounded-full border-2 border-sky-300" />
                  <span className="animate-ring absolute inset-0 rounded-full border-2 border-sky-300 [animation-delay:800ms]" />
                </>
              )}
              <span
                className={cx(
                  "relative grid h-20 w-20 place-items-center rounded-full border-4 shadow-lg transition",
                  listening
                    ? "border-sky-400 bg-sky-500 text-white"
                    : thinking
                      ? "border-indigo-300 bg-indigo-500 text-white"
                      : "border-sky-200 bg-white text-sky-600 group-hover:border-sky-400 group-hover:shadow-sky-200",
                  !canListen && "opacity-60",
                )}
              >
                {thinking ? <Sparkles size={26} /> : <Mic size={26} />}
              </span>
            </button>
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold uppercase tracking-[0.12em] text-sky-700">
                  {listening
                    ? "Listening"
                    : thinking
                      ? "Checking the roster and the rules"
                      : playback === "playing"
                        ? "Speaking"
                        : playback === "paused"
                          ? "Paused"
                          : "Alexa+ · CrewOps"}
                </span>
                <div className="flex items-center gap-1">
                  {playback !== "idle" && (
                    <>
                      <button
                        className="flex items-center gap-1 rounded-md bg-white px-2 py-1 text-xs font-medium text-slate-700 shadow-sm ring-1 ring-line transition hover:text-foreground"
                        title={playback === "playing" ? "Pause the answer" : "Resume the answer"}
                        onClick={() => {
                          const a = audio.current;
                          if (!a) return;
                          if (playback === "playing") {
                            a.pause();
                            setPlayback("paused");
                          } else {
                            a.play().then(
                              () => setPlayback("playing"),
                              () => setPlayback("idle"),
                            );
                          }
                        }}
                      >
                        {playback === "playing" ? <Pause size={13} /> : <Play size={13} />}
                        {playback === "playing" ? "Pause" : "Resume"}
                      </button>
                      <button
                        className="rounded-md p-1.5 text-muted transition hover:bg-white hover:text-foreground"
                        title="Stop"
                        onClick={stop}
                      >
                        <Square size={13} />
                      </button>
                    </>
                  )}
                  <button
                    className="rounded-md p-1.5 text-muted transition hover:bg-white hover:text-foreground"
                    title={voice ? "Spoken answers on" : "Spoken answers off"}
                    onClick={() => {
                      if (voice) stop();
                      setVoice((v) => !v);
                    }}
                  >
                    {voice ? <Volume2 size={15} /> : <VolumeX size={15} />}
                  </button>
                  <button
                    className="rounded-md p-1.5 text-muted transition hover:bg-white hover:text-foreground"
                    title="New conversation"
                    onClick={() => {
                      stop();
                      setTurns([]);
                      setConversation(newConversation());
                    }}
                  >
                    <RotateCcw size={15} />
                  </button>
                </div>
              </div>
              <Waveform active={busy} tone={listening ? "listen" : playback === "playing" ? "speak" : "think"} />
              <div key={last?.id ?? "idle"} className="animate-rise min-h-[3.5rem]">
                {listening ? (
                  <p className="text-lg text-sky-700">{heard || "Go ahead… (tap the microphone again to stop)"}</p>
                ) : voiceError ? (
                  <p className="text-sm text-amber-800">{voiceError}</p>
                ) : last?.answer ? (
                  <AnswerText
                    text={last.answer.answer}
                    className="max-h-40 overflow-y-auto pr-2 text-[15px] leading-relaxed text-slate-800 [scrollbar-width:thin]"
                  />
                ) : last?.error ? (
                  <p className="text-sm text-critical">{last.error}</p>
                ) : (
                  <p className="text-sm text-muted">Tap the microphone and ask, or pick a question below.</p>
                )}
                {last?.answer?.awaiting && !listening && (
                  <p className="mt-2 text-xs font-medium text-sky-700">Just answer — I remember what you asked.</p>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Suggested questions */}
        <div className="flex flex-wrap gap-2">
          {suggestions.map((s) => (
            <button
              key={s}
              onClick={() => ask(s)}
              disabled={thinking}
              className="lift rounded-full border border-line bg-surface px-3 py-1.5 text-left text-xs text-slate-700 disabled:opacity-50"
            >
              “{s}”
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="flex gap-2">
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder="Ask CrewOps anything about the roster…"
            maxLength={1000}
            className="min-w-0 flex-1 rounded-xl border border-line bg-surface px-3.5 py-2.5 text-sm outline-none transition focus:border-accent focus:ring-4 focus:ring-sky-100"
          />
          <button
            disabled={thinking || !typed.trim()}
            className="grid h-10 w-10 place-items-center rounded-xl bg-accent text-white shadow-sm transition hover:bg-sky-700 disabled:opacity-40"
            title="Ask"
          >
            <Send size={16} />
          </button>
        </form>
      </div>

      {/* The conversation, newest first; the latest exchange stands out */}
      <section className={cx("flex min-h-0 min-w-0 flex-col gap-2", wide && "lg:h-full")}>
        {wide && (
          <h2 className="text-[13px] font-semibold uppercase tracking-[0.08em] text-slate-600">
            Conversation {turns.length > 0 && <span className="font-mono text-muted">{turns.length}</span>}
          </h2>
        )}
        {wide && turns.length === 0 && (
          <p className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">
            Your questions and CrewOps&apos; answers appear here.
          </p>
        )}
        <ol className="flex min-h-0 flex-col gap-2 overflow-y-auto pr-1 [scrollbar-width:thin]">
          {[...turns].reverse().map((t) => (
            <li
              key={t.id}
              className={cx(
                "animate-rise rounded-xl border p-3 text-sm transition",
                t.id === latestId
                  ? "border-sky-300 bg-gradient-to-br from-sky-50 to-white shadow-sm ring-2 ring-sky-100"
                  : "border-line bg-sunken/60 opacity-80",
              )}
            >
              {t.id === latestId && (
                <span className="mb-1 inline-flex items-center gap-1 rounded-full bg-accent-soft px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sky-800">
                  {t.id === spokenId && playback === "playing" ? (
                    <>
                      <Volume2 size={11} /> Speaking
                    </>
                  ) : (
                    "Latest"
                  )}
                </span>
              )}
              <p className="font-medium text-slate-800">{t.question}</p>
              {t.answer && (
                <>
                  <AnswerText text={t.answer.answer} className="mt-1.5 text-slate-700" />
                  <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
                    <span
                      className={cx(
                        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium",
                        t.answer.verified ? "bg-ok-soft text-emerald-700" : "bg-slate-100 text-slate-600",
                      )}
                    >
                      {t.answer.verified && <BadgeCheck size={12} />}
                      {t.answer.verified ? "Verified" : "No facts stated"}
                    </span>
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600">
                      {SOURCE[t.answer.source] ?? t.answer.source}
                    </span>
                    {t.answer.intent && (
                      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600">
                        {t.answer.intent.replace(/_/g, " ").toLowerCase()}
                      </span>
                    )}
                  </div>
                  {t.answer.models && t.answer.models.state !== "closed" && (
                    <p className="mt-1.5 inline-flex items-center gap-1.5 rounded-lg bg-amber-50 px-2 py-1 text-xs text-amber-900 ring-1 ring-amber-200">
                      Models paused after repeated failures: answered straight from the roster
                      {t.answer.models.retry_in_seconds > 0 && <> (retrying in {t.answer.models.retry_in_seconds}s)</>}.
                    </p>
                  )}
                  {t.answer.source === "refused" && t.answer.notes.some((n) => n.startsWith("blocked:")) && (
                    <p className="mt-1.5 inline-flex items-center gap-1.5 rounded-lg bg-red-50 px-2 py-1 text-xs font-medium text-red-800 ring-1 ring-red-200">
                      Refused and logged for the administrator.
                    </p>
                  )}
                  {t.answer.notes.length > 0 && (
                    <details className="mt-1.5 text-xs text-muted">
                      <summary className="cursor-pointer">How this was answered</summary>
                      <ul className="mt-1 list-disc pl-5">
                        {t.answer.notes.map((n) => (
                          <li key={n}>{n}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                </>
              )}
              {t.error && <p className="mt-1.5 text-critical">{t.error}</p>}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
