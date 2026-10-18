// Runs once when the server starts. Some networks hand out IPv6 addresses but do not route
// IPv6, and AWS endpoints resolve to IPv6 first, so every AWS call (sign-in, the MCP
// server, speech) waited on a connection that never opened. Prefer IPv4 addresses, which
// every network here routes; IPv6 is still used where it is the only address.
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const dns = await import("node:dns");
    dns.setDefaultResultOrder("ipv4first");
  }
}
