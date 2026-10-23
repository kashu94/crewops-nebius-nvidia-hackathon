"""HTTPS endpoint for the CrewOps Alexa skill. It's a Lambda function URL because the stack
is in Sydney, where Alexa can't invoke Lambda directly.

Every request is checked as coming from Alexa (``crewops.alexa.verify``). Account linking
goes through Cognito, so a linked account's access token is a normal CrewOps sign-in and is
verified the same way. The person gets their role's access, same as in the console.

What it handles:

* Launch greets the person by name, or shows a link-account card. The first time it also
  asks for notification permission.
* "What's waiting?" reads the latest updates for their desk or approval queue.
* "Ask CrewOps <question>" goes through the same pipeline as the console's Alexa+, so the
  figures come from the engine.
* Notification permission changes, account linking and skill disable are stored so the
  notifier knows whose Echo to chime.
* Skill Messaging from the notifier sets a reminder a few seconds out with the alert text.
  A skill can't otherwise speak unprompted.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from crewops.alerts import spoken

log = logging.getLogger()
log.setLevel(logging.INFO)

PERMISSION = "alexa::devices:all:notifications:write"
REMINDERS = "alexa::alerts:reminders:skill:readwrite"
LOCALES = ("en-IN", "en-US", "en-AU")
HELP = (
    "You can ask: what's waiting for me. Or ask CrewOps a question, such as: ask CrewOps who "
    "covers C 1042 on P 2291."
)
QUESTION_STARTS = {
    "AskWhoIntent": "who",
    "AskWhosIntent": "who's",
    "AskWhatIntent": "what",
    "AskWhatsIntent": "what's",
    "AskWhichIntent": "which",
    "AskWhenIntent": "when",
    "AskWhereIntent": "where",
    "AskHowIntent": "how",
    "AskWhyIntent": "why",
    "AskIsIntent": "is",
    "AskAreIntent": "are",
    "AskAmIntent": "am",
    "AskCanIntent": "can",
    "AskCouldIntent": "could",
    "AskShouldIntent": "should",
    "AskWillIntent": "will",
    "AskDoIntent": "do",
    "AskDoesIntent": "does",
    "AskHasIntent": "has",
    "AskHaveIntent": "have",
    "AskExplainIntent": "explain",
    "AskShowIntent": "show",
    "AskListIntent": "list",
    "AskGiveMeIntent": "give me",
    "AskAnyIntent": "any",
}
"""A free-form question needs words before its slot (AMAZON.SearchQuery can't stand alone),
and the slot holds only what follows them. So each opening word has its own intent, and the
word is put back here: "who covers C 1042 on P 2291" arrives as AskWhoIntent with "covers C
1042 on P 2291"."""
SAID = {"HelloIntent": "hello", "ThanksIntent": "thank you"}

EXAMPLES = {
    "crew": "ask CrewOps when do I fly next",
    "reporter": "ask CrewOps is gate BLR G6 free",
    "auditor": "ask CrewOps is C 1042 legal for P 2291",
    "admin": "ask CrewOps how many open disruptions each controller has",
    "super_admin": "ask CrewOps how many open disruptions each controller has",
}


def help_for(caller: Any) -> str:
    """What this person can say: "what's waiting" only means something to a desk or an
    approval queue."""
    roles = {getattr(r, "value", r) for r in getattr(caller, "roles", ())}
    if roles & {"controller", "approver"}:
        return HELP
    example = next((EXAMPLES[r] for r in EXAMPLES if r in roles), "ask CrewOps what can I do")
    return f"Ask CrewOps a question, such as: {example}. Or say: ask CrewOps what can I do."


def first_name(user_id: str) -> str:
    """ "rohit.malhotra" -> "Rohit"; crew sign-ins ("c1042.nair") -> "Nair"."""
    parts = user_id.split(".")
    if re.fullmatch(r"c\d+", parts[0], re.I) and len(parts) > 1:
        return parts[-1].title()
    return parts[0].title()


def response(
    text: str,
    *,
    end: bool = True,
    reprompt: str | None = None,
    card: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "outputSpeech": {"type": "PlainText", "text": spoken(text)[:7000]},
        "shouldEndSession": end,
    }
    if reprompt:
        out["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": reprompt}}
    if card:
        out["card"] = card
    return {"version": "1.0", "response": out}


