import { beforeEach, describe, expect, it, vi } from "vitest";

// A fake MCP client: each connection either answers or, once AgentCore has ended its
// session, fails every call. Closing a connection fails its calls still in flight, as the
// SDK does ("Connection closed").
const state = vi.hoisted(() => ({ connections: 0, broken: new Set<number>(), closed: new Set<number>() }));

vi.mock("server-only", () => ({}));
vi.mock("@/lib/config", () => ({
  settings: { region: () => "ap-southeast-2", runtimeArn: () => "arn:runtime" },
  invocationUrl: () => "https://example.invalid/mcp",
}));
vi.mock("@modelcontextprotocol/sdk/client/streamableHttp.js", () => ({
  StreamableHTTPClientTransport: class {},
}));
vi.mock("@modelcontextprotocol/sdk/client/index.js", () => ({
  Client: class {
    id = 0;
    private pending: ((err: Error) => void)[] = [];
    async connect() {
      this.id = ++state.connections;
    }
    callTool() {
      return new Promise((resolve, reject) => {
        this.pending.push(reject);
        setTimeout(() => {
          if (state.closed.has(this.id) || state.broken.has(this.id)) reject(new Error("Connection closed"));
          else resolve({ structuredContent: { result: [`answer from connection ${this.id}`] } });
        }, 5);
      });
    }
    async close() {
      state.closed.add(this.id);
      this.pending.forEach((reject) => reject(new Error("Connection closed")));
    }
  },
}));

const { callTool } = await import("@/lib/mcp");

describe("MCP connections", () => {
  beforeEach(() => {
    state.broken.clear();
    state.closed.clear();
  });

  it("several calls on a session that ended share one new connection", async () => {
    const token = `token-${Math.random()}`;
    await callTool(token, "disruptions", {}); // connection 1, cached
    const first = state.connections;
    state.broken.add(first); // AgentCore ends the session
    const answers = await Promise.all([
      callTool(token, "disruptions", {}),
      callTool(token, "my_queue", {}),
      callTool(token, "disruptions", {}),
    ]);
    // One replacement, used by all three: no retry closed another's fresh connection.
    expect(state.connections).toBe(first + 1);
    expect(answers).toEqual(Array(3).fill([`answer from connection ${first + 1}`]));
    expect(state.closed.has(first + 1)).toBe(false);
  });
});
