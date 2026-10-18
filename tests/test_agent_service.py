"""The live pipeline's wiring, over moto's DynamoDB and a scripted Bedrock (no network)."""

from conftest import TEST_SETTINGS
from test_agent_models import FakeBedrock, text_reply

from crewops.agent import service
from crewops.config import Settings
from crewops.domain import Role
from crewops.ingestion.seed import load_seed
from crewops.store.ops_store import OpsStore
from crewops.tools import Caller

CONTROLLER = Caller("u-ctl", frozenset({Role.CONTROLLER}))


def test_build_wires_models_guardrail_and_live_table(ops_table, monkeypatch):
    OpsStore(ops_table).put_seed(load_seed())
    fake = FakeBedrock(
        [text_reply("Yes: D. Reddy (C-3310) is legal for P-2291, and every rule passes.")]
    )
    monkeypatch.setattr(service, "bedrock_runtime", lambda region, timeout=None: fake)
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
    settings = Settings.from_env(
        {"CREWOPS_STAGE": TEST_SETTINGS.stage, "AWS_REGION": TEST_SETTINGS.aws_region}
    )
    live = service.build(settings, ops_table, models=False)
    answer = live.pipeline.answer("C-1042 is sick, who covers?", CONTROLLER)
    assert answer.source == "template" and "D. Reddy (C-3310)" in answer.text
    assert live.usage()["calls"] == 0


def test_quick_calls_and_model_calls_have_their_own_timeouts(ops_table, monkeypatch):
    OpsStore(ops_table).put_seed(load_seed())
    made = []

    def client(region, timeout=None):
        made.append(timeout)
        return FakeBedrock()

    monkeypatch.setattr(service, "bedrock_runtime", client)
    settings = Settings.from_env(
        {"CREWOPS_STAGE": TEST_SETTINGS.stage, "AWS_REGION": TEST_SETTINGS.aws_region}
    )
    service.build(settings, ops_table)
    assert sorted(made) == [service.QUICK_CALL_SECONDS, settings.response_budget_seconds]
