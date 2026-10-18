"""The Alexa skill: request verification, the skill's answers, linking, and the notifications
that make an Echo chime. Offline: a test certificate chain stands in for Amazon's."""

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from cryptography.x509.verification import Store

from crewops.alexa import notifier, proactive
from crewops.alexa.skill import Skill
from crewops.alexa.verify import NotFromAlexa, valid_cert_url, verified
from crewops.domain import Role
from crewops.store.alexa import links, save_link, set_notifications
from crewops.tools import Caller

NOW = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)
SKILL = "amzn1.ask.skill.test-crewops"
CERT_URL = "https://s3.amazonaws.com/echo.api/echo-api-cert-12.pem"


def _cert(subject, issuer, key, signer, *, ca, san=None):
    b = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=30))
        .not_valid_after(NOW + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(signer.public_key()), critical=False
        )
    )
    if ca:
        b = b.add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
    else:
        b = b.add_extension(
            x509.KeyUsage(True, False, True, False, False, False, False, False, False),
            critical=True,
        ).add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
    if san:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
    return b.sign(signer, hashes.SHA256())


@pytest.fixture(scope="module")
def pki():
    root_key, mid_key, leaf_key = (rsa.generate_private_key(65537, 2048) for _ in range(3))
    root = _cert("Test Root", "Test Root", root_key, root_key, ca=True)
    mid = _cert("Test Intermediate", "Test Root", mid_key, root_key, ca=True)

    def leaf_for(name):
        return _cert(name, "Test Intermediate", leaf_key, mid_key, ca=False, san=name)

    return {"root": root, "mid": mid, "leaf_key": leaf_key, "leaf_for": leaf_for}


def envelope(kind="LaunchRequest", token="tok", skill=SKILL, at=NOW, **request):
    return {
        "version": "1.0",
        "context": {
            "System": {
                "application": {"applicationId": skill},
                "user": {
                    "userId": "amzn1.ask.account.ROHIT",
                    **({"accessToken": token} if token else {}),
                },
                "apiEndpoint": "https://api.eu.amazonalexa.com",
            }
        },
        "request": {"type": kind, "timestamp": at.strftime("%Y-%m-%dT%H:%M:%SZ"), **request},
    }


def signed(pki, body: bytes, name="echo-api.amazon.com"):
    chain = pki["leaf_for"](name).public_bytes(Encoding.PEM) + pki["mid"].public_bytes(Encoding.PEM)
    sig = pki["leaf_key"].sign(body, padding.PKCS1v15(), hashes.SHA256())
    headers = {"SignatureCertChainUrl": CERT_URL, "Signature-256": base64.b64encode(sig).decode()}
    return headers, (lambda url: chain)


def check(pki, body, headers, fetch, skill=SKILL, now=NOW):
    from crewops.alexa import verify

    verify._chains.clear()
    return verified(body, headers, skill, fetch=fetch, store=Store([pki["root"]]), now=now)


# -- Verification ------------------------------------------------------------------------


def test_only_amazons_certificate_urls_are_fetched():
    assert valid_cert_url("https://s3.amazonaws.com/echo.api/echo-api-cert.pem")
    assert valid_cert_url("https://s3.amazonaws.com:443/echo.api/../echo.api/echo-api-cert.pem")
    for bad in (
        "http://s3.amazonaws.com/echo.api/echo-api-cert.pem",
        "https://notamazon.com/echo.api/echo-api-cert.pem",
        "https://s3.amazonaws.com/EcHo.aPi/echo-api-cert.pem",
        "https://s3.amazonaws.com:563/echo.api/echo-api-cert.pem",
        "https://s3.amazonaws.com/invalid.path/echo-api-cert.pem",
        "https://s3.amazonaws.com/echo.api/../invalid/echo-api-cert.pem",
    ):
        assert not valid_cert_url(bad), bad


def test_a_genuine_request_passes_and_any_tampering_fails(pki):
    body = json.dumps(envelope()).encode()
    headers, fetch = signed(pki, body)
    assert check(pki, body, headers, fetch)["request"]["type"] == "LaunchRequest"
    # The body changed after signing.
    with pytest.raises(NotFromAlexa, match="signature"):
        check(pki, body.replace(b"Launch", b"Lunch!"), headers, fetch)
    # Signed by a certificate for someone else.
    other_headers, other_fetch = signed(pki, body, name="attacker.example.com")
    with pytest.raises(NotFromAlexa, match="chain"):
        check(pki, body, other_headers, other_fetch)
    # Old, or for another skill.
    with pytest.raises(NotFromAlexa, match="too old"):
        check(pki, body, headers, fetch, now=NOW + timedelta(seconds=200))
    with pytest.raises(NotFromAlexa, match="another skill"):
        check(pki, body, headers, fetch, skill="amzn1.ask.skill.someone-else")
    with pytest.raises(NotFromAlexa, match="missing"):
        check(pki, body, {}, fetch)


