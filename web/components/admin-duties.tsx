"use client";

import { Check, Copy, KeyRound, Undo2, UserCog, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button, cx, day, Empty, Mono, Notice, Panel, Spinner, Status } from "@/components/ui";
import { tool } from "@/lib/client";
import { describe, shownStatus, type FeedItem } from "@/lib/types";

// What each admin tier may do, as the server enforces it (crewops.mcp.api and
// crewops.auth.accounts). Shown so nobody has to guess why a button is missing.
const DUTIES: { duty: string; admin: boolean; superAdmin: boolean }[] = [
  { duty: "Revoke a report filed in error, before cover is approved", admin: true, superAdmin: true },
  { duty: "Book planned leave", admin: true, superAdmin: true },
  { duty: "Reset passwords of crew, reporters, controllers, approvers and auditors", admin: true, superAdmin: true },
  { duty: "Reset an admin's password", admin: false, superAdmin: true },
  { duty: "Tighten a legality threshold", admin: true, superAdmin: true },
  { duty: "Relax a threshold back towards the regulatory limit", admin: false, superAdmin: true },
  { duty: "Read the activity log, manage alert addresses", admin: true, superAdmin: true },
  { duty: "Submit, approve or reject a cover plan", admin: false, superAdmin: false },
];

export function AdminDuties({ superAdmin }: { superAdmin: boolean }) {
  const Mark = ({ yes }: { yes: boolean }) =>
    yes ? (
      <Check size={14} className="mx-auto text-emerald-600" aria-label="yes" />
    ) : (
      <X size={14} className="mx-auto text-[#b4b7ba]" aria-label="no" />
    );
  return (
    <Panel title="Who does what" icon={<UserCog size={14} className="text-accent" />}>
      <p className="mb-3 text-xs leading-relaxed text-muted">
        You are signed in as {superAdmin ? "a super admin" : "an admin"}. Admins look after the system and the team, not
        the cover: plans are submitted by controllers and approved by a different approver, never by an admin.
      </p>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted">
            <th className="py-1 font-medium">Duty</th>
            <th className={cx("w-24 py-1 text-center font-medium", !superAdmin && "text-accent")}>Admin</th>
            <th className={cx("w-24 py-1 text-center font-medium", superAdmin && "text-accent")}>Super admin</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {DUTIES.map((d) => (
            <tr key={d.duty}>
              <td className="py-1.5 pr-3">{d.duty}</td>
              <td className="py-1.5">
                <Mark yes={d.admin} />
              </td>
              <td className="py-1.5">
                <Mark yes={d.superAdmin} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

type Message = { tone: "ok" | "error"; text: string } | null;

const REVOCABLE = new Set(["REPORTED", "OPTIONS_READY", "SUBMITTED"]);

/** Reports an admin may still withdraw: nothing approved yet, or an absence only recorded. */
export function RevokeReports({ onChange }: { onChange?: () => void }) {
  const [items, setItems] = useState<FeedItem[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);

  const load = useCallback(() => {
    tool<FeedItem[]>("disruptions", { limit: 100 }).then(
      (all) =>
        setItems(all.filter((d) => REVOCABLE.has(d.status) || (d.status === "CLOSED" && d.handling === "recorded"))),
      (err: Error) => {
        setItems([]);
        setMessage({ tone: "error", text: err.message });
      },
    );
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function revoke(id: string) {
    setBusy(true);
    setMessage(null);
    try {
      await tool("revoke_report", { disruption_id: id, reason });
      setMessage({ tone: "ok", text: `${id} revoked. Anyone it named counts as available again.` });
      setOpen(null);
      setReason("");
      load();
      onChange?.();
    } catch (err) {
      setMessage({ tone: "error", text: err instanceof Error ? err.message : "Failed." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Revoke a report" icon={<Undo2 size={14} className="text-accent" />}>
      <p className="mb-3 text-xs leading-relaxed text-muted">
        For a report filed in error: a reporter&apos;s, a crew member&apos;s own sick or unavailable report, or planned
        leave. Possible until cover is approved; a plan waiting for approval is withdrawn with it. After approval the
        roster has changed, so the controller resolves it instead. Every revoke is in the activity log with your reason.
      </p>
      {message && (
        <div className="mb-3">
          <Notice tone={message.tone}>{message.text}</Notice>
        </div>
      )}
      {items === null ? (
        <Spinner />
      ) : items.length === 0 ? (
        <Empty icon={<Undo2 size={22} />}>Nothing that can be revoked.</Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((d) => (
            <li key={d.disruption_id} className="rounded-xl border border-line px-3 py-2 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="min-w-0">
                  <Mono className="text-muted">{d.disruption_id}</Mono> · {describe(d)}
                  {d.reported_by && <span className="text-muted"> · by {d.reported_by}</span>}
                  {d.reported_utc && <span className="text-muted"> · {day(d.reported_utc)}</span>}
                </span>
                <span className="flex items-center gap-2">
                  <Status value={shownStatus(d.status, d.handling)} />
                  {open !== d.disruption_id && (
                    <Button
                      tone="secondary"
                      className="px-2.5 py-1 text-xs"
                      onClick={() => {
                        setOpen(d.disruption_id);
                        setReason("");
                      }}
                    >
                      Revoke
                    </Button>
                  )}
                </span>
              </div>
              {open === d.disruption_id && (
                <form
                  className="mt-2 flex flex-wrap items-center gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void revoke(d.disruption_id);
                  }}
                >
                  <input
                    autoFocus
                    required
                    maxLength={300}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="Why it is revoked (filed twice, wrong person, wrong dates…)"
                    className="min-w-0 flex-1 rounded-md border border-control bg-surface px-3 py-1.5 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/20"
                  />
                  <Button type="submit" tone="danger" disabled={busy || !reason.trim()} className="py-1.5 text-xs">
                    {busy ? <Spinner /> : <Undo2 size={13} />} Revoke
                  </Button>
                  <Button type="button" tone="ghost" className="py-1.5 text-xs" onClick={() => setOpen(null)}>
                    Cancel
                  </Button>
                </form>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

type Account = { username: string; name: string | null; roles: string[]; crew_id: string | null; may_reset: boolean };

/** Give a team member a new password, shown once. */
export function Passwords({ onChange }: { onChange?: () => void }) {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [shown, setShown] = useState<{ username: string; password: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [message, setMessage] = useState<Message>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    tool<Account[]>("team_accounts").then(setAccounts, (err: Error) => {
      setAccounts([]);
      setMessage({ tone: "error", text: err.message });
    });
  }, []);

  async function reset(username: string) {
    if (!window.confirm(`Give ${username} a new password? Their current one stops working at once.`)) return;
    setBusy(username);
    setMessage(null);
    setShown(null);
    setCopied(false);
    try {
      const out = await tool<{ username: string; password: string }>("reset_password", { username });
      setShown(out);
      onChange?.();
    } catch (err) {
      setMessage({ tone: "error", text: err instanceof Error ? err.message : "Failed." });
    } finally {
      setBusy(null);
    }
  }

  const q = filter.trim().toLowerCase();
  const list = (accounts ?? []).filter(
    (a) =>
      !q || a.username.includes(q) || (a.name ?? "").toLowerCase().includes(q) || a.roles.some((r) => r.includes(q)),
  );

  return (
    <Panel title="Passwords" icon={<KeyRound size={14} className="text-accent" />}>
      <p className="mb-3 text-xs leading-relaxed text-muted">
        A new random password, shown to you once, to pass on in person. You can&apos;t reset your own here, nor the
        speaker device&apos;s; only a super admin resets an admin&apos;s.
      </p>
      {message && (
        <div className="mb-3">
          <Notice tone={message.tone}>{message.text}</Notice>
        </div>
      )}
      {shown && (
        <div className="mb-3 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-sm">
          <p className="text-amber-900">
            New password for <Mono>{shown.username}</Mono>. It won&apos;t be shown again.
          </p>
          <div className="mt-1 flex items-center gap-2">
            <Mono className="select-all rounded bg-white px-2 py-1 text-base">{shown.password}</Mono>
            <Button
              tone="secondary"
              className="px-2.5 py-1 text-xs"
              onClick={() => {
                void navigator.clipboard?.writeText(shown.password).then(() => setCopied(true));
              }}
            >
              {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? "Copied" : "Copy"}
            </Button>
            <Button tone="ghost" className="px-2 py-1 text-xs" onClick={() => setShown(null)}>
              Done
            </Button>
          </div>
        </div>
      )}
      <input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Find a person, sign-in or role"
        aria-label="Find a person"
        className="mb-2 w-full rounded-md border border-control bg-surface px-3 py-1.5 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/20"
      />
      {accounts === null ? (
        <Spinner />
      ) : list.length === 0 ? (
        <Empty icon={<KeyRound size={22} />}>No one matches.</Empty>
      ) : (
        <ul className="max-h-80 divide-y divide-line overflow-y-auto">
          {list.map((a) => (
            <li key={a.username} className="flex min-h-12 items-center justify-between gap-2 py-1.5 text-sm">
              <span className="min-w-0 truncate">
                {a.name ?? a.username} <Mono className="text-muted">{a.username}</Mono>
                <span className="text-muted"> · {a.roles.join(", ").replace(/_/g, " ")}</span>
              </span>
              {a.may_reset ? (
                <Button
                  tone="secondary"
                  className="shrink-0 px-2.5 py-1 text-xs"
                  disabled={busy !== null}
                  onClick={() => void reset(a.username)}
                >
                  {busy === a.username ? <Spinner /> : <KeyRound size={13} />} Reset
                </Button>
              ) : (
                <span className="inline-flex h-8 shrink-0 items-center px-2.5 text-xs text-muted">
                  Not yours to reset
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
