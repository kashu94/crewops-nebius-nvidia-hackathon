import "server-only";

import { cookies } from "next/headers";

import { refresh, revoke, type Tokens } from "./cognito";
import { expiresSoon } from "./tokens";

// The tokens live in httpOnly cookies: page scripts never see the access or refresh
// token. Only /api/live hands the ID token to the page, for the live-events socket.
const ACCESS = "crewops_access";
const ID = "crewops_id";
const REFRESH = "crewops_refresh";

// Browser-session cookies (no expiry): closing the browser signs out. A shared ops-room
// screen must not stay signed in as the last person who used it. The tokens themselves
// still expire (access and ID after an hour, renewed with the refresh token).
const OPTIONS = {
  httpOnly: true,
  secure: process.env.NODE_ENV === "production",
  sameSite: "lax" as const,
  path: "/",
};

export async function saveTokens(tokens: Tokens): Promise<void> {
  const jar = await cookies();
  jar.set(ACCESS, tokens.access, OPTIONS);
  jar.set(ID, tokens.id, OPTIONS);
  if (tokens.refresh) jar.set(REFRESH, tokens.refresh, OPTIONS);
}

export async function clearTokens(): Promise<void> {
  const jar = await cookies();
  for (const name of [ACCESS, ID, REFRESH]) jar.delete(name);
}

/** Signing out: the refresh token is revoked at Cognito, then the cookies go. */
export async function signOut(): Promise<void> {
  const stored = (await cookies()).get(REFRESH)?.value;
  if (stored) await revoke(stored);
  await clearTokens();
}

/** Current tokens, refreshed first when they are about to expire; null when signed out. */
export async function currentTokens(): Promise<Tokens | null> {
  const jar = await cookies();
  const access = jar.get(ACCESS)?.value;
  const id = jar.get(ID)?.value;
  const now = Math.floor(Date.now() / 1000);
  if (access && id && !expiresSoon(access, now) && !expiresSoon(id, now)) {
    return { access, id };
  }
  const stored = jar.get(REFRESH)?.value;
  if (!stored) return null;
  const fresh = await refresh(stored);
  if (!fresh) {
    await clearTokens();
    return null;
  }
  await saveTokens(fresh);
  return fresh;
}
