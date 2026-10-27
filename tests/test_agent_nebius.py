"""NVIDIA Nemotron on Nebius Token Factory: the Converse <-> chat completions translation,
errors and the breaker, the API key, and the advisor's tool loop end to end (no network)."""

import io
import json
import urllib.error

import boto3
import pytest
from agent_fixtures import examples, tools, vocab

from crewops.agent.breaker import CircuitBreaker, GuardedClient, is_outage
from crewops.agent.models import ModelAdvisor, ModelExplainer
from crewops.agent.nebius import (
    NEMOTRON,
    NebiusClient,
    NebiusError,
    NebiusUnavailable,
    api_key,
    from_chat_response,
    to_chat_request,
)
from crewops.agent.pipeline import Pipeline
from crewops.agent.router import Router
from crewops.domain import Role
from crewops.tools import Caller, bedrock_tool_config

CONTROLLER = Caller("u-ctl", frozenset({Role.CONTROLLER}))
UNPLANNED = "Which single flight leg has the most seats at risk if cancelled, and why?"
SEATS = {"entity": "flights", "sort_by": "seats", "sort_desc": True, "limit": 3}


def chat_text(text, finish="stop"):
    return {
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }


def chat_tool(name, args, call_id="call-1", finish="tool_calls"):
    call = {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": None, "tool_calls": [call]},
                "finish_reason": finish,
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 12},
    }


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeTokenFactory:
    """Stands in for ``urllib.request.urlopen``: answers from a script, keeps each request."""

    def __init__(self, replies=()):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data) if request.data else None
        self.requests.append(
            {"url": request.full_url, "headers": dict(request.headers), "body": body}
        )
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return Response(json.dumps(reply).encode())


def http_error(status, payload):
    return urllib.error.HTTPError(
        "https://api.tokenfactory.nebius.com/v1/chat/completions",
        status,
        "error",
        {},
        io.BytesIO(json.dumps(payload).encode()),
    )


# -- Converse -> chat completions --------------------------------------------------------


def test_a_converse_request_becomes_a_chat_completion():
    messages = [
        {"role": "user", "content": [{"text": "Who covers P-2291?"}]},
        {
            "role": "assistant",
            "content": [{"toolUse": {"toolUseId": "planned-0", "name": "lookup", "input": SEATS}}],
        },
        {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "planned-0",
                        "content": [{"text": '{"rows": []}'}],
                        "status": "success",
                    }
                }
            ],
        },
    ]
    body = to_chat_request(
        NEMOTRON,
        [{"text": "You are CrewOps."}],
        messages,
        {"maxTokens": 800, "temperature": 0},
        bedrock_tool_config({"lookup"}),
    )
    assert body["model"] == NEMOTRON
    assert body["max_tokens"] == 800 and body["temperature"] == 0
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    call = body["messages"][2]["tool_calls"][0]
    assert call["id"] == "planned-0" and json.loads(call["function"]["arguments"]) == SEATS
    assert body["messages"][3] == {
        "role": "tool",
        "tool_call_id": "planned-0",
        "content": '{"rows": []}',
    }
    (tool,) = body["tools"]
    assert tool["type"] == "function" and tool["function"]["name"] == "lookup"
    assert tool["function"]["parameters"]["type"] == "object"
    assert body["tool_choice"] == "auto"


def test_no_tools_means_no_tool_fields():
    body = to_chat_request(NEMOTRON, None, [{"role": "user", "content": [{"text": "q"}]}], {}, None)
    assert "tools" not in body and "tool_choice" not in body
    assert body["messages"] == [{"role": "user", "content": "q"}]


# -- chat completions -> Converse --------------------------------------------------------


def test_a_finished_reply_reads_as_end_turn_with_usage():
    out = from_chat_response(chat_text("D. Reddy (C-3310) covers P-2291."))
    assert out["stopReason"] == "end_turn"
    assert out["output"]["message"]["content"] == [{"text": "D. Reddy (C-3310) covers P-2291."}]
    assert out["usage"] == {"inputTokens": 120, "outputTokens": 30}


@pytest.mark.parametrize("finish", ["tool_calls", "stop"])
def test_tool_calls_read_as_tool_use_whatever_the_finish_reason(finish):
    out = from_chat_response(chat_tool("lookup", json.dumps(SEATS), finish=finish))
    assert out["stopReason"] == "tool_use"
    (block,) = out["output"]["message"]["content"]
    assert block["toolUse"] == {"toolUseId": "call-1", "name": "lookup", "input": SEATS}


