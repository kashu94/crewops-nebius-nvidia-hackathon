"""Notifications: status changes to console events and alerts, and admin-managed addresses."""

import json

import boto3
import pytest
from boto3.dynamodb.types import TypeSerializer

from crewops.domain import Role
from crewops.notify.changes import CONSOLE_CHANNEL, alert_for, change_of, console_event
from crewops.notify.channels import EventPublisher
from crewops.notify.stream_handler import process
from crewops.notify.subscriptions import DAILY_CAP, AlertError, AlertSubscriptions
from crewops.tools import Caller

ADMIN = Caller("asha", frozenset({Role.ADMIN}))
ROHIT = Caller("rohit", frozenset({Role.CONTROLLER}), desk="desk-2")
_ser = TypeSerializer().serialize


def record(new, old=None, name="MODIFY", seq="1"):
    data = {"SequenceNumber": seq, "NewImage": {k: _ser(v) for k, v in new.items()}}
    if old is not None:
        data["OldImage"] = {k: _ser(v) for k, v in old.items()}
    return {"eventName": name, "dynamodb": data}


def disruption(status, **extra):
    return {
        "entity": "disruption",
        "disruption_id": "r-sick1042",
        "status": status,
        "report": {"type": "crew_unavailable", "details": {"crew_id": "C-1042"}},
        **extra,
    }


# -- Changes ------------------------------------------------------------------------------


def test_only_status_changes_of_disruptions_count():
    reported = record(disruption("REPORTED"), name="INSERT")
    assert change_of(reported).before is None and change_of(reported).after == "REPORTED"
    ready = change_of(record(disruption("OPTIONS_READY"), disruption("REPORTED")))
    assert (ready.before, ready.after, ready.report_type) == (
        "REPORTED",
        "OPTIONS_READY",
        "crew_unavailable",
    )
    same = record(disruption("OPTIONS_READY", note="x"), disruption("OPTIONS_READY"))
    assert change_of(same) is None
    assert change_of(record({"entity": "plan", "status": "SUBMITTED"}, {})) is None
    assert change_of(record(disruption("CLOSED"), name="REMOVE")) is None


@pytest.mark.parametrize(
    ("before", "after", "role", "words"),
    [
        ("REPORTED", "OPTIONS_READY", "controller", "options ready"),
        ("OPTIONS_READY", "SUBMITTED", "approver", "waiting for approval"),
        ("SUBMITTED", "OPTIONS_READY", "controller", "sent back"),
        ("SUBMITTED", "COMMITTED", None, None),
        ("OPTIONS_READY", "CLOSED", None, None),
        (None, "REPORTED", None, None),
    ],
)
def test_which_changes_alert_whom(before, after, role, words):
    old = disruption(before) if before else None
    change = change_of(record(disruption(after), old, "MODIFY" if before else "INSERT"))
    alert = alert_for(change, "dev")
    if role is None:
        assert alert is None
        return
    assert alert.role == role and words in alert.subject and len(alert.subject) <= 100
    assert alert.subject.startswith("CrewOps dev:")


def test_events_and_alerts_never_name_a_person():
    change = change_of(record(disruption("OPTIONS_READY"), disruption("REPORTED")))
    alert = alert_for(change, "dev")
    for text in (json.dumps(console_event(change)), alert.subject, alert.message):
        assert "C-1042" not in text and "Nair" not in text
    assert console_event(change) == {
        "kind": "disruption_status",
        "disruption_id": "r-sick1042",
        "status": "OPTIONS_READY",
        "previous": "REPORTED",
        "report_type": "crew_unavailable",
    }


# -- The stream handler -------------------------------------------------------------------


class Recorder:
    def __init__(self, fail_on=None):
        self.sent = []
        self.fail_on = fail_on

    def publish(self, *args):
        if self.fail_on is not None and self.fail_on(*args):
            raise RuntimeError("unavailable")
        self.sent.append(args)


def test_changes_go_to_the_console_and_alerts_to_their_role():
    records = [
        record(disruption("REPORTED"), name="INSERT", seq="1"),
        record(disruption("OPTIONS_READY"), disruption("REPORTED"), seq="2"),
        record(disruption("SUBMITTED"), disruption("OPTIONS_READY"), seq="3"),
    ]
    events, alerts = Recorder(), Recorder()
    assert process(records, events, alerts, "dev") == {"batchItemFailures": []}
    ((channel, payload),) = events.sent
    assert channel == CONSOLE_CHANNEL and [e["status"] for e in payload] == [
        "REPORTED",
        "OPTIONS_READY",
        "SUBMITTED",
    ]
    assert [a[0].role for a in alerts.sent] == ["controller", "approver"]


