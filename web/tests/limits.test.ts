import { describe, expect, it } from "vitest";

import { clientAddress, declaredTooLarge, parseJson, RateLimit, readCapped } from "@/lib/limits";

describe("request limits", () => {
  it("refuses a body declared larger than the limit", () => {
    const req = (length?: string) =>
      new Request("http://x/api", { method: "POST", headers: length ? { "content-length": length } : {} });
    expect(declaredTooLarge(req("100"), 2000)).toBe(false);
    expect(declaredTooLarge(req("5000"), 2000)).toBe(true);
    expect(declaredTooLarge(req("abc"), 2000)).toBe(true);
    expect(declaredTooLarge(req(), 2000)).toBe(false);
  });

  it("allows a few uses per window and then pauses", () => {
    const limit = new RateLimit(2, 1000);
    expect(limit.allow("u", 0)).toBe(true);
    expect(limit.allow("u", 10)).toBe(true);
    expect(limit.allow("u", 20)).toBe(false);
    expect(limit.allow("other", 20)).toBe(true);
    expect(limit.allow("u", 1011)).toBe(true); // the first use has left the window
  });

  it("forgets callers whose uses have left the window", () => {
    const limit = new RateLimit(1, 1000);
    for (let i = 0; i < 5000; i++) limit.allow(`client-${i}`, 0);
    expect(limit.allow("late", 2000)).toBe(true);
    expect((limit as unknown as { uses: Map<string, number[]> }).uses.size).toBe(1);
    expect(limit.allow("late", 2001)).toBe(false); // the live caller is still limited
  });
});

describe("reading a body", () => {
  const stream = (parts: string[]) =>
    new Request("http://x/api", {
      method: "POST",
      body: new ReadableStream({
        start(c) {
          for (const p of parts) c.enqueue(new TextEncoder().encode(p));
          c.close();
        },
      }),
      // @ts-expect-error: Node needs this for a streamed body
      duplex: "half",
    });

  it("reads a body within the limit", async () => {
    expect(await readCapped(stream(['{"a":', "1}"]), 100)).toBe('{"a":1}');
  });

  it("stops a body that grows past the limit, whatever length was declared", async () => {
    expect(await readCapped(stream(["x".repeat(60), "y".repeat(60)]), 100)).toBeNull();
  });

  it("parses JSON or gives null", () => {
    expect(parseJson('{"code":"x"}')).toEqual({ code: "x" });
    expect(parseJson("not json")).toBeNull();
  });
});

describe("client address and pruning", () => {
  it("uses the address the proxy appended, not one the caller put first", () => {
    const req = new Request("http://x", { headers: { "x-forwarded-for": "6.6.6.6, 1.2.3.4" } });
    expect(clientAddress(req)).toBe("1.2.3.4");
    expect(clientAddress(new Request("http://x"))).toBe("direct");
  });

  it("forgets keys whose uses have all left the window", () => {
    const limit = new RateLimit(1, 1000);
    for (let i = 0; i < 1100; i++) limit.allow(`k${i}`, 0);
    limit.allow("late", 5000);
    expect(limit.size).toBeLessThan(10);
  });
});
