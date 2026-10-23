"""Escalation: a disruption left untouched on a desk is raised with the duty supervisor."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from test_plans import ANANYA, disruption

from crewops.analysis.runner import Operation
from crewops.ingestion.seed import load_seed
from crewops.notify.changes import Change
from crewops.notify.escalation import check, schedule_check, schedule_name
from crewops.plans.service import Plans
from crewops.store import plans as plan_store
from crewops.store.ops_store import OpsStore
from crewops.tools.port import describe_report


class FakeScheduler:
    def __init__(self):
        self.created = []

    def create_schedule(self, **kwargs):
        self.created.append(kwargs)


NOW = datetime(2026, 10, 13, 5, 0, tzinfo=UTC)


def test_a_check_is_scheduled_once_for_work_that_reaches_a_desk():
    scheduler = FakeScheduler()
    kwargs = {
        "prefix": "crewops-dev",
        "function_arn": "fn",
        "role_arn": "role",
        "minutes": 10,
        "now": NOW,
    }
    analysed = Change("r-sick1042", "REPORTED", "OPTIONS_READY", "crew_unavailable")
    name = schedule_check(scheduler, analysed, **kwargs)
    assert name.startswith("crewops-dev-esc-1791867600-") and name.endswith("-r-sick1042")
    (made,) = scheduler.created
    assert made["ScheduleExpression"] == "at(2026-10-13T05:10:00)"
    assert made["ScheduleExpressionTimezone"] == "UTC" and made["ActionAfterCompletion"] == "DELETE"
    assert json.loads(made["Target"]["Input"]) == {
        "disruption_id": "r-sick1042",
        "since": "2026-10-13T05:00:00Z",
    }
    sent_back = Change("r-sick1042", "SUBMITTED", "OPTIONS_READY", "crew_unavailable")
    assert schedule_check(scheduler, sent_back, **kwargs) is not None
    for before, after in (
        ("OPTIONS_READY", "SUBMITTED"),
        ("SUBMITTED", "COMMITTED"),
        (None, "REPORTED"),
    ):
        assert schedule_check(scheduler, Change("r-1", before, after, "x"), **kwargs) is None
    assert len(scheduler.created) == 2
    long_stage = "crewops-" + "s" * 20
    for one, other in (("r-" + "x" * 80, "r-" + "x" * 79 + "y"), ("r-a", "r-b")):
        a, b = schedule_name(long_stage, one, NOW), schedule_name(long_stage, other, NOW)
        assert len(a) <= 64 and a != b and "-1791867600-" in a


def test_an_untouched_disruption_is_escalated_once(ops_table):
    OpsStore(ops_table).put_seed(load_seed())
    disruption(ops_table, "r-sick1042", "C-1042", "2026-10-13", "2026-10-14")  # desk-1
    sent = []
    since = datetime.now(UTC) + timedelta(seconds=1)  # the clock starts after the analysis
    later = since + timedelta(minutes=10)
    stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert (
        check(ops_table, "r-sick1042", stamp, 10, sent.append, later, describe_report)
        == "escalated"
    )
    (alert,) = sent
    assert alert.role == "duty_supervisor"
    assert "has waited 10 minutes on Ananya Iyer's desk (desk-1)" in alert.message
    events = [e["action"] for e in plan_store.audit_trail(ops_table, "r-sick1042")]
    assert events[-1] == "ESCALATED"
    # A second check (an older schedule) sees the escalation and does nothing.
    assert (
        check(ops_table, "r-sick1042", stamp, 10, sent.append, later, describe_report) == "handled"
    )
    assert len(sent) == 1


def test_a_disruption_someone_acted_on_is_not_escalated(ops_table):
    OpsStore(ops_table).put_seed(load_seed())
    disruption(ops_table, "r-sick1042", "C-1042", "2026-10-13", "2026-10-14")
    stamp = (datetime.now(UTC) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    Plans(ops_table, load=Operation.load).submit(ANANYA, "r-sick1042")
    sent = []
    assert check(ops_table, "r-sick1042", stamp, 10, sent.append, NOW, describe_report) == "handled"
    assert check(ops_table, "r-nothere1", stamp, 10, sent.append, NOW, describe_report) == "missing"
    assert sent == []


def test_the_speaker_announces_an_escalation(ops_table):
    from crewops.alerts import alerts
    from crewops.store.reports import disruptions_with_status

    OpsStore(ops_table).put_seed(load_seed())
    disruption(ops_table, "r-sick1042", "C-1042", "2026-10-13", "2026-10-14")
    since = datetime.now(UTC) + timedelta(seconds=1)
    stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    check(
        ops_table,
        "r-sick1042",
        stamp,
        10,
        lambda a: None,
        since + timedelta(minutes=10),
        describe_report,
    )
    (alert,) = alerts(
        disruptions_with_status(ops_table, "OPTIONS_READY"),
        lambda d: plan_store.audit_trail(ops_table, d),
        lambda p: None,
        describe_report,
        "desk-1",
    )
    assert alert["event"] == "ESCALATED" and alert["title"] == "Escalated"
    # Flies the day it was reported, so medium. Said first so the room knows how urgent.
    assert alert["severity"] == "medium"
    assert alert["text"].startswith(
        "Medium severity. Still waiting: C-1042 unavailable 2026-10-13 to 2026-10-14"
    )
    assert alert["text"].endswith("On Ananya Iyer's desk. The duty supervisor has been told.")


def test_an_email_that_fails_leaves_no_escalation_mark_so_the_retry_sends_it(ops_table):
    OpsStore(ops_table).put_seed(load_seed())
    disruption(ops_table, "r-sick1042", "C-1042", "2026-10-13", "2026-10-14")
    since = datetime.now(UTC) + timedelta(seconds=1)
    later = since + timedelta(minutes=10)
    stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    def broken(alert):
        raise RuntimeError("SNS throttled")

    with pytest.raises(RuntimeError):
        check(ops_table, "r-sick1042", stamp, 10, broken, later, describe_report)
    events = [e["action"] for e in plan_store.audit_trail(ops_table, "r-sick1042")]
    assert "ESCALATED" not in events
    sent = []
    assert check(ops_table, "r-sick1042", stamp, 10, sent.append, later, describe_report) == (
        "escalated"
    )
    assert len(sent) == 1
