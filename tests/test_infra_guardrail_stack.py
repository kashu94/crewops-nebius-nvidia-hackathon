"""Synthesize the guardrail stack locally (no AWS access) and check what it would create."""

import pytest

pytest.importorskip("aws_cdk")

from aws_cdk import App, Environment  # noqa: E402
from aws_cdk.assertions import Template  # noqa: E402
from crewops_infra.guardrail_stack import GuardrailStack  # noqa: E402

from crewops.config import Settings  # noqa: E402


@pytest.fixture(scope="module")
def template():
    stack = GuardrailStack(
        App(),
        "Guardrail",
        settings=Settings.from_env({"CREWOPS_STAGE": "dev"}),
        env=Environment(account="111111111111", region="ap-southeast-2"),
    )
    return Template.from_stack(stack)


def test_guardrail_screens_content_and_prompt_attacks(template):
    (guardrail,) = template.find_resources("AWS::Bedrock::Guardrail").values()
    props = guardrail["Properties"]
    assert props["Name"] == "crewops-dev-guardrail"
    filters = {f["Type"]: f for f in props["ContentPolicyConfig"]["FiltersConfig"]}
    assert set(filters) == {"HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT", "PROMPT_ATTACK"}
    assert filters["PROMPT_ATTACK"]["InputStrength"] == "HIGH"
    assert filters["PROMPT_ATTACK"]["OutputStrength"] == "NONE"
    assert "SensitiveInformationPolicyConfig" not in props  # crew names must survive


def test_a_published_version_is_created(template):
    template.resource_count_is("AWS::Bedrock::GuardrailVersion", 1)
    outputs = template.find_outputs("*")
    assert {"GuardrailId", "GuardrailVersionNumber"} <= set(outputs)


def test_old_versions_are_kept_when_a_new_one_is_published(template):
    (version,) = template.find_resources("AWS::Bedrock::GuardrailVersion").values()
    assert version["DeletionPolicy"] == "Retain"
    assert version["Properties"]["Description"].startswith("Configuration ")
