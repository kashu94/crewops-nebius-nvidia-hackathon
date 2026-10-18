"""Tool schemas, argument checks, role checks and dispatch: a call never raises."""

import json

import pytest

from crewops.domain import Role
from crewops.engine import build_world
from crewops.ingestion.seed import load_seed
from crewops.tools import (
    TOOL_NAMES,
    TOOL_SCHEMAS,
    Caller,
    CrewOpsTools,
    allowed_tools,
    bedrock_tool_config,
    dispatch,
    validate_args,
)
from crewops.tools.errors import ToolError
from crewops.tools.rows import ENTITY_FIELDS

CONTROLLER = Caller("u-ctl", frozenset({Role.CONTROLLER}))


@pytest.fixture(scope="module")
def tools():
    seed = load_seed()
    return CrewOpsTools(build_world(seed), seed)


def call(tools, name, caller=CONTROLLER, **args):
    return dispatch(tools, caller, name, args)


# -- Schemas -----------------------------------------------------------------------------


def test_every_tool_has_an_implementation_and_a_strict_schema(tools):
    assert len(TOOL_SCHEMAS) == len(TOOL_NAMES) == 18
    for t in TOOL_SCHEMAS:
        assert callable(getattr(tools, t["name"]))
        schema = t["input_schema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema.get("required", ())) <= set(schema["properties"])
        assert t["description"].strip()


def test_lookup_describes_every_field_it_returns(tools):
    description = next(t for t in TOOL_SCHEMAS if t["name"] == "lookup")["description"]
    for entity, fields in ENTITY_FIELDS.items():
        assert f"{entity}: {', '.join(fields)}" in description
        for row in tools.rows[entity]:
            assert tuple(row) == fields, entity


def test_bedrock_tool_config_shape():
    config = bedrock_tool_config({"lookup", "explain_rule"})
    specs = [t["toolSpec"] for t in config["tools"]]
    assert [s["name"] for s in specs] == ["lookup", "explain_rule"]
    assert specs[0]["inputSchema"]["json"]["required"] == ["entity"]
    assert len(bedrock_tool_config()["tools"]) == 18
    json.dumps(bedrock_tool_config())  # what is sent to Bedrock must serialise


# -- Argument checks ---------------------------------------------------------------------


def test_constructed_id_is_refused_with_a_hint(tools):
    r = call(tools, "find_options", pairing_id="BLR->BOM")
    assert r.error_code == "UNRESOLVED_ENTITY"
    assert "suggest_pairing_ids" in r.error and "constructing" in r.error


@pytest.mark.parametrize(
    ("name", "args", "fragment"),
    [
        ("lookup", {"entity": "aircraft"}, "is not one of"),
        ("lookup", {}, "'entity' is required"),
        ("lookup", {"entity": "crew", "colour": "red"}, "unknown 'colour'"),
        ("lookup", {"entity": "crew", "limit": 0}, "at least 1"),
        ("lookup", {"entity": "crew", "limit": True}, "expected integer"),
        ("lookup", {"entity": "crew", "group_by": "rank"}, "expected array"),
        ("check_legality", {"crew_id": "C-1042", "delay_h": -1}, "at least 0"),
        ("simulate", {"event": {"pairing_id": "P-2291", "delay_hours": 0}}, "more than 0"),
        ("simulate", {"event": {"pairing_id": "P-2291"}}, "'delay_hours' is required"),
        ("ripple", {"event": {"kind": "SICK"}}, "unknown 'kind'"),
        ("joint_plan", {"events": [{"crew_id": "C-1042"}]}, "at least 2 items"),
        ("same_pairing", {"a": "", "b": "C-1042"}, "at least 1 characters"),
    ],
)
def test_bad_arguments_are_named(tools, name, args, fragment):
    r = dispatch(tools, CONTROLLER, name, args)
    assert not r.ok and fragment in r.error, r.error


def test_nested_id_patterns_are_checked():
    with pytest.raises(ToolError, match=r"joint_plan.events\[1\].crew_id"):
        validate_args("joint_plan", {"events": [{"crew_id": "C-1042"}, {"crew_id": "C-10"}]})


def test_nulls_are_treated_as_absent(tools):
    r = call(tools, "duty_clock", crew_id="C-1042", date=None)
    assert r.ok and r.result["as_of"] == "2026-10-12"


def test_arguments_must_be_an_object(tools):
    r = dispatch(tools, CONTROLLER, "lookup", ["crew"])
    assert r.error_code == "INVALID_ARGUMENT"


# -- Roles -------------------------------------------------------------------------------


def test_staff_see_every_tool_and_others_see_less():
    assert allowed_tools(CONTROLLER) == TOOL_NAMES
    assert allowed_tools(Caller("a", frozenset({Role.AUDITOR}))) == TOOL_NAMES
    admin = allowed_tools(Caller("a", frozenset({Role.ADMIN})))
    assert "controller_issue_counts" in admin and "lookup" not in admin
    reporter = allowed_tools(Caller("r", frozenset({Role.REPORTER})))
    assert "check_gate" in reporter and "find_options" not in reporter
    assert allowed_tools(Caller("x", frozenset())) == frozenset()


def test_crew_may_ask_only_about_themselves(tools):
    me = Caller("u-crew", frozenset({Role.CREW}), crew_id="C-1042")
    assert call(tools, "duty_clock", me, crew_id="C-1042").ok
    other = call(tools, "duty_clock", me, crew_id="C-3310")
    assert other.error_code == "FORBIDDEN" and "themselves" in other.error
    assert call(tools, "find_options", me, crew_id="C-1042").error_code == "FORBIDDEN"
    assert call(tools, "explain_rule", me, rule_id="RULE-REST-04").ok
    no_id = Caller("u-crew2", frozenset({Role.CREW}))
    assert call(tools, "duty_clock", no_id, crew_id="C-1042").error_code == "FORBIDDEN"


def test_crew_who_is_also_staff_is_not_limited(tools):
    both = Caller("u", frozenset({Role.CREW, Role.CONTROLLER}), crew_id="C-1042")
    assert call(tools, "duty_clock", both, crew_id="C-3310").ok


def test_admin_cannot_read_crew_records(tools):
    admin = Caller("u-adm", frozenset({Role.ADMIN}))
    assert call(tools, "lookup", admin, entity="crew").error_code == "FORBIDDEN"


def test_a_forbidden_caller_learns_nothing_about_ids(tools):
    reporter = Caller("u-rep", frozenset({Role.REPORTER}))
    r = call(tools, "lookup", reporter, entity="crew", filters={"crew_id": "C-1024"})
    assert r.error_code == "FORBIDDEN"  # no "did you mean C-1042 (A. Nair...)" for them


# -- Dispatch ----------------------------------------------------------------------------


def test_unknown_tool(tools):
    r = call(tools, "commit_decision")
    assert r.error_code == "UNRESOLVED_ENTITY" and "unknown tool" in r.error


def test_a_tool_bug_becomes_an_internal_error(tools, monkeypatch):
    def broken(**_):
        raise KeyError("boom")

    monkeypatch.setattr(tools, "explain_rule", broken)
    r = call(tools, "explain_rule", rule_id="RULE-REST-04")
    assert r.error_code == "INTERNAL" and "KeyError" in r.error


def test_trace_entry_shapes(tools):
    good = call(tools, "explain_rule", rule_id="RULE-REST-04")
    assert good.to_dict() == {"tool": "explain_rule", "ok": True, "result": good.result}
    bad = call(tools, "explain_rule", rule_id="RULE-REST-40")
    assert bad.to_dict()["ok"] is False and bad.to_dict()["error_code"] == "NEEDS_CONFIRMATION"


def test_every_result_is_json(tools):
    calls = [
        ("lookup", {"entity": "pairings", "limit": 2}),
        ("duty_clock", {"crew_id": "C-1042"}),
        ("notification_brief", {"crew_id": "C-3310", "pairing_id": "P-2291"}),
        ("check_legality", {"crew_id": "C-3310", "pairing_id": "P-2291"}),
        ("find_options", {"crew_id": "C-1042"}),
        ("ripple", {"event": {"crew_id": "C-1042"}}),
        ("simulate", {"event": {"pairing_id": "P-2291", "delay_hours": 2}}),
        ("check_gate", {"boarding_gate_number": "BLR-G1"}),
        ("list_controllers", {}),
    ]
    for name, args in calls:
        r = dispatch(tools, CONTROLLER, name, args)
        assert r.ok, (name, r.error)
        json.dumps(r.result)


def test_did_you_mean_hints_only_name_tools_the_caller_may_use(tools):
    me = Caller("u-crew", frozenset({Role.CREW}), crew_id="C-1042")
    r = call(tools, "duty_clock", me, crew_id="C-10")
    assert r.error_code == "UNRESOLVED_ENTITY" and "suggest_crew_ids" not in r.error
    assert "suggest_crew_ids" in call(tools, "duty_clock", crew_id="C-10").error
