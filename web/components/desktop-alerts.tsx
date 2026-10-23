"use client";

import { Bell, BellOff, BellRing } from "lucide-react";
import { useEffect, useState } from "react";

import { tool } from "@/lib/client";

import { cx } from "./ui";

type State = "loading" | "unsupported" | "unconfigured" | "off" | "on" | "blocked" | "busy";

function supported(): boolean {
  return (
    typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window
  );
}

function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = base64url.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (base64url.length % 4)) % 4);
  const raw = atob(padded);
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function b64u(buffer: ArrayBuffer | null): string {
  if (!buffer) return "";
  let s = "";
  for (const b of new Uint8Array(buffer)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function sameKey(sub: PushSubscription, key: string): boolean {
  const used = sub.options.applicationServerKey;
  return !used || b64u(used) === key;
}

async function register(sub: PushSubscription): Promise<void> {
  await tool("push_subscribe", {
    endpoint: sub.endpoint,
    p256dh: b64u(sub.getKey("p256dh")),
    auth: b64u(sub.getKey("auth")),
  });
}

async function publicKey(): Promise<string> {
  const r = await fetch("/api/push/key", { cache: "no-store" }).catch(() => null);
  const data = (await r?.json().catch(() => null)) as { key?: string } | null;
  return data?.key ?? "";
}

/** On sign-out, unsubscribe this browser from the person's alerts so a shared desk computer
 * doesn't show the next person someone else's. Best effort and quick. */
export async function forgetDesktopAlerts(): Promise<void> {
  if (!supported()) return;
  const reg = await navigator.serviceWorker.getRegistration("/").catch(() => undefined);
  const sub = await reg?.pushManager.getSubscription().catch(() => null);
  if (!sub) return;
  await tool("push_unsubscribe", { endpoint: sub.endpoint }).catch(() => undefined);
  await sub.unsubscribe().catch(() => undefined);
}

/** Desktop alerts for controllers and approvers: a notification from the browser when a
 * disruption reaches their desk or a plan waits for approval, even with CrewOps closed. */
export function DesktopAlerts() {
  const [state, setState] = useState<State>("loading");
  const [key, setKey] = useState("");
  const [problem, setProblem] = useState("");

  useEffect(() => {
    let live = true;
    (async () => {
      if (!supported()) return setState("unsupported");
      const k = await publicKey();
      if (!live) return;
      setKey(k);
      if (!k) return setState("unconfigured");
      if (Notification.permission === "denied") return setState("blocked");
      const reg = await navigator.serviceWorker.getRegistration("/");
      const sub = await reg?.pushManager.getSubscription();
      if (!sub) return live && setState("off");
      if (!sameKey(sub, k)) {
        // Made with keys since replaced: push services refuse it, so turn it off.
        await sub.unsubscribe().catch(() => undefined);
        return live && setState("off");
      }
      // On in this browser: make it this person's (a shared computer may have been someone
      // else's who never signed out).
      await register(sub);
      if (live) setState("on");
    })().catch(() => live && setState("off"));
    return () => {
      live = false;
    };
  }, []);

  async function turnOn() {
    setState("busy");
    setProblem("");
    try {
      const permission = await Notification.requestPermission();
      // "default" means the prompt was dismissed, not refused: the bell stays usable.
      if (permission === "denied") return setState("blocked");
      if (permission !== "granted") return setState("off");
      const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      await navigator.serviceWorker.ready;
      const subscribe = () => reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
      let sub = await reg.pushManager.getSubscription();
      if (sub && !sameKey(sub, key)) {
        await sub.unsubscribe().catch(() => undefined);
        sub = null;
      }
      try {
        sub = sub ?? (await subscribe());
      } catch {
        // a subscription left half-removed by an earlier turn-off: clear it and try once more
        await (await reg.pushManager.getSubscription())?.unsubscribe().catch(() => undefined);
        sub = await subscribe();
      }
      await register(sub);
      setState("on");
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "the browser refused");
      setState("off");
    }
  }

  async function turnOff() {
    setState("busy");
    try {
      await forgetDesktopAlerts();
    } finally {
      setState("off");
    }
  }

  if (state === "loading" || state === "unsupported" || state === "unconfigured") return null;
  const on = state === "on";
  const title =
    state === "blocked"
      ? "Desktop alerts are blocked for this site in the browser's settings"
      : on
        ? "Desktop alerts are on in this browser (click to turn off)"
        : problem
          ? `Desktop alerts could not be turned on (${problem}). Click to try again`
          : "Turn on desktop alerts: a notification when something needs you, even with CrewOps closed";
  return (
    <button
      className={cx(
        "rounded-md p-1.5 transition-colors hover:bg-active",
        on ? "text-accent" : problem ? "text-critical" : "text-muted hover:text-foreground",
        state === "busy" && "animate-pulse",
      )}
      title={title}
      aria-label={title}
      aria-pressed={on}
      disabled={state === "busy" || state === "blocked"}
      onClick={on ? turnOff : turnOn}
    >
      {state === "blocked" ? <BellOff size={16} /> : on ? <BellRing size={16} /> : <Bell size={16} />}
    </button>
  );
}
