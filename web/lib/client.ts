"use client";

// Browser-side calls to this app's own server routes. Tokens never pass through here.

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string | undefined,
    message: string,
  ) {
    super(message);
  }
}

async function post(path: string, body: unknown): Promise<unknown> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined" && path !== "/api/auth/login") {
      // A full load on purpose: it drops every piece of signed-in state in the page. The
      // page asked for comes back after sign-in (a desktop alert opens a disruption).
      const here = window.location.pathname + window.location.search;
      const next = here.startsWith("/login") || here === "/" ? "" : `?next=${encodeURIComponent(here)}`;
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign(`/login${next}`);
    }
    throw new ApiError(response.status, data.code, data.error ?? "Something went wrong.");
  }
  return data;
}

export async function tool<T = unknown>(name: string, args: Record<string, unknown> = {}) {
  const data = (await post(`/api/tools/${name}`, args)) as { result: T };
  return data.result;
}

export const signIn = (username: string, password: string) => post("/api/auth/login", { username, password });

export const signOut = () => post("/api/auth/logout", {});

export async function speak(text: string): Promise<Blob | null> {
  const response = await fetch("/api/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return response.ok ? response.blob() : null;
}
