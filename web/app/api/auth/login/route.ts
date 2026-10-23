import { signIn, SignInError } from "@/lib/cognito";
import { declaredTooLarge, parseJson, readCapped } from "@/lib/limits";
import { saveTokens } from "@/lib/session";
import { sameOrigin } from "@/lib/tools";

const NAME = /^[a-z0-9._-]{1,64}$/i;

export async function POST(request: Request) {
  if (!sameOrigin(request)) {
    return Response.json({ error: "Bad origin." }, { status: 403 });
  }
  if (declaredTooLarge(request, 2_000)) {
    return Response.json({ error: "Request too large." }, { status: 413 });
  }
  const text = await readCapped(request, 2_000);
  if (text === null) {
    return Response.json({ error: "Request too large." }, { status: 413 });
  }
  const body = parseJson(text) as {
    username?: unknown;
    password?: unknown;
  } | null;
  const username = typeof body?.username === "string" ? body.username.trim() : "";
  const password = typeof body?.password === "string" ? body.password : "";
  if (!NAME.test(username) || !password || password.length > 256) {
    return Response.json({ error: "Enter your username and password." }, { status: 400 });
  }
  try {
    await saveTokens(await signIn(username, password));
    return Response.json({ ok: true });
  } catch (err) {
    if (err instanceof SignInError) {
      return Response.json({ error: err.message }, { status: 401 });
    }
    console.error("sign-in failed", err);
    return Response.json({ error: "Sign-in is unavailable right now." }, { status: 502 });
  }
}
