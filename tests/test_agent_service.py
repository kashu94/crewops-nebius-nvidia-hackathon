"""The live pipeline's wiring, over moto's DynamoDB, a scripted Bedrock (embeddings and
guardrail) and a scripted Nebius Token Factory (the chat roles); no network."""

from conftest import TEST_SETTINGS
from test_agent_models import FakeBedrock, text_reply

from crewops.agent import service
from crewops.config import Settings
from crewops.domain import Role
from crewops.ingestion.seed import load_seed
from crewops.store.ops_store import OpsStore
from crewops.tools import Caller

CONTROLLER = Caller("u-ctl", frozenset({Role.CONTROLLER}))


def nebius(monkeypatch, client, key="nb-test"):
    """Stand in for Token Factory: ``client`` answers every chat role."""
    monkeypatch.setattr(service, "nebius_api_key", lambda settings: key)
    monkeypatch.setattr(service, "nebius_runtime", lambda settings, k, timeout: client)


def test_build_wires_models_guardrail_and_live_table(ops_table, monkeypatch):
    OpsStore(ops_table).put_seed(load_seed())
    fake = FakeBedrock(
        [text_reply("Yes: D. Reddy (C-3310) is legal for P-2291, and every rule passes.")]
    )
    monkeypatch.setattr(service, "bedrock_runtime", lambda region, timeout=None: fake)
    nebius(monkeypatch, fake)
    settings = Settings.from_env(
        {
            "CREWOPS_STAGE": TEST_SETTINGS.stage,
            "AWS_REGION": TEST_SETTINGS.aws_region,
            "CREWOPS_GUARDRAIL_ID": "gr-test",
            "CREWOPS_GUARDRAIL_VERSION": "1",
        }
    )
    live = service.build(settings, ops_table)
    answer, used = live.ask("Can C-3310 cover P-2291?", CONTROLLER)
    assert answer.source == "explained" and answer.verification.ok
    assert [s for s, _ in fake.screened] == ["INPUT", "OUTPUT"]
    assert fake.requests[0]["modelId"] == settings.explainer_model_id
    assert used["calls"] == 1
    _, again = live.ask("Who is on reserve at BLR on 13 Oct?", CONTROLLER)
    assert again["calls"] == 0  # per question, not a running total


def test_build_without_models(ops_table, monkeypatch):
    OpsStore(ops_table).put_seed(load_seed())
    monkeypatch.setattr(service, "bedrock_runtime", lambda region, timeout=None: FakeBedrock())
    nebius(monkeypatch, FakeBedrock())
    settings = Settings.from_env(
        {"CREWOPS_STAGE": TEST_SETTINGS.stage, "AWS_REGION": TEST_SETTINGS.aws_region}
    )
    live = service.build(settings, ops_table, models=False)
    answer = live.pipeline.answer("C-1042 is sick, who covers?", CONTROLLER)
    assert answer.source == "template" and "D. Reddy (C-3310)" in answer.text
    assert live.usage()["calls"] == 0


def test_quick_calls_and_model_calls_have_their_own_timeouts(ops_table, monkeypatch):
    OpsStore(ops_table).put_seed(load_seed())
    aws, chat = [], []

    def bedrock(region, timeout=None):
        aws.append(timeout)
        return FakeBedrock()

    def token_factory(settings, key, timeout):
        chat.append(timeout)
        return FakeBedrock()

    monkeypatch.setattr(service, "bedrock_runtime", bedrock)
    monkeypatch.setattr(service, "nebius_api_key", lambda settings: "nb-test")
    monkeypatch.setattr(service, "nebius_runtime", token_factory)
    settings = Settings.from_env(
        {"CREWOPS_STAGE": TEST_SETTINGS.stage, "AWS_REGION": TEST_SETTINGS.aws_region}
    )
    service.build(settings, ops_table)
    assert aws == [service.QUICK_CALL_SECONDS]  # embeddings and guardrail
    assert sorted(chat) == [service.QUICK_CALL_SECONDS, settings.response_budget_seconds]


def test_without_a_nebius_key_the_templates_answer(ops_table, monkeypatch):
    OpsStore(ops_table).put_seed(load_seed())
    monkeypatch.setattr(service, "bedrock_runtime", lambda region, timeout=None: FakeBedrock())
    monkeypatch.setattr(service, "nebius_api_key", lambda settings: None)

    def never(settings, key, timeout):
        raise AssertionError("no key, so no Token Factory client")

    monkeypatch.setattr(service, "nebius_runtime", never)
    settings = Settings.from_env(
        {"CREWOPS_STAGE": TEST_SETTINGS.stage, "AWS_REGION": TEST_SETTINGS.aws_region}
    )
    live = service.build(settings, ops_table)
    assert live.triage is live.advisor is live.explainer is None
    answer, used = live.ask("C-1042 is sick, who covers?", CONTROLLER)
    assert answer.source == "template" and used["calls"] == 0
