"use client";

import { AlertTriangle, ArrowUpRight, CheckCheck, Fingerprint, ShieldCheck, UserRound, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { FourEyes } from "@/components/four-eyes";
import { useLiveListener } from "@/components/live";
import { useMe } from "@/components/me";
import { Button, Empty, Mono, Notice, Panel, rupees, Spinner, Status } from "@/components/ui";
import { tool } from "@/lib/client";
import type { QueueItem } from "@/lib/types";

function Decision({ item, onDone }: { item: QueueItem; onDone: (text: string, ok: boolean) => void }) {
  const me = useMe();
  const [confirming, setConfirming] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const isApprover = me.roles.includes("approver");
  const ownPlan = item.submitted_by === me.user_id;

  async function act(name: "approve_plan" | "reject_plan") {
    setBusy(true);
    try {
      const out = await tool<{ status: string; reasons?: string[] }>(name, {
        disruption_id: item.disruption_id,
        plan_id: item.plan_id,
        ...(name === "reject_plan" ? { reason } : {}),
      });
      const why = out.reasons?.length ? ` (${out.reasons.join(" · ")})` : "";
      onDone(`${item.plan_id}: ${out.status}${why}`, out.status !== "STALE");
    } catch (err) {
      onDone(err instanceof Error ? err.message : "Failed.", false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="lift rounded-lg border border-amber-200 bg-surface p-4">
      <div className="flex flex-wrap items-start gap-3">
        <span className="animate-beacon grid h-10 w-10 shrink-0 place-items-center rounded-md bg-pending-soft text-pending">
          <Fingerprint size={20} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Mono className="text-sm font-semibold">{item.plan_id}</Mono>
            <span className="text-xs text-muted">for</span>
            <Link
              href={`/console/${encodeURIComponent(item.disruption_id)}`}
              className="flex items-center gap-0.5 text-sm text-accent hover:underline"
            >
              <Mono>{item.disruption_id}</Mono> <ArrowUpRight size={12} />
            </Link>
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 text-xs text-muted">
            <span className="flex items-center gap-1">
              <UserRound size={12} /> {item.submitted_by}
            </span>
            {item.desk && <Mono>{item.desk}</Mono>}
          </p>
        </div>
        <div className="text-right">
          <span className="block font-mono text-lg font-semibold">{rupees(item.total_cost_inr)}</span>
          <span className="text-[11px] text-muted">total cover cost</span>
        </div>
      </div>

      {item.note && (
        <p className="mt-3 rounded-md bg-sunken px-3 py-2 text-sm">
          <span className="text-muted">Note from {item.submitted_by}: </span>
          {item.note}
        </p>
      )}

      <FourEyes
        className="mt-3"
        fingerprint={item.fingerprint}
        submittedBy={item.submitted_by}
        submittedAt={item.submitted_utc}
      />

      <ul className="mt-3 flex flex-col gap-1.5">
        {item.assignments?.map((a) => (
          <li key={a} className="rounded-lg bg-sunken px-3 py-2 font-mono text-xs text-foreground">
            {a}
          </li>
        ))}
        {item.uncovered?.map((u) => (
          <li key={u} className="rounded-lg bg-critical-soft px-3 py-2 text-xs text-red-900">
            Uncovered: {u}
          </li>
        ))}
      </ul>
      {item.warnings && item.warnings.length > 0 && (
        <p className="mt-2 flex items-start gap-1.5 text-xs text-amber-800">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" /> {item.warnings.join(" · ")}
        </p>
      )}

      {isApprover && (
        <div className="mt-4 flex flex-col gap-2 border-t border-line pt-3">
          {ownPlan ? (
            <p className="text-xs text-muted">You submitted this plan; a different approver must decide it.</p>
          ) : rejecting ? (
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                autoFocus
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && e.preventDefault()}
                maxLength={300}
                placeholder="Why it goes back to the controller"
                className="min-w-0 flex-1 rounded-lg border border-line px-3 py-2 text-sm outline-none focus:border-critical"
              />
              <Button type="button" tone="danger" disabled={busy || !reason.trim()} onClick={() => act("reject_plan")}>
                Send back
              </Button>
              <Button type="button" tone="ghost" onClick={() => setRejecting(false)}>
                Cancel
              </Button>
            </div>
          ) : confirming ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="flex items-center gap-1.5 text-xs font-medium text-foreground">
                <ShieldCheck size={14} className="text-accent" /> Legality and the fingerprint are re-checked; the
                roster changes and your approval is sealed to this plan in one step.
              </span>
              <Button type="button" tone="accent" disabled={busy} onClick={() => act("approve_plan")}>
                {busy ? <Spinner /> : <CheckCheck size={14} />} Confirm and commit
              </Button>
              <Button type="button" tone="ghost" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button type="button" tone="accent" onClick={() => setConfirming(true)}>
                <CheckCheck size={14} /> Approve…
              </Button>
              <Button type="button" tone="secondary" onClick={() => setRejecting(true)}>
                <X size={14} /> Reject…
              </Button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

/** A disruption a controller resolved with a remark: the approver signs it off or sends it
 * back. No roster change rides on it, so there is no plan to commit. */
function SignOff({ item, onDone }: { item: QueueItem; onDone: (text: string, ok: boolean) => void }) {
  const [returning, setReturning] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  async function act(name: "confirm_resolution" | "return_resolution") {
    setBusy(true);
    try {
      await tool(name, {
        disruption_id: item.disruption_id,
        ...(name === "return_resolution" ? { reason } : {}),
      });
      onDone(
        name === "confirm_resolution"
          ? `${item.disruption_id} marked resolved.`
          : `${item.disruption_id} sent back to the desk.`,
        true,
      );
    } catch (err) {
      onDone(err instanceof Error ? err.message : "Failed.", false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-lg border border-line bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Link
          href={`/console/${encodeURIComponent(item.disruption_id)}`}
          className="flex items-center gap-0.5 text-sm font-semibold text-accent hover:underline"
        >
          <Mono>{item.disruption_id}</Mono> <ArrowUpRight size={12} />
        </Link>
        <Status value="RESOLVING" />
        <span className="ml-auto flex items-center gap-1 text-xs text-muted">
          <UserRound size={12} /> resolved by {item.submitted_by}
        </span>
      </div>
      {item.summary && <p className="mt-1 text-sm text-muted">{item.summary}</p>}
      <p className="mt-2 rounded-md bg-sunken px-3 py-2 text-sm">
        <span className="text-muted">What was done: </span>
        {item.remark}
      </p>
      <div className="mt-3 flex flex-col gap-2 border-t border-rule pt-3">
        {returning ? (
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              autoFocus
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={300}
              placeholder="Why it goes back to the desk"
              className="min-w-0 flex-1 rounded-md border border-control px-3 py-1.5 text-sm outline-none focus:border-accent"
            />
            <Button
              type="button"
              tone="danger"
              disabled={busy || !reason.trim()}
              onClick={() => act("return_resolution")}
            >
              Send back
            </Button>
            <Button type="button" tone="ghost" onClick={() => setReturning(false)}>
              Cancel
            </Button>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button type="button" tone="primary" disabled={busy} onClick={() => act("confirm_resolution")}>
              {busy ? <Spinner /> : <CheckCheck size={14} />} Mark resolved
            </Button>
            <Button type="button" tone="secondary" onClick={() => setReturning(true)}>
              <X size={14} /> Send back…
            </Button>
          </div>
        )}
      </div>
    </li>
  );
}

export default function Approvals() {
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [signOffs, setSignOffs] = useState<QueueItem[]>([]);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const load = useCallback(() => {
    tool<QueueItem[]>("my_queue").then(
      (q) => {
        setItems(q.filter((i) => i.kind === "to_decide"));
        setSignOffs(q.filter((i) => i.kind === "to_confirm"));
      },
      (err: Error) => setMessage({ tone: "error", text: err.message }),
    );
  }, []);
  useEffect(load, [load]);
  useLiveListener(load);

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
            Plans waiting for approval
            {items && items.length > 0 && (
              <span className="animate-beacon rounded-full bg-pending px-2.5 py-0.5 text-xs font-bold text-white">
                {items.length}
              </span>
            )}
          </h1>
          <p className="text-sm text-muted">
            Roster changes submitted by a controller, waiting for a second person&apos;s sign-off.
          </p>
        </div>
      </div>
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      <Panel bodyClassName="p-3">
        {!items ? (
          <div className="p-4 text-muted">
            <Spinner />
          </div>
        ) : items.length === 0 ? (
          <Empty icon={<ShieldCheck size={24} className="text-emerald-500" />}>Nothing waits for a decision.</Empty>
        ) : (
          <ol className="flex flex-col gap-3">
            {items.map((item) => (
              <Decision
                key={item.plan_id}
                item={item}
                onDone={(text, ok) => {
                  setMessage({ tone: ok ? "ok" : "error", text });
                  load();
                }}
              />
            ))}
          </ol>
        )}
      </Panel>
      <Panel title="Resolved by controllers, waiting for your sign-off" bodyClassName="p-3">
        {signOffs.length === 0 ? (
          <Empty>Nothing waits for sign-off.</Empty>
        ) : (
          <ol className="flex flex-col gap-3">
            {signOffs.map((item) => (
              <SignOff
                key={item.disruption_id}
                item={item}
                onDone={(text, ok) => {
                  setMessage({ tone: ok ? "ok" : "error", text });
                  load();
                }}
              />
            ))}
          </ol>
        )}
      </Panel>
    </div>
  );
}
