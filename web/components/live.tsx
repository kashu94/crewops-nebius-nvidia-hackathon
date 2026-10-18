"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

import type { DisruptionEvent } from "@/lib/live";
import { useLiveEvents, type LiveState } from "@/lib/use-live";

type Listener = (event: DisruptionEvent) => void;
type Live = { state: LiveState; latest: (DisruptionEvent & { at: Date }) | null; listen: (l: Listener) => () => void };

const LiveContext = createContext<Live | null>(null);

/** One live-events socket for the whole signed-in app; pages listen to it. */
export function LiveProvider({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  const listeners = useRef(new Set<Listener>());
  const [latest, setLatest] = useState<Live["latest"]>(null);
  const onEvent = useCallback((event: DisruptionEvent) => {
    setLatest({ ...event, at: new Date() });
    listeners.current.forEach((l) => l(event));
  }, []);
  const state = useLiveEvents(onEvent, enabled);
  const listen = useCallback((l: Listener) => {
    listeners.current.add(l);
    return () => {
      listeners.current.delete(l);
    };
  }, []);
  return <LiveContext value={{ state, latest, listen }}>{children}</LiveContext>;
}

export function useLive(): Live {
  const live = useContext(LiveContext);
  if (!live) throw new Error("useLive outside LiveProvider");
  return live;
}

/** Calls `handler` for every live event while the component is mounted. */
export function useLiveListener(handler: Listener) {
  const { listen } = useLive();
  const ref = useRef(handler);
  useEffect(() => {
    ref.current = handler;
  }, [handler]);
  useEffect(() => listen((e) => ref.current(e)), [listen]);
}
