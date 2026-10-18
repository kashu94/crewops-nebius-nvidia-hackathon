"""The three model roles, on Amazon Bedrock's Converse API.

Triage (Nova Micro) classifies a question that neither the rules nor the semantic match
could. It gets no tools.

Advisor (Nova 2 Lite) is the tool loop. It sees the planned calls' results as its own
earlier calls, may call more through the role-checked runner, and drafts the answer. The
pipeline verifies the draft, so the advisor never has the last word.

Explainer (Nova 2 Lite) rewrites a verified answer for speech, and that gets verified again.

Bedrock Guardrails screening of questions and answers lives here too. Model ids are
settings and everything goes through Converse, so swapping a model is a config change.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from crewops.agent.prompts import EXPLAINER_SYSTEM, advisor_system
from crewops.agent.router import Route
from crewops.tools import CrewOpsTools, TraceEntry, bedrock_tool_config

log = logging.getLogger(__name__)

MAX_RESULT_CHARS = 12_000
"""Longest tool result passed back to the model. Lookups are already capped at 60 rows, so
this is just a backstop for the context window."""


def bedrock_runtime(region: str, read_timeout: float = 10.0) -> Any:
    """Firm timeout, no retry. A voice answer that waits a minute is no answer, and every
    model role has a deterministic fallback anyway."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(
            connect_timeout=5,
            read_timeout=read_timeout,
            retries={"max_attempts": 1, "mode": "standard"},
        ),
    )


def _text(message: dict[str, Any]) -> str:
    return "\n".join(b["text"] for b in message.get("content", []) if "text" in b).strip()


COMPLETE = frozenset({"end_turn", "stop_sequence"})
"""Stop reasons that mean the model finished. Anything else (token limit, filtered,
malformed) counts as no answer. We never use half of one."""


def _finished(response: dict[str, Any]) -> str:
    if response.get("stopReason") not in COMPLETE:
        return ""
    return _text(response["output"]["message"])


@dataclass(slots=True)
class Usage:
    """Tokens and calls spent on one question."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, response: dict[str, Any]) -> None:
        usage = response.get("usage", {})
        self.calls += 1
        self.input_tokens += int(usage.get("inputTokens", 0))
        self.output_tokens += int(usage.get("outputTokens", 0))


# --------------------------------------------------------------------------
# Triage
# --------------------------------------------------------------------------


class NovaTriage:
    """Router's last resort. One call, no tools, JSON intent back."""

    def __init__(self, client: Any, model_id: str) -> None:
        self.client = client
        self.model_id = model_id
        self.usage = Usage()

    def __call__(self, instructions: str, question: str) -> str:
        response = self.client.converse(
            modelId=self.model_id,
            system=[{"text": instructions}],
            messages=[{"role": "user", "content": [{"text": question}]}],
            inferenceConfig={"maxTokens": 100, "temperature": 0},
        )
        self.usage.add(response)
        return _finished(response)

    def reset(self) -> None:
        self.usage = Usage()


# --------------------------------------------------------------------------
# Advisor
# --------------------------------------------------------------------------

ToolRunner = Callable[[str, dict[str, Any]], TraceEntry]


def _result_text(entry: TraceEntry) -> str:
    payload = entry.result if entry.ok else {"error": entry.error_code, "message": entry.error}
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + ' ..."(cut: ask a narrower question)"'
    return text


def _signature(name: str, args: Any) -> str:
    return f"{name}:{json.dumps(args, sort_keys=True, default=str)}"


@dataclass(slots=True)
class AdvisorRun:
    rounds: int = 0
    stop: str = ""
    usage: Usage = field(default_factory=Usage)


