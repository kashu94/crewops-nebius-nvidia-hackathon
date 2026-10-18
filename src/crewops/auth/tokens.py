"""Check a Cognito access token's signature and claims, before trusting anything in it.

AgentCore's JWT authorizer already verifies the token at the front door. The MCP server
verifies it again: the same check costs little once the pool's public keys are cached, and
it means the server never trusts a token that reached it some other way (a local run, a
misconfigured authorizer, a header injected by mistake).
"""

from __future__ import annotations

from typing import Any

import jwt

from crewops.auth.identity import AuthError


class TokenVerifier:
    def __init__(self, issuer: str, client_id: str, keys: Any = None) -> None:
        """``keys`` resolves a token to its signing key (``jwt.PyJWKClient`` by default,
        which fetches the pool's public keys once and caches them)."""
        if not issuer or not client_id:
            raise AuthError("the token issuer and app client must be configured")
        self.issuer = issuer
        self.client_id = client_id
        self.keys = keys or jwt.PyJWKClient(
            f"{issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600, timeout=3
        )

    def claims(self, token: str) -> dict[str, Any]:
        """The claims of a valid, current access token for this pool and client."""
        try:
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.issuer,
                options={"require": ["exp", "iss", "token_use", "client_id"], "verify_aud": False},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token: {exc}") from None
        if claims.get("token_use") != "access" or claims.get("client_id") != self.client_id:
            raise AuthError("not an access token for this app client")
        return claims


def bearer(header: str | None) -> str:
    """The token in an ``Authorization: Bearer ...`` header."""
    scheme, _, token = (header or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("sign in first: no bearer token")
    return token.strip()
