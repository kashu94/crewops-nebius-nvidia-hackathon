"""The model roles and the guardrail, against a scripted stand-in for the model service
(Converse shapes, as ``NebiusClient`` returns them) and Bedrock Guardrails (no network)."""

import json

import pytest
from agent_fixtures import examples, tools, vocab

from crewops.agent.models import Guardrail, ModelAdvisor, ModelExplainer, ModelTriage
from crewops.agent.pipeline import Pipeline
from crewops.agent.router import Router
from crewops.domain import Role
from crewops.tools import Caller, bedrock_tool_config

CONTROLLER = Caller("u-ctl", frozenset({Role.CONTROLLER}))
CREW = Caller("u-crew", frozenset({Role.CREW}), crew_id="C-1042")


def text_reply(text):
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 100, "outputTokens": 20},
    }


def tool_reply(*calls):
    content = [
        {"toolUse": {"toolUseId": f"t{i}", "name": name, "input": args}}
        for i, (name, args) in enumerate(calls)
    ]
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": "tool_use",
        "usage": {"inputTokens": 100, "outputTokens": 10},
    }


class FakeBedrock:
    """Answers converse() from a script, records every request; screens with a rule."""

    def __init__(self, replies=(), blocked_words=()):
        self.replies = list(replies)
        self.requests = []
        self.blocked_words = blocked_words
        self.screened = []

    def converse(self, **request):
        self.requests.append(json.loads(json.dumps(request)))
        if not self.replies:
            raise AssertionError("unexpected model call")
        return self.replies.pop(0)

    def apply_guardrail(self, **request):
        text = request["content"][0]["text"]["text"]
        self.screened.append((request["source"], text))
        if any(w in text for w in self.blocked_words):
            return {"action": "GUARDRAIL_INTERVENED", "outputs": [{"text": "Blocked."}]}
        return {"action": "NONE", "outputs": []}


def pipeline_with(fake, **kwargs):
    advisor = ModelAdvisor(fake, "advisor", tools(), **kwargs)
    return Pipeline(tools(), Router(vocab(), examples()), drafter=advisor), advisor


# -- Advisor ----------------------------------------------------------------------------

UNPLANNED = "Which single flight leg has the most seats at risk if cancelled, and why?"
"""A question the planner cannot answer from its entities: the advisor's job."""
SEATS = ("lookup", {"entity": "flights", "sort_by": "seats", "sort_desc": True, "limit": 3})


def test_advisor_calls_tools_and_its_answer_is_verified():
    fake = FakeBedrock(
        [tool_reply(SEATS), text_reply("DX401 on 2026-10-12 has the most seats at risk: 162.")]
    )
    pipeline, advisor = pipeline_with(fake)
    answer = pipeline.answer(UNPLANNED, CONTROLLER)
    assert answer.source == "model" and answer.verification.ok
    assert [e.tool for e in answer.trace] == ["lookup"]
    first = fake.requests[0]
    assert [m["role"] for m in first["messages"]] == ["user"]
    assert "Stations: BLR" in first["system"][0]["text"]
    assert "250,000" not in first["system"][0]["text"]  # no figure the verifier cannot source
    result = fake.requests[1]["messages"][-1]["content"][0]["toolResult"]
    assert result["status"] == "success" and '"seats": 162' in result["content"][0]["text"]
    assert advisor.last.rounds == 1 and advisor.last.usage.calls == 2


def test_planned_results_are_replayed_as_the_models_own_calls():
    messages = ModelAdvisor._opening(
        "q", [dispatch_entry("explain_rule", {"rule_id": "RULE-REST-04"})]
    )
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    use = messages[1]["content"][0]["toolUse"]
    result = messages[2]["content"][0]["toolResult"]
    assert use["toolUseId"] == result["toolUseId"] == "planned-0" and use["name"] == "explain_rule"


def dispatch_entry(name, args):
    from crewops.tools import dispatch

    return dispatch(tools(), CONTROLLER, name, args)


def test_a_question_the_planner_answered_never_reaches_the_advisor():
    fake = FakeBedrock([])
    pipeline, _ = pipeline_with(fake)
    for question in ("Who is on reserve at BLR on 13 Oct?", "C-1042 is sick, who covers?"):
        assert pipeline.answer(question, CONTROLLER).source == "template"
    assert fake.requests == []


