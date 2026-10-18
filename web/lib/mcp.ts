import "server-only";

import { createHash } from "node:crypto";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

import { invocationUrl, settings } from "./config";
import { ToolCallError, valueOf, type ToolResult } from "./mcp-result";
import { TOOLS, type ToolName } from "./tools";

const TIMEOUT_MS = 60_000;
const IDLE_MS = 4 * 60_000; // AgentCore ends an idle session after 5 minutes
const MAX_CLIENTS = 200;

type Entry = { client: Promise<Client>; used: number };

// One connected MCP client per access token (renewed hourly), keyed by its hash.
// Reusing it keeps the session AgentCore assigned at connect, so calls land on the same
// warm server instance instead of starting a new one, and skip the handshake.
const clients = new Map<string, Entry>();

function keyOf(token: string): string {
  return createHash("sha256").update(token).digest("hex");
}

async function connect(accessToken: string): Promise<Client> {
  const url = new URL(invocationUrl(settings.region(), settings.runtimeArn()));
  const transport = new StreamableHTTPClientTransport(url, {
    requestInit: { headers: { Authorization: `Bearer ${accessToken}` } },
  });
  const client = new Client({ name: "crewops-web", version: "0.1.0" });
  await client.connect(transport);
  return client;
}

function drop(key: string, entry: Entry) {
  // Only if it is still the cached one: a newer connection may have replaced it.
  if (clients.get(key) === entry) clients.delete(key);
  entry.client.then((c) => c.close()).catch(() => undefined);
}

function sweep(now: number) {
  for (const [key, entry] of clients) {
    if (now - entry.used > IDLE_MS) drop(key, entry);
  }
  // over the cap: evict least recently used
  const extra = clients.size - MAX_CLIENTS;
  if (extra > 0) {
    [...clients.entries()]
      .sort((a, b) => a[1].used - b[1].used)
      .slice(0, extra)
      .forEach(([key, entry]) => drop(key, entry));
  }
}

/** The cached connection for this token, or a new one. `failed` is the connection a call
 * just saw break, and only that one gets replaced. If several calls share a connection when
 * it breaks, the first retry opens a new one and the rest reuse it. Otherwise each retry
 * would close the previous one's fresh connection and fail it with "Connection closed". */
function clientFor(accessToken: string, failed?: Entry): { key: string; entry: Entry } {
  const now = Date.now();
  sweep(now);
  const key = keyOf(accessToken);
  const existing = clients.get(key);
  if (existing && existing !== failed) {
    existing.used = now;
    return { key, entry: existing };
  }
  if (existing) drop(key, existing);
  const entry: Entry = { client: connect(accessToken), used: now };
  entry.client.catch(() => {
    if (clients.get(key) === entry) clients.delete(key);
  });
  clients.set(key, entry);
  return { key, entry };
}

/** Call one MCP tool as the signed-in user: their access token goes to AgentCore, whose
 * authorizer and the server both verify it. */
export async function callTool(accessToken: string, name: ToolName, args: Record<string, unknown>): Promise<unknown> {
  let failed: Entry | undefined;
  for (const retry of [false, true]) {
    const { key, entry } = clientFor(accessToken, failed);
    try {
      const client = await entry.client;
      const result = await client.callTool({ name, arguments: args }, undefined, { timeout: TIMEOUT_MS });
      return valueOf(result as ToolResult, TOOLS[name].list);
    } catch (err) {
      // A tool's own refusal is an answer, and the connection is fine.
      if (err instanceof ToolCallError) throw err;
      // A transport failure (an ended session): drop that connection for every later call.
      drop(key, entry);
      failed = entry;
      // Reads get one retry on a new connection; a write may already have happened.
      if (retry || TOOLS[name].write) throw err;
    }
  }
  throw new Error("unreachable");
}
