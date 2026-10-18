import { settings } from "@/lib/config";
import { realtimeUrl } from "@/lib/live";
import { currentTokens } from "@/lib/session";

/** Token and endpoints for the live-events socket. AppSync's Cognito mode wants the ID token,
 * not the access token. Not cached. */
export async function GET() {
  const tokens = await currentTokens();
  if (!tokens) {
    return Response.json({ error: "Sign in first." }, { status: 401 });
  }
  return Response.json(
    {
      url: realtimeUrl(settings.eventsRealtimeDomain()),
      host: settings.eventsHttpDomain(),
      token: tokens.id,
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
