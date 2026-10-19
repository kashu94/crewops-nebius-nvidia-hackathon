"""NVIDIA Nemotron on Nebius Token Factory, behind the same ``converse`` call the roles use.

Token Factory serves open models over an OpenAI-compatible chat completions API. The three
model roles (``crewops.agent.models``) were written against Bedrock's Converse shapes: a
system list, messages of content blocks, ``toolUse`` / ``toolResult`` blocks, a stop reason
and token usage. ``NebiusClient.converse`` takes and returns exactly those shapes and
translates them to chat completions on the wire, so the tool loop, the stop-reason checks,
the circuit breaker and every fallback work unchanged. Only the inference moves; the
operation, search, guardrail, memory and hosting stay on AWS.

Plain ``urllib``, no SDK: the MCP package and the Alexa Lambda ship without extra wheels,
and one POST is all a role needs.

    uv run python -m crewops.agent.nebius            # are the configured models served?
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
NEMOTRON_70B = "nvidia/Llama-3.1-Nemotron-70B-Instruct-HF"
API_KEY_ENV = ("NEBIUS_API_KEY", "CREWOPS_NEBIUS_API_KEY")
"""Where a local run finds the key. Deployed services read it from Parameter Store."""

STOP_REASONS = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "content_filtered",
}
"""Chat completions ``finish_reason`` to the Converse ``stopReason`` the roles check."""

OUTAGE_STATUS = frozenset({408, 409, 425, 429})
"""Besides 5xx: a busy or rate-limited service, not a request it refused as invalid."""


class NebiusError(Exception):
    """Token Factory answered with an HTTP error."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Nebius Token Factory {status}: {message}")
        self.status = status

    @property
    def outage(self) -> bool:
        return self.status >= 500 or self.status in OUTAGE_STATUS


class NebiusUnavailable(ConnectionError):
    """No answer from Token Factory at all: a timeout, refused connection or DNS failure."""


# --------------------------------------------------------------------------
# Converse -> chat completions
# --------------------------------------------------------------------------


def _text(blocks: list[dict[str, Any]]) -> str:
    return "\n".join(b["text"] for b in blocks if "text" in b)


def to_chat_messages(
    system: list[dict[str, Any]] | None, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": _text(system)})
    for message in messages:
        blocks = message.get("content", [])
        if message["role"] == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": _text(blocks) or None}
            calls = [
                {
                    "id": b["toolUse"]["toolUseId"],
                    "type": "function",
                    "function": {
                        "name": b["toolUse"]["name"],
                        "arguments": json.dumps(b["toolUse"].get("input") or {}),
                    },
                }
                for b in blocks
                if "toolUse" in b
            ]
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
            continue
        # A user turn holds either the question or the results of the previous tool calls.
        for b in blocks:
            if "toolResult" in b:
                result = b["toolResult"]
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": result["toolUseId"],
                        "content": _text(result.get("content", [])),
                    }
                )
        text = _text(blocks)
        if text:
            out.append({"role": "user", "content": text})
    return out


def to_chat_tools(tool_config: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["toolSpec"]["name"],
                "description": t["toolSpec"].get("description", ""),
                "parameters": t["toolSpec"]["inputSchema"]["json"],
            },
        }
        for t in tool_config.get("tools", [])
        if "toolSpec" in t
    ]


def to_chat_request(
    model_id: str,
    system: list[dict[str, Any]] | None,
    messages: list[dict[str, Any]],
    inference: Mapping[str, Any] | None,
    tool_config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    inference = inference or {}
    body: dict[str, Any] = {"model": model_id, "messages": to_chat_messages(system, messages)}
    for ours, theirs in (
        ("maxTokens", "max_tokens"),
        ("temperature", "temperature"),
        ("topP", "top_p"),
        ("stopSequences", "stop"),
    ):
        if ours in inference:
            body[theirs] = inference[ours]
    tools = to_chat_tools(tool_config) if tool_config else []
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    return body


# --------------------------------------------------------------------------
# chat completions -> Converse
# --------------------------------------------------------------------------


def from_chat_response(response: Mapping[str, Any]) -> dict[str, Any]:
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content: list[dict[str, Any]] = []
    if message.get("content"):
        content.append({"text": message["content"]})
    stop = STOP_REASONS.get(choice.get("finish_reason") or "", choice.get("finish_reason") or "")
    calls = message.get("tool_calls") or []
    for call in calls:
        function = call.get("function") or {}
        try:
            args = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = None
        if not isinstance(args, dict) or not function.get("name"):
            # Half a tool call is no call. The advisor treats this as no answer.
            stop = "malformed_tool_use"
            break
        content.append(
            {
                "toolUse": {
                    "toolUseId": call.get("id") or "",
                    "name": function["name"],
                    "input": args,
                }
            }
        )
    else:
        if calls:
            # Some servers finish a tool call with "stop"; the calls are what count.
            stop = "tool_use"
    usage = response.get("usage") or {}
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop,
        "usage": {
            "inputTokens": int(usage.get("prompt_tokens") or 0),
            "outputTokens": int(usage.get("completion_tokens") or 0),
        },
    }


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------