def link_card() -> dict[str, Any]:
    return response(
        "To use CrewOps, link your CrewOps account in the Alexa app, then open CrewOps again.",
        card={"type": "LinkAccount"},
    )


def system(envelope: dict[str, Any]) -> dict[str, Any]:
    return (envelope.get("context") or {}).get("System") or {}


class Skill:
    """Dependencies are injected so the tests run offline."""

    def __init__(
        self, table: Any, api: Any, identify: Any, clock: Any = None, remind: Any = None
    ) -> None:
        from crewops.alexa.proactive import set_reminder

        self.table = table
        self.api = api
        self.identify = identify  # access token -> Caller, or raises
        self.clock = clock or (lambda: datetime.now(UTC))
        self.remind = remind or set_reminder  # (api_endpoint, api_access_token, body)

    def handle(self, envelope: dict[str, Any]) -> dict[str, Any]:
        from crewops.store.alexa import forget, save_link, set_notifications

        request = envelope.get("request") or {}
        kind = request.get("type", "")
        sys = system(envelope)
        alexa_user = (sys.get("user") or {}).get("userId", "")
        endpoint = sys.get("apiEndpoint", "")
        now = self.clock()

        if kind == "AlexaSkillEvent.ProactiveSubscriptionChanged":
            subs = (request.get("body") or {}).get("subscriptions") or []
            allowed = any(s.get("eventName") == "AMAZON.MessageAlert.Activated" for s in subs)
            set_notifications(self.table, alexa_user, endpoint, allowed, now)
            return {"version": "1.0", "response": {}}
        if kind == "Messaging.MessageReceived":
            return self.announce(request.get("message") or {}, sys, now)
        if kind == "AlexaSkillEvent.SkillDisabled":
            forget(self.table, alexa_user)
            return {"version": "1.0", "response": {}}

        token = (sys.get("user") or {}).get("accessToken")
        if kind == "AlexaSkillEvent.SkillAccountLinked":
            token = (request.get("body") or {}).get("accessToken") or token
        caller = None
        if token:
            try:
                caller = self.identify(token)
            except Exception as exc:  # expired or foreign token, treat as unlinked
                log.info("Alexa access token refused: %s", exc)
        if caller is not None and alexa_user:
            save_link(
                self.table,
                alexa_user,
                endpoint,
                caller.user_id,
                sorted(r.value for r in caller.roles),
                caller.desk,
                now,
                request.get("locale"),
            )
        if kind.startswith("AlexaSkillEvent."):
            return {"version": "1.0", "response": {}}
        if kind == "SessionEndedRequest":
            return {"version": "1.0", "response": {}}
        if caller is None:
            return link_card()
        if kind == "LaunchRequest":
            return self.launch(caller, envelope)
        if kind == "IntentRequest":
            return self.intent(caller, request.get("intent") or {})
        return response("Sorry, I can't do that yet. " + HELP, end=False, reprompt=HELP)

    def launch(self, caller: Any, envelope: dict[str, Any]) -> dict[str, Any]:
        text = f"Welcome to CrewOps, {first_name(caller.user_id)}. {help_for(caller)}"
        granted = ((system(envelope).get("user") or {}).get("permissions") or {}).get(
            "consentToken"
        )
        card = None
        if not granted:
            text += (
                " To hear about new disruptions on your desk, allow CrewOps notifications in "
                "the Alexa app."
            )
            text += " Allow reminders too, and your Echo will say what happened."
            card = {"type": "AskForPermissionsConsent", "permissions": [PERMISSION, REMINDERS]}
        return response(text, end=False, reprompt=help_for(caller), card=card)

    def announce(self, message: dict[str, Any], sys: dict[str, Any], now: datetime) -> dict:
        """Turns the notifier's text into a reminder a few seconds out. Safe to trust the
        text, since only our notifier has the skill credentials to send this and the
        request was already verified as Alexa's."""
        from crewops.alexa.proactive import reminder

        text = spoken(str(message.get("announce") or "")).strip()
        locale = str(message.get("locale") or "en-IN")
        if text:
            try:
                self.remind(
                    sys.get("apiEndpoint", ""),
                    sys.get("apiAccessToken", ""),
                    reminder(text, locale if locale in LOCALES else "en-IN", now),
                )
            except Exception as exc:  # no reminder permission etc. The chime still happened.
                log.info("announcement reminder not set: %s", exc)
        return {"version": "1.0", "response": {}}

    def intent(self, caller: Any, intent: dict[str, Any]) -> dict[str, Any]:
        name = intent.get("name", "")
        if name == "WhatsWaitingIntent":
            return self.waiting(caller)
        if name == "AskCrewOpsIntent" or name in QUESTION_STARTS or name in SAID:
            question = ((intent.get("slots") or {}).get("question") or {}).get("value", "").strip()
            if name in QUESTION_STARTS and question:
                question = f"{QUESTION_STARTS[name]} {question}"
            question = SAID.get(name, question)
            if not question:
                return response("What would you like to ask?", end=False, reprompt=help_for(caller))
            answer = self.api.ask(caller, question)
            return response(answer["answer"], end=False, reprompt="Anything else?")
        if name in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
            return response("Goodbye.")
        if name in ("AMAZON.HelpIntent", "AMAZON.FallbackIntent", "AMAZON.NavigateHomeIntent"):
            return response(help_for(caller), end=False, reprompt=help_for(caller))
        return response(
            "Sorry, I didn't catch that. " + help_for(caller), end=False, reprompt=help_for(caller)
        )

    def waiting(self, caller: Any) -> dict[str, Any]:
        from crewops.mcp.api import ApiError

        try:
            found = self.api.my_alerts(caller, limit=3)
        except ApiError:
            return response("Your role has no desk or approval queue to report on.")
        if not found:
            return response("Nothing is waiting for you right now.", end=False, reprompt=HELP)
        lead = f"You have {len(found)} recent update{'s' if len(found) != 1 else ''}."
        return response(
            " ".join([lead, *(a["text"] for a in found)]), end=False, reprompt="Anything else?"
        )


