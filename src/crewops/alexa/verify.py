"""Alexa request verification. Amazon requires this for skills on an HTTPS endpoint, since
the request doesn't come through an Alexa Lambda trigger.

1. ``SignatureCertChainUrl`` must be https on ``s3.amazonaws.com``, port 443, with a path
   under ``/echo.api/``.
2. The chain at that URL must be in date, chain to a trusted root, and cover
   ``echo-api.amazon.com``.
3. ``Signature-256`` must be a valid SHA-256 RSA signature of the raw body.
4. The timestamp must be within 150 seconds.
5. The application id must be ours.

We refuse before touching the body if any check fails. Chains are cached per URL.
"""

from __future__ import annotations

import base64
import json
import posixpath
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

TOLERANCE_SECONDS = 150
ECHO_NAME = "echo-api.amazon.com"


class NotFromAlexa(Exception):
    """Answer 400 and do nothing."""


def normalised_cert_url(url: str) -> str | None:
    """Normalised cert URL (dot segments and double slashes collapsed), or None if it isn't
    Amazon's. Query strings are refused too. Amazon never sends one, and allowing them
    would let anyone force a fresh fetch and cache entry per request."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    path = posixpath.normpath(parsed.path) if parsed.path else ""
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != "s3.amazonaws.com"
        or port not in (None, 443)
        or parsed.query
        or not path.startswith("/echo.api/")
    ):
        return None
    return f"https://s3.amazonaws.com{path}"


def valid_cert_url(url: str) -> bool:
    return normalised_cert_url(url) is not None


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 (an https URL checked above)
        return r.read()


_chains: dict[str, Any] = {}
MAX_CHAINS = 8
"""Amazon rarely rotates the signing cert, so a few cached chains is plenty."""


def _chain(url: str, fetch: Callable[[str], bytes]) -> list[Any]:
    from cryptography import x509

    if url not in _chains:
        try:
            chain = x509.load_pem_x509_certificates(fetch(url))
        except Exception as exc:  # unreachable, or not a certificate chain
            raise NotFromAlexa(f"certificate chain unavailable: {type(exc).__name__}") from None
        if len(_chains) >= MAX_CHAINS:
            _chains.pop(next(iter(_chains)))
        _chains[url] = chain
    return _chains[url]


def _trusted_store() -> Any:
    import certifi
    from cryptography import x509
    from cryptography.x509.verification import Store

    with open(certifi.where(), "rb") as f:
        return Store(x509.load_pem_x509_certificates(f.read()))


def check_chain(chain: list[Any], store: Any = None, now: datetime | None = None) -> Any:
    """Returns the leaf cert after verifying the chain for echo-api.amazon.com."""
    from cryptography import x509
    from cryptography.x509.verification import PolicyBuilder, VerificationError

    if not chain:
        raise NotFromAlexa("empty certificate chain")
    leaf, intermediates = chain[0], chain[1:]
    verifier = (
        PolicyBuilder()
        .store(store or _trusted_store())
        .time(now or datetime.now(UTC))
        .build_server_verifier(x509.DNSName(ECHO_NAME))
    )
    try:
        verifier.verify(leaf, intermediates)
    except VerificationError as exc:
        raise NotFromAlexa(f"certificate chain refused: {exc}") from None
    return leaf


def check_signature(leaf: Any, body: bytes, signature_b64: str) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    try:
        leaf.public_key().verify(
            base64.b64decode(signature_b64), body, padding.PKCS1v15(), hashes.SHA256()
        )
    except (InvalidSignature, ValueError) as exc:
        raise NotFromAlexa("signature does not match the body") from exc


def check_timestamp(envelope: dict[str, Any], now: float | None = None) -> None:
    stamp = (envelope.get("request") or {}).get("timestamp")
    try:
        at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise NotFromAlexa("request has no valid timestamp") from None
    if abs((time.time() if now is None else now) - at) > TOLERANCE_SECONDS:
        raise NotFromAlexa("request is too old or from the future")


def application_id(envelope: dict[str, Any]) -> str | None:
    system = (envelope.get("context") or {}).get("System") or {}
    app = (system.get("application") or {}).get("applicationId")
    return app or ((envelope.get("session") or {}).get("application") or {}).get("applicationId")


def verified(
    body: bytes,
    headers: dict[str, str],
    skill_id: str,
    *,
    fetch: Callable[[str], bytes] = _fetch,
    store: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Parsed envelope if every check passes, else raises ``NotFromAlexa``."""
    lower = {k.lower(): v for k, v in headers.items()}
    url = lower.get("signaturecertchainurl", "")
    signature = lower.get("signature-256", "")
    if not url or not signature:
        raise NotFromAlexa("missing signature headers")
    clean = normalised_cert_url(url)
    if clean is None:
        raise NotFromAlexa("certificate URL is not Amazon's")
    leaf = check_chain(_chain(clean, fetch), store, now)
    check_signature(leaf, body, signature)
    try:
        envelope = json.loads(body)
    except ValueError:
        raise NotFromAlexa("body is not JSON") from None
    check_timestamp(envelope, now.timestamp() if now else None)
    if not skill_id or application_id(envelope) != skill_id:
        raise NotFromAlexa("request is for another skill")
    return envelope