Opener = Callable[..., Any]


class NebiusClient:
    """One POST per call, firm timeout, no retry: like the Bedrock client it replaces, a
    voice answer that waits is no answer, and every role has a deterministic fallback."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 10.0,
        opener: Opener = urllib.request.urlopen,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self._open = opener

    def converse(
        self,
        *,
        modelId: str,
        messages: list[dict[str, Any]],
        system: list[dict[str, Any]] | None = None,
        inferenceConfig: Mapping[str, Any] | None = None,
        toolConfig: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Converse's parameter names and shapes, so the roles need no change."""
        body = to_chat_request(modelId, system, messages, inferenceConfig, toolConfig)
        return from_chat_response(self._request("POST", "chat/completions", body))

    def list_models(self) -> list[str]:
        return sorted(m["id"] for m in self._request("GET", "models").get("data", []))

    def _request(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            method=method,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with self._open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise NebiusError(exc.code, _error_message(exc)) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise NebiusUnavailable(f"Nebius Token Factory unreachable: {exc}") from None


def _error_message(exc: urllib.error.HTTPError) -> str:
    """The service's own message, short, never the request (it may hold roster data)."""
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace"))
        detail = payload.get("detail") or payload.get("error") or payload
        if isinstance(detail, dict):
            detail = detail.get("message") or detail
        return str(detail)[:300]
    except Exception:
        return exc.reason if isinstance(exc.reason, str) else "error"


# --------------------------------------------------------------------------
# The API key
# --------------------------------------------------------------------------


def api_key(parameter: str, region: str, env: Mapping[str, str] | None = None) -> str | None:
    """The Token Factory key: from the environment for a local run, else the SecureString
    ``parameter`` in Parameter Store, where the deployed services find it. ``None`` when
    neither has it; the pipeline then answers from rules and templates alone."""
    env = os.environ if env is None else env
    for name in API_KEY_ENV:
        value = (env.get(name) or "").strip()
        if value:
            return value
    try:
        import boto3

        ssm = boto3.Session(region_name=region).client("ssm")
        value = ssm.get_parameter(Name=parameter, WithDecryption=True)["Parameter"]["Value"]
    except Exception as exc:  # no parameter, no permission, no network: no models
        log.warning("no Nebius API key (%s): answering without models", type(exc).__name__)
        return None
    return value.strip() or None


def main(argv: list[str] | None = None) -> int:
    """Check the key works and the configured models are in the Token Factory catalogue."""
    import argparse
    import sys

    from crewops.config import Settings, load_env_file

    argparse.ArgumentParser(description=main.__doc__).parse_args(argv)
    load_env_file()
    settings = Settings.from_env()
    key = api_key(settings.nebius_api_key_parameter, settings.aws_region)
    if not key:
        print(
            f"No key: set NEBIUS_API_KEY or store it in {settings.nebius_api_key_parameter}.",
            file=sys.stderr,
        )
        return 1
    try:
        served = set(NebiusClient(key, settings.nebius_base_url).list_models())
    except (NebiusError, NebiusUnavailable) as exc:
        print(exc, file=sys.stderr)
        return 1
    missing = 0
    for role, model in (
        ("router", settings.router_model_id),
        ("advisor", settings.advisor_model_id),
        ("explainer", settings.explainer_model_id),
    ):
        ok = model in served
        missing += not ok
        print(f"{role:<10} {model}  {'served' if ok else 'NOT SERVED'}")
    if missing:
        nvidia = sorted(m for m in served if m.lower().startswith("nvidia/"))
        print("\nNVIDIA models this key can use:", *(nvidia or ["none"]), sep="\n  ")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
