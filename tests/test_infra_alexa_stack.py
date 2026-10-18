"""Synthesize the Alexa skill's stacks locally (no AWS access) with a stand-in package."""

import json

import pytest

pytest.importorskip("aws_cdk")

from aws_cdk import App, Environment  # noqa: E402
from aws_cdk.assertions import Template  # noqa: E402
from crewops_infra.alexa_stack import AlexaStack  # noqa: E402
from crewops_infra.auth_stack import AuthStack  # noqa: E402
from crewops_infra.data_stack import DataStack  # noqa: E402
from crewops_infra.guardrail_stack import GuardrailStack  # noqa: E402
from crewops_infra.notify_stack import NotifyStack  # noqa: E402

from crewops.config import Settings  # noqa: E402

ENV = Environment(account="111111111111", region="ap-southeast-2")
REDIRECTS = (
    "https://pitangui.amazon.com/api/skill/link/M2TESTVENDOR,"
    "https://layla.amazon.com/api/skill/link/M2TESTVENDOR,"
    "https://alexa.amazon.co.jp/api/skill/link/M2TESTVENDOR"
)
ALEXA = {
    "CREWOPS_STAGE": "dev",
    "CREWOPS_ALEXA_SKILL_ID": "amzn1.ask.skill.test",
    "CREWOPS_ALEXA_REDIRECT_URLS": REDIRECTS,
}


@pytest.fixture(scope="module")
def stacks(tmp_path_factory):
    package = tmp_path_factory.mktemp("mcp_server")
    (package / "main.py").write_text("print('stand-in')\n")
    settings = Settings.from_env(ALEXA)
    app = App()
    data = DataStack(app, "Data", settings=settings, env=ENV)
    guardrail = GuardrailStack(app, "Guardrail", settings=settings, env=ENV)
    auth = AuthStack(app, "Auth", settings=settings, env=ENV)
    notify = NotifyStack(app, "Notify", settings=settings, table=data.table, auth=auth, env=ENV)
    alexa = AlexaStack(
        app,
        "Alexa",
        settings=settings,
        table=data.table,
        auth=auth,
        guardrail=guardrail,
        package=package,
        env=ENV,
    )
    return {
        "auth": Template.from_stack(auth),
        "notify": Template.from_stack(notify),
        "alexa": Template.from_stack(alexa),
    }


def test_the_skill_is_a_public_https_endpoint_that_verifies_alexa(stacks):
    t = stacks["alexa"]
    (url,) = t.find_resources("AWS::Lambda::Url").values()
    assert url["Properties"]["AuthType"] == "NONE"  # Alexa calls it; the code checks Alexa
    skill = next(
        f["Properties"]
        for f in t.find_resources("AWS::Lambda::Function").values()
        if f["Properties"].get("Handler") == "crewops.alexa.skill.handler"
    )
    assert skill["Architectures"] == ["arm64"] and skill["Runtime"] == "python3.12"
    env = skill["Environment"]["Variables"]
    assert env["CREWOPS_ALEXA_SKILL_ID"] == "amzn1.ask.skill.test"
    assert env["CREWOPS_DATA_DIR"] == "/var/task/data" and "CREWOPS_ALEXA_CLIENT_ID" in env
    assert skill["Timeout"] <= 10
    t.has_output("SkillEndpointUrl", {})


def test_account_linking_has_its_own_client_with_alexas_redirects(stacks):
    t = stacks["auth"]
    clients = {
        c["Properties"]["ClientName"]: c["Properties"]
        for c in t.find_resources("AWS::Cognito::UserPoolClient").values()
    }
    alexa, web = clients["crewops-dev-alexa"], clients["crewops-dev-web"]
    assert alexa["GenerateSecret"] is True and alexa["AllowedOAuthFlows"] == ["code"]
    assert alexa["CallbackURLs"] == REDIRECTS.split(",")
    assert sorted(alexa["AllowedOAuthScopes"]) == ["openid", "profile"]
    assert "AllowedOAuthFlows" not in web  # the web client still has no hosted page
    assert len(t.find_resources("AWS::Cognito::UserPoolDomain")) == 1
    for name in ("AlexaClientId", "AlexaAuthorizeUri", "AlexaAccessTokenUri"):
        t.has_output(name, {})


def test_the_notifier_may_read_links_and_the_skill_credentials_only(stacks):
    t = stacks["notify"]
    notifier = next(
        f["Properties"]
        for f in t.find_resources("AWS::Lambda::Function").values()
        if f["Properties"].get("Handler") == "crewops.notify.stream_handler.handler"
    )
    assert notifier["Environment"]["Variables"]["CREWOPS_ALEXA_SKILL_ID"] == "amzn1.ask.skill.test"
    policy = json.dumps(
        {
            k: p
            for k, p in t.find_resources("AWS::IAM::Policy").items()
            if k.startswith("NotifierServiceRole")
        }
    )
    assert '"ssm:GetParameter"' in policy and "parameter/crewops-dev/alexa/*" in policy
    assert '"dynamodb:Query"' in policy and '"dynamodb:GetItem"' in policy
    assert "dynamodb:PutItem" not in policy  # reading who is linked, never writing


def test_without_alexa_settings_nothing_changes():
    settings = Settings.from_env({"CREWOPS_STAGE": "dev"})
    app = App()
    auth = AuthStack(app, "Auth", settings=settings, env=ENV)
    data = DataStack(app, "Data", settings=settings, env=ENV)
    notify = NotifyStack(app, "Notify", settings=settings, table=data.table, auth=auth, env=ENV)
    assert auth.alexa_client is None
    assert len(Template.from_stack(auth).find_resources("AWS::Cognito::UserPoolClient")) == 1
    assert "ssm:GetParameter" not in json.dumps(
        Template.from_stack(notify).find_resources("AWS::IAM::Policy")
    )
