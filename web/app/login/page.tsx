"use client";

import { ArrowRight, Eye, EyeOff, Lock, LogOut, Plane } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { NightApproach } from "@/components/login-art";
import { Button, Field, Notice, Spinner } from "@/components/ui";
import { signIn, signOut, tool } from "@/lib/client";
import { homeFor, type Me } from "@/lib/roles";

/** Current session, fetched without the usual redirect to sign-in (we're already on it). */
async function signedIn(): Promise<Me | null> {
  const r = await fetch("/api/tools/whoami", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  }).catch(() => null);
  if (!r?.ok) return null;
  const data = (await r.json().catch(() => null)) as { result?: Me } | null;
  return data?.result ?? null;
}

/** Post-sign-in target from `?next=`, same origin only. Parsed as a URL because "//host" and
 * "/<tab>/host" look like paths but browsers treat them as other sites. */
function nextPage(): string | null {
  const next = new URLSearchParams(window.location.search).get("next");
  if (!next || !next.startsWith("/")) return null;
  let url: URL;
  try {
    url = new URL(next, window.location.origin);
  } catch {
    return null;
  }
  if (url.origin !== window.location.origin || url.pathname.startsWith("/login")) return null;
  return url.pathname + url.search + url.hash;
}

// The rules every cover is checked against (data/seed/rules.json), and the airlines on the desk.
const RULES = [
  ["FDP", "Flight duty period of 13 h, less 30 min per sector after the second"],
  ["Duty", "60 duty hours in any 7 days"],
  ["Flight", "100 block hours in any 28 days"],
  ["Rest", "12 h rest between release and the next report"],
  ["Rating", "A valid rating for the aircraft type"],
  ["Certificates", "Every certificate valid on the duty date"],
  ["Base", "Reserves from their own base, or positioned"],
] as const;
const AIRLINES = [
  ["Deccan Express", "#0284c7"],
  ["Konkan Skyways", "#ea580c"],
  ["Nilgiri Connect", "#059669"],
] as const;

export default function Login() {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [current, setCurrent] = useState<Me | null>(null);

  useEffect(() => {
    signedIn().then(setCurrent);
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      await signIn(String(form.get("username") ?? ""), String(form.get("password") ?? ""));
      const me = await signedIn();
      if (me && (me.roles.includes("controller") || me.roles.includes("approver"))) {
        // "while you were away" summary, once per sign-in rather than per tab. Best effort;
        // must not block sign-in.
        await tool("welcome_back")
          .then((w) => sessionStorage.setItem("crewops-welcome", JSON.stringify(w)))
          .catch(() => undefined);
      }
      window.location.replace(me ? (nextPage() ?? homeFor(me.roles)) : "/login");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
      setBusy(false);
    }
  }

  return (
    <main className="grid flex-1 lg:grid-cols-[1.1fr_1fr]">
      <section className="relative hidden flex-col justify-between overflow-hidden bg-[#040a18] px-12 py-10 text-white lg:flex">
        {/* the night approach to the runway, behind the panel's words */}
        <NightApproach />
        <div className="relative flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-md bg-white text-[#0c2340]">
            <Plane size={16} />
          </span>
          <span className="text-[15px] font-semibold">CrewOps</span>
          <span className="text-sm text-[#b9c6d6]">with Alexa+</span>
        </div>
        <div className="relative max-w-xl">
          <h2 className="text-[34px] font-semibold leading-tight tracking-[-0.01em]">
            Crew control for three airlines
          </h2>
          <p className="mt-3 max-w-md text-base leading-relaxed text-[#d4dde8]">
            A sick call or a closed gate becomes ranked, legal cover within seconds, on the desk, on Alexa and on the
            ops-room wall. A second person approves every roster change.
          </p>
          <p className="mt-2 text-xs text-[#b9c6d6]">Answers by NVIDIA Nemotron on Nebius Token Factory.</p>
          <p className="mt-10 text-sm font-medium text-white">Every cover is checked against seven rules</p>
          <dl className="mt-3 grid grid-cols-[7rem_1fr] gap-x-4 gap-y-2 rounded-lg bg-[#040a18]/55 p-4 text-sm ring-1 ring-white/10">
            {RULES.map(([name, text]) => (
              <div key={name} className="contents">
                <dt className="text-[#b9c6d6]">{name}</dt>
                <dd className="text-[#eef2f7]">{text}</dd>
              </div>
            ))}
          </dl>
        </div>
        <ul className="relative flex flex-wrap gap-x-6 gap-y-2 text-sm text-[#d4dde8]">
          {AIRLINES.map(([name, color]) => (
            <li key={name} className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
              {name}
            </li>
          ))}
        </ul>
      </section>

      <section className="flex items-center justify-center px-4 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <span className="grid h-8 w-8 place-items-center rounded-md bg-[#0c2340] text-white">
              <Plane size={16} />
            </span>
            <span className="text-[15px] font-semibold">CrewOps</span>
            <span className="text-sm text-muted">with Alexa+</span>
          </div>
          {current && (
            <div className="mb-6 rounded-lg border border-line bg-sunken p-4">
              <p className="text-sm text-foreground">
                This browser is signed in as <b>{current.user_id}</b> ({current.roles.join(", ").replace(/_/g, " ")}).
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button type="button" tone="accent" onClick={() => window.location.replace(homeFor(current.roles))}>
                  Continue <ArrowRight size={14} />
                </Button>
                <Button
                  type="button"
                  tone="secondary"
                  onClick={async () => {
                    await signOut().catch(() => undefined);
                    setCurrent(null);
                  }}
                >
                  <LogOut size={14} /> Sign in as someone else
                </Button>
              </div>
            </div>
          )}
          <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
          <p className="mt-1 text-sm text-muted">Use the account your administrator created.</p>
          <form onSubmit={submit} className="mt-6 flex flex-col gap-4">
            <Field label="Username" name="username" autoComplete="username" required autoFocus />
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-foreground">Password</span>
              <span className="relative">
                <input
                  name="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  className="h-10 w-full rounded-md border border-control bg-surface pl-3 pr-10 outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/20"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  aria-pressed={showPassword}
                  title={showPassword ? "Hide password" : "Show password"}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-slate-400 hover:bg-sunken hover:text-slate-700"
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </span>
            </label>
            {error && <Notice tone="error">{error}</Notice>}
            <Button type="submit" tone="primary" disabled={busy} className="h-10">
              {busy ? <Spinner /> : <Lock size={14} />} Sign in
            </Button>
          </form>
        </div>
      </section>
    </main>
  );
}
