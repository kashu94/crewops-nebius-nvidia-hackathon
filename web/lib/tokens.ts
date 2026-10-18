// Reading a Cognito token's claims for display and expiry only. The MCP server verifies
// every token's signature itself; nothing here decides what anyone may do.

export type Claims = {
  username?: string;
  "cognito:username"?: string;
  "cognito:groups"?: string[];
  exp?: number;
  token_use?: string;
};

export function claimsOf(token: string | undefined): Claims | null {
  const payload = token?.split(".")[1];
  if (!payload) return null;
  try {
    const json = Buffer.from(payload.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8");
    const claims = JSON.parse(json);
    return claims && typeof claims === "object" ? (claims as Claims) : null;
  } catch {
    return null;
  }
}

/** True when the token is missing, unreadable, or expires within `marginSeconds`. */
export function expiresSoon(token: string | undefined, nowSeconds: number, marginSeconds = 60) {
  const exp = claimsOf(token)?.exp;
  return typeof exp !== "number" || exp - marginSeconds <= nowSeconds;
}
