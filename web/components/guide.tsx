"use client";

import { ChevronLeft, ChevronRight, HelpCircle, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import type { Role } from "@/lib/roles";

import { AIRLINES, AirlineBadge } from "./airline";
import { useMe } from "./me";
import { Button, cx, Status } from "./ui";

/** A step, and optionally real examples of what it describes, drawn with the app's own
 * components so the guide shows exactly what is on screen. */
type Visual = "kpis" | "feed" | "airlines" | "statuses" | "engine" | "contention" | "approve" | "reports";
type Step = { title: string; text: string; visual?: Visual };

const COMMON: Step[] = [
  {
    title: "Alexa+ is in the corner",
    text:
      "The round button bottom-right opens Alexa+ on any page. Ask by voice or text: who covers a sick captain, " +
      "who is on reserve, what a rule says. Every figure comes from the live roster and is checked before it is shown.",
  },
  {
    title: "The (i) icons",
    text: "Hover or tap any (i) for what a term means: delay, reserve cover kept, minutes away, joint plan and more.",
  },
];

const AIRLINES_STEP: Step = {
  title: "Which airline",
  text:
    "Crew control works for three airlines, each with its own aircraft and crew. The small logo on every disruption " +
    "says whose it is; hover it for the name. Cover for a seat only ever comes from that airline's own crew.",
  visual: "airlines",
};

const STATUS_STEP: Step = {
  title: "What the coloured labels mean",
  text:
    "Every disruption carries one label for where it stands, and changes live as people act. A second label says how " +
    "serious it is: High (a flight is at risk however it is handled), Medium (it needs a real decision, or flies " +
    "today or tomorrow) or Low (a reserve covers it with time to spare). Hover it for why.",
  visual: "statuses",
};

const BY_ROLE: Partial<Record<Role, Step[]>> = {
  controller: [
    {
      title: "Your console at a glance",
      text: "The four figures at the top count disruptions right now. Only the middle two are yours to act on.",
      visual: "kpis",
    },
    {
      title: "The ingestion feed",
      text:
        "Every disruption appears here a moment after it is reported, newest first, and is analysed within a minute. " +
        "Each one goes to the controller with the fewest open disruptions; you never pick, and nor does the reporter.",
      visual: "feed",
    },
    AIRLINES_STEP,
    STATUS_STEP,
    {
      title: "The solution engine",
      text:
        "Select a disruption to see its cover: the recommendation first, then every legal option, cheapest first. " +
        "Each one has passed every rule for the whole trip: no clashing flights, rest before, between and after, hour " +
        "limits, aircraft rating, certificates and base. Anyone who failed is under 'Excluded candidates' with the rule.",
      visual: "engine",
    },
    {
      title: "Competing for crew, and the same seat",
      text:
        "Two different disruptions can want the same reserve. When they do, both desks are warned, and 'Build a joint " +
        "plan' finds the cheapest answer for both. One person reported out on two days of one trip is one seat, not " +
        "two: it goes to one desk, and one cover fills both.",
      visual: "contention",
    },
    {
      title: "Submit, then four eyes",
      text:
        "Pick an option (or keep the recommendation) and submit. A different person, an approver, approves it; " +
        "approval re-checks every rule and changes the roster at once. If they send it back, or the roster changes " +
        "meanwhile, it returns to you under 'Sent back to you'.",
    },
  ],
  approver: [
    {
      title: "Plans waiting for approval",
      text:
        "Each plan shows who replaces whom, the cost and any crew another desk also wants. Approving re-checks every rule " +
        "and changes the roster in the same step; sending back needs a reason the controller will see. You never approve " +
        "a plan you submitted.",
      visual: "approve",
    },
    {
      title: "Every desk is yours to review",
      text:
        "Approvers are not tied to a desk: you see and approve plans from every controller. Another approver's decision " +
        "shows up on your screen at once, and the second one to click is told it was decided meanwhile.",
    },
    AIRLINES_STEP,
    STATUS_STEP,
  ],
  reporter: [
    {
      title: "The operating week",
      text:
        "Operations shows a week at a time (arrows move it, from five days back to two months ahead), every flight by day: airline, aircraft, captain, first officer and cabin crew, " +
        "passengers booked and any open disruption with its severity. Crew reported unavailable are struck through in " +
        "rose. Click a crew name to report them unavailable that day, or 'Delay' to report a flight late.",
    },
    {
      title: "Record a disruption",
      text:
        "Pick the type (sick crew, delay, gate closure...), pick the crew, flight, gate or station from the lists and record " +
        "it. CrewOps works out what it breaks and who must be told, and gives it to a controller.",
    },
    {
      title: "Follow every report",
      text:
        "'My reports' lists yours; 'From crew' lists what crew reported about themselves. Each card says how it stands " +
        "and which controller has it. Open one for every detail.",
      visual: "reports",
    },
    STATUS_STEP,
  ],
  crew: [
    {
      title: "Your week",
      text: "Your trips, duty hours against the limits, and any notice for you, such as a certificate about to lapse.",
    },
    {
      title: "Can't fly?",
      text:
        "Your calendar shows the last five days and the next two months: flying days in blue, days off in grey, days " +
        "you are out in rose, today outlined, with every flight listed below. Tap 'I can't fly' to report yourself unavailable. It reaches the reporting desk and crew control at once, and you " +
        "can follow it under 'My reports'. Days you have already reported cannot be reported again, and show in rose " +
        "in your week. Unwell on a day off? Report it too: nothing needs covering, but you won't be called out then.",
    },
  ],
  auditor: [
    { title: "Read everything", text: "Every disruption, plan, decision and audit trail, read-only." },
    AIRLINES_STEP,
    STATUS_STEP,
  ],
  admin: [
    {
      title: "Reports filed in error and passwords",
      text:
        "Revoke a report filed in error (a reporter's, a crew member's own sick report, planned leave) with a reason, until cover is approved; a plan waiting for approval is withdrawn with it. " +
        "Reset a team member's password: you get a new random one to pass on, once. Only a super admin resets an admin's. Admins never submit or approve a plan.",
    },
    {
      title: "Rules and alerts",
      text:
        "Book planned leave (it is re-crewed in advance, with no urgency). Tighten legality thresholds (never looser than regulation; only a super admin may relax one back), read the " +
        "activity log, and subscribe email addresses to a role's or an operational team's alerts.",
    },
  ],
};

function Example({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-line bg-sunken/60 p-3">{children}</div>;
}

function FeedRow({
  id,
  mine,
  what,
  airline,
  status,
}: {
  id: string;
  mine: boolean;
  what: string;
  airline: string;
  status: string;
}) {
  return (
    <div className="rounded-md border border-line bg-white px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[11px] text-muted">13 Oct 05:00Z</span>
        <Status value={status} />
      </div>
      <p className="mt-1 flex items-center gap-1.5 text-sm">
        <AirlineBadge compact code={airline} /> {what}
      </p>
      <p className="mt-0.5 flex items-center gap-2 text-[11px]">
        <span className="font-mono text-muted">{id}</span>
        {mine && <span className="rounded bg-pending-soft px-1.5 font-semibold text-amber-800">on your desk</span>}
      </p>
    </div>
  );
}

/** Each airline's aircraft, as in the operating week's records. */
const FLEET: Record<string, string> = {
  DX: "VT-DXA, VT-DXB (A320)",
  KS: "VT-DXC, VT-DXD (A320)",
  NG: "VT-DXE, VT-DXF (ATR 72)",
};

const STATUS_MEANING: [string, string][] = [
  ["REPORTED", "Just arrived; being analysed (under a minute)."],
  ["OPTIONS_READY", "Crew to cover: legal options are ready to choose from."],
  ["DECISION_NEEDED", "No legal cover, or a day too long for anyone: decide with operations."],
  ["TO_RESOLVE", "Nothing to re-crew (gates moved, a delay crew absorb): mark resolved with a remark."],
  ["SUBMITTED", "A plan is waiting for an approver."],
  ["COMMITTED", "Approved: the roster has changed."],
  ["CLOSED", "Ended without a plan, with the reason recorded."],
];

function StepVisual({ visual }: { visual: Visual }) {
  switch (visual) {
    case "kpis":
      return (
        <Example>
          <dl className="grid gap-2 text-sm">
            {[
              ["Open disruptions", "Every disruption still being worked, on any desk."],
              ["Waiting on your desk", "Yours, with options ready: they need your choice."],
              ["Sent back to you", "Your plans an approver returned, or that went stale."],
              ["Resolved", "Approved or closed, across the network."],
            ].map(([k, v]) => (
              <div key={k} className="grid grid-cols-[9.5rem_1fr] gap-2">
                <dt className="font-semibold">{k}</dt>
                <dd className="text-muted">{v}</dd>
              </div>
            ))}
          </dl>
        </Example>
      );
    case "feed":
      return (
        <Example>
          <div className="flex flex-col gap-2">
            <FeedRow
              id="r-8e33994254a"
              mine
              what="C-1042 unavailable 2026-10-13 (sick)"
              airline="KS"
              status="OPTIONS_READY"
            />
            <FeedRow id="r-gate-blr2" mine={false} what="BLR-G2 closed 00:00–08:00Z" airline="DX" status="TO_RESOLVE" />
          </div>
          <ul className="mt-3 flex flex-col gap-1 text-xs text-foreground">
            <li>
              <span className="rounded bg-pending-soft px-1.5 font-semibold text-amber-800">on your desk</span> yours to
              plan and submit.
            </li>
            <li>
              <b>No label:</b> another controller&apos;s. You can open and read it, and suggest cover; only its desk
              submits.
            </li>
          </ul>
        </Example>
      );
    case "airlines":
      return (
        <Example>
          <ul className="flex flex-col gap-2">
            {Object.keys(AIRLINES).map((code) => (
              <li key={code} className="flex items-center justify-between gap-3 text-sm">
                <AirlineBadge code={code} />
                <span className="text-xs text-muted">
                  flight numbers {code}… · {FLEET[code]}
                </span>
              </li>
            ))}
          </ul>
        </Example>
      );
    case "statuses":
      return (
        <Example>
          <ul className="flex flex-col gap-1.5">
            {STATUS_MEANING.map(([s, m]) => (
              <li key={s} className="grid grid-cols-[8.5rem_1fr] items-start gap-2 text-xs text-foreground">
                <span>
                  <Status value={s} />
                </span>
                {m}
              </li>
            ))}
          </ul>
        </Example>
      );
    case "engine":
      return (
        <Example>
          <ul className="flex flex-col gap-1.5 text-xs text-foreground">
            <li>
              <b>Recommended:</b> the cheapest legal cover, with the next price and what cancelling would cost.
            </li>
            <li>
              <b>Options 1, 2, 3…:</b> each a person who passed every rule; open one to see each rule&apos;s margin.
            </li>
            <li>
              <b>
                <span className="text-red-800 line-through">Struck through</span>:
              </b>{" "}
              reported unavailable since the options were worked out; cannot be chosen.
            </li>
            <li>
              <b>Then, if needed:</b> a command-qualified first officer, a captain in the right seat, flying one cabin
              crew short, and cancelling last, each behind a warning.
            </li>
          </ul>
        </Example>
      );
    case "contention":
      return (
        <Example>
          <div className="flex flex-col gap-2 text-xs">
            <p className="rounded-lg border border-amber-200 bg-pending-soft px-2 py-1.5 text-amber-900">
              <b>Competing for the same crew:</b> another disruption ranks this reserve too. Agree who takes whom, or
              build a joint plan.
            </p>
            <p className="rounded-lg border border-[#cfe0ef] bg-accent-soft/70 px-2 py-1.5 text-[#173f63]">
              <b>The same seat is also reported in r-…:</b> one person out on more days of one trip. Plan it once.
            </p>
          </div>
        </Example>
      );
    case "approve":
      return (
        <Example>
          <ul className="flex flex-col gap-1 text-xs text-foreground">
            <li>
              <b>Approve…</b> asks you to confirm, then re-checks every rule and commits.
            </li>
            <li>
              <b>Reject…</b> needs a reason; the plan goes back to its controller.
            </li>
            <li>
              <Status value="STALE" /> the roster changed after it was submitted; nothing was written.
            </li>
          </ul>
        </Example>
      );
    case "reports":
      return (
        <Example>
          <ul className="flex flex-col gap-1.5 text-xs text-foreground">
            <li className="flex items-center gap-2">
              <Status value="QUEUED" /> received, being checked against the roster.
            </li>
            <li className="flex items-center gap-2">
              <Status value="REJECTED" /> refused, with the reason (for example an unknown flight).
            </li>
            <li className="flex items-center gap-2">
              <Status value="OPTIONS_READY" /> with crew control: the card names the controller desk.
            </li>
          </ul>
        </Example>
      );
  }
}

function stepsFor(roles: Role[]): Step[] {
  const own = roles.flatMap((r) => BY_ROLE[r] ?? (r === "super_admin" ? (BY_ROLE.admin ?? []) : []));
  return [...own, ...COMMON];
}

const SEEN = "crewops-guide-seen";

export function GuideButton() {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [i, setI] = useState(0);
  const steps = stepsFor(me.roles);

  useEffect(() => {
    // First visit by this person in this browser: open the guide once.
    let seen: string[] = [];
    try {
      seen = JSON.parse(localStorage.getItem(SEEN) ?? "[]") as string[];
    } catch {
      return; // storage unavailable (private window): the button still works
    }
    if (seen.includes(me.user_id)) return;
    const t = setTimeout(() => {
      setOpen(true);
      try {
        localStorage.setItem(SEEN, JSON.stringify([...seen, me.user_id]));
      } catch {
        // not remembered: it opens again next time, which is harmless
      }
    }, 400); // after the page has drawn
    return () => clearTimeout(t);
  }, [me.user_id]);

  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
      if (e.key === "ArrowRight") setI((n) => Math.min(n + 1, steps.length - 1));
      if (e.key === "ArrowLeft") setI((n) => Math.max(n - 1, 0));
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [open, steps.length]);

  const step = steps[i];
  return (
    <>
      <button
        type="button"
        onClick={() => {
          setI(0);
          setOpen(true);
        }}
        className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-muted transition hover:bg-sunken hover:text-foreground"
        title="How CrewOps works"
      >
        <HelpCircle size={16} /> <span className="hidden sm:inline">Guide</span>
      </button>
      {open &&
        step &&
        // On the page body: the top bar's blur would otherwise confine a fixed overlay to the bar.
        createPortal(
          <div className="fixed inset-0 z-50 grid place-items-center bg-brand/30 p-4" onClick={() => setOpen(false)}>
            <div
              role="dialog"
              aria-modal
              aria-label="How CrewOps works"
              className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg bg-white p-6 shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-accent">
                  Step {i + 1} of {steps.length}
                </span>
                <button
                  className="rounded-lg p-1 text-muted hover:bg-sunken"
                  onClick={() => setOpen(false)}
                  aria-label="Close"
                >
                  <X size={16} />
                </button>
              </div>
              <h2 key={step.title} className="mt-2 text-xl font-semibold">
                {step.title}
              </h2>
              <p key={`${step.title}-text`} className="mt-2 text-sm leading-relaxed text-foreground">
                {step.text}
              </p>
              {step.visual && (
                <div key={`${step.title}-visual`} className="mt-3">
                  <StepVisual visual={step.visual} />
                </div>
              )}
              <div className="mt-5 flex items-center justify-between">
                <div className="flex gap-1.5">
                  {steps.map((s, n) => (
                    <span
                      key={s.title}
                      className={cx("h-1.5 w-5 rounded-full", n === i ? "bg-accent" : "bg-slate-200")}
                    />
                  ))}
                </div>
                <div className="flex gap-2">
                  <Button type="button" tone="ghost" disabled={i === 0} onClick={() => setI(i - 1)}>
                    <ChevronLeft size={14} /> Back
                  </Button>
                  {i < steps.length - 1 ? (
                    <Button type="button" tone="accent" onClick={() => setI(i + 1)}>
                      Next <ChevronRight size={14} />
                    </Button>
                  ) : (
                    <Button type="button" tone="accent" onClick={() => setOpen(false)}>
                      Done
                    </Button>
                  )}
                </div>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
