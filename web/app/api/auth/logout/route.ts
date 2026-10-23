import { signOut } from "@/lib/session";
import { sameOrigin } from "@/lib/tools";

export async function POST(request: Request) {
  if (!sameOrigin(request)) {
    return Response.json({ error: "Bad origin." }, { status: 403 });
  }
  await signOut();
  return Response.json({ ok: true });
}