def raw_body(event: dict[str, Any]) -> bytes:
    body = event.get("body") or ""
    return base64.b64decode(body) if event.get("isBase64Encoded") else body.encode()


_skill: Skill | None = None
_skill_id: str | None = None


def _build() -> Skill:
    """Same pipeline and token checks as the MCP server."""
    from crewops.agent.service import build
    from crewops.auth.identity import caller_from_claims
    from crewops.auth.tokens import TokenVerifier
    from crewops.config import Settings
    from crewops.mcp.api import CrewOpsApi
    from crewops.store.ops_store import table_for
    from crewops.store.users import get_profile

    global _skill_id
    settings = Settings.from_env()
    if not (settings.alexa_skill_id and settings.alexa_client_id and settings.issuer):
        raise RuntimeError(
            "CREWOPS_ALEXA_SKILL_ID, CREWOPS_ALEXA_CLIENT_ID and the user pool are needed"
        )
    _skill_id = settings.alexa_skill_id
    table = table_for(settings)
    verifier = TokenVerifier(settings.issuer, settings.alexa_client_id)

    def identify(token: str) -> Any:
        return caller_from_claims(
            verifier.claims(token),
            lambda u: get_profile(table, u),
            issuer=settings.issuer,
            client_id=settings.alexa_client_id,
        )

    def no_reports(_report: dict) -> None:
        raise RuntimeError("the Alexa skill does not file reports")

    api = CrewOpsApi(table, lambda: build(settings, table), no_reports)
    return Skill(table, api, identify)


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    from crewops.alexa.verify import NotFromAlexa, verified

    global _skill
    _skill = _skill or _build()
    body = raw_body(event)
    try:
        envelope = verified(body, event.get("headers") or {}, _skill_id or "")
    except NotFromAlexa as exc:
        log.warning("refused a request: %s", exc)
        return {"statusCode": 400, "body": json.dumps({"error": "not a valid Alexa request"})}
    try:
        out = _skill.handle(envelope)
    except Exception:
        log.exception("skill request failed")
        out = response("Sorry, CrewOps couldn't answer that just now. Please try again.")
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(out),
    }