# -- The skill ---------------------------------------------------------------------------

ROHIT = Caller("rohit.malhotra", frozenset({Role.CONTROLLER}), desk="desk-2")


class FakeApi:
    def __init__(self):
        self.asked = []

    def ask(self, caller, question):
        self.asked.append((caller.user_id, question))
        return {
            "answer": "For P-2291 Captain, the cheapest legal choice is D. Reddy (C-3310), ₹23,090."
        }

    def my_alerts(self, caller, limit=5):
        return [
            {"text": "C-1042 unavailable 2026-10-13 (sick). Recommended cover: D. Reddy (C-3310)."}
        ]


def skill(table, api=None):
    def identify(token):
        if token != "tok":
            raise ValueError("bad token")
        return ROHIT

    return Skill(table, api or FakeApi(), identify, clock=lambda: NOW)


def said(out):
    return out["response"]["outputSpeech"]["text"]


def test_an_unlinked_account_is_asked_to_link(ops_table):
    out = skill(ops_table).handle(envelope(token=None))
    assert out["response"]["card"] == {
        "type": "LinkAccount"
    } and "link your CrewOps account" in said(out)
    out = skill(ops_table).handle(envelope(token="expired"))
    assert out["response"]["card"]["type"] == "LinkAccount"
    assert links(ops_table) == []  # nobody is remembered from a refused token


def test_launch_greets_and_asks_once_for_notifications(ops_table):
    out = skill(ops_table).handle(envelope())
    assert said(out).startswith("Welcome to CrewOps, Rohit.")
    assert out["response"]["card"] == {
        "type": "AskForPermissionsConsent",
        "permissions": [
            "alexa::devices:all:notifications:write",
            "alexa::alerts:reminders:skill:readwrite",
        ],
    }
    (link,) = links(ops_table)
    assert (link["username"], link["desk"], link["roles"]) == (
        "rohit.malhotra",
        "desk-2",
        ["controller"],
    )
    assert link["api_endpoint"] == "https://api.eu.amazonalexa.com"
    granted = envelope()
    granted["context"]["System"]["user"]["permissions"] = {"consentToken": "c"}
    assert "card" not in skill(ops_table).handle(granted)["response"]


def test_whats_waiting_and_free_questions(ops_table):
    api = FakeApi()
    out = skill(ops_table, api).handle(
        envelope("IntentRequest", intent={"name": "WhatsWaitingIntent"})
    )
    assert said(out).startswith("You have 1 recent update. C 1042 unavailable 13 October")
    ask = {
        "name": "AskCrewOpsIntent",
        "slots": {"question": {"name": "question", "value": "who covers P-2291"}},
    }
    out = skill(ops_table, api).handle(envelope("IntentRequest", intent=ask))
    assert api.asked == [("rohit.malhotra", "who covers P-2291")]
    assert (
        "D. Reddy (C 3310), 23,090 rupees" in said(out)
        and out["response"]["shouldEndSession"] is False
    )
    bye = skill(ops_table, api).handle(
        envelope("IntentRequest", intent={"name": "AMAZON.StopIntent"})
    )
    assert said(bye) == "Goodbye." and bye["response"]["shouldEndSession"] is True


def test_notification_permission_is_remembered_and_withdrawn(ops_table):
    s = skill(ops_table)
    s.handle(envelope())  # linked
    allowed = envelope(
        "AlexaSkillEvent.ProactiveSubscriptionChanged",
        token=None,
        body={"subscriptions": [{"eventName": "AMAZON.MessageAlert.Activated"}]},
    )
    assert s.handle(allowed) == {"version": "1.0", "response": {}}
    assert links(ops_table)[0]["notifications"] is True
    s.handle(envelope("AlexaSkillEvent.ProactiveSubscriptionChanged", token=None, body={}))
    assert links(ops_table)[0]["notifications"] is False
    s.handle(envelope("AlexaSkillEvent.SkillDisabled", token=None))
    assert links(ops_table) == []


# -- Notifications -----------------------------------------------------------------------


