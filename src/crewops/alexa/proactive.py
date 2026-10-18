"""Proactive Events API calls. These make one linked person's Echo chime with the yellow
ring and put a notification in their Alexa app.

The skill's Login with Amazon client credentials get a short-lived token. Events go to the
API endpoint for the person's region, taken from their own skill requests rather than
guessed. ``AMAZON.MessageAlert.Activated`` makes Alexa say "you have a new message from
CrewOps", and asking the skill "what's waiting" gives the details.

Notifications can't carry text for the Echo to speak. To work around that the notifier also
sends the skill a Skill Messaging API message with the wording. The skill wakes up, sets a
reminder a few seconds out with that text, and the Echo reads it when it fires. Only works
if the person allowed CrewOps reminders.

Stdlib only, so it runs on the plain Lambda runtime.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

LWA_URL = "https://api.amazon.com/auth/o2/token"
SCOPE = "alexa::proactive_events"
MESSAGING_SCOPE = "alexa:skill_messaging"
REMINDER_DELAY_SECONDS = 5
REMINDER_TEXT_LIMIT = 250
EVENT = "AMAZON.MessageAlert.Activated"
ALLOWED_ENDPOINTS = (
    "https://api.amazonalexa.com",
    "https://api.eu.amazonalexa.com",
    "https://api.fe.amazonalexa.com",
)

Post = Callable[[str, bytes, dict[str, str]], tuple[int, bytes]]


class ProactiveError(Exception):
    pass


def _post(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as r:  # noqa: S310 (fixed https hosts)
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class Tokens:
    """Cached LWA token, refreshed a minute before expiry."""

    def __init__(
        self, client_id: str, client_secret: str, post: Post = _post, scope: str = SCOPE
    ) -> None:
        self.client_id, self.client_secret, self.post = client_id, client_secret, post
        self.scope = scope
        self._token: str | None = None
        self._expires = 0.0

    def get(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        if self._token and now < self._expires - 60:
            return self._token
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": self.scope,
            }
        ).encode()
        status, raw = self.post(
            LWA_URL, body, {"Content-Type": "application/x-www-form-urlencoded"}
        )
        if status != 200:
            raise ProactiveError(f"Login with Amazon refused the skill's credentials ({status})")
        data = json.loads(raw)
        self._token = data["access_token"]
        self._expires = now + float(data.get("expires_in", 3600))
        return self._token


def message_alert(alexa_user_id: str, now: datetime, minutes: int = 120) -> dict[str, Any]:
    """Unicast, urgent, unread message alert that expires after ``minutes``."""
    return {
        "timestamp": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "referenceId": uuid.uuid4().hex,
        "expiryTime": (now + timedelta(minutes=minutes))
        .astimezone(UTC)
        .strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "event": {
            "name": EVENT,
            "payload": {
                "state": {"status": "UNREAD", "freshness": "NEW"},
                "messageGroup": {"creator": {"name": "CrewOps"}, "count": 1, "urgency": "URGENT"},
            },
        },
        "relevantAudience": {"type": "Unicast", "payload": {"user": alexa_user_id}},
    }


def _base(api_endpoint: str) -> str:
    base = api_endpoint.rstrip("/")
    if base not in ALLOWED_ENDPOINTS:
        raise ProactiveError(f"{api_endpoint!r} is not an Alexa API endpoint")
    return base


def events_url(api_endpoint: str, stage: str) -> str:
    base = _base(api_endpoint)
    return (
        f"{base}/v1/proactiveEvents/stages/development"
        if stage == "development"
        else (f"{base}/v1/proactiveEvents")
    )


def send(
    tokens: Tokens,
    api_endpoint: str,
    event: dict[str, Any],
    stage: str = "development",
    post: Post = _post,
) -> None:
    status, raw = post(
        events_url(api_endpoint, stage),
        json.dumps(event).encode(),
        {"Content-Type": "application/json", "Authorization": f"Bearer {tokens.get()}"},
    )
    if status != 202:
        raise ProactiveError(f"Alexa refused the notification ({status}): {raw[:200]!r}")


def message_skill(
    tokens: Tokens,
    api_endpoint: str,
    alexa_user_id: str,
    data: dict[str, Any],
    post: Post = _post,
) -> None:
    """``tokens`` must carry MESSAGING_SCOPE, not the proactive events one."""
    url = (
        f"{_base(api_endpoint)}/v1/skillmessages/users/{urllib.parse.quote(alexa_user_id, safe='')}"
    )
    status, raw = post(
        url,
        json.dumps({"data": data, "expiresAfterSeconds": 300}).encode(),
        {"Content-Type": "application/json", "Authorization": f"Bearer {tokens.get()}"},
    )
    if status != 202:
        raise ProactiveError(f"Alexa refused the skill message ({status}): {raw[:200]!r}")


def reminder(text: str, locale: str, now: datetime) -> dict[str, Any]:
    return {
        "requestTime": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000"),
        "trigger": {"type": "SCHEDULED_RELATIVE", "offsetInSeconds": REMINDER_DELAY_SECONDS},
        "alertInfo": {
            "spokenInfo": {"content": [{"locale": locale, "text": text[:REMINDER_TEXT_LIMIT]}]}
        },
        "pushNotification": {"status": "ENABLED"},
    }


def set_reminder(
    api_endpoint: str, api_access_token: str, body: dict[str, Any], post: Post = _post
) -> None:
    """Uses the per-request apiAccessToken, not an LWA token."""
    status, raw = post(
        f"{_base(api_endpoint)}/v1/alerts/reminders",
        json.dumps(body).encode(),
        {"Content-Type": "application/json", "Authorization": f"Bearer {api_access_token}"},
    )
    if status not in (200, 201):
        raise ProactiveError(f"Alexa refused the reminder ({status}): {raw[:200]!r}")