@pytest.mark.parametrize("args", ["{not json", "[1, 2]"])
def test_a_malformed_tool_call_is_no_answer(args):
    assert from_chat_response(chat_tool("lookup", args))["stopReason"] == "malformed_tool_use"


@pytest.mark.parametrize(
    ("finish", "stop"), [("length", "max_tokens"), ("content_filter", "content_filtered")]
)
def test_a_cut_or_filtered_reply_keeps_its_reason(finish, stop):
    assert from_chat_response(chat_text("half", finish))["stopReason"] == stop


# -- The client, errors and the breaker --------------------------------------------------


def test_the_client_posts_to_token_factory_with_the_key():
    wire = FakeTokenFactory([chat_text("ok")])
    client = NebiusClient("nb-secret", "https://api.tokenfactory.nebius.com/v1", opener=wire)
    out = client.converse(
        modelId=NEMOTRON,
        messages=[{"role": "user", "content": [{"text": "hi"}]}],
        inferenceConfig={"maxTokens": 10},
    )
    assert out["stopReason"] == "end_turn"
    (request,) = wire.requests
    assert request["url"] == "https://api.tokenfactory.nebius.com/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer nb-secret"
    assert request["body"]["model"] == NEMOTRON


@pytest.mark.parametrize(
    ("status", "outage"), [(429, True), (503, True), (400, False), (401, False)]
)
def test_http_errors_trip_the_breaker_only_when_the_service_is_down(status, outage):
    wire = FakeTokenFactory([http_error(status, {"detail": "nope"})])
    client = NebiusClient("k", opener=wire)
    with pytest.raises(NebiusError) as caught:
        client.converse(modelId="m", messages=[{"role": "user", "content": [{"text": "q"}]}])
    assert caught.value.status == status and "nope" in str(caught.value)
    assert is_outage(caught.value) is outage


def test_an_unreachable_service_is_an_outage_and_opens_the_breaker():
    breaker = CircuitBreaker(threshold=2)
    wire = FakeTokenFactory([urllib.error.URLError("timed out")] * 2)
    client = GuardedClient(NebiusClient("k", opener=wire), breaker)
    for _ in range(2):
        with pytest.raises(NebiusUnavailable):
            client.converse(modelId="m", messages=[{"role": "user", "content": [{"text": "q"}]}])
    assert breaker.state == "open"


def test_the_model_catalogue_is_listed():
    wire = FakeTokenFactory([{"data": [{"id": NEMOTRON}, {"id": "meta-llama/x"}]}])
    assert NebiusClient("k", opener=wire).list_models() == ["meta-llama/x", NEMOTRON]
    assert wire.requests[0]["url"].endswith("/v1/models")


# -- The roles, end to end through the translation ---------------------------------------


def test_the_advisor_runs_its_tool_loop_on_nemotron_and_is_verified():
    wire = FakeTokenFactory(
        [
            chat_tool("lookup", json.dumps(SEATS)),
            chat_text("DX401 on 2026-10-12 has the most seats at risk: 162."),
        ]
    )
    advisor = ModelAdvisor(NebiusClient("k", opener=wire), NEMOTRON, tools())
    pipeline = Pipeline(tools(), Router(vocab(), examples()), drafter=advisor)
    answer = pipeline.answer(UNPLANNED, CONTROLLER)
    assert answer.source == "model" and answer.verification.ok
    assert [e.tool for e in answer.trace] == ["lookup"]
    second = wire.requests[1]["body"]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert second[3]["tool_call_id"] == "call-1" and '"seats": 162' in second[3]["content"]
    assert advisor.last.usage.calls == 2 and advisor.last.usage.input_tokens == 220


def test_an_invented_figure_from_nemotron_falls_back_to_the_template():
    wire = FakeTokenFactory(
        [chat_tool("lookup", json.dumps(SEATS)), chat_text("DX401 has 999 seats at risk.")]
    )
    advisor = ModelAdvisor(NebiusClient("k", opener=wire), NEMOTRON, tools())
    answer = Pipeline(tools(), Router(vocab(), examples()), drafter=advisor).answer(
        UNPLANNED, CONTROLLER
    )
    assert answer.source == "template" and "999" not in answer.text


def test_a_token_factory_outage_still_gets_an_answer():
    wire = FakeTokenFactory([http_error(503, {"detail": "overloaded"})])
    explainer = ModelExplainer(NebiusClient("k", opener=wire), NEMOTRON)
    pipeline = Pipeline(tools(), Router(vocab(), examples()), polisher=explainer)
    answer = pipeline.answer("Can C-3310 cover P-2291?", CONTROLLER)
    assert answer.source == "template" and answer.verification.ok