def stream_record(before, after, desk):
    analysis = {"M": {"desk": {"S": desk}}} if desk else None
    new = {
        "entity": {"S": "disruption"},
        "status": {"S": after},
        "disruption_id": {"S": "r-sick1042"},
        "report": {"M": {"type": {"S": "crew_unavailable"}}},
        **({"analysis": analysis} if analysis else {}),
    }
    return {
        "eventName": "MODIFY",
        "dynamodb": {"NewImage": new, "OldImage": {"status": {"S": before}}, "SequenceNumber": "1"},
    }


def test_only_the_desk_or_the_approvers_echo_chimes(ops_table):
    at = NOW
    for user, alexa, roles, desk in (
        ("rohit.malhotra", "A-ROHIT", ["controller"], "desk-2"),
        ("ananya.iyer", "A-ANANYA", ["controller"], "desk-1"),
        ("meera.bhatt", "A-MEERA", ["approver"], None),
        ("karan.mehta", "A-KARAN", ["approver"], None),
    ):
        save_link(ops_table, alexa, "https://api.eu.amazonalexa.com", user, roles, desk, at)
        set_notifications(
            ops_table, alexa, "https://api.eu.amazonalexa.com", user != "karan.mehta", at
        )
    found = links(ops_table)

    def who(record):
        return sorted(link["username"] for link in notifier.recipients(record, found))

    assert who(stream_record("REPORTED", "OPTIONS_READY", "desk-2")) == ["rohit.malhotra"]
    assert who(stream_record("SUBMITTED", "OPTIONS_READY", "desk-2")) == ["rohit.malhotra"]
    assert who(stream_record("OPTIONS_READY", "SUBMITTED", "desk-2")) == [
        "meera.bhatt"
    ]  # karan said no
    assert who(stream_record("SUBMITTED", "COMMITTED", "desk-2")) == []
    sent = []
    assert (
        notifier.process(
            [stream_record("REPORTED", "OPTIONS_READY", "desk-2")],
            found,
            lambda link, now, text: sent.append((link["username"], text)),
            at,
        )
        == 1
    )
    ((who_, said_),) = sent
    assert who_ == "rohit.malhotra" and said_.startswith("CrewOps: new on your desk.")

    def boom(link, now, text):
        raise RuntimeError("Alexa down")

    # A failed notification is logged, never retried into a second chime.
    assert (
        notifier.process([stream_record("REPORTED", "OPTIONS_READY", "desk-2")], found, boom, at)
        == 0
    )


def test_a_notification_is_a_message_alert_to_the_right_region():
    calls = []

    def post(url, body, headers):
        calls.append((url, body, headers))
        if url == proactive.LWA_URL:
            return 200, json.dumps({"access_token": "lwa-token", "expires_in": 3600}).encode()
        return 202, b""

    tokens = proactive.Tokens("client", "secret", post=post)
    event = proactive.message_alert("A-ROHIT", NOW)
    assert event["event"]["name"] == "AMAZON.MessageAlert.Activated"
    assert event["relevantAudience"] == {"type": "Unicast", "payload": {"user": "A-ROHIT"}}
    assert event["expiryTime"] == "2026-10-13T07:00:00.000Z"
    proactive.send(tokens, "https://api.eu.amazonalexa.com", event, "development", post=post)
    proactive.send(tokens, "https://api.eu.amazonalexa.com", event, "development", post=post)
    urls = [u for u, _, _ in calls]
    assert urls.count(proactive.LWA_URL) == 1  # the token is reused until it nears expiry
    assert urls[-1] == "https://api.eu.amazonalexa.com/v1/proactiveEvents/stages/development"
    assert calls[-1][2]["Authorization"] == "Bearer lwa-token"
    assert b"scope=alexa%3A%3Aproactive_events" in calls[0][1]
    with pytest.raises(proactive.ProactiveError):
        proactive.events_url("https://evil.example.com", "development")


def test_certificate_urls_with_queries_are_refused_and_fetch_failures_are_clean(pki):
    from crewops.alexa import verify

    assert not valid_cert_url("https://s3.amazonaws.com/echo.api/echo-api-cert.pem?x=1")
    assert (
        verify.normalised_cert_url(
            "HTTPS://S3.amazonaws.com:443/echo.api//../echo.api/cert.pem#frag"
        )
        == "https://s3.amazonaws.com/echo.api/cert.pem"
    )
    body = json.dumps(envelope()).encode()
    headers, _ = signed(pki, body)

    def unreachable(url):
        raise OSError("timed out")

    verify._chains.clear()
    with pytest.raises(NotFromAlexa, match="unavailable"):
        verified(body, headers, SKILL, fetch=unreachable, store=Store([pki["root"]]), now=NOW)
    with pytest.raises(NotFromAlexa, match="unavailable"):
        verified(
            body, headers, SKILL, fetch=lambda u: b"not a pem", store=Store([pki["root"]]), now=NOW
        )
    # However many distinct (valid) URLs arrive, only a few chains are kept.
    verify._chains.clear()
    good = signed(pki, body)[1]
    for n in range(20):
        verify._chain(f"https://s3.amazonaws.com/echo.api/cert-{n}.pem", good)
    assert len(verify._chains) <= verify.MAX_CHAINS