def test_a_failed_alert_is_retried_from_that_record_and_the_console_is_best_effort():
    records = [
        record(disruption("OPTIONS_READY"), disruption("REPORTED"), seq="2"),
        record(disruption("SUBMITTED"), disruption("OPTIONS_READY"), seq="3"),
    ]
    events = Recorder(fail_on=lambda *a: True)
    alerts = Recorder(fail_on=lambda alert: alert.role == "approver")
    out = process(records, events, alerts, "dev")
    assert out == {"batchItemFailures": [{"itemIdentifier": "3"}]}
    assert [a[0].role for a in alerts.sent] == ["controller"]


def test_without_a_topic_or_api_nothing_is_sent():
    records = [record(disruption("OPTIONS_READY"), disruption("REPORTED"))]
    assert process(records, None, None, "dev") == {"batchItemFailures": []}


def test_console_events_are_signed_for_appsync_and_batched(monkeypatch):
    from botocore.credentials import Credentials

    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"successful": [], "failed": []}'

    def urlopen(request, timeout):
        calls.append((request, timeout))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    publisher = EventPublisher(
        "abc.appsync-api.ap-southeast-2.amazonaws.com",
        "ap-southeast-2",
        Credentials("AKID", "secret", "token"),
    )
    publisher.publish(CONSOLE_CHANNEL, [{"n": i} for i in range(7)])
    assert len(calls) == 2  # at most five events a request
    request, timeout = calls[0]
    assert request.full_url == "https://abc.appsync-api.ap-southeast-2.amazonaws.com/event"
    headers = {k.lower(): v for k, v in request.header_items()}
    assert "/ap-southeast-2/appsync/aws4_request" in headers["authorization"]
    assert headers["x-amz-security-token"] == "token"
    body = json.loads(request.data)
    assert body["channel"] == CONSOLE_CHANNEL and len(body["events"]) == 5
    assert json.loads(body["events"][0]) == {"n": 0}


# -- Alert addresses ----------------------------------------------------------------------


@pytest.fixture
def subscriptions(ops_table):
    sns = boto3.client("sns", region_name="ap-southeast-2")
    topic = sns.create_topic(Name="crewops-test-alerts")["TopicArn"]
    return AlertSubscriptions(sns, topic, ops_table)


def test_only_an_admin_manages_alert_addresses(subscriptions):
    for call in (
        lambda: subscriptions.add(ROHIT, "ops@example.com", "controller"),
        lambda: subscriptions.list(ROHIT),
        lambda: subscriptions.remove(ROHIT, "x"),
    ):
        with pytest.raises(AlertError) as err:
            call()
        assert err.value.code == "FORBIDDEN"


def test_an_address_receives_only_its_role(subscriptions):
    out = subscriptions.add(ADMIN, "ops@example.com", "controller")
    assert out["status"] == "pending confirmation"
    (held,) = subscriptions.list(ADMIN)
    assert held["email"] == "ops@example.com" and held["role"] == "controller"
    assert held["added_by"] == "asha"
    arn = f"{subscriptions.topic_arn}:{out['subscription_id']}"
    attrs = subscriptions.sns.get_subscription_attributes(SubscriptionArn=arn)["Attributes"]
    assert json.loads(attrs["FilterPolicy"]) == {"role": ["controller"]}
    assert attrs["Protocol"] == "email"


def test_bad_and_repeated_addresses_are_refused(subscriptions):
    for email, role in (("not-an-address", "controller"), ("a@example.com", "crew")):
        with pytest.raises(AlertError) as err:
            subscriptions.add(ADMIN, email, role)
        assert err.value.code == "INVALID"
    subscriptions.add(ADMIN, "ops@example.com", "approver")
    for role in ("approver", "controller"):  # one address, one role: SNS keeps one
        with pytest.raises(AlertError) as err:
            subscriptions.add(ADMIN, "OPS@example.com", role)
        assert err.value.code == "DUPLICATE" and "approver alerts" in err.value.message


class Refusing:
    """SNS refusing every subscription, as it does for an address it will not accept."""

    def __init__(self, sns, code):
        self.sns, self.code = sns, code

    def subscribe(self, **kwargs):
        from botocore.exceptions import ClientError

        raise ClientError({"Error": {"Code": self.code, "Message": "no"}}, "Subscribe")

    def unsubscribe(self, **kwargs):
        self.subscribe()

    def __getattr__(self, name):
        return getattr(self.sns, name)