class NovaAdvisor:
    def __init__(
        self,
        client: Any,
        model_id: str,
        tools: CrewOpsTools,
        *,
        max_rounds: int = 8,
        budget_seconds: float = 6.0,
        max_tokens: int = 800,
    ) -> None:
        self.client = client
        self.model_id = model_id
        self.tools = tools
        self.max_rounds = max_rounds
        self.budget_seconds = budget_seconds
        self.max_tokens = max_tokens
        self.last = AdvisorRun()

    def reset(self) -> None:
        self.last = AdvisorRun()

    def __call__(
        self,
        question: str,
        route: Route,
        trace: list[TraceEntry],
        run: ToolRunner,
        allowed: frozenset[str],
    ) -> str:
        deadline = time.monotonic() + self.budget_seconds
        self.last = AdvisorRun()
        messages = self._opening(question, trace)
        seen = {_signature(e.tool, e.args) for e in trace}
        config = {"toolConfig": bedrock_tool_config(allowed)} if allowed else {}
        system = [{"text": advisor_system(self.tools, route.intent)}]

        while True:
            # check before calling, not after: no paying for a reply we'd throw away
            if time.monotonic() > deadline:
                self.last.stop = "budget"
                return ""
            response = self.client.converse(
                modelId=self.model_id,
                system=system,
                messages=messages,
                inferenceConfig={"maxTokens": self.max_tokens, "temperature": 0},
                **config,
            )
            self.last.usage.add(response)
            message = response["output"]["message"]
            self.last.stop = response.get("stopReason", "")
            if self.last.stop != "tool_use":
                return _finished(response)
            # Drop calls to tools the caller wasn't offered. Converse rejects a history that
            # names a tool missing from toolConfig.
            content = [
                b
                for b in message.get("content", [])
                if "toolUse" not in b or b["toolUse"]["name"] in allowed
            ]
            uses = [b["toolUse"] for b in content if "toolUse" in b]
            if not uses or self.last.rounds >= self.max_rounds:
                # no draft; the template answers instead
                self.last.stop = "budget" if uses else "no_usable_tool"
                return ""
            self.last.rounds += 1
            messages.append({"role": "assistant", "content": content})
            results = []
            for use in uses:
                name, args = use["name"], use.get("input") or {}
                signature = _signature(name, args)
                if signature in seen:
                    # same args, same result. Saying so breaks the loop sooner.
                    text, status = '{"note": "already called with these arguments"}', "error"
                else:
                    seen.add(signature)
                    entry = run(name, args)
                    text, status = _result_text(entry), "success" if entry.ok else "error"
                results.append(
                    {
                        "toolResult": {
                            "toolUseId": use["toolUseId"],
                            "content": [{"text": text}],
                            "status": status,
                        }
                    }
                )
            messages.append({"role": "user", "content": results})

    @staticmethod
    def _opening(question: str, trace: list[TraceEntry]) -> list[dict[str, Any]]:
        """The question, then the planned calls replayed as if the model had made them."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": [{"text": question}]}]
        if not trace:
            return messages
        uses, results = [], []
        for i, entry in enumerate(trace):
            use_id = f"planned-{i}"
            args = entry.args if isinstance(entry.args, dict) else {}
            uses.append({"toolUse": {"toolUseId": use_id, "name": entry.tool, "input": args}})
            results.append(
                {
                    "toolResult": {
                        "toolUseId": use_id,
                        "content": [{"text": _result_text(entry)}],
                        "status": "success" if entry.ok else "error",
                    }
                }
            )
        messages.append({"role": "assistant", "content": uses})
        messages.append({"role": "user", "content": results})
        return messages


# --------------------------------------------------------------------------
# Explainer
# --------------------------------------------------------------------------


class NovaExplainer:
    def __init__(self, client: Any, model_id: str, max_tokens: int = 1200) -> None:
        self.client = client
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.usage = Usage()

    def __call__(self, question: str, answer: str) -> str:
        response = self.client.converse(
            modelId=self.model_id,
            system=[{"text": EXPLAINER_SYSTEM}],
            messages=[
                {
                    "role": "user",
                    "content": [{"text": f"Question: {question}\n\nAnswer to rewrite:\n{answer}"}],
                }
            ],
            inferenceConfig={"maxTokens": self.max_tokens, "temperature": 0},
        )
        self.usage.add(response)
        return _finished(response)

    def reset(self) -> None:
        self.usage = Usage()


# --------------------------------------------------------------------------
# Guardrails
# --------------------------------------------------------------------------


class Guardrail:
    """``screen`` returns the blocked message, or None when the text passes."""

    def __init__(self, client: Any, guardrail_id: str, version: str) -> None:
        self.client = client
        self.guardrail_id = guardrail_id
        self.version = version

    def screen(self, text: str, source: str) -> str | None:
        response = self.client.apply_guardrail(
            guardrailIdentifier=self.guardrail_id,
            guardrailVersion=self.version,
            source=source,
            content=[{"text": {"text": text}}],
        )
        if response.get("action") != "GUARDRAIL_INTERVENED":
            return None
        outputs = response.get("outputs") or [{}]
        return outputs[0].get("text") or "I can't help with that."

    def question(self, text: str) -> str | None:
        return self.screen(text, "INPUT")

    def answer(self, text: str) -> str | None:
        return self.screen(text, "OUTPUT")
