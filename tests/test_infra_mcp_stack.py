"""Synthesize the MCP stack locally (no AWS access) with a stand-in package."""

import json

import pytest

pytest.importorskip("aws_cdk")

from aws_cdk import App, Environment  # noqa: E402
from aws_cdk.assertions import Template  # noqa: E402
from crewops_infra.auth_stack import AuthStack  # noqa: E402
from crewops_infra.data_stack import DataStack  # noqa: E402
from crewops_infra.guardrail_stack import GuardrailStack  # noqa: E402
from crewops_infra.ingestion_stack import IngestionStack  # noqa: E402
from crewops_infra.mcp_stack import McpStack, model_arns  # noqa: E402
from crewops_infra.notify_stack import NotifyStack  # noqa: E402

from crewops.config import Settings  # noqa: E402

ENV = Environment(account="111111111111", region="ap-southeast-2")
OPERATOR = "arn:aws:iam::111111111111:user/crewops-deployer"


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    package = tmp_path_factory.mktemp("mcp_server")
    (package / "main.py").write_text("print('stand-in')\n")
    settings = Settings.from_env({"CREWOPS_STAGE": "dev", "CREWOPS_QUEUE_SENDERS": OPERATOR})
    app = App()
    data = DataStack(app, "Data", settings=settings, env=ENV)
    ingestion = IngestionStack(app, "Ingestion", settings=settings, table=data.table, env=ENV)
    guardrail = GuardrailStack(app, "Guardrail", settings=settings, env=ENV)
    auth = AuthStack(app, "Auth", settings=settings, env=ENV)
    notify = NotifyStack(app, "Notify", settings=settings, table=data.table, auth=auth, env=ENV)
    stack = McpStack(
        app,
        "Mcp",
        settings=settings,
        table=data.table,
        queue=ingestion.queue,
        auth=auth,
        guardrail=guardrail,
        notify=notify,
        package=package,
        env=ENV,
    )
    return Template.from_stack(stack)


def test_runtime_speaks_mcp_behind_the_cognito_authorizer(template):
    (runtime,) = template.find_resources("AWS::BedrockAgentCore::Runtime").values()
    p = runtime["Properties"]
    assert p["AgentRuntimeName"] == "crewops_dev_mcp"
    assert p["ProtocolConfiguration"] == "MCP"
    jwt = p["AuthorizerConfiguration"]["CustomJWTAuthorizer"]
    assert "AllowedClients" in jwt and "openid-configuration" in json.dumps(jwt["DiscoveryUrl"])
    assert p["RequestHeaderConfiguration"]["RequestHeaderAllowlist"] == ["Authorization"]
    code = p["AgentRuntimeArtifact"]["CodeConfiguration"]
    assert code["Runtime"] == "PYTHON_3_12" and code["EntryPoint"] == ["main.py"]
    env = p["EnvironmentVariables"]
    for key in (
        "CREWOPS_USER_POOL_ID",
        "CREWOPS_USER_POOL_CLIENT_ID",
        "CREWOPS_INGEST_QUEUE_URL",
        "CREWOPS_GUARDRAIL_ID",
        "CREWOPS_GUARDRAIL_VERSION",
    ):
        assert key in env, key
    assert env["CREWOPS_NEBIUS_BASE_URL"] == "https://api.tokenfactory.nebius.com/v1/"
    assert env["CREWOPS_ADVISOR_MODEL_ID"] == "nvidia/Llama-3.1-Nemotron-70B-Instruct-HF"
    assert not any("API_KEY" in k for k in env)  # the key is read from Parameter Store
    assert p["LifecycleConfiguration"]["IdleRuntimeSessionTimeout"] == 300


def statements(template):
    out = []
    for policy in template.find_resources("AWS::IAM::Policy").values():
        out += policy["Properties"]["PolicyDocument"]["Statement"]
    return out


def test_permissions_are_only_what_the_server_needs(template):
    by_action = {}
    for s in statements(template):
        actions = s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
        for a in actions:
            by_action.setdefault(a, []).append(s["Resource"])
    invoke = json.dumps(by_action["bedrock:InvokeModel"])
    # Chat models run on Nebius Token Factory: Bedrock is only asked for embeddings.
    assert invoke.count("foundation-model/") == 1
    assert "foundation-model/amazon.titan-embed-text-v2:0" in invoke
    ssm = json.dumps(by_action["ssm:GetParameter"])
    assert "parameter/crewops-dev/nebius/api-key" in ssm and "*" not in ssm
    assert "bedrock:ApplyGuardrail" in by_action
    assert "s3vectors:QueryVectors" in by_action and "sqs:SendMessage" in by_action
    assert "dynamodb:PutItem" in by_action
    assert "*" not in json.dumps(by_action["bedrock:InvokeModel"]).replace("bedrock:*::", "")


def test_runtime_runs_as_the_role_the_queue_policy_names(template):
    roles = template.find_resources("AWS::IAM::Role").values()
    (role,) = [r for r in roles if r["Properties"].get("RoleName") == "crewops-dev-mcp-runtime"]
    (trust,) = role["Properties"]["AssumeRolePolicyDocument"]["Statement"]
    assert trust["Principal"] == {"Service": "bedrock-agentcore.amazonaws.com"}
    assert "runtime/crewops_dev_mcp*" in json.dumps(trust["Condition"]["ArnLike"])
    (runtime,) = template.find_resources("AWS::BedrockAgentCore::Runtime").values()
    assert "McpServerExecutionRole" not in json.dumps(runtime)
    assert "ExecutionRole" in json.dumps(runtime["Properties"]["RoleArn"])
    # The construct still adds its own needs (logs, workload identity) to the role.
    sids = {s.get("Sid") for s in statements(template)}
    assert {"LogGroupAccess", "GetAgentAccessToken"} <= sids


def test_mcp_stack_leaves_the_queue_policy_to_the_ingestion_stack(template):
    assert template.find_resources("AWS::SQS::QueuePolicy") == {}


def test_conversation_memory_is_short_term_and_the_server_may_use_it(template):
    (memory,) = template.find_resources("AWS::BedrockAgentCore::Memory").values()
    assert memory["Properties"]["Name"] == "crewops_dev_memory"
    assert memory["Properties"]["EventExpiryDuration"] == 7
    assert "MemoryStrategies" not in memory["Properties"]
    (runtime,) = template.find_resources("AWS::BedrockAgentCore::Runtime").values()
    env = runtime["Properties"]["EnvironmentVariables"]
    assert "CREWOPS_MEMORY_ID" in env and "CREWOPS_ALERTS_TOPIC_ARN" in env
    actions = {
        a
        for s in statements(template)
        for a in (s["Action"] if isinstance(s["Action"], list) else [s["Action"]])
    }
    assert {"bedrock-agentcore:CreateEvent", "bedrock-agentcore:ListEvents"} <= actions
    assert {"sns:Subscribe", "sns:Unsubscribe", "sns:GetSubscriptionAttributes"} <= actions
    assert "sns:Publish" not in actions  # alerts are sent by the notifier, not the server


def test_model_arns():
    assert model_arns("amazon.nova-micro-v1:0", "ap-southeast-2", "1") == [
        "arn:aws:bedrock:ap-southeast-2::foundation-model/amazon.nova-micro-v1:0"
    ]
    arns = model_arns("global.amazon.nova-2-lite-v1:0", "ap-southeast-2", "1")
    assert (
        arns[0]
        == "arn:aws:bedrock:ap-southeast-2:1:inference-profile/global.amazon.nova-2-lite-v1:0"
    )
    assert "arn:aws:bedrock:*::foundation-model/amazon.nova-2-lite-v1:0" in arns
