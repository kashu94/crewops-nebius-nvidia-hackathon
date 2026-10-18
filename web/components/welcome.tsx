"use client";

import { Volume2, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { speak, tool } from "@/lib/client";

import { useMe } from "./me";

type WelcomeAlert = { id: string; disruption_id: string; title: string; text: string; at: string };
type Welcome = { since: string | null; alerts: WelcomeAlert[]; speech: string };

const HEARTBEAT_MS = 5 * 60 * 1000;

/** "While you were away": what came to this controller's desk, or to approvals, since they
 * last had CrewOps open. Fetched once per sign-in by the sign-in page and shown on the first
 * page; while a console stays open and visible it tells the server so every few minutes. */
export function WelcomeBack() {
  const me = useMe();
  const [data, setData] = useState<Welcome | null>(null);
  const [playing, setPlaying] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  const staff = me.roles.includes("controller") || me.roles.includes("approver");

  useEffect(() => {
    if (!staff) return;
    let saved: string | null = null;
    try {
      saved = sessionStorage.getItem("crewops-welcome");
      sessionStorage.removeItem("crewops-welcome");
    } catch {
      saved = null;
    }
    if (!saved) return;
    const t = setTimeout(() => {
      try {
        const w = JSON.parse(saved) as Welcome;
        if (w.alerts?.length) setData(w);
      } catch {
        // not shown: the console has the same news
      }
    });
    return () => clearTimeout(t);
  }, [staff]);

  useEffect(() => {
    if (!staff) return;
    const beat = () => {
      if (document.visibilityState === "visible") void tool("still_here").catch(() => undefined);
    };
    const timer = setInterval(beat, HEARTBEAT_MS);
    document.addEventListener("visibilitychange", beat);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", beat);
    };
  }, [staff]);

  useEffect(() => () => audio.current?.pause(), []);

  if (!data) return null;

  async function play() {
    if (!data || playing) return;
    setPlaying(true);
    const blob = await speak(data.speech).catch(() => null);
    if (!blob) {
      setPlaying(false);
      return;
    }
    const url = URL.createObjectURL(blob);
    audio.current = new Audio(url);
    audio.current.onended = () => {
      URL.revokeObjectURL(url);
      setPlaying(false);
    };
    audio.current.play().catch(() => setPlaying(false));
  }

  return (
    <aside
      role="status"
      className="animate-rise fixed right-4 top-20 z-40 w-[min(26rem,calc(100vw-2rem))] rounded-2xl border border-sky-200 bg-white/95 p-4 shadow-2xl backdrop-blur"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-sky-700">While you were away</p>
          <p className="mt-0.5 text-sm text-slate-700">
            {data.alerts.length} update{data.alerts.length === 1 ? "" : "s"} since you last had CrewOps open
          </p>
        </div>
        <button
          type="button"
          onClick={() => setData(null)}
          aria-label="Dismiss"
          className="rounded-full p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
        >
          <X size={15} />
        </button>
      </div>
      <ol className="mt-3 flex max-h-64 flex-col gap-2 overflow-y-auto">
        {data.alerts.map((a) => (
          <li key={a.id}>
            <Link
              href={`/console/${encodeURIComponent(a.disruption_id)}`}
              className="block rounded-xl border border-slate-200 px-3 py-2 text-sm hover:border-sky-300 hover:bg-sky-50/50"
            >
              <span className="flex items-center justify-between gap-2">
                <span className="font-semibold text-slate-800">{a.title}</span>
                <span className="font-mono text-[11px] text-slate-400">
                  {a.at.slice(5, 10)} {a.at.slice(11, 16)}Z
                </span>
              </span>
              <span className="mt-0.5 block text-xs text-slate-600">{a.text}</span>
            </Link>
          </li>
        ))}
      </ol>
      <button
        type="button"
        onClick={play}
        disabled={playing}
        className="mt-3 flex items-center gap-2 rounded-full bg-slate-900 px-4 py-2 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-60"
      >
        <Volume2 size={14} /> {playing ? "Speaking…" : "Hear it"}
      </button>
    </aside>
  );
}
