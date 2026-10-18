"""Semantic search over rules, example questions and past scenarios.

Three small, fixed S3 Vectors indexes.

``rules`` holds the seven legality rules, for legality questions that don't name a rule id
("how much rest does a pilot need?").

``intents`` holds the gold example questions with ids masked. It's the router's fallback
when no routing rule expected the phrasing: the closest example lends its intent, never its
ids or answer.

``scenarios`` holds past disruption scenarios and is only used to tell what kind of
disruption a free-text description is. We never return a scenario's answer because its crew
and costs were right for that moment only.

Documents are embedded with Titan Text Embeddings V2 and stored with their text as
metadata. A query pulls every document in the index with its distance, scores the same
documents with BM25 and blends the two (see ``hybrid``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crewops.search.hybrid import blend, bm25

RULES, INTENTS, SCENARIOS = "rules", "intents", "scenarios"

ID_PATTERNS = (
    (re.compile(r"\b[A-Z]{2}\d{2,4}-\d{4}-\d{2}-\d{2}\b"), "<flight>"),
    (re.compile(r"\b(?:DX|KS|NG)\d{2,4}\b", re.I), "<flight>"),
    (re.compile(r"\bC-\d{4}\b", re.I), "<crew>"),
    (re.compile(r"\bP-\d{4}\b", re.I), "<pairing>"),
    (re.compile(r"\bVT-[A-Z]{3}\b", re.I), "<aircraft>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "<date>"),
)
"""Ids swapped for placeholders, in example questions and in queries against them. An
example stands for a kind of request, not for C-3310 or P-2291. Masking stops ids from
dominating the keyword score and stops one question's answer (a crew id it names) turning
up in another's search."""


def mask_ids(text: str) -> str:
    for pattern, placeholder in ID_PATTERNS:
        text = pattern.sub(placeholder, text)
    return text


INDEXES = (RULES, INTENTS, SCENARIOS)
QUERY_LIMIT = 100
"""Neighbours per query and also the cap on documents per index. S3 Vectors pages at 100,
so staying within one page means a query sees every document and BM25 runs over the whole
index. ``put`` enforces it."""
TEXT_KEY = "text"
"""Metadata key for the embedded text. Non-filterable on every index so it can be long
without hitting the filterable-metadata size limit."""

SCENARIO_KIND = {
    "SICK_CREW": "crew_unavailable",
    "MULTI_SICK": "crew_unavailable",
    "STATION_CLOSURE": "station_disruption",
    "CERT_EXPIRY": "certification_lapse",
    "DELAY": "flight_delay",
}
"""Scenario event type -> report type. A delay naming an aircraft rather than a flight is
treated as an aircraft fault (see ``scenario_documents``)."""


@dataclass(frozen=True, slots=True)
class Document:
    key: str
    text: str
    """Embedded and keyword-matched."""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    """Returned with a hit. Never holds an answer key."""


def rule_documents(rules_doc: Mapping[str, Any]) -> list[Document]:
    return [
        Document(r["rule_id"], f"{r['rule_id']}: {r['text']}", {"rule_id": r["rule_id"]})
        for r in rules_doc["rules"]
    ]


def intent_documents(questions: Sequence[Mapping[str, Any]]) -> list[Document]:
    """Ids are masked and expected answers are left out. A hit gives the example's id and
    tier, and the router maps the id to an intent."""
    return [
        Document(
            q["question_id"],
            mask_ids(q["prompt"]),
            {"question_id": q["question_id"], "tier": int(q["tier"])},
        )
        for q in questions
    ]


def scenario_documents(scenarios: Sequence[Mapping[str, Any]]) -> list[Document]:
    docs = []
    for s in scenarios:
        event = s["event"]
        kind = SCENARIO_KIND.get(event["type"], "unknown")
        if event["type"] == "DELAY" and "aircraft" in event:
            kind = "aircraft_technical"
        text = f"{s['title']}. {event.get('narrative', '')}".strip()
        docs.append(
            Document(
                s["scenario_id"],
                text,
                {
                    "scenario_id": s["scenario_id"],
                    "report_type": kind,
                    "difficulty": s["difficulty"],
                },
            )
        )
    return docs


def load_documents(seed_dir: Path, eval_dir: Path) -> dict[str, list[Document]]:
    read = lambda p: json.loads(p.read_text(encoding="utf-8"))  # noqa: E731
    return {
        RULES: rule_documents(read(seed_dir / "rules.json")),
        INTENTS: intent_documents(read(eval_dir / "questions.json")),
        SCENARIOS: scenario_documents(read(eval_dir / "scenarios.json")),
    }


# --------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------

Embed = Callable[[str], list[float]]


def titan_embedder(bedrock_runtime: Any, model_id: str, dimensions: int) -> Embed:
    """Titan Text Embeddings V2, normalised to unit length so distance is cosine."""

    def embed(text: str) -> list[float]:
        response = bedrock_runtime.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({"inputText": text, "dimensions": dimensions, "normalize": True}),
        )
        return json.loads(response["body"].read())["embedding"]

    return embed


# --------------------------------------------------------------------------
# Index and query
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Hit:
    key: str
    score: float
    """Blended 0-1 score, relative to the other documents of the same index."""
    semantic: float
    """Absolute cosine similarity, -1 to 1. Use this for floors. The blended score is
    relative, so the best document scores high even when nothing really matches."""
    keyword: float
    metadata: Mapping[str, Any]


class VectorSearch:
    def __init__(self, s3vectors: Any, bucket: str, embed: Embed) -> None:
        self.s3vectors = s3vectors
        self.bucket = bucket
        self.embed = embed

    def put(self, index: str, documents: Sequence[Document]) -> int:
        """Embed and store. An existing key's vector is replaced."""
        if len(documents) > QUERY_LIMIT:
            raise ValueError(
                f"{index}: {len(documents)} documents; at most {QUERY_LIMIT} keep every query "
                "within one result page"
            )
        vectors = [
            {
                "key": d.key,
                "data": {"float32": self.embed(d.text)},
                "metadata": {**d.metadata, TEXT_KEY: d.text},
            }
            for d in documents
        ]
        for start in range(0, len(vectors), 100):
            self.s3vectors.put_vectors(
                vectorBucketName=self.bucket,
                indexName=index,
                vectors=vectors[start : start + 100],
            )
        return len(vectors)

    def search(self, index: str, query: str, top_k: int = 3, alpha: float = 0.5) -> list[Hit]:
        if index == INTENTS:
            query = mask_ids(query)
        response = self.s3vectors.query_vectors(
            vectorBucketName=self.bucket,
            indexName=index,
            queryVector={"float32": self.embed(query)},
            topK=QUERY_LIMIT,
            returnMetadata=True,
            returnDistance=True,
        )
        found = response.get("vectors", [])
        if not found:
            return []
        texts = [v.get("metadata", {}).get(TEXT_KEY, "") for v in found]
        semantic = [1.0 - float(v.get("distance", 1.0)) for v in found]
        keyword = bm25(query, texts)
        blended = blend(keyword, semantic, alpha)
        hits = [
            Hit(
                key=v["key"],
                score=round(blended[i], 3),
                semantic=round(semantic[i], 3),
                keyword=round(keyword[i], 3),
                metadata={k: val for k, val in v.get("metadata", {}).items() if k != TEXT_KEY}
                | {TEXT_KEY: texts[i]},
            )
            for i, v in enumerate(found)
        ]
        hits.sort(key=lambda h: (-h.score, -h.semantic, h.key))
        return hits[:top_k]
