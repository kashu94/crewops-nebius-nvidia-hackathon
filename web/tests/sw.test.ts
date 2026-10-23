import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// The service worker run against a fake `self`: which tab a desktop-alert click opens.
type Tab = { url: string; focus: () => Promise<void>; navigate: (url: string) => Promise<void> };

function click(tabs: string[], url: string) {
  const handlers: Record<string, (event: unknown) => void> = {};
  const navigated: string[] = [];
  const opened: string[] = [];
  const open: Tab[] = tabs.map((at) => ({
    url: at,
    focus: async () => undefined,
    navigate: async (to) => void navigated.push(`${at} -> ${to}`),
  }));
  const self = {
    location: { origin: "https://crewops.example" },
    addEventListener: (type: string, handler: (event: unknown) => void) => (handlers[type] = handler),
    clients: {
      matchAll: async () => open,
      openWindow: async (to: string) => void opened.push(to),
      claim: async () => {},
    },
  };
  new Function("self", readFileSync(new URL("../public/sw.js", import.meta.url), "utf8"))(self);
  let done: Promise<unknown> = Promise.resolve();
  handlers.notificationclick({
    notification: { close: () => undefined, data: { url } },
    waitUntil: (p: Promise<unknown>) => (done = p),
  });
  return done.then(() => ({ navigated, opened }));
}

describe("desktop alert click", () => {
  it("opens the disruption in a console tab, never on a desk speaker", async () => {
    expect(await click(["https://crewops.example/speaker?for=desk-2"], "/console/D-1")).toEqual({
      navigated: [],
      opened: ["https://crewops.example/console/D-1"],
    });
    expect(await click(["https://crewops.example/speaker", "https://crewops.example/console"], "/console/D-1")).toEqual(
      {
        navigated: ["https://crewops.example/console -> https://crewops.example/console/D-1"],
        opened: [],
      },
    );
  });

  it("stays on this site whatever the push said", async () => {
    expect(await click([], "https://evil.example/x")).toEqual({ navigated: [], opened: ["https://crewops.example/"] });
  });
});
