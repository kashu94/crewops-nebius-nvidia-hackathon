"use client";

import { Info as InfoIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { GLOSSARY, type Term } from "@/lib/glossary";

import { cx } from "./ui";

/** A small (i) that explains a term: on hover, on keyboard focus, or on a tap. */
export function Info({ term, className }: { term: Term; className?: string }) {
  const [open, setOpen] = useState(false);
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
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label={`What is ${entry.title.toLowerCase()}?`}
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className="grid h-4 w-4 place-items-center rounded-full text-slate-400 transition hover:text-accent"
      >
        <InfoIcon size={13} />
      </button>
      {open && (
        <span
          role="tooltip"
          className="animate-rise absolute left-1/2 top-full z-50 mt-1.5 w-72 -translate-x-1/2 rounded-xl border border-line bg-white p-3 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-slate-700 shadow-xl"
        >
          <span className="mb-1 block text-[13px] font-semibold text-slate-900">{entry.title}</span>
          {entry.text}
        </span>
      )}
    </span>
  );
}
