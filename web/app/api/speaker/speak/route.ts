import { paired } from "@/lib/device";
import { declaredTooLarge, parseJson, RateLimit, readCapped } from "@/lib/limits";
import { synthesize } from "@/lib/polly";
import { sameOrigin } from "@/lib/tools";

const perScreen = new RateLimit(60, 10 * 60 * 1000); // a busy ops room, not a loop

export async function POST(request: Request) {
  if (!sameOrigin(request)) return Response.json({ error: "Bad origin." }, { status: 403 });
  if (declaredTooLarge(request, 8_000)) return Response.json({ error: "Request too large." }, { status: 413 });
  if (!(await paired())) {
    return Response.json({ error: "This screen is not paired as a speaker." }, { status: 401 });
  }
  if (!perScreen.allow("speaker")) {
    return Response.json({ error: "Speech is paused for a few minutes." }, { status: 429 });
  }
  const raw = await readCapped(request, 8_000);
  if (raw === null) return Response.json({ error: "Request too large." }, { status: 413 });
  const body = parseJson(raw) as { text?: unknown } | null;
  const text = typeof body?.text === "string" ? body.text.trim() : "";
  if (!text) return Response.json({ error: "Nothing to say." }, { status: 400 });
  try {
    return new Response(await synthesize(text), {
      headers: { "Content-Type": "audio/mpeg", "Cache-Control": "no-store" },
    });
  } catch (err) {
    console.error("speaker speech failed", err);
    return Response.json({ error: "Speech is unavailable; the alert is on screen." }, { status: 502 });
  }
}
