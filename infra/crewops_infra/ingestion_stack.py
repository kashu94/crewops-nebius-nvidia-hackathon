"""Ingestion: roster uploads (batch) and live reports (real time).

Batch: dropping a JSON file under ``uploads/`` in the roster bucket triggers the loader. It
validates the upload against the current operation and writes it to the table only if there
are no errors. Either way it leaves a report under ``results/``.

Real time: reports are sent to an SQS queue. A worker Lambda checks the role the sender
states and every id the report names, then stores it once as a REPORTED disruption. The
sender is trusted to have set its identity from a verified login, so the queue's policy
refuses messages from anyone but the MCP server (which sets it from the verified token) and
the operator ARNs listed in ``CREWOPS_QUEUE_SENDERS``. Messages that fail unexpectedly are
retried and, after three attempts, moved to a dead-letter queue.

The function's code is the ``crewops`` package itself. Its only dependency, boto3, ships with
the Lambda Python runtime, so no build step or Docker is needed.
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_notifications as s3n
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from crewops.config import Settings
from crewops.ingestion.roster_upload import RESULTS_PREFIX, UPLOADS_PREFIX

from .data_stack import DISPOSABLE_STAGES

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
RETENTION = Duration.days(90)
WORKER_TIMEOUT = Duration.seconds(30)
MAX_RECEIVES = 3


def _code() -> lambda_.Code:
    """The crewops package. boto3 ships with the Lambda runtime, so nothing else is needed."""
    return lambda_.Code.from_asset(
        str(SRC_DIR), exclude=["**/__pycache__", "**/__pycache__/**", "**/*.pyc"]
    )


class IngestionStack(Stack):
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
        disposable = settings.stage in DISPOSABLE_STAGES

        # Bucket names are global, so the account id keeps this one unique.
        self.bucket = s3.Bucket(
            self,
            "RosterBucket",
            bucket_name=f"{settings.roster_bucket}-{self.account}",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(prefix=UPLOADS_PREFIX, expiration=RETENTION),
                s3.LifecycleRule(prefix=RESULTS_PREFIX, expiration=RETENTION),
            ],
            removal_policy=RemovalPolicy.DESTROY if disposable else RemovalPolicy.RETAIN,
            auto_delete_objects=disposable,
        )

        self.loader = lambda_.Function(
            self,
            "RosterLoader",
            description="Validates roster uploads and writes accepted ones to the ops table.",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="crewops.ingestion.roster_upload.handler",
            code=_code(),
            memory_size=512,
            timeout=Duration.seconds(60),
            environment={"CREWOPS_STAGE": settings.stage},
            log_group=logs.LogGroup(
                self,
                "RosterLoaderLogs",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )

        table.grant_read_write_data(self.loader)
        self.bucket.grant_read(self.loader, f"{UPLOADS_PREFIX}*")
        self.bucket.grant_put(self.loader, f"{RESULTS_PREFIX}*")
        self.bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(self.loader),
            s3.NotificationKeyFilter(prefix=UPLOADS_PREFIX, suffix=".json"),
        )

        # -- Live reports ---------------------------------------------------------
        self.dead_letters = sqs.Queue(
            self,
            "IngestDeadLetters",
            queue_name=settings.ingest_dlq,
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            retention_period=Duration.days(14),
        )
        self.queue = sqs.Queue(
            self,
            "IngestQueue",
            queue_name=settings.ingest_queue,
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            # Several times the worker timeout, so a message is never redelivered while a
            # worker may still be processing it.
            visibility_timeout=Duration.seconds(WORKER_TIMEOUT.to_seconds() * 6),
            dead_letter_queue=sqs.DeadLetterQueue(
                queue=self.dead_letters, max_receive_count=MAX_RECEIVES
            ),
        )
        # One policy document per queue, so this rule lives here, beside enforce_ssl's. The
        # server's role is named, not referenced, so the queue does not depend on the MCP stack.
        senders = [
            f"arn:{self.partition}:iam::{self.account}:role/{settings.mcp_runtime_role}",
            *settings.queue_senders,
        ]
        self.queue.add_to_resource_policy(
            iam.PolicyStatement(
                sid="OnlyTheMcpServerSendsReports",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["sqs:SendMessage"],
                resources=[self.queue.queue_arn],
                conditions={"ArnNotEquals": {"aws:PrincipalArn": senders}},
            )
        )
        self.worker = lambda_.Function(
            self,
            "ReportWorker",
            description="Validates live reports and stores them once as REPORTED disruptions.",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="crewops.ingestion.report_worker.handler",
            code=_code(),
            memory_size=256,
            timeout=WORKER_TIMEOUT,
            environment={"CREWOPS_STAGE": settings.stage},
            log_group=logs.LogGroup(
                self,
                "ReportWorkerLogs",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )
        # Only what the worker does: look items up, query the status index, put items, and
        # move the roster version of each crew member a report takes out (an update). Each
        # part of a transaction needs its own action; there is no separate transact action.
        table.grant(
            self.worker,
            "dynamodb:GetItem",
            "dynamodb:Query",
            "dynamodb:PutItem",
            "dynamodb:UpdateItem",
        )
        self.worker.add_event_source(
            sources.SqsEventSource(self.queue, batch_size=10, report_batch_item_failures=True)
        )

        CfnOutput(self, "RosterBucketName", value=self.bucket.bucket_name)
        CfnOutput(self, "RosterLoaderName", value=self.loader.function_name)
        CfnOutput(self, "IngestQueueUrl", value=self.queue.queue_url)
        CfnOutput(self, "IngestDeadLetterQueueUrl", value=self.dead_letters.queue_url)
        CfnOutput(self, "ReportWorkerName", value=self.worker.function_name)
