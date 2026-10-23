"""The model-free pipeline end to end: plan, call, render, verify, redact."""

import pytest
from agent_fixtures import examples, questions, tools, vocab

from crewops.agent.pipeline import SCOPE, Pipeline
from crewops.agent.planner import false_premise, seed_calls
from crewops.agent.router import Router
from crewops.domain import Role
from crewops.tools import Caller, TraceEntry

CONTROLLER = Caller("u-ctl", frozenset({Role.CONTROLLER}))
NEEDS_A_MODEL = {"Q26", "Q30", "Q38"}
"""Gold questions no planned call answers; the model advisor handles them."""


@pytest.fixture(scope="module")
def pipeline():
    return Pipeline.offline(tools(), examples())


def ask(pipeline, text, caller=CONTROLLER):
    return pipeline.answer(text, caller)


def test_every_gold_answer_is_verified_or_says_what_it_needs(pipeline):
    for q in questions():
        answer = ask(pipeline, q["prompt"])
        if q["question_id"] in NEEDS_A_MODEL:
            assert answer.source == "clarify" and "doesn't mean all is clear" in answer.text
            continue
        assert answer.source == "template", q["question_id"]
        assert answer.verification.ok, (q["question_id"], answer.verification.summary())


@pytest.mark.parametrize(
    ("qid", "fragments"),
    [
        ("Q01", ["12 reserve duties", "D. Reddy (C-3310)", "06:00Z to 18:00Z"]),
        ("Q11", ["S. Kapoor (C-2210)"]),
        ("Q17", ["KS412, KS413 and KS588", "397 passengers"]),
        ("Q18", ["not legal", "RULE-DUTY-02 fails"]),
        ("Q21", ["RULE-REST-04 fails", "9.5h rest"]),
        ("Q22", ["RULE-CERT-06 fails"]),
        ("Q24", ["V. Menon (C-3305) is not legal"]),
        ("Q31", ["D. Reddy (C-3310)", "₹23,090"]),
        ("Q32", ["Cheapest joint plan", "Total ₹48,980"]),
        ("Q33", ["DX404 must come off this crew"]),
        ("Q36", ["acknowledge by 05:30Z"]),
        ("Q37", ["N. Verma (C-3316)", "₹27,410"]),
    ],
)
def test_gold_answers_say_the_right_things(pipeline, qid, fragments):
    prompt = next(q["prompt"] for q in questions() if q["question_id"] == qid)
    text = ask(pipeline, prompt).text
    for fragment in fragments:
        assert fragment in text, (qid, fragment, text)


def test_a_false_premise_is_asked_about_before_anything_runs(pipeline):
    answer = ask(pipeline, "FO C-1042 is sick, who covers?")
    assert answer.source == "clarify" and answer.awaiting == "confirmation"
    assert "is a Captain, not a First Officer" in answer.text and answer.trace == []
    assert "isn't one of our stations" in ask(pipeline, "Flights from BLR to XYZ").text
    assert (
        false_premise("C-2210 (DEL base) on P-2291", _ents("C-2210 (DEL base)"), tools(), vocab())
        is None
    )


def _ents(text):
    from crewops.agent.entities import extract

    return extract(text, vocab())


def test_a_wrong_id_asks_and_never_guesses(pipeline):
    answer = ask(pipeline, "Is C-1024 legal for P-2291?")
    assert answer.awaiting == "confirmation" and "Did you mean C-1042" in answer.text


def test_ambiguous_flight_asks_for_a_date(pipeline):
    answer = ask(pipeline, "Who can cover the captain on DX401?")
    assert answer.awaiting == "detail" and "Which date?" in answer.text


def test_off_topic_gets_the_scope(pipeline):
    assert ask(pipeline, "Tell me a joke").text == SCOPE


def test_crew_see_only_their_own_records(pipeline):
    me = Caller("u-crew", frozenset({Role.CREW}), crew_id="C-1042")
    mine = ask(pipeline, "How many duty hours do I have?", me)
    assert mine.verification.ok and "A. Nair (C-1042)" in mine.text
    other = ask(pipeline, "How many duty hours does C-3310 have?", me)
    assert other.text == "That isn't available to your role."
    assert "D. Reddy" not in other.text
    roster = ask(pipeline, "Who is on reserve at BLR on 13 Oct?", me)
    assert "Your role doesn't give access" in roster.text and roster.trace == []


UNPLANNED = "Which single flight leg has the most seats at risk if cancelled, and why?"
"""No planned call answers this one, so the drafter is consulted."""
SEATS = {"entity": "flights", "sort_by": "seats", "sort_desc": True, "limit": 3}


