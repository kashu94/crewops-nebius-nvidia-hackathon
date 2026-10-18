"""Synthesize the notifications stack locally (no AWS access) and check what it would create."""

import json
import shutil
import subprocess

import pytest

pytest.importorskip("aws_cdk")

from aws_cdk import App, Environment  # noqa: E402
from aws_cdk.assertions import Template  # noqa: E402
from crewops_infra.auth_stack import AuthStack  # noqa: E402
from crewops_infra.data_stack import DataStack  # noqa: E402
from crewops_infra.notify_stack import HANDLERS, NotifyStack  # noqa: E402

from crewops.config import Settings  # noqa: E402

ENV = Environment(account="111111111111", region="ap-southeast-2")


@pytest.fixture(scope="module")
def template():
    settings = Settings.from_env({"CREWOPS_STAGE": "dev"})
    app = App()
    data = DataStack(app, "Data", settings=settings, env=ENV)
    auth = AuthStack(app, "Auth", settings=settings, env=ENV)
    stack = NotifyStack(app, "Notify", settings=settings, table=data.table, auth=auth, env=ENV)
    return Template.from_stack(stack)


def test_alert_topic_has_no_subscribers_and_requires_tls(template):
    (topic,) = template.find_resources("AWS::SNS::Topic").values()
    assert topic["Properties"]["TopicName"] == "crewops-dev-alerts"
    assert template.find_resources("AWS::SNS::Subscription") == {}
    (policy,) = template.find_resources("AWS::SNS::TopicPolicy").values()
    assert "aws:SecureTransport" in json.dumps(policy)


def test_console_events_publish_by_iam_and_subscribe_by_sign_in(template):
    (api,) = template.find_resources("AWS::AppSync::Api").values()
    config = api["Properties"]["EventConfig"]
    assert [m["AuthType"] for m in config["DefaultPublishAuthModes"]] == ["AWS_IAM"]
    assert [m["AuthType"] for m in config["DefaultSubscribeAuthModes"]] == [
        "AMAZON_COGNITO_USER_POOLS"
    ]
    (namespace,) = template.find_resources("AWS::AppSync::ChannelNamespace").values()
    assert namespace["Properties"]["Name"] == "ops"
    assert "onSubscribe" in namespace["Properties"]["CodeHandlers"]


def actions_of(template, function_prefix):
    """The IAM actions of one function's role policy (by its logical id)."""
    actions = set()
    for key, policy in template.find_resources("AWS::IAM::Policy").items():
        if not key.startswith(f"{function_prefix}ServiceRole"):
            continue
        for s in policy["Properties"]["PolicyDocument"]["Statement"]:
            actions |= set(s["Action"] if isinstance(s["Action"], list) else [s["Action"]])
    return actions


def test_notifier_reads_only_disruption_changes_and_may_only_publish(template):
    (mapping,) = template.find_resources("AWS::Lambda::EventSourceMapping").values()
    (pattern,) = mapping["Properties"]["FilterCriteria"]["Filters"]
    assert json.loads(pattern["Pattern"]) == {
        "dynamodb": {"NewImage": {"entity": {"S": ["disruption"]}}}
    }
    assert mapping["Properties"]["FunctionResponseTypes"] == ["ReportBatchItemFailures"]
    actions = actions_of(template, "Notifier")
    assert {"sns:Publish", "appsync:EventPublish"} <= actions
    assert not any(a.startswith("dynamodb:Put") or a.startswith("dynamodb:Update") for a in actions)
    (fn,) = [
        f
        for f in template.find_resources("AWS::Lambda::Function").values()
        if f["Properties"].get("Handler") == "crewops.notify.stream_handler.handler"
    ]
    env = fn["Properties"]["Environment"]["Variables"]
    assert "CREWOPS_ALERTS_TOPIC_ARN" in env and "CREWOPS_EVENTS_HTTP_DOMAIN" in env


