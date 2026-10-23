import { Fragment, type ReactNode } from "react";

import { cx } from "./ui";

// An answer as the engine writes it, set out to read: one paragraph per line, a list where
// the line is "lead: a; b; c", and ids, flights, rules and amounts picked out. Nothing is
// added or reworded; only the layout changes.

const TOKEN =
  /(₹[\d,]+(?:\.\d+)?|\bC-\d{4}\b|\bP-\d{4}\b|\b[A-Z]{2}\d{3}(?:-\d{4}-\d{2}-\d{2})?\b|\bRULE-[A-Z]{3,4}-\d{2}\b|\bVT-[A-Z]{3}\b|\b\d{1,2}:\d{2}Z\b)/g;

function marked(text: string): ReactNode[] {
  return text.split(TOKEN).map((part, i) => {
    if (i % 2 === 0) return <Fragment key={i}>{part}</Fragment>;
    const money = part.startsWith("₹");
    return (
      <span
        key={i}
        className={cx(
          "rounded px-1 font-mono text-[0.92em]",
          money ? "bg-emerald-50 font-semibold text-emerald-800" : "bg-accent-soft text-[#173f63]",
        )}
      >
        {part}
      </span>
    );
  });
}

/** Sentences the engine runs together that read better on their own line. */
const BREAK = /\. (?=(?:Next:|Cancelling|\d+ legal options|Desks:|Those are|Which |Only |Nothing ))/g;

/** "Lead: a; b" (or three items without a lead) as a lead and its items. */
function asList(line: string): { lead: string; items: string[] } | null {
  const parts = line.split("; ");
  if (parts.length < 2 || (parts.length < 3 && !parts[0].includes(": "))) return null;
  const colon = parts[0].indexOf(": ");
  if (colon < 0) return { lead: "", items: parts };
  return { lead: parts[0].slice(0, colon + 1), items: [parts[0].slice(colon + 2), ...parts.slice(1)] };
}

export function AnswerText({ text, className }: { text: string; className?: string }) {
  const lines = text
    .replace(BREAK, ".\n")
    .split("\n")
    .filter((l) => l.trim());
  return (
    <div className={cx("flex flex-col gap-2", className)}>
      {lines.map((line, i) => {
        const list = asList(line);
        if (!list) return <p key={i}>{marked(line)}</p>;
        return (
          <div key={i}>
            {list.lead && <p className="font-medium">{marked(list.lead)}</p>}
            <ul className="mt-1 list-disc space-y-0.5 pl-5">
              {list.items.map((item, j) => (
                <li key={j}>{marked(item.replace(/\.$/, ""))}</li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}
