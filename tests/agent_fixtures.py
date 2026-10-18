"""Shared, read-only fixtures for the agent tests: the seed week and its tools."""

import json
from functools import lru_cache

from crewops.agent.entities import Vocabulary
from crewops.engine import build_world
from crewops.ingestion.seed import default_seed_dir, load_seed
from crewops.tools import CrewOpsTools

EVAL_DIR = default_seed_dir().parent / "eval"


@lru_cache(maxsize=1)
def tools() -> CrewOpsTools:
    seed = load_seed()
    return CrewOpsTools(build_world(seed), seed)


@lru_cache(maxsize=1)
def vocab() -> Vocabulary:
    return Vocabulary.from_world(tools().world)


@lru_cache(maxsize=1)
def questions() -> list[dict]:
    return json.loads((EVAL_DIR / "questions.json").read_text(encoding="utf-8"))


def examples() -> dict[str, str]:
    return {q["question_id"]: q["prompt"] for q in questions()}


@lru_cache(maxsize=1)
def paraphrases() -> dict:
    return json.loads((EVAL_DIR / "paraphrases.json").read_text(encoding="utf-8"))
