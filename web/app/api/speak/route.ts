import { declaredTooLarge, parseJson, RateLimit, readCapped } from "@/lib/limits";
import { currentTokens } from "@/lib/session";
import { synthesize } from "@/lib/polly";
import { claimsOf } from "@/lib/tokens";
import { sameOrigin } from "@/lib/tools";

const perUser = new RateLimit(30, 10 * 60 * 1000);

/** Polly for signed-in users only. Locally it picks up the AWS profile from .env.local. */
export async function POST(request: Request) {
  if (!sameOrigin(request)) {
    return Response.json({ error: "Bad origin." }, { status: 403 });
  }
  if (declaredTooLarge(request, 8_000)) {
    return Response.json({ error: "Request too large." }, { status: 413 });
  }
  const tokens = await currentTokens();
  if (!tokens) {
    return Response.json({ error: "Sign in first." }, { status: 401 });
  }
  const user = claimsOf(tokens.id)?.["cognito:username"] ?? "unknown";
  if (!perUser.allow(user)) {
    return Response.json({ error: "Speech is paused for a few minutes; the answers stay on screen." }, { status: 429 });
  }
  const raw = await readCapped(request, 8_000);
  if (raw === null) return Response.json({ error: "Request too large." }, { status: 413 });
  const body = parseJson(raw) as { text?: unknown } | null;
  const text = typeof body?.text === "string" ? body.text.trim() : "";
  if (!text) {
    return Response.json({ error: "Nothing to say." }, { status: 400 });
  }
  try {
    const audio = await synthesize(text);
    return new Response(audio, {
      headers: { "Content-Type": "audio/mpeg", "Cache-Control": "no-store" },
    });
  } catch (err) {
    console.error("speech failed", err);
    return Response.json({ error: "Speech is unavailable; the answer is on screen." }, { status: 502 });
  }
}