NODE_CHECK = """
const src = require('fs').readFileSync(process.argv[1], 'utf8')
  .replace(/^import .*$/m, '').replace('export function', 'function');
let refused = 0;
const util = { unauthorized() { refused += 1; } };
const onSubscribe = new Function('util', src + '; return onSubscribe;')(util);
const results = {};
for (const [name, groups] of Object.entries(JSON.parse(process.argv[2]))) {
  refused = 0;
  onSubscribe({ identity: groups === null ? null : { groups } });
  results[name] = refused === 0;
}
console.log(JSON.stringify(results));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_only_staff_who_read_disruptions_may_subscribe():
    cases = {
        "controller": ["controller"],
        "approver": ["approver"],
        "auditor": ["auditor"],
        "admin": ["admin"],
        "crew": ["crew"],
        "reporter": ["reporter"],
        "no groups": [],
        "no identity": None,
    }
    out = subprocess.run(
        ["node", "-e", NODE_CHECK, str(HANDLERS), json.dumps(cases)],
        capture_output=True,
        text=True,
        check=True,
    )
    admitted = json.loads(out.stdout)
    assert admitted == {
        "controller": True,
        "approver": True,
        "auditor": True,
        "admin": True,
        "crew": False,
        "reporter": False,
        "no groups": False,
        "no identity": False,
    }


def test_escalation_checks_are_scheduled_and_run_with_least_privilege(template):
    notifier = actions_of(template, "Notifier")
    assert {"scheduler:CreateSchedule", "iam:PassRole"} <= notifier
    policy = json.dumps(template.find_resources("AWS::IAM::Policy"))
    assert "schedule/default/crewops-dev-esc-*" in policy  # only its own schedules
    escalation = actions_of(template, "Escalation")
    assert {"dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem", "sns:Publish"} <= escalation
    assert not any(a in escalation for a in ("dynamodb:DeleteItem", "dynamodb:UpdateItem"))
    roles = template.find_resources("AWS::IAM::Role")
    (scheduler_role,) = [r for k, r in roles.items() if k.startswith("EscalationSchedulerRole")]
    principal = scheduler_role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0][
        "Principal"
    ]
    assert principal == {"Service": "scheduler.amazonaws.com"}
    (fn,) = [
        f
        for f in template.find_resources("AWS::Lambda::Function").values()
        if f["Properties"].get("Handler") == "crewops.notify.stream_handler.handler"
    ]
    env = fn["Properties"]["Environment"]["Variables"]
    assert env["CREWOPS_ESCALATE_AFTER_MINUTES"] == "10"
    assert "CREWOPS_ESCALATION_FUNCTION_ARN" in env and "CREWOPS_ESCALATION_ROLE_ARN" in env


def test_with_the_package_the_notifier_sends_desktop_alerts(tmp_path):
    (tmp_path / "main.py").write_text("print('stand-in')\n")
    settings = Settings.from_env({"CREWOPS_STAGE": "dev"})
    app = App()
    data = DataStack(app, "Data", settings=settings, env=ENV)
    auth = AuthStack(app, "Auth", settings=settings, env=ENV)
    stack = NotifyStack(
        app, "Notify", settings=settings, table=data.table, auth=auth, package=tmp_path, env=ENV
    )
    t = Template.from_stack(stack)
    actions = actions_of(t, "Notifier")
    # GetItem: the profile that says who is on which desk now.
    assert {
        "dynamodb:Query",
        "dynamodb:GetItem",
        "dynamodb:DeleteItem",
        "ssm:GetParameter",
    } <= actions
    assert not any(a.startswith("dynamodb:Put") or a.startswith("dynamodb:Update") for a in actions)
    policy = json.dumps(t.find_resources("AWS::IAM::Policy"))
    assert "parameter/crewops-dev/push/*" in policy
    (fn,) = [
        f["Properties"]
        for f in t.find_resources("AWS::Lambda::Function").values()
        if f["Properties"].get("Handler") == "crewops.notify.stream_handler.handler"
    ]
    assert fn["Environment"]["Variables"]["CREWOPS_PUSH_ENABLED"] == "1"
