import { settings } from "@/lib/config";

/** Public VAPID key for desktop alert subscriptions. Safe to hand out, the private half stays
 * in Parameter Store. Empty until someone sets it up. */
export async function GET() {
  return Response.json({ key: settings.pushPublicKey() }, { headers: { "Cache-Control": "no-store" } });
}