def model_pipeline(drafter):
    return Pipeline(tools(), Router(vocab(), examples()), drafter=drafter)


def test_a_verified_model_draft_is_used():
    def drafter(question, route, trace, run, allowed):
        run("lookup", SEATS)
        return "DX401 on 2026-10-12 has the most seats at risk: 162."

    answer = model_pipeline(drafter).answer(UNPLANNED, CONTROLLER)
    assert answer.source == "model" and answer.verification.ok


def test_an_unverified_draft_falls_back():
    def drafter(question, route, trace, run, allowed):
        run("lookup", SEATS)
        return "DX999 has 400 seats at risk."

    answer = model_pipeline(drafter).answer(UNPLANNED, CONTROLLER)
    assert answer.source == "template" and "DX999" not in answer.text
    assert "model draft rejected" in answer.notes[0]


def test_a_failing_model_never_costs_the_answer():
    def drafter(question, route, trace, run, allowed):
        raise TimeoutError

    answer = model_pipeline(drafter).answer(UNPLANNED, CONTROLLER)
    assert answer.source == "clarify" and "TimeoutError" in answer.notes[0]


def test_drafter_tool_calls_count_as_evidence():
    def drafter(question, route, trace, run, allowed):
        extra = run("duty_clock", {"crew_id": "C-3310"})
        return f"C-3310 has {extra.result['duty_hours_7d']} duty hours this week."

    answer = model_pipeline(drafter).answer(UNPLANNED, CONTROLLER)
    assert answer.source == "model" and answer.trace[-1].tool == "duty_clock"


def test_answer_record_is_redacted():
    from crewops.agent.pipeline import Answer

    answer = Answer("q", "Call ops on +91 98765 43210.", None, notes=["mail a@b.example"])
    record = str(answer.to_dict())
    assert "98765" not in record and "a@b.example" not in record


def test_empty_and_long_questions(pipeline):
    assert ask(pipeline, "  ").awaiting == "detail"
    assert ask(pipeline, "x" * 1001).source == "refused"


def test_planned_calls_for_a_cover_question_on_someone_else():
    route = Router(vocab(), examples()).route("Can reserve C-3305 cover P-2291?")
    calls = seed_calls(route, "Can reserve C-3305 cover P-2291?", vocab(), tools())
    assert [c.name for c in calls] == ["check_legality"]


def test_template_handles_a_failed_call():
    from crewops.agent.render import render

    failed = TraceEntry("duty_clock", {}, error_code="UNRESOLVED_ENTITY", error="no crew C-9")
    assert render([failed]) == "no crew C-9"


# -- Regressions -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "Is FO C-2087 on P-2291?",
        "Is C-2087 (DEL base) on anything?",
        "C-2087 is ATR72-rated, right?",
    ],
)
def test_premise_check_reveals_nothing_to_crew_about_others(pipeline, question):
    me = Caller("u-crew", frozenset({Role.CREW}), crew_id="C-1042")
    text = ask(pipeline, question, me).text
    assert "Captain" not in text and "BLR" not in text and "A320" not in text
    reporter = Caller("u-rep", frozenset({Role.REPORTER}))
    assert "Captain" not in ask(pipeline, question, reporter).text


def test_premise_check_still_covers_the_crew_members_own_record(pipeline):
    me = Caller("u-crew", frozenset({Role.CREW}), crew_id="C-1042")
    assert "is a Captain, not a First Officer" in ask(pipeline, "Am I FO C-1042?", me).text


@pytest.mark.parametrize(
    "question", ["My duty hours?", "What are my duty hours on Friday?", "Am I legal for P-2291?"]
)
def test_first_person_crew_questions(pipeline, question):
    me = Caller("u-crew", frozenset({Role.CREW}), crew_id="C-1042")
    answer = ask(pipeline, question, me)
    assert "C-1042" in answer.text and answer.verification.ok, answer.text


def test_drafter_cannot_add_unrecorded_evidence():
    seen = {}

    def drafter(question, route, trace, run, allowed):
        seen["forbidden"] = run("lookup", {"entity": "crew"}).error_code
        return "C-1042 is sick."

    me = Caller("u-crew", frozenset({Role.CREW}), crew_id="C-1042")
    p = Pipeline(tools(), Router(vocab(), examples()), drafter=drafter)
    p.answer("Which crew have 45 or more duty hours in the 7 days ending 2026-10-13?", me)
    assert seen["forbidden"] == "FORBIDDEN"


