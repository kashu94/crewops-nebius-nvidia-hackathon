// Request limits for the server routes. Next applies no body-size limit to route handlers,
// so each route refuses an oversized request from its declared length before reading it.

export function declaredTooLarge(request: Request, maxBytes: number): boolean {
  const length = Number(request.headers.get("content-length") ?? "0");
  return !Number.isFinite(length) || length > maxBytes;
}

/** At most `max` uses per key in any `windowMs`. In-memory and per instance, so it only stops
 * one user running up Polly charges; it isn't a distributed quota. */
export class RateLimit {
  private uses = new Map<string, number[]>();

  constructor(
    private max: number,
    private windowMs: number,
  ) {}

  allow(key: string, now = Date.now()): boolean {
    const recent = (this.uses.get(key) ?? []).filter((t) => now - t < this.windowMs);
    if (recent.length >= this.max) {
      this.uses.set(key, recent);
      return false;
    }
    recent.push(now);
    this.uses.set(key, recent);
    return true;
  }
}
