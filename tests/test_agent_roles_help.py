"""Questions about the assistant, the roles and the rules, answered before routing."""

import pytest
from agent_fixtures import examples, tools

from crewops.agent.pipeline import Pipeline
from crewops.agent.roles_help import about_roles
from crewops.agent.rule_topics import ALL, rule_topic
from crewops.alexa.skill import first_name, help_for
from crewops.domain import Role
from crewops.tools import Caller

CREW = Caller("c1042.nair", frozenset({Role.CREW}), crew_id="C-1042")
ADMIN = Caller("priya.desai", frozenset({Role.ADMIN}))
SUPER = Caller("vikram.saxena", frozenset({Role.SUPER_ADMIN}))
REPORTER = Caller("imran.sheikh", frozenset({Role.REPORTER}))
APPROVER = Caller("meera.bhatt", frozenset({Role.APPROVER}))
CONTROLLER = Caller("ananya.iyer", frozenset({Role.CONTROLLER}), desk="desk-1")


@pytest.fixture(scope="module")
def pipeline():
    return Pipeline.offline(tools(), examples())


def test_who_are_you_introduces_the_assistant_and_who_am_i_still_names_the_caller(pipeline):
    intro = pipeline.answer("Who are you?", CREW).text
    assert intro.startswith("I'm CrewOps") and "when do I fly next" in intro
    assert pipeline.answer("who am i", CREW).text.startswith("You're A. Nair (C-1042)")
    assert about_roles("who are you covering on P-2291", CONTROLLER) is None


def test_an_admin_can_do_nothing_a_super_admin_cannot(pipeline):
    text = pipeline.answer("what things can I do that superadmin can't", ADMIN).text
    assert text.startswith("Nothing: a super admin can do everything you can")
    assert "relax a threshold" in text and "Controller desks" not in text
    other = pipeline.answer("what can a super admin do that I can't", ADMIN).text
    assert "relax a threshold" in other and "reset an admin's password" in other
    assert about_roles("what can I do that a super admin can't", SUPER) == (
        "You're a super admin yourself."
    )


@pytest.mark.parametrize(
    ("caller", "question", "start"),
    [
        (
            ADMIN,
            "can I relax a threshold",
            "No: to relax a threshold, you need to be a super admin",
        ),
        (SUPER, "can I relax a threshold", "Yes"),
        (ADMIN, "can I change a threshold", "Yes: you can tighten a threshold"),
        (ADMIN, "can I reset an admin's password", "No"),
        (APPROVER, "can I approve my own plan", "No one can approve your own plan"),
        (REPORTER, "who approves plans", "An approver can approve"),
        (REPORTER, "can I file a delay", "Yes"),
    ],
)
def test_can_i_follows_the_server_rules(caller, question, start):
    assert about_roles(question, caller).startswith(start)


def test_help_is_for_the_callers_role(pipeline):
    assert "your own trips" in pipeline.answer("what can I do", CREW).text
    assert "file a sick call" in pipeline.answer("help", REPORTER).text
    assert "everything an admin can" in pipeline.answer("what can you do", SUPER).text
    assert "As crew" in pipeline.answer("hello", CREW).text


def test_crew_are_pointed_to_crew_control_when_they_are_sick(pipeline):
    assert "Call crew control" in pipeline.answer("I'm sick", CREW).text


def test_rule_questions_in_everyday_words_reach_the_rule(pipeline):
    assert rule_topic("what is the flight duty period limit") == "RULE-FDP-01"
    assert rule_topic("how much rest does crew need") == "RULE-REST-04"
    assert rule_topic("what are the rules") == ALL
    assert rule_topic("how many duty hours does C-1042 have") is None
    assert rule_topic("how many duty hours does she have left") is None
    assert pipeline.answer("what's the FDP limit", REPORTER).text.startswith("RULE-FDP-01")
    assert "7 rules" in pipeline.answer("what rules do you check", CREW).text


def test_crew_my_next_trip_checks_that_trip(pipeline):
    assert "legal for P-2291" in pipeline.answer("am I legal for my next trip", CREW).text
    assert pipeline.answer("when is my next trip", CREW).text.startswith("Your trips")


def test_waiting_for_me_reads_the_queue(pipeline, monkeypatch):
    rows = [
        {"disruption_id": "D-1", "what": "C-1042 sick", "status": "SUBMITTED", "desks": ["desk-1"]},
        {
            "disruption_id": "D-2",
            "what": "DX404 late",
            "status": "OPTIONS_READY",
            "desks": ["desk-1"],
        },
        {"disruption_id": "D-3", "what": "BLR-G6 shut", "status": "RESOLVING", "desks": ["desk-2"]},
    ]

    def open_disruptions(desk=None):
        mine = [r for r in rows if not desk or desk in r["desks"]]
        return {"desk": desk, "count": len(mine), "disruptions": mine}

    monkeypatch.setattr(pipeline.tools, "list_open_disruptions", open_disruptions)
    approver = pipeline.answer("what's waiting for my approval", APPROVER).text
    assert (
        approver
        == "1 plan to approve: D-1, C-1042 sick. 1 resolution to sign off: D-3, BLR-G6 shut."
    )
    controller = pipeline.answer("what should I do next", CONTROLLER).text
    assert controller.startswith("1 on desk-1 needs a plan: D-2, DX404 late")


def test_gate_answer_names_the_dates(pipeline):
    text = pipeline.answer("is gate BLR-G6 free", REPORTER).text
    assert "KS412 of 2026-10-13 holds it until 07:00Z on 2026-10-13" in text


def test_alexa_welcome_and_help_fit_the_role():
    assert first_name("c1042.nair") == "Nair" and first_name("rohit.malhotra") == "Rohit"
    assert "what's waiting" not in help_for(CREW) and "when do I fly next" in help_for(CREW)
    assert "what's waiting" in help_for(CONTROLLER)
