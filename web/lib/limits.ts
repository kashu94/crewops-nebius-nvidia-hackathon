// Request limits for the server routes. Next applies no body-size limit to route handlers,
// so each route refuses an oversized request from its declared length before reading it.

export function declaredTooLarge(request: Request, maxBytes: number): boolean {
  const length = Number(request.headers.get("content-length") ?? "0");
  return !Number.isFinite(length) || length > maxBytes;
}

/** The body as text, read no further than `maxBytes`; null when it is longer. A chunked
 * upload declares no length, so what actually arrives is counted, not what was declared. */
export async function readCapped(request: Request, maxBytes: number): Promise<string | null> {
  if (!request.body) return "";
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > maxBytes) {
      await reader.cancel().catch(() => undefined);
      return null;
    }
    chunks.push(value);
  }
  const all = new Uint8Array(size);
  let at = 0;
  for (const chunk of chunks) {
    all.set(chunk, at);
    at += chunk.byteLength;
  }
  return new TextDecoder().decode(all);
}

/** JSON.parse, or null for text that isn't JSON. */
export function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/** The client address a proxy in front of the app appended last; a caller can put any value
 * at the start of X-Forwarded-For, but not after the proxy's own entry. */
export function clientAddress(request: Request): string {
  const hops = (request.headers.get("x-forwarded-for") ?? "").split(",").map((h) => h.trim());
  return hops.filter(Boolean).at(-1) || "direct";
}

/** At most `max` uses per key in any `windowMs`. In-memory and per instance, so it only stops
 * one user running up Polly charges; it isn't a distributed quota. */
export class RateLimit {
  private uses = new Map<string, number[]>();
  private swept = 0;

  constructor(
    private max: number,
    private windowMs: number,
  ) {}

  allow(key: string, now = Date.now()): boolean {
    // Forget callers whose uses have all left the window, so a stream of new keys (anyone can
    // send a new X-Forwarded-For) can't grow the map without bound.
    if (now - this.swept >= this.windowMs) {
      this.swept = now;
      for (const [k, times] of this.uses) {
        if (now - times[times.length - 1] >= this.windowMs) this.uses.delete(k);
      }
    }
    const recent = (this.uses.get(key) ?? []).filter((t) => now - t < this.windowMs);
    if (recent.length >= this.max) {
      this.uses.set(key, recent);
      return false;
    }
    recent.push(now);
    this.uses.set(key, recent);
    return true;
  }

  /** Keys still held, for tests. */
  get size(): number {
    return this.uses.size;
  }
}