def test_notifications_follow_the_persons_current_desk_and_team():
    links_now = [
        {
            "username": "rohit.malhotra",
            "roles": ["controller"],
            "desk": "desk-2",
            "notifications": True,
        },
        {
            "username": "gone.person",
            "roles": ["controller"],
            "desk": "desk-2",
            "notifications": True,
        },
    ]
    profiles = {"rohit.malhotra": {"roles": ["controller"], "desk": "desk-3"}}  # moved desk
    fresh = notifier.current(links_now, profiles.get)
    assert [(link["username"], link["desk"]) for link in fresh] == [("rohit.malhotra", "desk-3")]
    assert notifier.recipients(stream_record("REPORTED", "OPTIONS_READY", "desk-2"), fresh) == []
    assert (
        len(notifier.recipients(stream_record("REPORTED", "OPTIONS_READY", "desk-3"), fresh)) == 1
    )


def test_the_echo_says_what_happened_through_a_reminder(ops_table):
    """The notifier wakes the skill with the alert's words; the skill sets a reminder a few
    seconds ahead with them, which the Echo reads out."""
    record = stream_record("OPTIONS_READY", "SUBMITTED", None)
    from boto3.dynamodb.types import TypeSerializer
    from test_plans import report

    from crewops.domain import Report

    sick = Report.from_dict(
        report(
            report_id="r-sick1042",
            details={"crew_id": "C-1042", "from_date": "2026-10-13", "reason": "sick"},
        )
    )
    record["dynamodb"]["NewImage"]["report"] = TypeSerializer().serialize(sick.to_dict())
    text = notifier.announcement(record)
    assert text.startswith("CrewOps: a plan for C 1042") and "waiting for your approval" in text

    calls = []

    def post(url, body, headers):
        calls.append((url, json.loads(body) if url != proactive.LWA_URL else body, headers))
        if url == proactive.LWA_URL:
            return 200, json.dumps({"access_token": "msg-token", "expires_in": 3600}).encode()
        return 202, b""

    tokens = proactive.Tokens("client", "secret", post=post, scope=proactive.MESSAGING_SCOPE)
    proactive.message_skill(
        tokens, "https://api.eu.amazonalexa.com", "amzn1.ask.account.X/Y", {"announce": text}, post
    )
    assert b"scope=alexa%3Askill_messaging" in calls[0][1]
    url, body, _ = calls[-1]
    assert url == "https://api.eu.amazonalexa.com/v1/skillmessages/users/amzn1.ask.account.X%2FY"
    assert body["data"] == {"announce": text}

    reminders = []
    s = Skill(
        ops_table,
        FakeApi(),
        lambda t: None,
        clock=lambda: NOW,
        remind=lambda *a: reminders.append(a),
    )
    envelope = {
        "request": {
            "type": "Messaging.MessageReceived",
            "message": {"announce": text, "locale": "xx"},
        },
        "context": {
            "System": {
                "user": {"userId": "A-ROHIT"},
                "apiEndpoint": "https://api.eu.amazonalexa.com",
                "apiAccessToken": "skill-token",
            }
        },
    }
    assert s.handle(envelope) == {"version": "1.0", "response": {}}
    ((endpoint, token, made),) = reminders
    assert endpoint == "https://api.eu.amazonalexa.com" and token == "skill-token"
    assert made["trigger"] == {"type": "SCHEDULED_RELATIVE", "offsetInSeconds": 5}
    (content,) = made["alertInfo"]["spokenInfo"]["content"]
    assert content["locale"] == "en-IN" and content["text"] == text  # unknown locale: India

    # Reminders not allowed: Alexa refuses, the skill answers quietly and the chime stands.
    def refused(*_):
        raise proactive.ProactiveError("401")

    quiet = Skill(ops_table, FakeApi(), lambda t: None, clock=lambda: NOW, remind=refused)
    assert quiet.handle(envelope) == {"version": "1.0", "response": {}}
    with pytest.raises(proactive.ProactiveError):
        proactive.set_reminder("https://evil.example.com", "t", made)