def test_controller_profile_answer_verifies(pipeline):
    answer = ask(pipeline, "Show C-1042 duty hours, certifications and risk")
    assert answer.verification.ok, answer.verification.summary()


def test_every_paraphrase_and_off_topic_answer_verifies(pipeline):
    from agent_fixtures import paraphrases

    data = paraphrases()
    for item in data["paraphrases"] + data["off_topic"]:
        answer = ask(pipeline, item["prompt"])
        if answer.source == "template":
            assert answer.verification.ok, (item["id"], answer.verification.summary())


@pytest.mark.parametrize("question", ["Play some relaxing music.", "Set an alarm for six."])
def test_off_topic_never_looks_up_people(pipeline, question):
    answer = ask(pipeline, question)
    assert answer.text == SCOPE and answer.trace == []


def test_second_round_planner_findings(pipeline):
    reserves = ask(pipeline, "Who is on reserve at BLR between 13 and 15 Oct?")
    assert reserves.trace[0].args["filters"]["dates"] == {"gte": "2026-10-13", "lte": "2026-10-15"}
    seat = ask(pipeline, "Who is legal for the Captain seat on P-2291?")
    assert seat.trace[0].tool == "find_options" and "D. Reddy (C-3310)" in seat.text
    atr = ask(pipeline, "List ATR 72 first officers based at BLR")
    assert atr.trace[0].args["filters"]["ratings"] == "ATR72"
    me = Caller("u-crew", frozenset({Role.CREW}), crew_id="C-1042")
    friday = ask(pipeline, "What are my duty hours on Friday?", me)
    assert "as of 2026-10-16" in friday.text
    assert (
        "isn't one of our stations" not in ask(pipeline, "Flights in GMT on 14 Oct from BLR").text
    )


@pytest.mark.parametrize(
    "question", ["How much is a ticket from Delhi to London?", "Book me a hotel room in Mumbai"]
)
def test_unrecognised_questions_with_a_city_get_the_scope(pipeline, question):
    answer = ask(pipeline, question)
    assert answer.text == SCOPE and answer.trace == []


def test_third_round_routing(pipeline):
    assert "Which seat needs cover on KS412" in ask(pipeline, "Who covers KS412 on 14 Oct?").text
    assert ask(pipeline, "Did KS412 fly on Monday?").trace[0].tool == "lookup"
    assert (
        ask(pipeline, "Show flights in GMT on 14 Oct from BLR").trace[0].args["entity"] == "flights"
    )
    assert "A. Nair (C-1042)" in ask(pipeline, "C-1042?").text


def test_the_model_never_picks_the_seat_to_cover():
    called = []

    def drafter(question, route, trace, run, allowed):
        called.append(question)
        return "The cheapest legal option for the Captain seat is C-3310."

    answer = model_pipeline(drafter).answer("Who can cover P-2291 on 13 Oct?", CONTROLLER)
    assert answer.source == "clarify" and answer.awaiting == "detail"
    assert answer.text.startswith("Which seat needs cover on P-2291") and not called


def test_cover_options_read_as_a_sentence(pipeline):
    prompt = next(q["prompt"] for q in questions() if q["question_id"] == "Q31")
    text = ask(pipeline, prompt).text
    assert "the cheapest legal choice is to assign Captain D. Reddy (C-3310)" in text
    assert "Assign " not in text


# -- Conversation, identity and voice --------------------------------------------------

ROHIT = Caller("rohit", frozenset({Role.CONTROLLER}), desk="desk-2")
NAIR = Caller("crew1042", frozenset({Role.CREW}), crew_id="C-1042")


@pytest.mark.parametrize(
    ("text", "starts"),
    [
        ("Hi", "Hello Rohit."),
        ("hello alexa", "Hello Rohit."),
        ("Good morning!", "Hello Rohit."),
        ("how are you?", "All good, thanks, Rohit."),
        ("Thank you", "You're welcome."),
        ("ok thanks", "You're welcome."),
        ("bye", "Goodbye, Rohit."),
    ],
)
def test_greetings_and_thanks_get_a_reply_not_a_lookup(pipeline, text, starts):
    answer = ask(pipeline, text, ROHIT)
    assert answer.source == "smalltalk" and answer.text.startswith(starts)
    assert answer.trace == []


