"use client";

import {
  Activity,
  CalendarDays,
  FilePlus2,
  LayoutList,
  ListChecks,
  LogOut,
  Menu,
  Mic,
  Plane,
  Radio,
  Settings,
  Stamp,
  UserRound,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

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
  const [menu, setMenu] = useState(false);
  return (
    <LiveProvider enabled={me.roles.some((r) => STAFF.includes(r))}>
      {/* The frame: sidebar and top bar share one tint with no rule between them; the work
          sits on a white sheet inset 8px from the window's right and bottom edges. */}
      <div className="flex min-h-screen overflow-x-clip bg-background">
        <aside className="sticky top-0 hidden h-screen w-[228px] shrink-0 flex-col px-3 pb-3 lg:flex">
          <Brand />
          <Nav />
          <Account />
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar onMenu={() => setMenu(true)} />
          <Banner />
          <main className="mb-0 flex-1 border-line bg-surface px-4 py-6 sm:px-6 lg:mb-2 lg:mr-2 lg:rounded-2xl lg:border lg:px-8">
            {children}
          </main>
        </div>
        {menu && <MenuSheet onClose={() => setMenu(false)} />}
        <AlexaButton />
        <WelcomeBack />
      </div>
    </LiveProvider>
  );
}

const ICONS: Record<string, typeof Plane> = {
  "/console": LayoutList,
  "/disruptions": ListChecks,
  "/operations": CalendarDays,
  "/report": FilePlus2,
  "/approvals": Stamp,
  "/crew": UserRound,
  "/admin": Settings,
  "/assistant": Mic,
};

function Brand() {
  const me = useMe();
  return (
    <Link href={homeFor(me.roles)} className="flex h-12 items-center gap-2.5 px-2">
      <span className="grid h-7 w-7 place-items-center rounded-md bg-accent text-white">
        <Plane size={15} strokeWidth={2} />
      </span>
      <span className="text-[15px] font-semibold tracking-tight">CrewOps</span>
      <span className="text-xs text-muted">with Alexa+</span>
    </Link>
  );
}

function Nav() {
  const me = useMe();
  const path = usePathname();
  const views = viewsFor(me.roles);
  return (
    <nav aria-label="Views" className="mt-3 flex flex-col gap-0.5">
      {views.map((v) => {
        const Icon = ICONS[v.href] ?? LayoutList;
        const active = path.startsWith(v.href);
        return (
          <Link
            key={v.href}
            href={v.href}
            aria-current={active ? "page" : undefined}
            title={v.hint}
            className={cx(
              "flex h-8 items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors",
              active ? "bg-active font-medium text-foreground" : "text-[#4b4d50] hover:bg-active/60",
            )}
          >
            <Icon size={16} strokeWidth={1.6} className={active ? "text-foreground" : "text-muted"} />
            {v.label}
          </Link>
        );
      })}
    </nav>
  );
}

function Account() {
  const me = useMe();
  const router = useRouter();
  return (
    <div className="mt-auto flex items-center gap-2 rounded-md px-2 py-2">
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-active text-xs font-semibold uppercase text-foreground">
        {me.user_id.slice(0, 1)}
      </span>
      <span className="min-w-0 flex-1 leading-tight">
        <span className="block truncate text-sm font-medium">{me.user_id}</span>
        <span className="block truncate text-xs text-muted">
          {me.roles.join(", ").replace(/_/g, " ")}
          {me.desk && <> · {me.desk}</>}
          {me.crew_id && <> · {me.crew_id}</>}
        </span>
      </span>
      <button
        className="rounded-md p-1.5 text-muted transition-colors hover:bg-active hover:text-foreground"
        title="Sign out"
        aria-label="Sign out"
        onClick={async () => {
          await forgetDesktopAlerts().catch(() => undefined);
          await signOut().catch(() => undefined);
          router.replace("/login");
        }}
      >
        <LogOut size={16} />
      </button>
    </div>
  );
}

function TopBar({ onMenu }: { onMenu: () => void }) {
  const me = useMe();
  const path = usePathname();
  const views = viewsFor(me.roles);
  const current = views.find((v) => path.startsWith(v.href)) ?? views[0];
  const { state } = useLive();

  return (
    <header className="sticky top-0 z-30 flex h-12 shrink-0 items-center gap-3 bg-background px-4 lg:static lg:pl-1 lg:pr-3">
      <button
        className="-ml-1 rounded-md p-1.5 text-muted hover:bg-active hover:text-foreground lg:hidden"
        onClick={onMenu}
        aria-label="Open the menu"
      >
        <Menu size={18} />
      </button>
      <span className="lg:hidden">
        <Brand />
      </span>
      <p className="hidden min-w-0 truncate text-sm text-muted lg:block">
        {current.label} <span className="text-[#9a9c9f]">·</span> {current.hint}
      </p>
      <div className="ml-auto flex items-center gap-2 text-sm">
        {state !== "unavailable" && (
          <span
            className="hidden items-center gap-1.5 text-xs text-muted sm:flex"
            title="Disruption status changes arrive here as they happen"
          >
            <span className={cx("h-2 w-2 rounded-full", state === "live" ? "bg-ok" : "bg-[#9a9c9f]")} />
            {state === "live" ? "Live" : state === "connecting" ? "Connecting" : "Reconnecting"}
          </span>
        )}
        <GuideButton />
        {(me.roles.includes("controller") || me.roles.includes("approver")) && <DesktopAlerts />}
      </div>
    </header>
  );
}

/** The phone's navigation: the sidebar's content in a sheet over the screen. */
function MenuSheet({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const escape = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
      <button className="absolute inset-0 bg-black/30" aria-label="Close the menu" onClick={onClose} />
      <div className="absolute inset-y-0 left-0 flex w-[280px] flex-col bg-background px-3 pb-3 shadow-xl">
        <div className="flex items-center justify-between">
          <Brand />
          <button className="rounded-md p-1.5 text-muted hover:bg-active" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        {/* choosing a view closes the sheet */}
        <div onClick={onClose}>
          <Nav />
        </div>
        <Account />
      </div>
    </div>
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
      className="group fixed bottom-5 right-5 z-40 grid h-12 w-12 place-items-center rounded-full"
    >
      <span className="grid h-12 w-12 place-items-center rounded-full bg-brand text-white shadow-[0_4px_14px_rgb(0_0_0/0.18)] transition-colors group-hover:bg-accent">
        <Mic size={20} />
      </span>
      <span className="pointer-events-none absolute right-14 whitespace-nowrap rounded-md bg-brand px-2.5 py-1 text-xs font-medium text-white opacity-0 transition-opacity group-hover:opacity-100">
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
        "animate-drop fixed right-4 top-14 z-40 w-[min(560px,calc(100vw-2rem))] rounded-lg border shadow-[0_8px_24px_rgb(0_0_0/0.12)]",
        critical
          ? "border-[#f5c2bd] bg-critical-soft"
          : waiting
            ? "border-[#fde68a] bg-pending-soft"
            : "border-[#cfe0ef] bg-accent-soft",
      )}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
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
