"""Web push: the message a browser receives decrypts as the standard says, and the sender's
signature verifies with the app's public key."""

import base64
import json

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from crewops.notify import webpush
from crewops.notify.webpush import Sender, allowed_endpoint, b64u, encrypt, new_keys, unb64u


def browser():
    """A browser's subscription keys (what PushManager.subscribe hands the page)."""
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return key, b64u(public), b64u(b"0123456789abcdef")


def decrypt(body: bytes, key, p256dh: str, auth: str) -> bytes:
    """The receiving side of RFC 8291 (aes128gcm), written from the RFC, not the sender."""
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_public = body[21 : 21 + idlen]
    ciphertext = body[21 + idlen :]
    assert rs == 4096 and idlen == 65
    sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
    shared = key.exchange(ec.ECDH(), sender)
    ikm = webpush._hkdf(unb64u(auth), shared, b"WebPush: info\x00" + unb64u(p256dh) + as_public, 32)
    cek = webpush._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = webpush._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert plain.endswith(b"\x02")  # a single, last record
    return plain[:-1]


def test_hkdf_matches_rfc_5869_test_case_1():
    ikm = bytes.fromhex("0b" * 22)
    salt = bytes.fromhex("000102030405060708090a0b0c")
    info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
    okm = bytes.fromhex("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf")
    assert webpush._hkdf(salt, ikm, info, 32) == okm


def test_a_message_decrypts_in_the_browser_and_nowhere_else():
    key, p256dh, auth = browser()
    message = json.dumps({"title": "CrewOps", "body": "C-1042 unavailable"}).encode()
    body = encrypt(message, p256dh, auth)
    assert decrypt(body, key, p256dh, auth) == message
    other, _, _ = browser()
    with pytest.raises(InvalidTag):
        decrypt(body, other, p256dh, auth)  # another browser's key cannot read it
    with pytest.raises(ValueError):
        encrypt(b"x" * 5000, p256dh, auth)


def test_the_sender_signs_with_the_app_key_and_posts_only_to_push_services():
    pem, public = new_keys()
    key, p256dh, auth = browser()
    posts = []

    def post(url, body, headers):
        posts.append((url, body, headers))
        return 201

    sender = Sender(pem, public, post=post)
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "p256dh": p256dh, "auth": auth}
    assert sender.send(sub, {"title": "t", "body": "b", "url": "/console/r-1"}) == "sent"
    url, body, headers = posts[0]
    assert headers["Content-Encoding"] == "aes128gcm" and headers["TTL"] == "3600"
    assert json.loads(decrypt(body, key, p256dh, auth))["url"] == "/console/r-1"
    # The VAPID token verifies with the app's public key and names the push service.
    token = headers["Authorization"].split("t=")[1].split(",")[0]
    head, claims, signature = token.split(".")
    assert headers["Authorization"].endswith(f"k={public}")
    raw = unb64u(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    app_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(public))
    app_key.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    decoded = json.loads(base64.urlsafe_b64decode(claims + "=="))
    assert decoded["aud"] == "https://fcm.googleapis.com" and decoded["sub"].startswith("https://")
    # Unsubscribed browsers are reported, and nothing goes to an address outside push services.
    assert Sender(pem, public, post=lambda u, b, h: 410).send(sub, {}) == "gone"
    assert sender.send({**sub, "endpoint": "https://evil.example.com/x"}, {}) == "failed"
    assert len(posts) == 1


def test_only_browser_push_services_are_accepted():
    assert allowed_endpoint("https://fcm.googleapis.com/fcm/send/x")
    assert allowed_endpoint("https://updates.push.services.mozilla.com/wpush/v2/x")
    assert allowed_endpoint("https://wns2-pn1p.notify.windows.com/w/?token=x")
    for bad in (
        "http://fcm.googleapis.com/x",
        "https://169.254.169.254/latest",
        "https://notify.windows.com.evil.io/x",
    ):
        assert not allowed_endpoint(bad), bad