def test_who_am_i_comes_from_the_sign_in(pipeline):
    me = ask(pipeline, "what is my name", ROHIT)
    assert me.text.startswith("You're Rohit Malhotra") and "desk-2" in me.text
    assert ask(pipeline, "who am I?", NAIR).text.startswith("You're A. Nair (C-1042), Captain")
    # A real question that opens with a greeting is still answered.
    assert ask(pipeline, "hi, who covers C-1042 on 13 Oct?", ROHIT).source == "template"
    # So is one that opens by saying who you are; "who am I and what can I do" stays small talk.
    for question in (
        "I am a controller, who covers C-1042 on 13 Oct?",
        "Am I a crew member on P-2291?",
    ):
        assert ask(pipeline, question, ROHIT).source != "smalltalk"
    assert ask(pipeline, "who am I and what can I do?", ROHIT).source == "smalltalk"
    assert ask(pipeline, "what's my desk today?", ROHIT).source == "smalltalk"


def test_my_certificate_means_the_asker_and_never_everyone(pipeline):
    controller = ask(pipeline, "is my certification valid?", ROHIT)
    assert "not on the crew roster" in controller.text and controller.trace == []
    crew = ask(pipeline, "is my certification valid?", NAIR)
    assert crew.text.startswith("4 certifications: C-1042 licence")
    assert "600" not in ask(pipeline, "list certifications", ROHIT).text
    # Crew read only their own records.
    other = ask(pipeline, "is C-3310's medical valid?", NAIR)
    assert "C-3310 medical" not in other.text


@pytest.mark.parametrize(
    ("heard", "means"),
    [
        ("see 3231 is sick who covers", "C-3231 is sick who covers"),
        ("C 32 31 is sick", "C-3231 is sick"),
        ("c3231 sick", "C-3231 sick"),
        ("who flies pee 2291", "who flies P-2291"),
        ("is K S 412 late", "is KS412 late"),
        ("is D X 401 late", "is DX401 late"),
        ("I want to see 2026-10-13", "I want to see 2026-10-13"),
        ("see you at 10", "see you at 10"),
    ],
)
def test_ids_are_read_as_voice_input_writes_them(heard, means):
    from crewops.agent.entities import spoken_ids

    assert spoken_ids(heard) == means


def test_a_spoken_crew_id_is_answered(pipeline):
    answer = ask(pipeline, "see 3231 is sick who covers", ROHIT)
    assert answer.question.startswith("C-3231") and "C-3231 flies 2 pairings" in answer.text


def test_my_desk_means_the_signed_in_controllers_desk(pipeline):
    answer = ask(pipeline, "what disruptions are there on my desk", ROHIT)
    (call,) = [e for e in answer.trace if e.tool == "list_open_disruptions"]
    assert call.args == {"desk": "desk-2"}
    # Questions about one disruption's effect keep their own route.
    impact = ask(pipeline, "what is the impact of the disruption on P-2291", ROHIT)
    assert impact.route.intent.value == "IMPACT_OF_EVENT"


def test_a_missing_id_offers_who_was_just_asked_about_first(pipeline):
    from datetime import UTC, datetime

    from crewops.agent.context import Turn

    first = ask(pipeline, "C-1042 certifications", ROHIT)
    then = pipeline.answer(
        "is there any id with crew C-123?", ROHIT, Turn.of(first, datetime.now(UTC))
    )
    assert then.text.startswith("C-123 doesn't exist. Did you mean A. Nair (C-1042)")


def test_crew_ask_about_themselves_and_get_their_own_roster(pipeline):
    tomorrow = ask(pipeline, "what are my flights tomorrow", NAIR)
    assert tomorrow.text.startswith("Your trips tomorrow: 13 Oct: P-2291 on VT-DXC as Captain")
    assert ask(pipeline, "what are my flights today", NAIR).text == "No trips today."
    typo = ask(pipeline, "am I legal for p 22291", NAIR).text
    assert typo.startswith("There's no trip P-22291. Your next: 13 Oct: P-2291")
    assert ask(pipeline, "am I a captain or a first officer", NAIR).text == (
        "You're a Captain with Konkan Skyways, based at BLR."
    )
    assert ask(pipeline, "when am I not available", NAIR).text == (
        "You have no days reported unavailable or on leave."
    )
    # A real trip still goes to the legality engine.
    assert "legal for P-2291" in ask(pipeline, "am I legal for P-2291", NAIR).text
    reserve = Caller("c3310", frozenset({Role.CREW}), crew_id="C-3310")
    assert ask(pipeline, "am I on standby", reserve).text.startswith(
        "You're on call at BLR, 06:00 to 18:00Z"
    )