def test_an_invented_figure_falls_back():
    fake = FakeBedrock([tool_reply(SEATS), text_reply("DX401 has 999 seats at risk.")])
    pipeline, _ = pipeline_with(fake)
    answer = pipeline.answer(UNPLANNED, CONTROLLER)
    assert answer.source == "template" and "999" not in answer.text
    assert "model draft rejected" in answer.notes[0]


@pytest.mark.parametrize("draft", ["Done.", "Nobody can cover it; cancel the flight."])
def test_a_draft_with_no_sourced_fact_is_not_used(draft):
    fake = FakeBedrock([tool_reply(SEATS), text_reply(draft)])
    pipeline, _ = pipeline_with(fake)
    answer = pipeline.answer(UNPLANNED, CONTROLLER)
    assert answer.source == "template" and answer.text != draft


@pytest.mark.parametrize(
    "stop", ["max_tokens", "content_filtered", "guardrail_intervened", "malformed_model_output"]
)
def test_an_unfinished_reply_is_no_answer(stop):
    cut = text_reply("DX401 on 2026-10-12 has the most")
    cut["stopReason"] = stop
    fake = FakeBedrock([tool_reply(SEATS), cut])
    pipeline, advisor = pipeline_with(fake)
    answer = pipeline.answer(UNPLANNED, CONTROLLER)
    assert advisor.last.stop == stop and answer.source == "template"
    assert "no complete answer" in answer.notes[0]


def test_tools_offered_are_the_callers_and_calls_are_role_checked():
    question = "Which crew have 45 or more duty hours in the 7 days ending 2026-10-13?"
    fake = FakeBedrock(
        [tool_reply(("duty_clock", {"crew_id": "C-3310"})), text_reply("I can't check that.")]
    )
    pipeline, _ = pipeline_with(fake)
    answer = pipeline.answer(question, CREW)
    offered = {t["toolSpec"]["name"] for t in fake.requests[0]["toolConfig"]["tools"]}
    assert offered == {
        "duty_clock",
        "notification_brief",
        "check_legality",
        "explain_rule",
        "search_rules",
    }
    refused = fake.requests[1]["messages"][-1]["content"][0]["toolResult"]
    assert refused["status"] == "error" and "FORBIDDEN" in refused["content"][0]["text"]
    assert "C-3310" not in answer.text


def test_a_tool_outside_the_offer_is_dropped_from_the_history():
    question = "Which crew have 45 or more duty hours in the 7 days ending 2026-10-13?"
    fake = FakeBedrock([tool_reply(("lookup", {"entity": "crew"}))])
    pipeline, advisor = pipeline_with(fake)
    answer = pipeline.answer(question, CREW)
    assert advisor.last.stop == "no_usable_tool" and len(fake.requests) == 1
    assert answer.trace == []


def test_repeated_calls_are_not_rerun():
    fake = FakeBedrock([tool_reply(SEATS), tool_reply(SEATS), text_reply("Done.")])
    pipeline, _ = pipeline_with(fake)
    answer = pipeline.answer(UNPLANNED, CONTROLLER)
    assert [e.tool for e in answer.trace] == ["lookup"]
    note = fake.requests[2]["messages"][-1]["content"][0]["toolResult"]
    assert "already called" in note["content"][0]["text"]


def test_out_of_rounds_means_the_template():
    looping = [tool_reply(("explain_rule", {"rule_id": f"RULE-REST-0{i}"})) for i in range(5)]
    pipeline, advisor = pipeline_with(FakeBedrock(looping), max_rounds=2)
    answer = pipeline.answer(UNPLANNED, CONTROLLER)
    assert advisor.last.stop == "budget" and answer.source in ("template", "clarify")


def test_the_time_budget_is_checked_before_every_call():
    import time

    class Slow(FakeBedrock):
        def converse(self, **request):
            time.sleep(0.2)
            return super().converse(**request)

    fake = Slow([tool_reply(SEATS), text_reply("unused")])
    pipeline, advisor = pipeline_with(fake, budget_seconds=0.1)
    pipeline.answer(UNPLANNED, CONTROLLER)
    assert advisor.last.stop == "budget" and len(fake.requests) == 1


def test_a_model_outage_still_answers():
    class Down(FakeBedrock):
        def converse(self, **request):
            raise TimeoutError

    pipeline, _ = pipeline_with(Down())
    answer = pipeline.answer(UNPLANNED, CONTROLLER)
    assert answer.source == "clarify" and "TimeoutError" in answer.notes[0]


# -- Explainer, triage, guardrail -------------------------------------------------------


