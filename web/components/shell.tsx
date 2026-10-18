"use client";

import { Activity, ChevronDown, LogOut, Mic, Plane, Radio } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { signOut } from "@/lib/client";
import { homeFor, viewsFor } from "@/lib/roles";

import { DesktopAlerts, forgetDesktopAlerts } from "./desktop-alerts";
import { GuideButton } from "./guide";
import { LiveProvider, useLive } from "./live";
import { useMe } from "./me";
import { cx, Mono, Status } from "./ui";
import { WelcomeBack } from "./welcome";

const STAFF = ["controller", "approver", "auditor", "admin", "super_admin"];

export function Shell({ children }: { children: ReactNode }) {
  const me = useMe();
  return (
    <LiveProvider enabled={me.roles.some((r) => STAFF.includes(r))}>
      <div className="flex min-h-screen flex-col">
        <TopBar />
        <Banner />
        <main className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-5 sm:px-6">{children}</main>
        <AlexaButton />
        <WelcomeBack />
      </div>
    </LiveProvider>
  );
}

function TopBar() {
  const me = useMe();
  const router = useRouter();
  const path = usePathname();
  const views = viewsFor(me.roles);
  const current = views.find((v) => path.startsWith(v.href)) ?? views[0];
  const [open, setOpen] = useState(false);
  const menu = useRef<HTMLDivElement>(null);
  const { state } = useLive();

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!menu.current?.contains(e.target as Node)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, []);

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-white/85 backdrop-blur-md">
      <div className="mx-auto flex max-w-[1500px] items-center gap-4 px-4 py-2.5 sm:px-6">
        <Link href={homeFor(me.roles)} className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand text-white shadow-sm">
            <Plane size={16} strokeWidth={2.2} />
          </span>
          <span className="leading-tight">
            <span className="block text-sm font-semibold tracking-tight">CrewOps</span>
            <span className="block text-[11px] text-muted">with Alexa+</span>
          </span>
        </Link>

        <div ref={menu} className="relative">
          <button
            onClick={() => setOpen((o) => !o)}
            className="flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm shadow-sm transition hover:border-sky-300"
            aria-expanded={open}
          >
            <span className="text-muted">View</span>
            <span className="font-semibold">{current.label}</span>
            <ChevronDown size={14} className={cx("transition", open && "rotate-180")} />
          </button>
          {open && (
            <ul className="animate-rise absolute left-0 top-full z-40 mt-2 w-72 overflow-hidden rounded-xl border border-line bg-surface p-1 shadow-xl">
              {views.map((v) => (
                <li key={v.href}>
                  <button
                    aria-current={v.href === current.href ? "page" : undefined}
                    onClick={() => {
                      setOpen(false);
                      router.push(v.href);
                    }}
                    className={cx(
                      "flex w-full flex-col items-start rounded-lg px-3 py-2 text-left transition",
                      v.href === current.href ? "bg-accent-soft" : "hover:bg-sunken",
                    )}
                  >
                    <span className="text-sm font-medium">{v.label}</span>
                    <span className="text-xs text-muted">{v.hint}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="ml-auto flex items-center gap-3 text-sm">
          {state !== "unavailable" && (
            <span
              className={cx(
                "hidden items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium sm:flex",
                state === "live" ? "bg-ok-soft text-emerald-700" : "bg-slate-100 text-slate-600",
              )}
              title="AppSync Events: disruption status changes arrive here as they happen"
            >
              <span className="relative flex h-2 w-2">
                {state === "live" && <span className="absolute inset-0 animate-ping rounded-full bg-emerald-400" />}
                <span
                  className={cx("relative h-2 w-2 rounded-full", state === "live" ? "bg-emerald-500" : "bg-slate-400")}
                />
              </span>
              {state === "live" ? "Live" : state === "connecting" ? "Connecting" : "Reconnecting"}
            </span>
          )}
          <GuideButton />
          {(me.roles.includes("controller") || me.roles.includes("approver")) && <DesktopAlerts />}
          <span className="hidden flex-col items-end leading-tight md:flex">
            <span className="font-medium">{me.user_id}</span>
            <span className="text-[11px] text-muted">
              {me.roles.join(", ").replace(/_/g, " ")}
              {me.desk && (
                <>
                  {" "}
                  · <Mono>{me.desk}</Mono>
                </>
              )}
              {me.crew_id && (
                <>
                  {" "}
                  · <Mono>{me.crew_id}</Mono>
                </>
              )}
            </span>
          </span>
          <button
            className="rounded-lg p-2 text-muted transition hover:bg-sunken hover:text-foreground"
            title="Sign out"
            onClick={async () => {
              await forgetDesktopAlerts().catch(() => undefined);
              await signOut().catch(() => undefined);
              router.replace("/login");
            }}
          >
            <LogOut size={16} />
          </button>
        </div>
      </div>
    </header>
  );
}

/** Alexa+ on every page: a round button in the corner that opens the assistant. */
function AlexaButton() {
  const path = usePathname();
  if (path.startsWith("/assistant")) return null;
  return (
    <Link
      href="/assistant"
      aria-label="Ask Alexa+"
      title="Ask Alexa+"
      className="group fixed bottom-6 right-6 z-40 grid h-16 w-16 place-items-center rounded-full"
    >
      <span className="animate-ring absolute inset-0 rounded-full border-2 border-sky-300/70" />
      <span className="relative grid h-16 w-16 place-items-center rounded-full bg-gradient-to-br from-sky-400 via-sky-500 to-indigo-500 text-white shadow-lg shadow-sky-200 ring-4 ring-white transition group-hover:scale-105">
        <Mic size={24} />
      </span>
      <span className="pointer-events-none absolute right-[4.6rem] whitespace-nowrap rounded-lg bg-slate-900 px-2.5 py-1 text-xs font-medium text-white opacity-0 shadow transition group-hover:opacity-100">
        Ask Alexa+
      </span>
    </Link>
  );
}

/** A banner that slides in with each status change, then steps aside. */
function Banner() {
  const { latest } = useLive();
  const [hidden, setHidden] = useState<number | null>(null);
  useEffect(() => {
    if (!latest) return;
    const t = setTimeout(() => setHidden(latest.at.getTime()), 9000);
    return () => clearTimeout(t);
  }, [latest]);
  const shown = latest && latest.at.getTime() !== hidden ? latest : null;
  if (!shown) return null;
  const critical = shown.status === "REPORTED";
  const waiting = shown.status === "SUBMITTED";
  return (
    <div
      key={shown.at.getTime()}
      role="status"
      aria-live="polite"
      className={cx(
        // Floats over the page, so it never pushes the workspace into scrolling.
        "animate-drop fixed inset-x-0 top-[57px] z-20 border-b shadow-sm",
        critical
          ? "border-red-200 bg-critical-soft"
          : waiting
            ? "border-amber-200 bg-pending-soft"
            : "border-sky-200 bg-accent-soft",
      )}
    >
      <div className="mx-auto flex max-w-[1500px] items-center gap-3 px-4 py-2 text-sm sm:px-6">
        {critical ? <Radio size={16} className="text-critical" /> : <Activity size={16} className="text-accent" />}
        <span className="font-medium">{critical ? "New disruption" : "Status change"}:</span>
        <Link href={`/console/${encodeURIComponent(shown.disruption_id)}`} className="hover:underline">
          <Mono>{shown.disruption_id}</Mono>
        </Link>
        <span className="text-muted">{shown.report_type.replace(/_/g, " ")}</span>
        <span className="flex items-center gap-1.5">
          {shown.previous && <Status value={shown.previous} />}
          {shown.previous && <span className="text-muted">→</span>}
          <Status value={shown.status} />
        </span>
        <span className="ml-auto text-xs text-muted">{shown.at.toLocaleTimeString()}</span>
      </div>
    </div>
  );
}
