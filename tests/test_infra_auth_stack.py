"""Synthesize the auth stack locally (no AWS access) and check what it would create."""

import pytest

pytest.importorskip("aws_cdk")

from aws_cdk import App, Environment  # noqa: E402
from aws_cdk.assertions import Match, Template  # noqa: E402
from crewops_infra.auth_stack import AuthStack  # noqa: E402

from crewops.config import Settings  # noqa: E402
from crewops.domain import Role  # noqa: E402


def synth(stage="dev"):
    stack = AuthStack(
        App(),
        "Auth",
        settings=Settings.from_env({"CREWOPS_STAGE": stage}),
        env=Environment(account="111111111111", region="ap-southeast-2"),
    )
    return Template.from_stack(stack)


def test_pool_is_admin_created_username_only_and_sends_nothing():
    t = synth()
    (pool,) = t.find_resources("AWS::Cognito::UserPool").values()
    p = pool["Properties"]
    assert p["UserPoolName"] == "crewops-dev-users"
    assert p["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is True
    assert "UsernameAttributes" not in p and "AliasAttributes" not in p
    assert p["AccountRecoverySetting"]["RecoveryMechanisms"] == [
        {"Name": "admin_only", "Priority": 1}
    ]
    assert p["UserPoolTier"] == "LITE" and p["MfaConfiguration"] == "OFF"
    assert p["Policies"]["PasswordPolicy"]["MinimumLength"] == 12
    assert not [a for a in p.get("Schema", []) if a.get("Name") in ("crew_id", "desk")]
    assert pool["DeletionPolicy"] == "Delete"


def test_one_group_per_role():
    groups = synth().find_resources("AWS::Cognito::UserPoolGroup")
    names = {g["Properties"]["GroupName"] for g in groups.values()}
    assert names == {r.value for r in Role}


def test_client_signs_in_directly_and_reads_only_the_name():
    t = synth()
    t.has_resource_properties(
        "AWS::Cognito::UserPoolClient",
        {
            "GenerateSecret": False,
            "ReadAttributes": ["name"],
            "WriteAttributes": ["locale"],  # never empty: empty would mean "all standard"
            "PreventUserExistenceErrors": "ENABLED",
            "AllowedOAuthFlowsUserPoolClient": False,
            "CallbackURLs": Match.absent(),
        },
    )


def test_a_lasting_stage_keeps_its_users():
    (pool,) = synth("prod").find_resources("AWS::Cognito::UserPool").values()
    assert pool["DeletionPolicy"] == "Retain"
    assert pool["Properties"]["DeletionProtection"] == "ACTIVE"
