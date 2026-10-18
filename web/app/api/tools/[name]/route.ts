import { declaredTooLarge } from "@/lib/limits";
import { callTool } from "@/lib/mcp";
import { ToolCallError } from "@/lib/mcp-result";
import { currentTokens } from "@/lib/session";
import { isTool, sameOrigin } from "@/lib/tools";

const MAX_BODY = 16_000;

/** Calls one MCP tool as the signed-in user. Auth and role checks happen in the MCP server;
 * this route just refuses unknown tools and other origins. */
export async function POST(request: Request, ctx: RouteContext<"/api/tools/[name]">) {
  const { name } = await ctx.params;
  if (!isTool(name)) {
    return Response.json({ error: "Unknown tool." }, { status: 404 });
  }
  if (!sameOrigin(request)) {
    return Response.json({ error: "Bad origin." }, { status: 403 });
  }
  if (declaredTooLarge(request, MAX_BODY)) {
    return Response.json({ error: "Request too large." }, { status: 413 });
  }
  const tokens = await currentTokens();
  if (!tokens) {
    return Response.json({ error: "Sign in first." }, { status: 401 });
  }
  const text = await request.text();
  if (text.length > MAX_BODY) {
    return Response.json({ error: "Request too large." }, { status: 413 });
  }
  let args: Record<string, unknown> = {};
  if (text) {
    try {
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
      args = parsed;
    } catch {
      return Response.json({ error: "Arguments must be a JSON object." }, { status: 400 });
    }
  }
  try {
    return Response.json({ result: await callTool(tokens.access, name, args) });
  } catch (err) {
    if (err instanceof ToolCallError) {
      const status = err.code === "UNAUTHORIZED" ? 401 : 422;
      return Response.json({ error: err.message, code: err.code }, { status });
    }
    console.error(`tool ${name} failed`, err);
    return Response.json({ error: "CrewOps is unreachable right now. Try again in a moment." }, { status: 502 });
  }
}