def test_desktop_alerts_go_to_the_desk_or_the_approvers_and_gone_browsers_are_forgotten():
    from test_alexa import stream_record

    from crewops.notify import push_alerts

    subs = [
        {
            "username": "ananya",
            "endpoint": "https://fcm.googleapis.com/a",
            "roles": ["controller"],
            "desk": "desk-1",
        },
        {
            "username": "rohit",
            "endpoint": "https://fcm.googleapis.com/r",
            "roles": ["controller"],
            "desk": "desk-2",
        },
        {
            "username": "meera",
            "endpoint": "https://fcm.googleapis.com/m",
            "roles": ["approver"],
            "desk": None,
        },
    ]
    sent, forgotten = [], []

    def send(sub, note):
        sent.append((sub["username"], note))
        return "gone" if sub["username"] == "meera" else "sent"

    analysed = stream_record("REPORTED", "OPTIONS_READY", "desk-1")
    submitted = stream_record("OPTIONS_READY", "SUBMITTED", "desk-1")
    assert push_alerts.process([analysed, submitted], subs, send, forgotten.append) == 1
    assert [u for u, _ in sent] == ["ananya", "meera"]
    note = sent[0][1]
    assert note["url"] == "/console/r-sick1042" and note["title"] == "CrewOps: New on your desk"
    assert "C-1042" not in json.dumps(note)  # names the disruption, never the crew member
    assert [s["username"] for s in forgotten] == ["meera"]


def test_only_controllers_and_approvers_subscribe_with_real_browser_keys(ops_table):
    from crewops.domain import Role
    from crewops.mcp.api import ApiError, CrewOpsApi
    from crewops.store import push
    from crewops.tools import Caller

    api = CrewOpsApi(ops_table, lambda: None, lambda *_: None)
    rohit = Caller("rohit", frozenset({Role.CONTROLLER}), desk="desk-2")
    _, p256dh, auth = browser()
    endpoint = "https://fcm.googleapis.com/fcm/send/r1"
    assert api.push_subscribe(rohit, endpoint, p256dh, auth)["status"] == "subscribed"
    api.push_subscribe(rohit, endpoint, p256dh, auth)  # the same browser again: one entry
    assert [s["username"] for s in push.subscriptions(ops_table)] == ["rohit"]
    with pytest.raises(ApiError):
        api.push_subscribe(Caller("crew1042", frozenset({Role.CREW})), endpoint, p256dh, auth)
    with pytest.raises(ApiError):
        api.push_subscribe(rohit, "https://169.254.169.254/x", p256dh, auth)
    with pytest.raises(ApiError):
        api.push_subscribe(rohit, endpoint, "not-a-key", auth)
    for n in range(push.MAX_PER_PERSON - 1):
        api.push_subscribe(rohit, f"{endpoint}-{n}", p256dh, auth)
    with pytest.raises(ApiError):
        api.push_subscribe(rohit, f"{endpoint}-more", p256dh, auth)
    api.push_unsubscribe(rohit, endpoint)
    assert len(push.subscriptions(ops_table, "rohit")) == push.MAX_PER_PERSON - 1
    # A shared computer: the next person to turn alerts on takes the browser over, and the
    # one before can no longer remove it.
    ananya = Caller("ananya", frozenset({Role.CONTROLLER}), desk="desk-1")
    shared = f"{endpoint}-0"
    api.push_subscribe(ananya, shared, p256dh, auth)
    owners = {s["endpoint"]: s["username"] for s in push.subscriptions(ops_table)}
    assert owners[shared] == "ananya"
    api.push_unsubscribe(rohit, shared)
    assert any(s["endpoint"] == shared for s in push.subscriptions(ops_table, "ananya"))


def test_slow_push_services_cannot_hold_the_notifier_and_replaced_keys_are_forgotten():
    import time

    from test_alexa import stream_record

    from crewops.notify import push_alerts

    subs = [
        {"username": f"a{n}", "endpoint": f"https://fcm.googleapis.com/{n}", "roles": ["approver"]}
        for n in range(12)
    ]

    def slow(sub, note):
        time.sleep(2)
        return "sent"

    started = time.monotonic()
    submitted = stream_record("OPTIONS_READY", "SUBMITTED", None)
    assert push_alerts.process([submitted], subs, slow, lambda s: None, budget=0.3) == 0
    assert time.monotonic() - started < 1.5
    pem, public = new_keys()
    _, p256dh, auth = browser()
    sub = {"endpoint": "https://fcm.googleapis.com/x", "p256dh": p256dh, "auth": auth}
    assert Sender(pem, public, post=lambda u, b, h: 403).send(sub, {}) == "gone"


def test_the_severity_is_said_first_on_alexa_and_in_desktop_alerts():
    from test_alexa import stream_record

    from crewops.alexa.notifier import announcement
    from crewops.notify.push_alerts import message

    record = stream_record("REPORTED", "OPTIONS_READY", "desk-2")
    record["dynamodb"]["NewImage"]["analysis"] = {
        "M": {
            "desk": {"S": "desk-2"},
            "seats": {
                "L": [{"M": {"pairing_id": {"S": "P-2291"}, "status": {"S": "no_legal_cover"}}}]
            },
        }
    }
    assert announcement(record).startswith("CrewOps, high severity: new on your desk.")
    assert message(record)["title"] == "CrewOps: High severity: new on your desk"
