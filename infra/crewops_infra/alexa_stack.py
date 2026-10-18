"""The CrewOps Alexa skill's endpoint.

Alexa can't trigger a Lambda directly in ap-southeast-2, which is where everything else
lives, so the skill points at a Lambda function URL instead. The URL has to be public for
Alexa. Every request is checked for Alexa's signature, certificate, timestamp and skill id
(``crewops.alexa.verify``) before anything runs, and a linked person goes through the same
token checks as on the MCP server.

The function runs the MCP server's code package (``build/mcp_server``), which carries the
pipeline, its data and the libraries the checks need. Notifications are sent by the existing
notifier on the table's stream (``NotifyStack``), not from here.

Deployed only when ``CREWOPS_ALEXA_SKILL_ID`` and the Alexa redirect URLs are set.
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from crewops.config import Settings
from crewops.search.vectors import INDEXES
from crewops_infra.auth_stack import AuthStack
from crewops_infra.guardrail_stack import GuardrailStack
from crewops_infra.mcp_stack import PACKAGE, model_arns


class AlexaStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: Settings,
        table: dynamodb.ITableV2,
        auth: AuthStack,
        guardrail: GuardrailStack,
        package: Path = PACKAGE,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)
        if auth.alexa_client is None or not settings.alexa_skill_id:
            raise ValueError("the Alexa stack needs the skill id and the Alexa app client")
        region, account = self.region, self.account

        self.skill = lambda_.Function(
            self,
            "Skill",
            description="The CrewOps Alexa skill's HTTPS endpoint.",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,  # the package's wheels are ARM64
            handler="crewops.alexa.skill.handler",
            code=lambda_.Code.from_asset(str(package)),
            memory_size=1024,
            # Alexa waits 8 s; the answer has its own budget inside that.
            timeout=Duration.seconds(10),
            environment={
                "CREWOPS_STAGE": settings.stage,
                "CREWOPS_REAL_TIME": "1",
                "CREWOPS_DATA_DIR": "/var/task/data",
                "CREWOPS_USER_POOL_ID": auth.user_pool.user_pool_id,
                "CREWOPS_USER_POOL_CLIENT_ID": auth.client.user_pool_client_id,
                "CREWOPS_ALEXA_SKILL_ID": settings.alexa_skill_id,
                "CREWOPS_ALEXA_CLIENT_ID": auth.alexa_client.user_pool_client_id,
                "CREWOPS_GUARDRAIL_ID": guardrail.guardrail.attr_guardrail_id,
                "CREWOPS_GUARDRAIL_VERSION": guardrail.version.attr_version,
                "CREWOPS_ROUTER_MODEL_ID": settings.router_model_id,
                "CREWOPS_ADVISOR_MODEL_ID": settings.advisor_model_id,
                "CREWOPS_EXPLAINER_MODEL_ID": settings.explainer_model_id,
                "CREWOPS_EMBEDDING_MODEL_ID": settings.embedding_model_id,
                "CREWOPS_EMBEDDING_DIMENSIONS": str(settings.embedding_dimensions),
                "CREWOPS_ROUTER_SEMANTIC_FLOOR": str(settings.router_semantic_floor),
                "CREWOPS_RESPONSE_BUDGET_SECONDS": str(settings.response_budget_seconds),
                "CREWOPS_MAX_TOOL_ITERATIONS": str(settings.max_tool_iterations),
            },
            log_group=logs.LogGroup(
                self,
                "SkillLogs",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )
        # Reads the operation and answers; writes only linked accounts and conversation-free
        # state (it files no reports and decides no plans).
        table.grant_read_write_data(self.skill)
        models = {
            settings.router_model_id,
            settings.advisor_model_id,
            settings.explainer_model_id,
            settings.embedding_model_id,
        }
        self.skill.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=sorted({a for m in models for a in model_arns(m, region, account)}),
            )
        )
        self.skill.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:ApplyGuardrail"],
                resources=[guardrail.guardrail.attr_guardrail_arn],
            )
        )
        self.skill.add_to_role_policy(
            iam.PolicyStatement(
                actions=["s3vectors:QueryVectors", "s3vectors:GetVectors"],
                resources=[
                    f"arn:aws:s3vectors:{region}:{account}:bucket/{settings.vector_bucket}"
                    f"/index/{name}"
                    for name in INDEXES
                ],
            )
        )
        url = self.skill.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)

        CfnOutput(self, "SkillEndpointUrl", value=url.url)
