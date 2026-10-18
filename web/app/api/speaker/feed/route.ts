import { DeviceError, deviceCall, paired } from "@/lib/device";
import { ToolCallError } from "@/lib/mcp-result";

const SCOPES = new Set(["room", "approvals", "desk-1", "desk-2", "desk-3"]);

/** Alert feed for a desk or ops-room speaker. Fetched as the speaker device, so nobody has to
 * be signed in. */
export async function GET(request: Request) {
  if (!(await paired())) {
    return Response.json({ error: "This screen is not paired as a speaker." }, { status: 401 });
  }
  const scope = new URL(request.url).searchParams.get("for") ?? "room";
  if (!SCOPES.has(scope)) {
    return Response.json({ error: `A speaker serves one of: ${[...SCOPES].join(", ")}.` }, { status: 400 });
  }
  try {
    return Response.json({ result: await deviceCall("speaker_feed", { scope }) });
  } catch (err) {
    if (err instanceof DeviceError) return Response.json({ error: err.message }, { status: err.status });
    if (err instanceof ToolCallError) return Response.json({ error: err.message, code: err.code }, { status: 422 });
    console.error("speaker feed failed", err);
    return Response.json({ error: "CrewOps is unreachable right now." }, { status: 502 });
  }
}
