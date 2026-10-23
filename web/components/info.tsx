"use client";

import { Info as InfoIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { GLOSSARY, type Term } from "@/lib/glossary";

import { cx } from "./ui";

/** A small (i) that explains a term: on hover, on keyboard focus, or on a tap. */
export function Info({ term, className }: { term: Term; className?: string }) {
  const [open, setOpen] = useState(false);
  // Open upwards when there's no room below, so the tooltip never grows the page.
  const [above, setAbove] = useState(false);
  const show = () => {
    const rect = box.current?.getBoundingClientRect();
    setAbove(!!rect && rect.bottom + 220 > window.innerHeight);
    setOpen(true);
  };
  const box = useRef<HTMLSpanElement>(null);
  const entry = GLOSSARY[term];

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <span
      ref={box}
      className={cx("relative inline-flex align-middle", className)}
      onMouseEnter={show}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label={`What is ${entry.title.toLowerCase()}?`}
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          if (open) setOpen(false);
          else show();
        }}
        onFocus={show}
        onBlur={() => setOpen(false)}
        className="grid h-4 w-4 place-items-center rounded-full text-muted transition hover:text-accent"
      >
        <InfoIcon size={13} />
      </button>
      {open && (
        <span
          role="tooltip"
          className={cx(
            "absolute left-1/2 z-50 w-72 max-w-[calc(100vw-2rem)] -translate-x-1/2 rounded-md",
            "border border-line bg-white p-3 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-foreground shadow-xl",
            above ? "bottom-full mb-1.5" : "top-full mt-1.5",
          )}
        >
          <span className="mb-1 block text-[13px] font-semibold text-foreground">{entry.title}</span>
          {entry.text}
        </span>
      )}
    </span>
  );
}
