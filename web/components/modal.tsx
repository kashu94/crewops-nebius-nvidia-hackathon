"use client";

import { X } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** A dialog in the middle of the screen with the page blurred behind it. Closes on the
 * close button, Escape, or a click outside. Portalled to the body because a transformed
 * parent would otherwise trap the fixed overlay. */
export function Modal({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    // The page behind stays put while the dialog is open.
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", key);
      document.body.style.overflow = overflow;
    };
  }, [onClose]);

  return createPortal(
    <div className="fixed inset-0 z-50 grid place-items-center bg-brand/35 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal
        className={`flex max-h-[85vh] w-full flex-col overflow-hidden rounded-lg bg-white shadow-2xl ${
          wide ? "max-w-2xl" : "max-w-lg"
        }`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-line px-6 py-4">
          <div className="min-w-0 flex-1">{title}</div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-full p-1.5 text-muted transition hover:bg-sunken hover:text-foreground"
          >
            <X size={16} />
          </button>
        </div>
        <div className="overflow-y-auto px-6 py-5">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
