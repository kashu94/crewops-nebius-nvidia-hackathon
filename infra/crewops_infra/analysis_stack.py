"""Disruption analysis: the table's change stream triggers a Lambda for each new disruption.

The event source mapping filters the stream down to inserted disruption items, so the
thousands of other writes (roster uploads, audit events, analyses themselves) never invoke
the function. Failed records are retried with partial-batch reporting and, after the retry
limit, recorded in a dead-letter queue instead of blocking the stream.
"""

from __future__ import annotations

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from crewops.config import Settings

from .ingestion_stack import _code

ANALYSIS_TIMEOUT = Duration.seconds(120)
STREAM_RETRIES = 3


class AnalysisStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        settings: Settings,
        table: dynamodb.ITableV2,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)

        self.failed = sqs.Queue(
            self,
            "AnalysisDeadLetters",
            queue_name=f"{settings.prefix}-analysis-dlq",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            retention_period=Duration.days(14),
        )

        self.analyser = lambda_.Function(
            self,
            "DisruptionAnalyser",
            description="Analyses each new disruption and stores it as OPTIONS_READY.",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="crewops.analysis.stream_handler.handler",
            code=_code(),
            # loads the whole operation and scores every candidate; memory buys CPU here
            memory_size=1024,
            timeout=ANALYSIS_TIMEOUT,
            environment={"CREWOPS_STAGE": settings.stage, "CREWOPS_REAL_TIME": "1"},
            log_group=logs.LogGroup(
                self,
                "DisruptionAnalyserLogs",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )

        # Reads the whole operation (Scan), reads the disruption (GetItem), and writes the
        # analysis and audit event in one transaction (UpdateItem + PutItem).
        table.grant(
            self.analyser,
            "dynamodb:Scan",
            "dynamodb:GetItem",
            "dynamodb:Query",
            "dynamodb:UpdateItem",
            "dynamodb:PutItem",
        )
        self.analyser.add_event_source(
            sources.DynamoEventSource(
                table,
                starting_position=lambda_.StartingPosition.LATEST,
                batch_size=5,
                retry_attempts=STREAM_RETRIES,
                bisect_batch_on_error=True,
                report_batch_item_failures=True,
                on_failure=sources.SqsDlq(self.failed),
                filters=[
                    lambda_.FilterCriteria.filter(
                        {
                            "eventName": lambda_.FilterRule.is_equal("INSERT"),
                            "dynamodb": {
                                "NewImage": {
                                    "entity": {"S": lambda_.FilterRule.is_equal("disruption")}
                                }
                            },
                        }
                    )
                ],
            )
        )

        CfnOutput(self, "AnalyserName", value=self.analyser.function_name)
        CfnOutput(self, "AnalysisDeadLetterQueueUrl", value=self.failed.queue_url)
