"""Minimal web push sender (no pywebpush dependency).

Delivery goes through the browser vendor's push service (FCM for Chrome, WNS for Edge,
Mozilla's for Firefox), which costs nothing. Payloads are encrypted per browser with
RFC 8291 aes128gcm, so the push service can't read them, and signed with our VAPID key
(RFC 8292) so nobody else can push to our subscribers.

    uv run python -m crewops.notify.webpush keys   # one-off, creates the VAPID key pair

The private key lives in Parameter Store as a SecureString. The public key is a plain
parameter because the web app needs it for browsers to subscribe.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

PUSH_HOSTS = (
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
)
"""Allow-list for subscription endpoints, plus ``*.notify.windows.com``. The endpoint comes
from the browser, so without this the notifier would POST wherever it was told."""
SUBJECT = "https://github.com/kashu94/crewops-nebius-nvidia-hackathon"
"""VAPID ``sub`` claim, a contact URL for the push services."""
RECORD_SIZE = 4096

Post = Callable[[str, bytes, dict[str, str]], int]


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def allowed_endpoint(endpoint: str) -> bool:
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host in PUSH_HOSTS or host.endswith(".notify.windows.com"))


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    from cryptography.hazmat.primitives import hashes, hmac

    def mac(key: bytes, data: bytes) -> bytes:
        h = hmac.HMAC(key, hashes.SHA256())
        h.update(data)
        return h.finalize()

    prk = mac(salt, ikm)
    return mac(prk, info + b"\x01")[:length]  # one block is enough for 32 bytes or fewer


def encrypt(
    payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None, key: Any = None
) -> bytes:
    """RFC 8291 request body, one aes128gcm record. Fresh salt and ECDH key unless given."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    ua_public = unb64u(p256dh)
    secret = unb64u(auth)
    browser = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ours = key or ec.generate_private_key(ec.SECP256R1())
    as_public = ours.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    shared = ours.exchange(ec.ECDH(), browser)
    ikm = _hkdf(secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    if len(payload) + 17 > RECORD_SIZE:
        raise ValueError("push payload too large for one record")
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # \x02 = last record delimiter
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + body


def vapid_header(endpoint: str, private_key: Any, public_b64: str, now: float | None = None) -> str:
    """``Authorization: vapid t=<JWT>, k=<public key>`` for this push service."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    parsed = urlparse(endpoint)
    claims = {
        "aud": f"{parsed.scheme}://{parsed.netloc}",
        "exp": int((time.time() if now is None else now) + 12 * 3600),
        "sub": SUBJECT,
    }
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{head}.{body}".encode()
    r, s = decode_dss_signature(private_key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    signature = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={head}.{body}.{signature}, k={public_b64}"


def _post(url: str, body: bytes, headers: dict[str, str]) -> int:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as r:  # noqa: S310 (allow-listed hosts)
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


class Sender:
    def __init__(self, private_pem: str, public_b64: str, post: Post = _post) -> None:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        self.key = load_pem_private_key(private_pem.encode(), password=None)
        self.public_b64 = public_b64
        self.post = post

    def send(self, subscription: dict[str, Any], message: dict[str, Any]) -> str:
        """ "sent", "gone" (forget the subscription) or "failed"."""
        endpoint = subscription["endpoint"]
        if not allowed_endpoint(endpoint):
            return "failed"
        body = encrypt(json.dumps(message).encode(), subscription["p256dh"], subscription["auth"])
        status = self.post(
            endpoint,
            body,
            {
                "Authorization": vapid_header(endpoint, self.key, self.public_b64),
                "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
                "TTL": "3600",
                "Urgency": "high",
            },
        )
        if status in (200, 201, 202):
            return "sent"
        # 404/410 means the browser unsubscribed. 401/403 usually means it subscribed with
        # VAPID keys we have since replaced.
        return "gone" if status in (401, 403, 404, 410) else "failed"


def new_keys() -> tuple[str, str]:
    """(private PEM, public key as base64url uncompressed point for applicationServerKey)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    public = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return pem, b64u(public)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Web push keys for CrewOps")
    parser.add_argument("command", choices=["keys"])
    parser.add_argument("--replace", action="store_true", help="replace existing keys")
    args = parser.parse_args(argv)

    import boto3
    from botocore.exceptions import ClientError

    from crewops.config import Settings, load_env_file

    load_env_file()
    settings = Settings.from_env()
    ssm = boto3.Session(region_name=settings.aws_region).client("ssm")
    prefix = settings.push_parameter_prefix
    try:
        ssm.get_parameter(Name=f"{prefix}/vapid-public")
        if not args.replace:
            print(
                "Push keys already exist (--replace makes new ones; browsers then subscribe again)."
            )
            return 0
    except ClientError:
        pass
    pem, public = new_keys()
    ssm.put_parameter(
        Name=f"{prefix}/vapid-private", Value=pem, Type="SecureString", Overwrite=True
    )
    ssm.put_parameter(Name=f"{prefix}/vapid-public", Value=public, Type="String", Overwrite=True)
    print(
        f"Stored the push keys under {prefix}. Run `crewops.web_env` again for the web app.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
