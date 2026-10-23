import { DeviceError, deviceCall, paired } from "@/lib/device";
import { declaredTooLarge, RateLimit } from "@/lib/limits";
import { ToolCallError } from "@/lib/mcp-result";
import { answerRoomQuestion, type RoomBoard } from "@/lib/room-answers";
import { sameOrigin } from "@/lib/tools";

const perScreen = new RateLimit(60, 10 * 60 * 1000);

/** A question asked at the desk speaker. Only the room's own questions (desks, controllers,
 * open disruptions, today's numbers) get an answer; anything else is turned away politely. */
export async function POST(request: Request) {
  if (!sameOrigin(request)) return Response.json({ error: "Bad origin." }, { status: 403 });
  if (declaredTooLarge(request, 4_000)) return Response.json({ error: "Request too large." }, { status: 413 });
  if (!(await paired())) {
    return Response.json({ error: "This screen is not paired as a speaker." }, { status: 401 });
  }
  if (!perScreen.allow("speaker-ask")) {
    return Response.json({ error: "Too many questions; try again in a few minutes." }, { status: 429 });
  }
  const body = (await request.json().catch(() => null)) as { question?: unknown; scope?: unknown } | null;
  const question = typeof body?.question === "string" ? body.question.trim().slice(0, 300) : "";
  const scope = typeof body?.scope === "string" ? body.scope : "room";
  if (!question) return Response.json({ error: "Ask a question." }, { status: 400 });
  try {
    const board = (await deviceCall("ops_board", {})) as RoomBoard;
    return Response.json({ result: answerRoomQuestion(question, board, scope) });
  } catch (err) {
    if (err instanceof DeviceError) return Response.json({ error: err.message }, { status: err.status });
    if (err instanceof ToolCallError) return Response.json({ error: err.message, code: err.code }, { status: 422 });
    console.error("speaker question failed", err);
    return Response.json({ error: "CrewOps is unreachable right now." }, { status: 502 });
  }
}
