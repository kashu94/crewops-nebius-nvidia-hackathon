// The AppSync Events WebSocket protocol, as plain functions (no socket here), so it can be
// tested. Cognito user-pool auth sends the ID token and the API's HTTP host.

export const CHANNEL = "/ops/disruptions";
export const PROTOCOL = "aws-appsync-event-ws";

export type DisruptionEvent = {
  kind: "disruption_status";
  disruption_id: string;
  status: string;
  previous: string | null;
  report_type: string;
};

function base64Url(text: string): string {
  // Tokens and domains are ASCII, which btoa takes as is.
  const b64 = typeof btoa === "function" ? btoa(text) : Buffer.from(text, "utf8").toString("base64");
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function realtimeUrl(realtimeDomain: string): string {
  return `wss://${realtimeDomain}/event/realtime`;
}

export function subprotocols(httpDomain: string, idToken: string): string[] {
  const header = base64Url(JSON.stringify({ host: httpDomain, Authorization: idToken }));
  return [PROTOCOL, `header-${header}`];
}

export function subscribeMessage(id: string, httpDomain: string, idToken: string) {
  return {
    type: "subscribe",
    id,
    channel: CHANNEL,
    authorization: { Authorization: idToken, host: httpDomain },
  };
}

export type Incoming =
  | { kind: "ack"; timeoutMs: number }
  | { kind: "keepalive" }
  | { kind: "subscribed" }
  | { kind: "events"; events: DisruptionEvent[] }
  | { kind: "error"; message: string }
  | { kind: "other" };

function asEvent(value: unknown): DisruptionEvent | null {
  const parsed = typeof value === "string" ? safeJson(value) : value;
  const e = parsed as Partial<DisruptionEvent> | null;
  return e && e.kind === "disruption_status" && typeof e.disruption_id === "string" ? (e as DisruptionEvent) : null;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export function parseMessage(raw: string): Incoming {
  const msg = safeJson(raw) as Record<string, unknown> | null;
  if (!msg) return { kind: "other" };
  switch (msg.type) {
    case "connection_ack":
      return { kind: "ack", timeoutMs: Number(msg.connectionTimeoutMs) || 300000 };
    case "ka":
      return { kind: "keepalive" };
    case "subscribe_success":
      return { kind: "subscribed" };
    case "data": {
      // The event arrives as a JSON string; some clients have seen a list of them.
      const raw = Array.isArray(msg.event) ? msg.event : [msg.event];
      const events = raw.map(asEvent).filter((e): e is DisruptionEvent => e !== null);
      return { kind: "events", events };
    }
    case "connection_error":
    case "subscribe_error":
    case "broadcast_error":
    case "error": {
      const errors = (msg.errors as { message?: string }[] | undefined) ?? [];
      return { kind: "error", message: errors.map((e) => e.message).join("; ") || String(msg.type) };
    }
    default:
      return { kind: "other" };
  }
}
