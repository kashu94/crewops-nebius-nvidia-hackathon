"""Measure where the router's similarity floor belongs, from real embeddings.

    uv run python -m crewops.agent.calibrate

Each paraphrase in ``data/eval/paraphrases.json`` (the example questions reworded, with other
ids) is matched against the intents index, as the router's semantic stage would match it,
and so is each off-topic question. The floor must sit above every off-topic match and every
wrong match, so the router never borrows an intent it should not; the report shows how many
right matches still clear it. Rules are ignored here: this measures the semantic stage alone.

Set the result as ``CREWOPS_ROUTER_SEMANTIC_FLOOR`` in ``.env``.
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crewops.agent.intents import Intent

MARGIN = 0.01


@dataclass(frozen=True, slots=True)
class Match:
    item_id: str
    prompt: str
    expected: Intent | None
    """None for an off-topic question: nothing should match."""
    matched_question: str | None
    matched: Intent | None
    semantic: float

    @property
    def correct(self) -> bool:
        return self.expected is not None and self.matched == self.expected


@dataclass(slots=True)
class Report:
    matches: list[Match] = field(default_factory=list)

    @property
    def floor(self) -> float:
        """Just above every match that must be refused: off-topic, or the wrong intent."""
        refuse = [m.semantic for m in self.matches if not m.correct]
        if not refuse:
            return 0.0
        return min(0.99, math.ceil((max(refuse) + MARGIN) * 100) / 100)

    def cleared(self, floor: float) -> tuple[int, int]:
        right = [m for m in self.matches if m.correct]
        return sum(m.semantic >= floor for m in right), len(right)

    def lines(self) -> list[str]:
        out = []
        for m in sorted(self.matches, key=lambda m: -m.semantic):
            verdict = "right" if m.correct else ("off-topic" if m.expected is None else "WRONG")
            got = m.matched.value if m.matched else "-"
            out.append(
                f"{m.semantic:6.3f}  {verdict:<9}  {m.item_id:<4} -> {got:<20} {m.prompt[:60]}"
            )
        passed, total = self.cleared(self.floor)
        out.append("")
        out.append(f"Recommended floor: {self.floor:.2f}")
        out.append(f"Right matches that clear it: {passed} of {total}.")
        out.append("Below the floor, the question goes to the triage model.")
        return out


Search = Callable[[str], Sequence[Any]]


def calibrate(
    paraphrases: Mapping[str, Any], example_intents: Mapping[str, Intent], search: Search
) -> Report:
    """Match every paraphrase and off-topic question; never through the rules."""
    report = Report()
    # A paraphrase of an example the rules cannot classify has no intent to compare with;
    # it is left out rather than counted as off-topic, which would push the floor up.
    items = [
        (p["id"], p["prompt"], example_intents[p["of"]])
        for p in paraphrases["paraphrases"]
        if p["of"] in example_intents
    ]
    items += [(n["id"], n["prompt"], None) for n in paraphrases["off_topic"]]
    for item_id, prompt, expected in items:
        hits = list(search(prompt))
        best = hits[0] if hits else None
        report.matches.append(
            Match(
                item_id,
                prompt,
                expected,
                best.key if best else None,
                example_intents.get(best.key) if best else None,
                float(best.semantic) if best else -1.0,
            )
        )
    return report


def default_paraphrases() -> Path:
    from crewops.ingestion.seed import default_seed_dir

    return default_seed_dir().parent / "eval" / "paraphrases.json"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    from crewops.config import Settings, load_env_file

    load_env_file()
    profile = os.environ.get("AWS_PROFILE")
    if not profile or profile == "default":
        print("Set AWS_PROFILE (for example in .env) to a named profile.", file=sys.stderr)
        return 1

    from botocore.exceptions import BotoCoreError, ClientError

    from crewops.agent.entities import Vocabulary
    from crewops.agent.router import Router
    from crewops.engine import build_world
    from crewops.ingestion.seed import default_seed_dir, load_seed
    from crewops.search.index import clients
    from crewops.search.vectors import INTENTS, VectorSearch, titan_embedder

    settings = Settings.from_env()
    examples_path = default_seed_dir().parent / "eval" / "questions.json"
    examples = {q["question_id"]: q["prompt"] for q in json.loads(examples_path.read_text())}
    router = Router(Vocabulary.from_world(build_world(load_seed())), examples)
    paraphrases = json.loads(default_paraphrases().read_text(encoding="utf-8"))
    try:
        bedrock, s3vectors = clients(settings)
        embed = titan_embedder(bedrock, settings.embedding_model_id, settings.embedding_dimensions)
        search = VectorSearch(s3vectors, settings.vector_bucket, embed)
        report = calibrate(
            paraphrases, router.example_intents, lambda q: search.search(INTENTS, q, top_k=1)
        )
    except (ClientError, BotoCoreError) as exc:
        print(f"AWS error: {exc}", file=sys.stderr)
        return 1
    print("\n".join(report.lines()))
    print(f"\nCurrent setting: {settings.router_semantic_floor:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
