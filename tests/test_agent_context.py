"""Follow-up questions: what the previous turn may lend, and what it never may."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from agent_fixtures import examples, tools
from botocore.stub import ANY, Stubber

from crewops.agent.context import Turn
from crewops.agent.memory import ConversationMemory, valid_conversation
from crewops.agent.pipeline import Pipeline
from crewops.domain import Role
from crewops.tools import Caller

NOW = datetime(2026, 10, 8, 5, 0, tzinfo=UTC)
CONTROLLER = Caller("rohit", frozenset({Role.CONTROLLER}), desk="desk-2")
CREW = Caller("crew1042", frozenset({Role.CREW}), crew_id="C-1042")


@pytest.fixture(scope="module")
def pipeline():
    return Pipeline.offline(tools(), examples())


def turn_after(pipeline, question, caller=CONTROLLER):
    return Turn.of(pipeline.answer(question, caller), NOW)


def test_a_short_reply_is_read_with_the_question_it_answers(pipeline):
    asked = turn_after(pipeline, "Who can cover P-2291 on 13 Oct?")
    assert asked.awaiting == "detail" and asked.pairing_ids == ("P-2291",)
    answer = pipeline.answer("Captain", CONTROLLER, asked)
    assert answer.awaiting is None and "Captain seat on P-2291" in answer.text
    assert "read together with the question it answers" in answer.notes


def test_a_new_question_after_a_question_back_stands_on_its_own(pipeline):
    asked = turn_after(pipeline, "Who can cover P-2291 on 13 Oct?")
    answer = pipeline.answer("Who is on reserve at BLR on 13 Oct?", CONTROLLER, asked)
    assert "D. Reddy (C-3310)" in answer.text and not answer.notes


def test_she_means_the_one_crew_member_just_asked_about(pipeline):
    about = turn_after(pipeline, "How many duty hours does C-1042 have?")
    answer = pipeline.answer("Is she legal for P-2291?", CONTROLLER, about)
    assert "C-1042" in answer.text
    assert "read as C-1042, from your previous question" in answer.notes
    alone = pipeline.answer("Is she legal for P-2291?", CONTROLLER)
    assert "Nothing was checked" in alone.text


@pytest.mark.parametrize(
    "reply",
    ["What is rule FDP-1?", "Reserves at BLR?", "Duty hours C-3310", "Thanks, that is all"],
)
def test_anything_but_the_missing_detail_is_a_new_question(pipeline, reply):
    asked = turn_after(pipeline, "Who can cover P-2291 on 13 Oct?")
    together = pipeline.answer(reply, CONTROLLER, asked)
    alone = pipeline.answer(reply, CONTROLLER)
    assert together.text == alone.text
    assert "read together with the question it answers" not in together.notes


@pytest.mark.parametrize("reply", ["Captain", "the first officer", "13 Oct", "C-1042"])
def test_a_seat_a_date_or_one_id_answers_the_question_back(pipeline, reply):
    asked = turn_after(pipeline, "Who can cover P-2291 on 13 Oct?")
    answer = pipeline.answer(reply, CONTROLLER, asked)
    assert "read together with the question it answers" in answer.notes


@pytest.mark.parametrize(
    "question",
    [
        "Who is on reserve at BLR on 13 Oct and are they rested?",
        "List the reserves at BLR on 13 Oct with their duty hours",
        "Is there a plan waiting and has she approved it?",
        "Who is her captain on P-2291?",
    ],
)
def test_plural_and_other_people_are_not_the_last_crew_member(pipeline, question):
    about = turn_after(pipeline, "How many duty hours does C-1042 have?")
    together = pipeline.answer(question, CONTROLLER, about)
    assert together.text == pipeline.answer(question, CONTROLLER).text
    assert not any("previous question" in n for n in together.notes)


def test_a_crew_member_asking_about_themselves_is_not_redirected(pipeline):
    about = Turn("x", NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), crew_ids=("C-3310",))
    question = "How many duty hours do I have before she rosters me again?"
    answer = pipeline.answer(question, CREW, about)
    assert "C-1042" in answer.text and not any("previous" in n for n in answer.notes)


def test_a_pronoun_never_guesses_between_two_people(pipeline):
    about = Turn("compare", NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), crew_ids=("C-1042", "C-3310"))
    answer = pipeline.answer("Is she legal for P-2291?", CONTROLLER, about)
    assert "Nothing was checked" in answer.text and not answer.notes


def test_carried_ids_still_go_through_the_role_checks(pipeline):
    about = Turn("x", NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), crew_ids=("C-3310",))
    answer = pipeline.answer("How many duty hours does she have?", CREW, about)
    assert answer.text == "That isn't available to your role."


def test_turns_go_stale_and_bad_stored_data_is_ignored():
    turn = Turn("q", "2026-10-08T05:00:00Z")
    assert turn.fresh(NOW + timedelta(minutes=10))
    assert not turn.fresh(NOW + timedelta(minutes=16))
    assert not turn.fresh(NOW - timedelta(minutes=1))  # from the future
    assert not Turn("q", "yesterday").fresh(NOW)
    for bad in (None, "text", {"question": 3, "at": "x"}, {"question": "q"}):
        assert Turn.from_dict(bad) is None
    odd = Turn.from_dict({"question": "q", "at": "t", "crew_ids": ["C-1", 7], "awaiting": 1})
    assert odd.crew_ids == ("C-1",) and odd.awaiting is None
    assert Turn.from_dict(turn.to_dict()) == turn


# -- AgentCore Memory, against botocore's own request validation --------------------------

MEMORY_ID = "crewops_test_memory-abcdEFGH12"


def memory():
    import boto3

    client = boto3.client(
        "bedrock-agentcore",
        region_name="ap-southeast-2",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    return ConversationMemory(client, MEMORY_ID), Stubber(client)


def event(payload, at=NOW):
    return {
        "memoryId": MEMORY_ID,
        "actorId": "rohit",
        "sessionId": "c1",
        "eventId": "e1",
        "eventTimestamp": at,
        "payload": payload,
    }


def test_a_turn_is_stored_and_read_back():
    mem, stub = memory()
    turn = Turn("Who can cover P-2291?", "2026-10-08T05:00:00Z", "detail", (), ("P-2291",))
    stub.add_response(
        "create_event",
        {"event": event([])},
        {
            "memoryId": MEMORY_ID,
            "actorId": "rohit",
            "sessionId": "c1",
            "eventTimestamp": ANY,
            "payload": [
                {"conversational": {"content": {"text": turn.question}, "role": "USER"}},
                {"conversational": {"content": {"text": "Which seat?"}, "role": "ASSISTANT"}},
                {"blob": json.dumps(turn.to_dict())},
            ],
        },
    )
    older = Turn("older", "2026-10-08T04:59:00Z")
    stub.add_response(
        "list_events",
        {
            "events": [
                event([{"blob": turn.to_dict()}], NOW),  # an object is read too
                event([{"blob": json.dumps(older.to_dict())}], NOW - timedelta(minutes=1)),
            ]
        },
        {
            "memoryId": MEMORY_ID,
            "actorId": "rohit",
            "sessionId": "c1",
            "includePayloads": True,
            "maxResults": 100,
        },
    )
    with stub:
        mem.remember("rohit", "c1", turn, "Which seat?")
        assert mem.last_turn("rohit", "c1", NOW + timedelta(minutes=5)) == turn


def test_memory_failures_never_cost_an_answer():
    mem, stub = memory()
    stub.add_client_error("list_events", "ThrottlingException")
    stub.add_client_error("create_event", "ServiceUnavailableException")
    with stub:
        assert mem.last_turn("rohit", "c1", NOW) is None
        mem.remember("rohit", "c1", Turn("q", "t"), "a")  # logged, not raised


def test_conversation_ids_follow_agentcore_rules():
    assert valid_conversation("alexa-session_42")
    for bad in ("", "-x", "a b", "a/b", "x" * 101):
        assert not valid_conversation(bad)
    mem, stub = memory()
    with stub:  # no call is made for an id AgentCore would refuse
        assert mem.last_turn("rohit", "a b", NOW) is None
        mem.remember("rohit", "a b", Turn("q", "t"), "a")