# -- The API key ------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_key_read_earlier(monkeypatch):
    """Each test starts with no key remembered from another test."""
    from crewops.agent import nebius

    monkeypatch.setattr(nebius, "_KEYS", {})


def test_the_key_comes_from_the_environment_first():
    env = {"NEBIUS_API_KEY": " nb-local "}
    assert api_key("/crewops-test/nebius/api-key", "ap-southeast-2", env) == "nb-local"


def test_the_deployed_key_comes_from_parameter_store(moto_aws):
    ssm = boto3.client("ssm", region_name="ap-southeast-2")
    ssm.put_parameter(Name="/crewops-test/nebius/api-key", Value="nb-ssm", Type="SecureString")
    assert api_key("/crewops-test/nebius/api-key", "ap-southeast-2", {}) == "nb-ssm"


def test_no_key_anywhere_means_no_models(moto_aws):
    assert api_key("/crewops-test/nebius/api-key", "ap-southeast-2", {}) is None


# -- Review fixes: one call per turn, ids, errors, time left, the key on a hiccup ---------


def test_several_calls_in_one_turn_become_one_call_per_turn():
    """Llama 3.1 chat templates refuse an assistant turn with two tool calls."""
    messages = [
        {"role": "user", "content": [{"text": "q"}]},
        {
            "role": "assistant",
            "content": [
                {"toolUse": {"toolUseId": "planned-0", "name": "lookup", "input": {"a": 1}}},
                {"toolUse": {"toolUseId": "planned-1", "name": "explain_rule", "input": {}}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"toolResult": {"toolUseId": "planned-0", "content": [{"text": "r0"}]}},
                {"toolResult": {"toolUseId": "planned-1", "content": [{"text": "r1"}]}},
            ],
        },
    ]
    chat = to_chat_request(NEMOTRON, None, messages, {}, None)["messages"]
    assert [m["role"] for m in chat] == ["user", "assistant", "tool", "assistant", "tool"]
    assert all(len(m["tool_calls"]) == 1 for m in chat if m["role"] == "assistant")
    assert [m["tool_call_id"] for m in chat if m["role"] == "tool"] == ["planned-0", "planned-1"]
    assert chat[2]["content"] == "r0" and chat[4]["content"] == "r1"


def test_tool_calls_without_ids_get_distinct_ones():
    reply = chat_tool("lookup", "{}")
    second = {"type": "function", "function": {"name": "explain_rule", "arguments": "{}"}}
    reply["choices"][0]["message"]["tool_calls"][0].pop("id")
    reply["choices"][0]["message"]["tool_calls"].append(second)
    ids = [
        b["toolUse"]["toolUseId"] for b in from_chat_response(reply)["output"]["message"]["content"]
    ]
    assert ids == ["call-0", "call-1"]


@pytest.mark.parametrize(
    "failure",
    [
        __import__("http.client").client.IncompleteRead(b"half"),
        __import__("ssl").SSLError("bad record mac"),
        ConnectionResetError("reset"),
    ],
)
def test_a_connection_lost_half_way_is_an_outage(failure):
    client = NebiusClient("k", opener=FakeTokenFactory([failure]))
    with pytest.raises(NebiusUnavailable) as caught:
        client.converse(modelId="m", messages=[{"role": "user", "content": [{"text": "q"}]}])
    assert is_outage(caught.value)


def test_a_reply_that_is_not_json_is_an_outage_not_a_crash():
    class NotJson(FakeTokenFactory):
        def __call__(self, request, timeout=None):
            return Response(b"<html>502 Bad Gateway</html>")

    with pytest.raises(NebiusError) as caught:
        NebiusClient("k", opener=NotJson()).converse(
            modelId="m", messages=[{"role": "user", "content": [{"text": "q"}]}]
        )
    assert caught.value.status == 502 and is_outage(caught.value)


def test_a_call_never_waits_longer_than_the_time_left():
    seen = []

    class Timed(FakeTokenFactory):
        def __call__(self, request, timeout=None):
            seen.append(timeout)
            return super().__call__(request, timeout)

    client = NebiusClient("k", timeout=6.0, opener=Timed([chat_text("a"), chat_text("b")]))
    question = [{"role": "user", "content": [{"text": "q"}]}]
    client.converse(modelId="m", messages=question)
    client.converse(modelId="m", messages=question, timeout=1.5)
    assert seen == [6.0, 1.5]


