"""Notifications: live console updates (AppSync Events) and admin-managed email alerts (SNS).

The notifier is a Lambda on the table's stream, filtered to disruption items. Every status
change goes to the console channel. The ones someone has to act on also go to the alerts
topic, tagged with the role they're for.

The console is an AppSync Events API. People subscribe with their Cognito sign-in and the
namespace handler (console_events.js) only admits roles that can read disruptions.
Publishing is IAM only, and only the notifier has it.

The SNS topic starts with no subscribers. An admin adds an address for a role, the address
has to confirm, and each subscription is filtered to that role.

Events and alerts carry a disruption id and status, never a person's name. All of it is
pay per use, so a quiet day with nobody connected costs nothing.
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_appsync as appsync
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from crewops.config import Settings
from crewops.notify.changes import CONSOLE_CHANNEL
from crewops_infra.auth_stack import AuthStack

from .ingestion_stack import _code

HANDLERS = Path(__file__).with_name("console_events.js")
NAMESPACE = CONSOLE_CHANNEL.strip("/").split("/")[0]
STREAM_RETRIES = 3


class NotifyStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: Settings,
        table: dynamodb.ITableV2,
        auth: AuthStack,
        package: Path | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.topic = sns.Topic(
            self,
            "Alerts",
            topic_name=settings.alerts_topic,
            display_name="CrewOps alerts",
            enforce_ssl=True,
        )

        user_pool = appsync.AppSyncAuthorizationType.USER_POOL
        iam_auth = appsync.AppSyncAuthorizationType.IAM
        self.events = appsync.EventApi(
            self,
            "ConsoleEvents",
            api_name=settings.event_api_name,
            authorization_config=appsync.EventApiAuthConfig(
                auth_providers=[
                    appsync.AppSyncAuthProvider(
                        authorization_type=user_pool,
                        cognito_config=appsync.AppSyncCognitoConfig(
                            user_pool=auth.user_pool,
                            app_id_client_regex=auth.client.user_pool_client_id,
                        ),
                    ),
                    appsync.AppSyncAuthProvider(authorization_type=iam_auth),
                ],
                connection_auth_mode_types=[user_pool, iam_auth],
                default_publish_auth_mode_types=[iam_auth],
                default_subscribe_auth_mode_types=[user_pool],
            ),
        )
        self.events.add_channel_namespace(
            "Ops",
            channel_namespace_name=NAMESPACE,
            code=appsync.Code.from_inline(HANDLERS.read_text(encoding="utf-8")),
        )

        self.failed = sqs.Queue(
            self,
            "NotifierDeadLetters",
            queue_name=f"{settings.prefix}-notify-dlq",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            retention_period=Duration.days(14),
        )
        self.notifier = lambda_.Function(
            self,
            "Notifier",
            description="Publishes disruption status changes to the console and alerts.",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="crewops.notify.stream_handler.handler",
            # With the MCP package built, the notifier runs it (its cryptography library
            # encrypts desktop alerts); without it, the plain source, and no desktop alerts.
            code=lambda_.Code.from_asset(str(package)) if package else _code(),
            memory_size=256,
            # Room for email, Alexa and desktop alerts; each stops sending well before this.
            timeout=Duration.seconds(60),
            environment={
                "CREWOPS_STAGE": settings.stage,
                "CREWOPS_ALERTS_TOPIC_ARN": self.topic.topic_arn,
                "CREWOPS_EVENTS_HTTP_DOMAIN": self.events.http_dns,
                **({"CREWOPS_PUSH_ENABLED": "1"} if package else {}),
            },
            log_group=logs.LogGroup(
                self,
                "NotifierLogs",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )
        self.topic.grant_publish(self.notifier)
        self.events.grant_publish(self.notifier)

        # Escalation (crewops.notify.escalation): when a disruption reaches a desk the
        # notifier creates a one-time schedule; when it fires, this checks whether the
        # disruption is still untouched and, if so, emails the duty supervisor.
        self.escalation = lambda_.Function(
            self,
            "Escalation",
            description="Escalates a disruption left untouched on a desk.",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="crewops.notify.escalation.handler",
            code=_code(),
            memory_size=256,
            timeout=Duration.seconds(30),
            environment={
                "CREWOPS_STAGE": settings.stage,
                "CREWOPS_ALERTS_TOPIC_ARN": self.topic.topic_arn,
                "CREWOPS_ESCALATE_AFTER_MINUTES": str(settings.escalate_after_minutes),
            },
            log_group=logs.LogGroup(
                self,
                "EscalationLogs",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )
        table.grant(self.escalation, "dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem")
        self.topic.grant_publish(self.escalation)
        scheduler_role = iam.Role(
            self,
            "EscalationSchedulerRole",
            assumed_by=iam.ServicePrincipal(
                "scheduler.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
            description="Lets EventBridge Scheduler run the escalation check, and nothing else.",
        )
        self.escalation.grant_invoke(scheduler_role)
        self.notifier.add_environment(
            "CREWOPS_ESCALATION_FUNCTION_ARN", self.escalation.function_arn
        )
        self.notifier.add_environment("CREWOPS_ESCALATION_ROLE_ARN", scheduler_role.role_arn)
        self.notifier.add_environment(
            "CREWOPS_ESCALATE_AFTER_MINUTES", str(settings.escalate_after_minutes)
        )
        self.notifier.add_to_role_policy(
            iam.PolicyStatement(
                actions=["scheduler:CreateSchedule"],
                resources=[
                    f"arn:aws:scheduler:{self.region}:{self.account}:schedule/default/"
                    f"{settings.prefix}-esc-*"
                ],
            )
        )
        self.notifier.add_to_role_policy(
            iam.PolicyStatement(actions=["iam:PassRole"], resources=[scheduler_role.role_arn])
        )
        if settings.alexa_skill_id:
            # Linked Echos chime for their desk (crewops.alexa.notifier): read who is linked,
            # and the skill's Login with Amazon credentials, nothing else.
            self.notifier.add_environment("CREWOPS_ALEXA_SKILL_ID", settings.alexa_skill_id)
            self.notifier.add_environment("CREWOPS_ALEXA_EVENTS_STAGE", settings.alexa_events_stage)
            # GetItem: each person's current role and desk (their profile).
            table.grant(self.notifier, "dynamodb:Query", "dynamodb:GetItem")
            self.notifier.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["ssm:GetParameter"],
                    resources=[
                        f"arn:aws:ssm:{self.region}:{self.account}:parameter"
                        f"{settings.alexa_parameter_prefix}/*"
                    ],
                )
            )
        if package:
            # Desktop alerts (crewops.notify.push_alerts): read the browsers subscribed and
            # forget the ones gone, and read the app's push keys.
            table.grant(self.notifier, "dynamodb:Query", "dynamodb:GetItem", "dynamodb:DeleteItem")
            self.notifier.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["ssm:GetParameter"],
                    resources=[
                        f"arn:aws:ssm:{self.region}:{self.account}:parameter"
                        f"{settings.push_parameter_prefix}/*"
                    ],
                )
            )
        self.notifier.add_event_source(
            sources.DynamoEventSource(
                table,
                starting_position=lambda_.StartingPosition.LATEST,
                batch_size=10,
                retry_attempts=STREAM_RETRIES,
                bisect_batch_on_error=True,
                report_batch_item_failures=True,
                on_failure=sources.SqsDlq(self.failed),
                filters=[
                    lambda_.FilterCriteria.filter(
                        {
                            "dynamodb": {
                                "NewImage": {
                                    "entity": {"S": lambda_.FilterRule.is_equal("disruption")}
                                }
                            }
                        }
                    )
                ],
            )
        )

        CfnOutput(self, "AlertsTopicArn", value=self.topic.topic_arn)
        CfnOutput(self, "EventsHttpDomain", value=self.events.http_dns)
        CfnOutput(self, "EventsRealtimeDomain", value=self.events.realtime_dns)
        CfnOutput(self, "NotifierName", value=self.notifier.function_name)