def test_a_refused_address_gives_its_daily_slot_back(subscriptions):
    real = subscriptions.sns
    subscriptions.sns = Refusing(real, "InvalidParameter")
    for _ in range(DAILY_CAP + 2):
        with pytest.raises(AlertError) as err:
            subscriptions.add(ADMIN, "ops@example.com", "controller")
        assert err.value.code == "INVALID"
    subscriptions.sns = real
    for i in range(DAILY_CAP):  # the whole day's allowance is still there
        subscriptions.add(ADMIN, f"ops{i}@example.com", "controller")


def test_an_unconfirmed_address_cannot_be_removed_yet(subscriptions):
    out = subscriptions.add(ADMIN, "ops@example.com", "controller")
    real = subscriptions.sns
    subscriptions.sns = Refusing(real, "InvalidParameter")
    with pytest.raises(AlertError) as err:
        subscriptions.remove(ADMIN, out["subscription_id"])
    assert err.value.code == "PENDING"
    subscriptions.sns = real
    assert [a["subscription_id"] for a in subscriptions.list(ADMIN)] == [out["subscription_id"]]


def test_a_day_allows_only_a_few_new_addresses(subscriptions):
    for i in range(DAILY_CAP):
        subscriptions.add(ADMIN, f"ops{i}@example.com", "controller")
    with pytest.raises(AlertError) as err:
        subscriptions.add(ADMIN, "one-more@example.com", "controller")
    assert err.value.code == "LIMIT"


def test_removing_an_address_unsubscribes_it_and_is_audited(subscriptions, ops_table):
    out = subscriptions.add(ADMIN, "ops@example.com", "controller")
    assert subscriptions.remove(ADMIN, out["subscription_id"])["status"] == "removed"
    assert subscriptions.list(ADMIN) == []
    with pytest.raises(AlertError) as err:
        subscriptions.remove(ADMIN, out["subscription_id"])
    assert err.value.code == "NOT_FOUND"
    actions = [
        i["action"]
        for i in ops_table.scan()["Items"]
        if i.get("entity") == "audit_event" and i["pk"] == "ALERTS"
    ]
    assert sorted(actions) == ["ALERT_ADDED", "ALERT_REMOVED"]


# -- Operational teams -----------------------------------------------------------------


def notice(team, scope, message, channel="email"):
    return {
        "team": team,
        "team_title": team.replace("_", " ").capitalize(),
        "scope": scope,
        "channel": channel,
        "why": "act on it",
        "message": message,
    }


ANALYSED = {
    "notices": [
        notice("flight_info", "NETWORK", "KS412 BLR-BOM now departs 08:00Z 13 Oct"),
        notice("station_ops", "BLR", "Hold boarding at BLR-G6"),
        notice("station_ops", "BOM", "KS413 leaves BOM an hour late"),
        notice("crew", "C-5417", "Your recurrent training expires", channel="in_app"),
    ]
}


def test_each_team_named_by_the_analysis_gets_one_email_when_it_is_analysed():
    records = [
        record(
            disruption("OPTIONS_READY", analysis=ANALYSED),
            disruption("REPORTED"),
            seq="2",
        ),
        # Later moves never repeat the teams' alerts.
        record(disruption("SUBMITTED", analysis=ANALYSED), disruption("OPTIONS_READY"), seq="3"),
    ]
    events, alerts = Recorder(), Recorder()
    assert process(records, events, alerts, "dev") == {"batchItemFailures": []}
    sent = {a[0].role: a[0] for a in alerts.sent}
    assert list(sent) == ["controller", "flight_info", "station_ops", "approver"]
    station = sent["station_ops"].message
    assert "BLR: Hold boarding at BLR-G6" in station and "BOM: KS413" in station
    # In-app notices are read in CrewOps, never emailed.
    assert all("recurrent training" not in a[0].message for a in alerts.sent)
    assert sent["flight_info"].subject.startswith("CrewOps dev: Flight info")


def test_an_address_can_follow_an_operational_team(subscriptions):
    out = subscriptions.add(ADMIN, "screens@example.com", "flight_info")
    arn = f"{subscriptions.topic_arn}:{out['subscription_id']}"
    attrs = subscriptions.sns.get_subscription_attributes(SubscriptionArn=arn)["Attributes"]
    assert json.loads(attrs["FilterPolicy"]) == {"role": ["flight_info"]}
