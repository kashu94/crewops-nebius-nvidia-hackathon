"""Rule thresholds: tightened by admins only, audited, and used by the engine."""

import json
from datetime import UTC, datetime

import pytest

from crewops.analysis.runner import Operation
from crewops.ingestion.seed import default_seed_dir, load_seed
from crewops.store import rules
from crewops.store.ops_store import OpsStore

AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


@pytest.fixture
def table(ops_table):
    OpsStore(ops_table).put_seed(load_seed())
    return ops_table


def test_the_sentences_match_the_regulatory_rules_exactly():
    seeded = json.loads((default_seed_dir() / "rules.json").read_text())["rules"]
    by_id = {r["rule_id"]: r for r in seeded}
    for t in rules.THRESHOLDS:
        rule = by_id[t.rule_id]
        assert t.text(rule["params"]) == rule["text"], t.rule_id
        assert rule["params"][t.param] == (t.maximum if t.stricter == "lower" else t.minimum)


def test_thresholds_show_value_and_range(table):
    rest = next(t for t in rules.thresholds(table) if t["param"] == "min_rest_hours")
    assert rest["value"] == 12 and rest["min"] == 12 and rest["max"] == 16
    assert rest["stricter"] == "higher"


def test_a_stricter_value_is_stored_audited_and_used_by_the_engine(table):
    out = rules.set_threshold(
        table, "RULE-REST-04", "min_rest_hours", 13, 12, "priya", "fatigue review", AT
    )
    assert out["text"] == "Min 13h rest between release and next report."
    assert Operation.load(table).world.book.min_rest_hours == 13
    (change,) = rules.rule_changes(table)
    assert change["actor"] == "priya" and change["from"] == 12 and change["to"] == 13
    assert change["reason"] == "fatigue review"
    assert rules.activity(table)[0]["action"] == "RULE_CHANGED"


@pytest.mark.parametrize(
    ("rule_id", "param", "value", "words"),
    [
        ("RULE-DUTY-02", "max_duty_hours", 61, "never looser than regulation"),
        ("RULE-REST-04", "min_rest_hours", 11, "never looser than regulation"),
        ("RULE-FDP-01", "base_fdp_hours", 12.3, "steps of 0.5h"),
        ("RULE-QUAL-05", "anything", 1, "not adjustable"),
        ("RULE-REST-04", "min_rest_hours", True, "must be numbers"),
    ],
)
def test_looser_or_malformed_values_are_refused(table, rule_id, param, value, words):
    with pytest.raises(rules.RuleError) as err:
        rules.set_threshold(table, rule_id, param, value, 12, "priya", "x", AT)
    assert words in err.value.message


def test_a_change_needs_a_reason_and_the_value_it_replaces(table):
    with pytest.raises(rules.RuleError, match="say why"):
        rules.set_threshold(table, "RULE-REST-04", "min_rest_hours", 13, 12, "priya", " ", AT)
    with pytest.raises(rules.RuleError) as err:
        rules.set_threshold(table, "RULE-REST-04", "min_rest_hours", 13, 14, "priya", "x", AT)
    assert err.value.code == "CONFLICT"
    assert rules.rule_changes(table) == []


def test_two_quick_changes_keep_both_audit_records(table):
    rules.set_threshold(table, "RULE-REST-04", "min_rest_hours", 13, 12, "priya", "a", AT)
    rules.set_threshold(table, "RULE-REST-04", "min_rest_hours", 14, 13, "priya", "b", AT)
    assert sorted((c["from"], c["to"]) for c in rules.rule_changes(table)) == [(12, 13), (13, 14)]


@pytest.mark.parametrize("expected", [None, "12", True])
def test_the_replaced_value_must_be_a_number(table, expected):
    with pytest.raises(rules.RuleError) as err:
        rules.set_threshold(table, "RULE-REST-04", "min_rest_hours", 13, expected, "p", "x", AT)
    assert err.value.code == "INVALID"


def test_only_a_super_admin_may_relax_a_threshold(table):
    rules.set_threshold(table, "RULE-REST-04", "min_rest_hours", 14, 12, "priya", "review", AT)
    # Back from 14h to 13h rest is less strict: refused without the right to relax.
    with pytest.raises(rules.RuleError) as err:
        rules.set_threshold(table, "RULE-REST-04", "min_rest_hours", 13, 14, "priya", "x", AT)
    assert err.value.code == "FORBIDDEN" and "only a super admin" in err.value.message
    rules.set_threshold(
        table, "RULE-REST-04", "min_rest_hours", 13, 14, "vikram", "rota", AT, may_relax=True
    )
    # Directions differ by rule: for a limit, a higher number is the relaxation.
    duty = next(t for t in rules.THRESHOLDS if t.param == "max_duty_hours")
    rest = next(t for t in rules.THRESHOLDS if t.param == "min_rest_hours")
    assert rules.relaxes(duty, 55, 58) and not rules.relaxes(duty, 58, 55)
    assert rules.relaxes(rest, 14, 13) and not rules.relaxes(rest, 13, 14)
    assert not rules.relaxes(rest, 13, 13)
