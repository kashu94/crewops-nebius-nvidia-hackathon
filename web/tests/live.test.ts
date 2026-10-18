import { describe, expect, it } from "vitest";

import { CHANNEL, PROTOCOL, parseMessage, realtimeUrl, subprotocols, subscribeMessage } from "@/lib/live";

const HOST = "abc.appsync-api.ap-southeast-2.amazonaws.com";

describe("AppSync Events protocol", () => {
  it("connects to the realtime endpoint with the auth header as a subprotocol", () => {
    expect(realtimeUrl("abc.appsync-realtime-api.ap-southeast-2.amazonaws.com")).toBe(
      "wss://abc.appsync-realtime-api.ap-southeast-2.amazonaws.com/event/realtime",
    );
    const [protocol, header] = subprotocols(HOST, "id.token.value");
    expect(protocol).toBe(PROTOCOL);
    expect(header.startsWith("header-")).toBe(true);
    const encoded = header.slice("header-".length);
    expect(encoded).not.toMatch(/[+/=]/);
    const decoded = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
    expect(decoded).toEqual({ host: HOST, Authorization: "id.token.value" });
  });

  it("subscribes to the disruption channel with the same authorization", () => {
    expect(subscribeMessage("sub-1", HOST, "tok")).toEqual({
      type: "subscribe",
      id: "sub-1",
      channel: CHANNEL,
      authorization: { Authorization: "tok", host: HOST },
    });
  });

  it("reads acks, keep-alives, events and errors", () => {
    expect(parseMessage('{"type":"connection_ack","connectionTimeoutMs":300000}')).toEqual({
      kind: "ack",
      timeoutMs: 300000,
    });
    expect(parseMessage('{"type":"ka"}')).toEqual({ kind: "keepalive" });
    const event = {
      kind: "disruption_status",
      disruption_id: "r-1",
      status: "OPTIONS_READY",
      previous: "REPORTED",
      report_type: "crew_unavailable",
    };
    const single = parseMessage(JSON.stringify({ type: "data", id: "s", event: JSON.stringify(event) }));
    expect(single).toEqual({ kind: "events", events: [event] });
    const listed = parseMessage(JSON.stringify({ type: "data", id: "s", event: [JSON.stringify(event)] }));
    expect(listed).toEqual({ kind: "events", events: [event] });
    expect(parseMessage(JSON.stringify({ type: "data", event: '{"kind":"other"}' }))).toEqual({
      kind: "events",
      events: [],
    });
    expect(
      parseMessage(JSON.stringify({ type: "subscribe_error", errors: [{ message: "Unauthorized" }] })),
    ).toEqual({ kind: "error", message: "Unauthorized" });
    expect(parseMessage("not json")).toEqual({ kind: "other" });
  });
});