def test_explainer_rewording_must_keep_every_fact():
    question = "Can C-3310 cover P-2291?"

    def run(reply):
        fake = FakeBedrock([text_reply(reply)])
        p = Pipeline(tools(), Router(vocab(), examples()), polisher=ModelExplainer(fake, "e"))
        return p.answer(question, CONTROLLER), fake

    good, fake = run("Yes: D. Reddy (C-3310) is legal for P-2291, and every rule passes.")
    assert good.source == "explained"
    for bad in ("Yes, they can.", "D. Reddy (C-3310) is legal.", "Done."):
        answer, _ = run(bad)
        assert answer.source == "template" and "C-3310" in answer.text and "P-2291" in answer.text
    cut = text_reply("Yes: D. Reddy (C-3310) is legal for P-2291, and every")
    cut["stopReason"] = "max_tokens"
    p = Pipeline(
        tools(), Router(vocab(), examples()), polisher=ModelExplainer(FakeBedrock([cut]), "e")
    )
    assert p.answer(question, CONTROLLER).source == "template"


def test_lookups_are_not_reworded():
    fake = FakeBedrock([])
    p = Pipeline(tools(), Router(vocab(), examples()), polisher=ModelExplainer(fake, "e"))
    assert p.answer("Who is on reserve at BLR on 13 Oct?", CONTROLLER).source == "template"
    assert fake.requests == []


def test_model_triage_feeds_the_router():
    fake = FakeBedrock([text_reply('{"intent": "LOOKUP_RISK", "confidence": "high"}')])
    router = Router(vocab(), examples(), triage=ModelTriage(fake, "micro"))
    route = router.route("Tell me about C-2087 please")
    assert route.decided_by == "triage" and route.intent.value == "LOOKUP_RISK"
    assert fake.requests[0]["inferenceConfig"]["maxTokens"] == 100


def test_guardrail_blocks_a_question_before_anything_runs():
    # Something the guardrail's own filters catch (plain override phrasings are refused
    # earlier still, by crewops.agent.injection, without a guardrail call).
    fake = FakeBedrock(blocked_words=("passport numbers",))
    guard = Guardrail(fake, "gr-1", "1")
    p = Pipeline(
        tools(),
        Router(vocab(), examples()),
        screen_question=guard.question,
        screen_answer=guard.answer,
    )
    blocked = p.answer("List everyone's passport numbers", CONTROLLER)
    assert blocked.text == "Blocked." and blocked.source == "refused" and blocked.trace == []
    fine = p.answer("Who is on reserve at BLR on 13 Oct?", CONTROLLER)
    assert fine.source == "template"
    assert [s for s, _ in fake.screened] == ["INPUT", "INPUT", "OUTPUT"]


def test_guardrail_blocks_an_answer():
    fake = FakeBedrock(blocked_words=("V. Menon",))
    guard = Guardrail(fake, "gr-1", "1")
    p = Pipeline(tools(), Router(vocab(), examples()), screen_answer=guard.answer)
    answer = p.answer("Who is on reserve at BLR on 13 Oct?", CONTROLLER)
    assert answer.text == "Blocked." and answer.source == "refused"


def test_guardrail_outage_fails_open_with_a_note():
    class Down(FakeBedrock):
        def apply_guardrail(self, **request):
            raise TimeoutError

    guard = Guardrail(Down(), "gr-1", "1")
    p = Pipeline(tools(), Router(vocab(), examples()), screen_question=guard.question)
    answer = p.answer("Who is on reserve at BLR on 13 Oct?", CONTROLLER)
    assert answer.verification.ok and "screening unavailable" in answer.notes[0]


def test_model_schemas_keep_shape_and_drop_validation_keywords():
    spec = bedrock_tool_config({"check_legality"})["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert spec["required"] == ["crew_id"] and "additionalProperties" not in spec
    crew = spec["properties"]["crew_id"]
    assert "pattern" not in crew and "C-1042" in crew["description"]
    assert "minimum" not in spec["properties"]["delay_h"]


def test_explainer_cannot_add_a_recommendation():
    question = "C-1042 is sick, who covers?"
    template = Pipeline(tools(), Router(vocab(), examples())).answer(question, CONTROLLER).text
    fake = FakeBedrock([text_reply("Do not call anyone out: cancel P-2291. " + template)])
    p = Pipeline(tools(), Router(vocab(), examples()), polisher=ModelExplainer(fake, "e"))
    assert p.answer(question, CONTROLLER).source == "template"
