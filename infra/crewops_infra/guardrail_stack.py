"""Bedrock Guardrails: content and prompt-attack screening on every question and answer.

The guardrail catches what the deterministic layers cannot judge: harmful content and
attempts to talk the assistant out of its instructions. It does not check facts (the
verifier does) and does not remove personal data (the redaction step does; Bedrock's own
PII filter would also remove crew names, which a controller needs).

Billed per text unit screened, with no idle cost. A published version is created so the
application pins an exact configuration rather than the editable draft.
"""

from __future__ import annotations

import hashlib
import json

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_bedrock as bedrock
from constructs import Construct

from crewops.config import Settings

BLOCKED_INPUT = (
    "I can't help with that. I can answer crew-control questions: rosters, legality, "
    "cover, disruptions and callouts."
)
BLOCKED_OUTPUT = "I can't give that answer. Please rephrase the question."

FILTERS = (
    ("HATE", "HIGH", "HIGH"),
    ("INSULTS", "MEDIUM", "MEDIUM"),
    ("SEXUAL", "HIGH", "HIGH"),
    ("VIOLENCE", "MEDIUM", "MEDIUM"),
    ("MISCONDUCT", "MEDIUM", "MEDIUM"),
    ("PROMPT_ATTACK", "HIGH", "NONE"),
)
"""(type, input strength, output strength). Medium for insults, violence and misconduct,
so ordinary operational words ("crew injured", "aircraft grounded") are not blocked. A
prompt attack can only arrive in the input."""


class GuardrailStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, settings: Settings, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        self.guardrail = bedrock.CfnGuardrail(
            self,
            "Guardrail",
            name=settings.guardrail_name,
            description="Screens crew-control questions and answers.",
            blocked_input_messaging=BLOCKED_INPUT,
            blocked_outputs_messaging=BLOCKED_OUTPUT,
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type=kind, input_strength=inp, output_strength=out
                    )
                    for kind, inp, out in FILTERS
                ]
            ),
        )
        # The description carries a fingerprint of the configuration, so any change to the
        # filters or messages publishes a new version instead of leaving the old one in use.
        fingerprint = hashlib.sha256(
            json.dumps([FILTERS, BLOCKED_INPUT, BLOCKED_OUTPUT]).encode()
        ).hexdigest()[:12]
        self.version = bedrock.CfnGuardrailVersion(
            self,
            "GuardrailVersion",
            guardrail_identifier=self.guardrail.attr_guardrail_id,
            description=f"Configuration {fingerprint}",
        )
        # A new version replaces the old in the stack, but the old one is kept: a running
        # service pinned to it keeps screening until its settings move to the new number.
        self.version.apply_removal_policy(RemovalPolicy.RETAIN)

        CfnOutput(self, "GuardrailId", value=self.guardrail.attr_guardrail_id)
        CfnOutput(self, "GuardrailVersionNumber", value=self.version.attr_version)
