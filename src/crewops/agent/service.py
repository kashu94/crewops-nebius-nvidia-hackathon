"""Build the live question pipeline from settings: table, search, models and guardrail.

The chat roles run on NVIDIA Nemotron through Nebius Token Factory; the table, search,
embeddings and guardrail stay on AWS.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from crewops.agent.breaker import BEDROCK_BREAKER, MODEL_BREAKER, GuardedClient
from crewops.agent.entities import Vocabulary
from crewops.agent.models import (
    Guardrail,
    ModelAdvisor,
    ModelExplainer,
    ModelTriage,
    bedrock_runtime,
)
from crewops.agent.nebius import NebiusClient, api_key
from crewops.agent.pipeline import Pipeline
from crewops.agent.router import Router
from crewops.config import Settings
from crewops.ingestion.seed import default_seed_dir
from crewops.search.vectors import INTENTS, VectorSearch, titan_embedder
from crewops.tools import CrewOpsTools

log = logging.getLogger(__name__)

QUICK_CALL_SECONDS = 3.0


def nebius_runtime(settings: Settings, key: str, timeout: float) -> NebiusClient:
    return NebiusClient(key, settings.nebius_base_url, timeout)


def nebius_api_key(settings: Settings) -> str | None:
    return api_key(settings.nebius_api_key_parameter, settings.aws_region)


def example_questions() -> dict[str, str]:
    path = default_seed_dir().parent / "eval" / "questions.json"
    return {q["question_id"]: q["prompt"] for q in json.loads(path.read_text(encoding="utf-8"))}


@dataclass(slots=True)
class LivePipeline:
    pipeline: Pipeline
    triage: ModelTriage | None
    advisor: ModelAdvisor | None
    explainer: ModelExplainer | None

    def ask(self, question: str, caller: Any, previous: Any = None) -> tuple[Any, dict[str, int]]:
        """Answer plus the model calls and tokens used for this question only. ``previous``
        is the conversation's last turn (``agent.context.Turn``), if any."""
        for role in (self.triage, self.explainer):
            if role is not None:
                role.reset()
        if self.advisor is not None:
            self.advisor.reset()
        answer = self.pipeline.answer(question, caller, previous)
        return answer, self.usage()

    def usage(self) -> dict[str, int]:
        """Calls and tokens since the last ``ask``, summed over all roles."""
        total = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        for u in (
            self.triage.usage if self.triage else None,
            self.advisor.last.usage if self.advisor else None,
            self.explainer.usage if self.explainer else None,
        ):
            if u is not None:
                total["calls"] += u.calls
                total["input_tokens"] += u.input_tokens
                total["output_tokens"] += u.output_tokens
        return total


def build(settings: Settings, table: Any, *, models: bool = True) -> LivePipeline:
    """Pipeline over the live table and search index. ``models`` adds Nemotron on Nebius
    Token Factory for triage, advisor and explainer, when an API key is found; the guardrail
    is added whenever one is configured."""
    import boto3

    session = boto3.Session(region_name=settings.aws_region)
    # One attempt per call. Short calls (triage, embeddings, guardrail) get
    # QUICK_CALL_SECONDS, the advisor and explainer get the response budget; past that the
    # template answers. Each service has its own breaker, so an outage on either doesn't
    # make every question wait out its own timeout, and doesn't pause the other service.
    aws = GuardedClient(bedrock_runtime(settings.aws_region, QUICK_CALL_SECONDS), BEDROCK_BREAKER)
    embed = titan_embedder(aws, settings.embedding_model_id, settings.embedding_dimensions)
    search = VectorSearch(session.client("s3vectors"), settings.vector_bucket, embed)
    tools = CrewOpsTools.load(table, search)

    triage = advisor = explainer = None
    key = nebius_api_key(settings) if models else None
    if models and not key:
        log.warning("models off: no Nebius Token Factory API key")
    if key:
        quick = GuardedClient(nebius_runtime(settings, key, QUICK_CALL_SECONDS), MODEL_BREAKER)
        runtime = GuardedClient(
            nebius_runtime(settings, key, settings.response_budget_seconds), MODEL_BREAKER
        )
        triage = ModelTriage(quick, settings.router_model_id)
        advisor = ModelAdvisor(
            runtime,
            settings.advisor_model_id,
            tools,
            max_rounds=settings.max_tool_iterations,
            budget_seconds=settings.response_budget_seconds,
        )
        explainer = ModelExplainer(runtime, settings.explainer_model_id)
    router = Router(
        Vocabulary.from_world(tools.world),
        example_questions(),
        search=lambda q: search.search(INTENTS, q, top_k=1),
        floor=settings.router_semantic_floor,
        triage=triage,
    )
    guard = (
        Guardrail(aws, settings.guardrail_id, settings.guardrail_version)
        if settings.guardrail_id
        else None
    )
    pipeline = Pipeline(
        tools,
        router,
        drafter=advisor,
        polisher=explainer,
        screen_question=guard.question if guard else None,
        screen_answer=guard.answer if guard else None,
        budget_seconds=settings.response_budget_seconds,
    )
    return LivePipeline(pipeline, triage, advisor, explainer)