def test_the_advisor_gives_each_round_only_the_time_left():
    timeouts = []

    class Recording:
        def __init__(self, replies):
            self.replies = list(replies)

        def converse(self, **request):
            timeouts.append(request["timeout"])
            return from_chat_response(self.replies.pop(0))

    advisor = ModelAdvisor(
        Recording([chat_tool("lookup", json.dumps(SEATS)), chat_text("DX401 ... 162.")]),
        NEMOTRON,
        tools(),
        budget_seconds=6.0,
    )
    Pipeline(tools(), Router(vocab(), examples()), drafter=advisor).answer(UNPLANNED, CONTROLLER)
    assert len(timeouts) == 2 and 0 < timeouts[1] <= timeouts[0] <= 6.0


def test_a_parameter_store_hiccup_keeps_the_key_read_earlier(moto_aws, monkeypatch):
    from botocore.exceptions import ClientError

    from crewops.agent import nebius

    name = "/crewops-test/nebius/api-key"
    boto3.client("ssm", region_name="ap-southeast-2").put_parameter(
        Name=name, Value="nb-first", Type="SecureString"
    )
    assert api_key(name, "ap-southeast-2", {}) == "nb-first"

    def failing(code):
        class Ssm:
            def get_parameter(self, **_):
                raise ClientError({"Error": {"Code": code, "Message": "x"}}, "GetParameter")

        class Session:
            def __init__(self, **_):
                pass

            def client(self, *_, **__):
                return Ssm()

        monkeypatch.setattr(boto3, "Session", Session)

    failing("ThrottlingException")  # a hiccup: the key read earlier stands in
    assert api_key(name, "ap-southeast-2", {}) == "nb-first"
    failing("ParameterNotFound")  # removed on purpose: no models
    assert api_key(name, "ap-southeast-2", {}) is None
    failing("ThrottlingException")
    monkeypatch.setattr(nebius, "KEY_REUSE_SECONDS", 0)  # too long ago to trust
    assert api_key(name, "ap-southeast-2", {}) is None


# -- Nemotron 3 specifics: thinking off, tool choice, leaked calls, the last round ------


def test_thinking_is_off_unless_asked_for():
    q = [{"role": "user", "content": [{"text": "q"}]}]
    off = to_chat_request(NEMOTRON, None, q, {}, None)
    assert off["chat_template_kwargs"] == {"enable_thinking": False}
    assert "chat_template_kwargs" not in to_chat_request(NEMOTRON, None, q, {}, None, True)
    wire = FakeTokenFactory([chat_text("ok")])
    NebiusClient("k", opener=wire, reasoning=True).converse(modelId="m", messages=q)
    assert "chat_template_kwargs" not in wire.requests[0]["body"]


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        (None, "auto"),
        ({"auto": {}}, "auto"),
        ({"none": {}}, "none"),
        ({"any": {}}, "required"),
        ({"tool": {"name": "lookup"}}, {"type": "function", "function": {"name": "lookup"}}),
    ],
)
def test_tool_choice_is_translated(choice, expected):
    config = bedrock_tool_config({"lookup"})
    if choice is not None:
        config = {**config, "toolChoice": choice}
    body = to_chat_request(
        NEMOTRON, None, [{"role": "user", "content": [{"text": "q"}]}], {}, config
    )
    assert body["tool_choice"] == expected


@pytest.mark.parametrize("text", ["<tool_call>\n<function=lookup>", "Sure. <function=ripple>{}"])
def test_tool_call_syntax_written_as_text_is_no_answer(text):
    assert from_chat_response(chat_text(text))["stopReason"] == "malformed_tool_use"


def test_the_advisors_last_round_may_not_call_tools_and_is_asked_to_answer():
    seen = []

    class Recording:
        def __init__(self, replies):
            self.replies = list(replies)

        def converse(self, **request):
            seen.append(json.loads(json.dumps(request)))
            return from_chat_response(self.replies.pop(0))

    advisor = ModelAdvisor(
        Recording([chat_tool("lookup", json.dumps(SEATS)), chat_text("DX401 ... 162.")]),
        NEMOTRON,
        tools(),
        max_rounds=1,
        budget_seconds=60,
    )
    Pipeline(tools(), Router(vocab(), examples()), drafter=advisor).answer(UNPLANNED, CONTROLLER)
    assert len(seen) == 2
    assert "toolChoice" not in seen[0]["toolConfig"]
    assert seen[1]["toolConfig"]["toolChoice"] == {"none": {}}
    assert seen[1]["messages"][-1]["content"][-1]["text"].startswith("Answer now")
