"""Routing: rules first, then the closest example above a floor, then triage, then a lookup."""

from dataclasses import dataclass

import pytest
from agent_fixtures import examples, paraphrases, questions, vocab

from crewops.agent.intents import Confidence, Intent, Tier
from crewops.agent.router import TRIAGE_INSTRUCTIONS, Router, is_bare_identifier


@pytest.fixture(scope="module")
def router():
    return Router(vocab(), examples())


@dataclass
class Hit:
    key: str
    semantic: float


def test_every_example_question_routes_by_rule_to_its_tier(router):
    assert len(router.example_intents) == 38
    for q in questions():
        decided = router.route(q["prompt"])
        assert decided.decided_by == "rule", q["question_id"]
        assert decided.tier == Tier(q["tier"]), q["question_id"]


def test_rules_route_the_paraphrases(router):
    right = 0
    for p in paraphrases()["paraphrases"]:
        decided = router.route(p["prompt"])
        expected = router.example_intents[p["of"]]
        right += decided.intent is expected
        assert decided.tier == expected.tier, p["id"]
    assert right >= 37


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Both A320 captains are sick, what's the plan?", Intent.JOINT_PLAN),
        ("C-1042 is sick", Intent.FIND_REPLACEMENT),
        ("C-1042 is sick, which flights are affected?", Intent.IMPACT_OF_EVENT),
        ("What is C-2087's rank?", Intent.LOOKUP_CREW),
        ("Duty headroom, expiring certificates and risk for C-1042", Intent.LOOKUP_CREW),
        ("Is C-3940 likely to go sick?", Intent.LOOKUP_RISK),
        ("Does DX401 still clear its gate if delayed 90 minutes?", Intent.CHECK_GATE),
        ("Which controllers cover VT-DXA?", Intent.LOOKUP_CONTROLLERS),
        ("Draft the callout notification to C-3310 for P-2291", Intent.DRAFT_NOTIFICATION),
    ],
)
def test_rule_decisions(router, text, intent):
    decided = router.route(text)
    assert decided.intent is intent and decided.decided_by == "rule" and decided.matched


def test_semantic_match_above_the_floor_lends_its_intent_only():
    search = lambda q: [Hit("Q16", 0.81)]  # noqa: E731
    router = Router(vocab(), examples(), search=search, floor=0.5)
    decided = router.route("Tell me about C-2087 please")
    assert decided.decided_by == "semantic" and decided.matched == "Q16"
    assert decided.intent is Intent.LOOKUP_RISK and decided.confidence is Confidence.MEDIUM
    assert decided.entities.crew_ids == ["C-2087"]  # this question's ids, never the example's


def test_semantic_match_below_the_floor_is_not_trusted():
    router = Router(vocab(), examples(), search=lambda q: [Hit("Q16", 0.3)], floor=0.5)
    decided = router.route("Tell me about C-2087 please")
    assert decided.decided_by == "default" and decided.intent is Intent.LOOKUP_CREW


def test_bare_ids_never_go_to_semantic_search():
    called = []
    router = Router(vocab(), examples(), search=lambda q: called.append(q) or [], floor=0.1)
    router.route("C-1042")
    assert (
        called == []
        and is_bare_identifier("C-1042 P-2291")
        and not is_bare_identifier("C-1042 status")
    )


def test_triage_reply_is_parsed_and_never_trusted_as_high():
    def triage(instructions, q):
        return 'Sure: ```{"intent": "CHECK_LEGALITY", "confidence": "high"}```'

    router = Router(vocab(), examples(), triage=triage)
    decided = router.route("Tell me about C-2087 please")
    assert decided.decided_by == "triage" and decided.intent is Intent.CHECK_LEGALITY
    assert decided.confidence is Confidence.MEDIUM


@pytest.mark.parametrize("reply", ["not json", '{"intent": "LAUNCH_ROCKETS"}', "{}"])
def test_unusable_triage_falls_back_to_a_lookup(reply):
    router = Router(vocab(), examples(), triage=lambda i, q: reply)
    decided = router.route("Tell me about C-2087 please")
    assert decided.decided_by == "default" and decided.intent is Intent.LOOKUP_CREW


def test_triage_can_say_a_question_is_not_crew_control():
    router = Router(vocab(), examples(), triage=lambda i, q: '{"intent": "NONE"}')
    decided = router.route("Translate good morning into Hindi.")
    assert decided.decided_by == "default" and decided.confidence is Confidence.LOW
    assert decided.notes == ["triage: not a crew-control question"] and decided.off_topic


def test_triage_instructions_count_the_operation_as_crew_control():
    """Plain flight questions were once declined as off-topic."""
    for word in ("Flights", "aircraft", "airports", "gates", "rosters", "disruptions"):
        assert word in TRIAGE_INSTRUCTIONS
    assert "nothing to do with the airline's" in TRIAGE_INSTRUCTIONS


def test_triage_failure_falls_back_to_a_lookup():
    def boom(instructions, q):
        raise TimeoutError

    decided = Router(vocab(), examples(), triage=boom).route("Tell me about C-2087 please")
    assert decided.decided_by == "default" and "TimeoutError" in decided.notes[0]


def test_triage_casing_is_tolerated():
    router = Router(
        vocab(),
        examples(),
        triage=lambda i, q: '{"intent": "check_legality", "confidence": "HIGH"}',
    )
    assert router.route("Tell me about C-2087 please").intent is Intent.CHECK_LEGALITY


def test_a_failing_semantic_search_falls_through_instead_of_failing_the_question():
    def down(q):
        raise TimeoutError

    decided = Router(vocab(), examples(), search=down, floor=0.1).route("Tell me about C-2087")
    assert decided.decided_by == "default" and decided.entities.crew_ids == ["C-2087"]
    with_triage = Router(
        vocab(),
        examples(),
        search=down,
        triage=lambda i, q: '{"intent": "LOOKUP_RISK", "confidence": "medium"}',
    )
    assert with_triage.route("Tell me about C-2087").decided_by == "triage"
