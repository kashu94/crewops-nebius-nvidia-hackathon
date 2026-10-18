import { pair, paired } from "@/lib/device";
import { declaredTooLarge, RateLimit } from "@/lib/limits";
import { sameOrigin } from "@/lib/tools";

const attempts = new RateLimit(10, 10 * 60 * 1000); // per client, too slow to guess a code

/** GET: is this screen paired as a speaker? POST: pair it with a code. */
export async function GET() {
  return Response.json({ paired: await paired() });
}

export async function POST(request: Request) {
  if (!sameOrigin(request)) return Response.json({ error: "Bad origin." }, { status: 403 });
  if (declaredTooLarge(request, 1_000)) return Response.json({ error: "Request too large." }, { status: 413 });
  // keyed by client so one caller can't lock every screen out of pairing
  const client = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "direct";
  if (!attempts.allow(client)) {
    return Response.json({ error: "Too many tries; wait a few minutes." }, { status: 429 });
  }
  const body = (await request.json().catch(() => null)) as { code?: unknown } | null;
  const code = typeof body?.code === "string" ? body.code.trim() : "";
  if (!(await pair(code))) return Response.json({ error: "That pairing code is not right." }, { status: 401 });
  return Response.json({ paired: true });
}
