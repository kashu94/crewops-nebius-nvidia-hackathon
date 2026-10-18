"use client";

import { useEffect, useRef, useState } from "react";

import { parseMessage, subprotocols, subscribeMessage, type DisruptionEvent } from "./live";

export type LiveState = "connecting" | "live" | "offline" | "unavailable";

/** Subscribes to disruption status changes over AppSync Events, reconnecting with backoff.
 * Events only say what changed; callers read the details through the server. */
export function useLiveEvents(onEvent: (event: DisruptionEvent) => void, enabled = true): LiveState {
  const [state, setState] = useState<LiveState>(enabled ? "connecting" : "unavailable");
  const handler = useRef(onEvent);
  useEffect(() => {
    handler.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    if (!enabled) return; // roles that may not watch the channel open no socket
    let socket: WebSocket | null = null;
    let stopped = false;
    let attempt = 0;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let watchdog: ReturnType<typeof setTimeout> | undefined;

    const schedule = () => {
      if (stopped) return;
      setState("offline");
      const delay = Math.min(30_000, 1000 * 2 ** attempt) * (0.5 + Math.random() / 2);
      attempt += 1;
      retry = setTimeout(connect, delay);
    };

    const arm = (ms: number) => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => socket?.close(), ms);
    };

    const giveUp = () => {
      // Signed out, or this role may not watch the channel: retrying cannot help.
      stopped = true;
      setState("unavailable");
    };

    async function connect() {
      if (stopped) return;
      setState("connecting");
      const response = await fetch("/api/live", { cache: "no-store" }).catch(() => null);
      if (stopped) return;
      if (response?.status === 401) return giveUp();
      if (!response?.ok) return schedule();
      const live = (await response.json().catch(() => null)) as {
        url: string;
        host: string;
        token: string;
      } | null;
      if (stopped) return; // the page went away while we waited: open nothing
      if (!live) return schedule();
      const { url, host, token } = live;
      let timeoutMs = 300_000;
      socket = new WebSocket(url, subprotocols(host, token));
      socket.onopen = () => socket?.send(JSON.stringify({ type: "connection_init" }));
      socket.onmessage = (message) => {
        const incoming = parseMessage(String(message.data));
        if (incoming.kind === "ack") {
          timeoutMs = incoming.timeoutMs;
          arm(timeoutMs);
          socket?.send(JSON.stringify(subscribeMessage(crypto.randomUUID(), host, token)));
        } else if (incoming.kind === "keepalive") {
          arm(timeoutMs);
        } else if (incoming.kind === "subscribed") {
          attempt = 0;
          setState("live");
        } else if (incoming.kind === "events") {
          incoming.events.forEach((e) => handler.current(e));
        } else if (incoming.kind === "error") {
          if (/unauthori[sz]ed/i.test(incoming.message)) giveUp();
          socket?.close();
        }
      };
      socket.onclose = () => {
        clearTimeout(watchdog);
        schedule();
      };
    }

    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      clearTimeout(watchdog);
      socket?.close();
    };
  }, [enabled]);

  return state;
}
