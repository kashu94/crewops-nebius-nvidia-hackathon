"""Search documents, indexing and queries, against an in-memory S3 Vectors stand-in (no network)."""

import hashlib
import math

import pytest

from crewops.ingestion.seed import default_seed_dir
from crewops.search import index as index_command
from crewops.search.hybrid import tokens
from crewops.search.vectors import (
    INTENTS,
    RULES,
    SCENARIOS,
    VectorSearch,
    load_documents,
)

DIMS = 64
BUCKET = "crewops-test-vectors"


def fake_embed(text: str) -> list[float]:
    """Bag of hashed words, unit length: similar wording gives similar vectors."""
    vec = [0.0] * DIMS
    for word in tokens(text):
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIMS] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


@pytest.fixture(scope="module")
def documents():
    return load_documents(default_seed_dir(), default_seed_dir().parent / "eval")


class FakeS3Vectors:
    """In-memory put_vectors and query_vectors.

    moto stores vectors but can't query them. Distances are cosine, like the real indexes.
    """

    def __init__(self) -> None:
        self.indexes: dict[tuple[str, str], dict[str, dict]] = {}
        self.queries = 0

    def put_vectors(self, vectorBucketName, indexName, vectors):  # noqa: N803
        store = self.indexes.setdefault((vectorBucketName, indexName), {})
        for v in vectors:
            store[v["key"]] = v

    def query_vectors(  # noqa: N803
        self, vectorBucketName, indexName, queryVector, topK, returnMetadata, returnDistance
    ):
        self.queries += 1
        q = queryVector["float32"]
        found = []
        for key, v in self.indexes.get((vectorBucketName, indexName), {}).items():
            data = v["data"]["float32"]
            similarity = sum(a * b for a, b in zip(q, data, strict=True))
            found.append({"key": key, "distance": 1.0 - similarity, "metadata": v["metadata"]})
        found.sort(key=lambda f: f["distance"])
        return {"vectors": found[:topK]}


@pytest.fixture
def search(documents):
    s = VectorSearch(FakeS3Vectors(), BUCKET, fake_embed)
    for name, docs in documents.items():
        s.put(name, docs)
    return s


def test_corpora(documents):
    assert len(documents[RULES]) == 7
    assert len(documents[INTENTS]) == 38
    assert len(documents[SCENARIOS]) == 6


def test_scenarios_carry_their_kind_but_never_their_answer(documents):
    kinds = {d.key: d.metadata["report_type"] for d in documents[SCENARIOS]}
    assert kinds == {
        "S1": "crew_unavailable",
        "S2": "crew_unavailable",
        "S3": "station_disruption",
        "S4": "aircraft_technical",
        "S5": "certification_lapse",
        "S6": "crew_unavailable",
    }
    for d in documents[SCENARIOS]:
        assert "answer_key" not in d.metadata
        assert "C-3310" not in d.text and "₹" not in d.text  # no stale answers to leak


def test_intents_never_carry_expected_answers(documents):
    for d in documents[INTENTS]:
        assert set(d.metadata) == {"question_id", "tier"}


def test_intent_texts_name_no_specific_crew_pairing_or_flight(documents):
    import re

    for d in documents[INTENTS]:
        assert not re.search(r"\bC-\d{4}\b|\bP-\d{4}\b|\bDX\d+", d.text), (d.key, d.text)
    q36 = next(d for d in documents[INTENTS] if d.key == "Q36")
    assert q36.text == "Draft the callout notification to <crew> for covering <pairing>."


def test_intent_query_is_masked_too(search):
    # Ids no longer decide the match: any crew and pairing ids find the same example.
    first = search.search(INTENTS, "Draft the callout notification to C-3310 for covering P-2291.")
    second = search.search(INTENTS, "Draft the callout notification to C-1017 for covering P-2204.")
    assert first[0].key == second[0].key == "Q36"
    assert "C-3310" not in first[0].metadata["text"]


SYNONYMS = {"sleep": "rest", "pause": "rest", "kip": "rest", "downtime": "rest"}


def synonym_embed(text: str) -> list[float]:
    """Like fake_embed, but treats a few words as the same meaning: a stand-in for semantics."""
    return fake_embed(" ".join(SYNONYMS.get(t, t) for t in tokens(text)))


def test_paraphrase_sharing_no_words_is_found_by_meaning_alone(documents):
    s = VectorSearch(FakeS3Vectors(), BUCKET, synonym_embed)
    s.put(RULES, documents[RULES])
    hits = s.search(RULES, "sleep kip downtime")
    assert hits[0].key == "RULE-REST-04"
    assert hits[0].keyword == 0.0  # no shared words: the keyword side was flat and dropped
    assert hits[0].score == 1.0


def test_an_index_larger_than_one_result_page_is_refused(documents):
    from crewops.search.vectors import Document

    s = VectorSearch(FakeS3Vectors(), BUCKET, fake_embed)
    with pytest.raises(ValueError, match="at most 100"):
        s.put(RULES, [Document(f"d{i}", "text") for i in range(101)])


def test_index_command_reports_aws_errors_in_one_line(monkeypatch, capsys):
    from botocore.exceptions import NoCredentialsError

    monkeypatch.setattr(index_command, "load_env_file", lambda: {})
    monkeypatch.setenv("AWS_PROFILE", "crewops")

    def no_credentials(settings):
        raise NoCredentialsError()

    monkeypatch.setattr(index_command, "clients", no_credentials)
    assert index_command.main([]) == 1
    assert capsys.readouterr().err.startswith("AWS error: Unable to locate credentials")


def test_rule_found_by_paraphrase(search):
    hits = search.search(RULES, "minimum rest between release and next report")
    assert hits[0].key == "RULE-REST-04"
    assert hits[0].score == 1.0 and -1.0 <= hits[0].semantic <= 1.0
    assert "rest" in hits[0].metadata["text"].lower()


def test_rule_found_by_its_id(search):
    assert search.search(RULES, "what does RULE-CERT-06 say")[0].key == "RULE-CERT-06"


def test_example_question_found(search, documents):
    prompt = next(d.text for d in documents[INTENTS] if d.key == "Q02")
    hits = search.search(INTENTS, prompt, top_k=1)
    assert hits[0].key == "Q02" and hits[0].metadata["tier"] == 1


def test_scenario_kind_classified(search):
    hits = search.search(SCENARIOS, "BLR is closed to all departures and arrivals", top_k=1)
    assert hits[0].key == "S3" and hits[0].metadata["report_type"] == "station_disruption"


def test_put_replaces_rather_than_duplicates(search, documents):
    search.put(RULES, documents[RULES])
    assert len(search.search(RULES, "rest", top_k=100)) == 7


def test_index_dry_run_needs_no_aws(capsys):
    assert index_command.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "rules: 7 documents" in out and "intents: 38 documents" in out


def test_index_needs_a_named_profile(monkeypatch, capsys):
    monkeypatch.setattr(index_command, "load_env_file", lambda: {})
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    assert index_command.main([]) == 1
    assert "AWS_PROFILE" in capsys.readouterr().err
