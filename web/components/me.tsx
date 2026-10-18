"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { tool } from "@/lib/client";
import type { Me } from "@/lib/roles";

const MeContext = createContext<Me | null>(null);

/** Who is signed in, as the MCP server sees them (from the verified token). */
export function MeProvider({ children, fallback }: { children: ReactNode; fallback: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    // A 401 sends the browser to sign-in; anything else is shown, not spun on forever.
    tool<Me>("whoami").then(setMe, (err: Error) => setError(err.message));
  }, []);
  if (me) return <MeContext value={me}>{children}</MeContext>;
  if (error) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <p className="max-w-md rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          {error}{" "}
          <button className="underline" onClick={() => window.location.reload()}>
            Try again
          </button>
        </p>
      </div>
    );
  }
  return <>{fallback}</>;
}

export function useMe(): Me {
  const me = useContext(MeContext);
  if (!me) throw new Error("useMe outside MeProvider");
  return me;
}
