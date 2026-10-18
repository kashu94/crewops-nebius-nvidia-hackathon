"""The held-out evaluation: its scoring, and the guarantees it must always show.

Rewording scores are reported, never asserted, or the held-out set would become a tuning
target. What is asserted holds whatever the scores: no unsourced claim, no personal data,
and every role, question-back, follow-up and injection case behaving.
"""

import pytest

from crewops.agent.evaluate import (
    evaluate,
    facts,
    gold_questions,
    load_heldout,
    offline_asker,
)


@pytest.fixture(scope="module")
def report():
    asker, router = offline_asker()
    return evaluate(load_heldout(), gold_questions(), router.example_intents, asker, "offline")


def test_facts_are_the_ids_times_and_figures_stated():
    text = "D. Reddy (C-3310) covers P-2291 on DX401 at 06:00Z for ₹18,500; RULE-REST-04 holds."
    assert facts(text) == {"C-3310", "P-2291", "DX401", "06:00Z", "18,500", "RULE-REST-04"}
    assert facts("") == set()


def test_every_rewording_keeps_the_ids_and_dates_of_its_gold_question():
    import re

    gold = gold_questions()
    ids = re.compile(r"\b(?:C-\d{4}|P-\d{4}|DX\d{3}|VT-[A-Z]{3}|RULE-[A-Z]+-\d+)\b")
    for case in load_heldout()["rewordings"]:
        assert set(ids.findall(case["prompt"])) <= set(ids.findall(gold[case["of"]])), case["id"]


def test_the_set_covers_every_gold_question_once():
    of = [c["of"] for c in load_heldout()["rewordings"]]
    assert sorted(of) == sorted(gold_questions()) and len(set(of)) == len(of)


def test_no_answer_states_an_unsourced_fact_or_personal_data(report):
    summary = report.summary()
    assert summary["unsourced_answers"] == 0 and summary["personal_data_leaks"] == 0


@pytest.mark.parametrize("kind", ["role", "question_back", "follow_up", "injection"])
def test_safety_and_conversation_cases_all_pass(report, kind):
    failed = [r.case_id for r in report.kind(kind) if not r.passed]
    assert not failed, failed


def test_the_report_counts_every_case(report):
    held = load_heldout()
    expected = sum(len(v) for k, v in held.items() if k != "note")
    assert len(report.results) == expected
    assert "rewording" in report.summary()["passed"]
