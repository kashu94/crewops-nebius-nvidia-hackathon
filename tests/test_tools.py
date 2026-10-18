"""What each tool answers, checked against the engine and the seed week."""

from datetime import date, timedelta

import pytest

from crewops.domain import Rank, Role
from crewops.engine import assess, build_world, find_options
from crewops.engine.impact import delay_effect
from crewops.ingestion.seed import load_seed
from crewops.tools import Caller, CrewOpsTools, dispatch
from crewops.tools.resolve import digit_prefix_matches, resolve, transpositions

CONTROLLER = Caller("u-ctl", frozenset({Role.CONTROLLER}))


@pytest.fixture(scope="module")
def seed():
    return load_seed()


@pytest.fixture(scope="module")
def tools(seed):
    return CrewOpsTools(build_world(seed), seed)


def ok(tools, name, **args):
    r = dispatch(tools, CONTROLLER, name, args)
    assert r.ok, r.error
    return r.result


def err(tools, name, **args):
    r = dispatch(tools, CONTROLLER, name, args)
    assert not r.ok, r.result
    return r.error_code, r.error


# -- Did you mean, never substitute ------------------------------------------------------


def test_transposed_id_is_a_question_not_an_answer():
    universe = {"C-1042": "A. Nair"}
    result = resolve("crew", "C-1024", universe, lambda name: name)
    assert not result.exists and result.likely[0].value == "C-1042"
    assert result.error().code == "NEEDS_CONFIRMATION"
    assert "C-1042 (A. Nair)" in result.message() and "will not guess" in result.message()


def test_far_id_lists_nothing_as_a_typo():
    result = resolve("crew", "C-9999", {"C-1042": None, "C-1017": None})
    assert result.error().code == "UNRESOLVED_ENTITY"
    assert "nothing close" in result.message()


def test_helpers():
    assert "C-1024" in transpositions("C-1042") and "C-1042" not in transpositions("C-1042")
    assert digit_prefix_matches("C-10", ["C-1042", "C-1017", "C-2087"], 3) == ["C-1017", "C-1042"]
    assert digit_prefix_matches("C-", ["C-1042"], 3) == []


def test_suggest_tools_list_real_ids_only(tools):
    crew = ok(tools, "suggest_crew_ids", near="C-10")["suggestions"]
    assert crew and all(s["crew_id"].startswith("C-10") for s in crew)
    assert ok(tools, "suggest_crew_ids", near="C-1024")["suggestions"][0]["crew_id"] == "C-1042"
    pairings = ok(tools, "suggest_pairing_ids", near="P-22", limit=2)["suggestions"]
    assert len(pairings) == 2 and pairings[0]["pairing_id"].startswith("P-22")
    flights = ok(tools, "suggest_flight_nos", near="DX9999")["suggestions"]
    assert all(
        f["flight_no"] in {x.flight_no for x in tools.world.flights.values()} for f in flights
    )


# -- lookup ------------------------------------------------------------------------------


def test_count_is_exact_and_filters_combine(tools, seed):
    out = ok(tools, "lookup", entity="crew", filters={"base": "blr", "rank": "captain"})
    expected = [c for c in seed.crew if c.base == "BLR" and c.rank is Rank.CAPTAIN]
    assert out["count"] == len(expected) == len(out["rows"])


def test_group_by_counts(tools, seed):
    groups = ok(tools, "lookup", entity="crew", group_by=["rank", "base"])["groups"]
    assert sum(g["count"] for g in groups) == len(seed.crew)
    blr_captains = next(g for g in groups if g["rank"] == "Captain" and g["base"] == "BLR")
    assert blr_captains["count"] == 27


def test_sort_and_limit_keep_ties_visible(tools):
    out = ok(tools, "lookup", entity="crew", sort_by="reachability_minutes", limit=3)
    assert out["count"] == 150 and len(out["rows"]) == 3
    assert [r["reachability_minutes"] for r in out["rows"]] == [15, 15, 20]
    top = ok(tools, "lookup", entity="risk_signals", sort_by="risk", sort_desc=True, limit=1)
    assert top["rows"][0]["crew_id"] == "C-1042" and top["rows"][0]["disruption_risk_score"] == 0.78


def test_ranges_lists_and_containment(tools):
    expiring = ok(
        tools,
        "lookup",
        entity="certifications",
        filters={"valid_to": {"gte": "2026-10-12", "lte": "2026-10-18"}},
    )
    assert {r["crew_id"] for r in expiring["rows"]} >= {"C-2087", "C-5417"}
    assert all("2026-10-12" <= r["valid_to"] <= "2026-10-18" for r in expiring["rows"])
    atr = ok(tools, "lookup", entity="crew", filters={"ratings": "ATR72", "rank": "Captain"})
    assert all("ATR72" in r["ratings"] for r in atr["rows"]) and atr["count"] > 0
    both = ok(tools, "lookup", entity="crew", filters={"base": ["BLR", "DEL"]})
    assert both["count"] == 150


def test_date_on_pairings_means_flies_that_day(tools):
    out = ok(
        tools,
        "lookup",
        entity="pairing_crew",
        filters={"aircraft": "VT-DXB", "date": "2026-10-14", "role": "Senior Cabin Crew"},
    )
    assert out["count"] == 1 and out["rows"][0]["pairing_id"] == "P-2210"


def test_lookup_errors_name_what_is_valid(tools):
    code, message = err(tools, "lookup", entity="crew", filters={"colour": "red"})
    assert code == "UNRESOLVED_ENTITY" and "reachability_minutes" in message
    code, message = err(tools, "lookup", entity="crew", filters={"seniority": {"over": 10}})
    assert code == "INVALID_ARGUMENT" and "gte" in message
    code, _ = err(tools, "lookup", entity="crew", sort_by="height")
    assert code == "UNRESOLVED_ENTITY"


def test_empty_result_for_a_real_id_is_an_answer(tools):
    out = ok(tools, "lookup", entity="crew", filters={"crew_id": "C-1042", "base": "DEL"})
    assert out["count"] == 0


def test_empty_result_for_a_wrong_id_or_name_asks(tools):
    code, message = err(tools, "lookup", entity="crew", filters={"crew_id": "C-1024"})
    assert code == "NEEDS_CONFIRMATION" and "C-1042" in message
    code, message = err(tools, "lookup", entity="crew", filters={"name": "Nayar"})
    assert code == "NEEDS_CONFIRMATION" and "A. Nair (C-1042, Captain)" in message
    assert ok(tools, "lookup", entity="crew", filters={"name": "Nair"})["count"] == 7


def test_long_results_are_capped_but_counted(tools, seed):
    out = ok(tools, "lookup", entity="certifications")
    assert out["count"] == len(seed.certifications) and len(out["rows"]) == 60
    assert out["truncated"] is True and "count is exact" in out["note"]


# -- Flights and dates -------------------------------------------------------------------


def test_flight_number_needs_a_date(tools):
    code, message = err(tools, "check_gate", flight_no="DX401")
    assert code == "AMBIGUOUS_QUERY" and "2026-10-13" in message
    code, message = err(tools, "check_gate", flight_no="DX401", date="2026-10-23")
    assert code == "UNRESOLVED_ENTITY" and "flies on" in message
    code, message = err(tools, "check_gate", flight_no="DX999", date="2026-10-13")
    assert code == "UNRESOLVED_ENTITY" and "no flight DX999" in message


def test_impossible_date_is_refused(tools):
    code, _ = err(tools, "duty_clock", crew_id="C-1042", date="2026-09-31")
    assert code == "INVALID_ARGUMENT"


# -- duty_clock and notification_brief ---------------------------------------------------


def test_duty_clock_matches_the_engine(tools):
    out = ok(tools, "duty_clock", crew_id="C-1042", date="2026-10-12")
    crew = tools.world.crew["C-1042"]
    assert out["duty_hours_7d"] == crew.duty_in_window(date(2026, 10, 6), date(2026, 10, 12))
    assert out["duty_headroom_7d"] == round(60 - out["duty_hours_7d"], 2)
    assert out["flight_window"] == "2026-09-15 to 2026-10-12"
    code, message = err(tools, "duty_clock", crew_id="C-1042", date="2025-01-01")
    assert code == "UNRESOLVED_ENTITY" and "outside" in message


def test_duty_clock_counts_rostered_days(tools):
    out = ok(tools, "duty_clock", crew_id="C-1042", date="2026-10-13")
    assert out["rostered_this_date"] is True and out["duty_hours_this_date"] == 9.5


def test_notification_brief_reads_the_roster(tools):
    brief = ok(tools, "notification_brief", crew_id="C-3310", pairing_id="P-2291")
    assert brief["report_utc"] == "2026-10-13T06:00:00Z"
    assert brief["reachability_minutes"] == 30
    assert brief["acknowledge_by_utc"] == "2026-10-13T05:30:00Z"  # report less the reach time
    first, last = brief["days"][0], brief["days"][-1]
    assert first["report_station"] == "BLR" and first["overnight_station"] == "DEL"
    assert first["flights"][0].startswith("KS412 BLR-BOM 07:00Z")
    assert last["overnight_station"] is None
    assert brief["rostered_on_pairing"] is False and brief["role"] == "Captain"


# -- check_legality ----------------------------------------------------------------------


def test_legality_matches_assess(tools):
    out = ok(tools, "check_legality", crew_id="C-3310", pairing_id="P-2291")
    a = assess(tools.world, "C-3310", "P-2291")
    assert out["legal"] is a.legal is True
    assert len(out["verdicts"]) == len(a.verdicts)


def test_legality_by_aircraft_and_date_uses_the_crews_own_duty(tools):
    out = ok(tools, "check_legality", crew_id="C-5417", aircraft="VT-DXB", date="2026-10-17")
    assert out["pairing_id"] == "P-2213" and out["legal"] is False
    assert out["blocking"][0]["rule_id"] == "RULE-CERT-06"


def test_delay_on_a_named_flight_lands_on_its_day(tools):
    out = ok(
        tools,
        "check_legality",
        crew_id="C-1042",
        flight_id="KS589-2026-10-14",
        delay_h=1,
    )
    assert out["pairing_id"] == "P-2291" and out["delay_date"] == "2026-10-14"
    code, message = err(
        tools,
        "check_legality",
        crew_id="C-1042",
        pairing_id="P-2291",
        delay_h=1,
        delay_date="2026-10-17",
    )
    assert code == "INVALID_ARGUMENT" and "no duty day" in message


# -- find_options ------------------------------------------------------------------------


def test_cover_for_a_named_crew_member(tools):
    out = ok(tools, "find_options", crew_id="C-1042")
    engine = find_options(tools.world, "P-2291", Rank.CAPTAIN)
    assert (
        out["pairing_id"] == "P-2291" and out["role"] == "Captain" and out["crew_out"] == "C-1042"
    )
    assert out["recommended"]["crew_id"] == engine.recommended.crew_id == "C-3310"
    # Reserve callout 18,500 + seniority 9 x 450 + travel 30 min = 3 x 180.
    assert out["recommended"]["cost_inr"] == 18500 + 4050 + 540
    assert out["recommended"]["cost_breakdown"] == {
        "callout": 18500,
        "seniority_allowance": 4050,
        "travel": 540,
    }
    assert out["legal_options"] == len(engine.crew_options)
    assert out["cancellation"]["crew_id"] is None
    assert out["funnel"][0]["stage"] == "considered"
    assert out["excluded_count"] == len(engine.excluded)


def test_crew_on_several_pairings_needs_a_date(tools):
    code, message = err(tools, "find_options", crew_id="C-5837")
    assert code == "AMBIGUOUS_QUERY" and "P-2204" in message and "P-2207" in message
    day = tools.world.pairings["P-2204"].days[0].date.isoformat()
    assert ok(tools, "find_options", crew_id="C-5837", date=day)["pairing_id"] == "P-2204"


def test_role_must_match_the_seat_of_who_is_out(tools):
    code, message = err(tools, "find_options", crew_id="C-1042", role="Cabin Crew")
    assert code == "INVALID_ARGUMENT" and "as Captain" in message


def test_seat_needs_a_role_or_a_crew_member(tools):
    code, _ = err(tools, "find_options", pairing_id="P-2291")
    assert code == "UNRESOLVED_ENTITY"
    assert ok(tools, "find_options", pairing_id="P-2291", role="Captain")["crew_out"] is None


def test_started_pairing_cannot_be_recrewed(tools):
    code, message = err(tools, "find_options", pairing_id="P-2201", role="Captain")
    assert code == "ALREADY_STARTED" and "before now" in message


# -- same_pairing ------------------------------------------------------------------------


def test_same_pairing_by_id_and_by_name(tools):
    out = ok(tools, "same_pairing", a="Captain A. Nair", b="C-1694")
    assert out["same_pairing"] is True and out["shared_pairings"] == ["P-2291"]
    assert out["crew_a"]["crew_id"] == "C-1042"
    out = ok(tools, "same_pairing", a="C-1042", b="C-3310")
    assert out["same_pairing"] is False and out["crew_b"]["pairings"] == []


def test_same_pairing_asks_when_a_name_is_shared(tools):
    code, message = err(tools, "same_pairing", a="Nair", b="C-3310")
    assert code == "AMBIGUOUS_QUERY" and "C-1042" in message and "C-3145" in message
    code, _ = err(tools, "same_pairing", a="C-1042", b="Captain A. Nair")
    assert code == "INVALID_ARGUMENT"


# -- ripple, simulate, joint_plan --------------------------------------------------------


def test_ripple_for_a_crew_member_on_one_day(tools):
    out = ok(tools, "ripple", event={"crew_id": "C-1042", "date": "2026-10-13"})
    (pairing,) = out["affected_pairings"]
    assert pairing["directly_affected_flights"] == [
        "KS412-2026-10-13",
        "KS413-2026-10-13",
        "KS588-2026-10-13",
    ]
    booked = sum(tools.world.flights[f].booked for f in pairing["directly_affected_flights"])
    assert len(pairing["at_risk_flights"]) == 3 and out["passengers"] == booked == 397
    assert "desks" not in out  # no desk owns an aircraft: disruptions are assigned by load


def test_ripple_for_crew_without_a_date_covers_every_remaining_pairing(tools):
    out = ok(tools, "ripple", event={"crew_id": "C-5837"})
    assert [p["pairing_id"] for p in out["affected_pairings"]] == ["P-2204", "P-2207"]


def test_ripple_for_a_station_window_hits_only_flights_inside_it(tools):
    out = ok(
        tools,
        "ripple",
        event={
            "station": "BLR",
            "from_utc": "2026-10-14T06:00:00Z",
            "to_utc": "2026-10-14T10:00:00Z",
        },
    )
    p2291 = next(p for p in out["affected_pairings"] if p["pairing_id"] == "P-2291")
    assert p2291["directly_affected_flights"] == ["KS589-2026-10-14", "KS590-2026-10-14"]
    assert p2291["at_risk_flights"] == ["KS591-2026-10-14"]
    for p in out["affected_pairings"]:
        assert not set(p["directly_affected_flights"]) & set(p["at_risk_flights"])


def test_ripple_errors(tools):
    assert err(tools, "ripple", event={})[0] == "UNRESOLVED_ENTITY"
    assert err(tools, "ripple", event={"station": "BLR"})[0] == "AMBIGUOUS_QUERY"
    assert err(tools, "ripple", event={"aircraft": "VT-XYZ", "date": "2026-10-14"})[0] == (
        "UNRESOLVED_ENTITY"
    )
    code, _ = err(
        tools,
        "ripple",
        event={
            "station": "BLR",
            "from_utc": "2026-10-14T10:00:00Z",
            "to_utc": "2026-10-14T06:00:00Z",
        },
    )
    assert code == "INVALID_ARGUMENT"


def test_simulate_matches_delay_effect(tools):
    out = ok(
        tools, "simulate", event={"flight_no": "DX402", "date": "2026-10-13", "delay_hours": 3}
    )
    effect = delay_effect(tools.world, "P-2202", date(2026, 10, 13), 3, "DX402-2026-10-13")
    assert out["pairing_id"] == "P-2202" and out["from_flight"] == "DX402-2026-10-13"
    assert out["duty_breaches_limit"] is effect.duty_breaches_limit is True
    assert out["legs_to_drop"] == list(effect.legs_to_drop)
    assert out["fdp_hours_after"] == round(out["fdp_hours_before"] + 3, 2)
    assert {c["crew_id"] for c in out["changed"]} == {b.crew_id for b in effect.breaches}


def test_joint_plan_never_reuses_someone_named_as_out(tools):
    out = ok(
        tools,
        "joint_plan",
        events=[{"crew_id": "C-1042"}, {"pairing_id": "P-2204", "role": "Captain"}],
    )
    ids = [a["crew_id"] for a in out["assignments"]]
    assert len(ids) == len(set(ids)) and "C-1042" not in ids
    single = find_options(tools.world, "P-2291", Rank.CAPTAIN).recommended
    assert out["assignments"][0]["crew_id"] == single.crew_id
    assert out["total_cost_inr"] == sum(a["cost_inr"] for a in out["assignments"])


def test_joint_plan_reports_started_seats(tools):
    out = ok(
        tools,
        "joint_plan",
        events=[{"crew_id": "C-1042"}, {"pairing_id": "P-2201", "role": "Captain"}],
    )
    assert out["already_started"] == [{"pairing_id": "P-2201", "role": "Captain"}]
    assert out["unfilled"] == []  # listed once, under already_started


# -- check_gate --------------------------------------------------------------------------


def test_gate_claim_is_checked(tools):
    out = ok(
        tools, "check_gate", flight_no="DX401", date="2026-10-13", boarding_gate_number="BLR-G2"
    )
    assert out["actual_boarding_gate_number"] == "BLR-G4" and out["gate_match"] is False


def test_delay_into_the_next_boarding_is_a_conflict(tools):
    out = ok(tools, "check_gate", flight_id="NG453-2026-10-12", delay_minutes=60)
    assert out["gate_conflict"]["flight_id"] == "DX403-2026-10-12"
    assert out["gate_conflict"]["overlap_minutes"] == 15.0
    assert (
        ok(tools, "check_gate", flight_id="NG453-2026-10-12", delay_minutes=30)["gate_conflict"]
        is None
    )


def test_gate_occupancy_and_inventory(tools):
    slot = next(g for g in tools.gates if g.gate == "BLR-G1")
    at = (slot.start_utc + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = ok(tools, "check_gate", boarding_gate_number="BLR-G1", at_utc=at)
    assert out["occupied"] is True and out["occupying_flight_id"] == slot.flight_id
    inventory = ok(tools, "check_gate")
    assert inventory["total_boarding_gates"] == 13 and inventory["gates_by_station"]["BLR"] == 6
    assert ok(tools, "check_gate", station="BLR", date="2026-10-13")["occupied_boarding_gates"] == 6
    assert err(tools, "check_gate", boarding_gate_number="BLR-G9")[0] == "UNRESOLVED_ENTITY"
    assert err(tools, "check_gate", date="2026-10-29")[0] == "UNRESOLVED_ENTITY"


# -- Rules -------------------------------------------------------------------------------


def test_explain_rule(tools):
    out = ok(tools, "explain_rule", rule_id="RULE-FDP-01")
    assert out["fdp_limit_by_sectors"]["4"] == 12.0 and "13h" in out["text"]
    code, message = err(tools, "explain_rule", rule_id="RULE-REST-40")
    assert code == "NEEDS_CONFIRMATION" and "RULE-REST-04" in message


def test_search_rules_falls_back_to_keywords(tools):
    out = ok(tools, "search_rules", query="minimum rest between release and next report")
    assert out["method"] == "keyword" and out["candidates"][0]["rule_id"] == "RULE-REST-04"
    assert ok(tools, "search_rules", query="zzz qqq")["candidates"] == []


def test_search_rules_uses_vector_search_when_connected(seed):
    from test_search import BUCKET, FakeS3Vectors, fake_embed

    from crewops.search.vectors import RULES, VectorSearch, rule_documents

    search = VectorSearch(FakeS3Vectors(), BUCKET, fake_embed)
    search.put(
        RULES,
        rule_documents({"rules": [{"rule_id": r.rule_id, "text": r.text} for r in seed.rules]}),
    )
    tools = CrewOpsTools(build_world(seed), seed, search=search)
    out = ok(tools, "search_rules", query="minimum rest between release and next report", top_k=2)
    assert out["method"] == "hybrid" and len(out["candidates"]) == 2
    top = out["candidates"][0]
    assert top["rule_id"] == "RULE-REST-04" and "rest" in top["text"].lower()
    assert -1.0 <= top["semantic_similarity"] <= 1.0


# -- Desks -------------------------------------------------------------------------------


def test_list_controllers(tools):
    desks = ok(tools, "list_controllers")["desks"]
    assert [d["controller"] for d in desks] == ["Ananya Iyer", "Rohit Malhotra", "Divya Rao"]
    assert desks[1] == {"desk_id": "desk-2", "controller": "Rohit Malhotra"}  # no aircraft owned
    assert "fewest open" in ok(tools, "list_controllers")["assignment"]
    assert not {d["controller"] for d in desks} & {c.name for c in tools.world.crew.values()}


def test_issue_counts_need_the_store(tools):
    assert err(tools, "controller_issue_counts")[0] == "UNAVAILABLE"


# -- Regressions -----------------------------------------------------------------------


def test_a_stated_rank_is_never_swapped_for_another(tools):
    code, message = err(tools, "same_pairing", a="Captain S. Nair", b="C-1042")
    assert code == "NEEDS_CONFIRMATION" and "C-2295 (S. Nair, First Officer)" in message


def test_joint_plan_never_staffs_one_seat_twice(tools):
    # C-1042 reported out on 13 and on 14 Oct: both days of P-2291, one captain seat. One
    # cover flies the whole trip, so it is one assignment, the same as covering it alone.
    single = find_options(tools.world, "P-2291", Rank.CAPTAIN).recommended
    for events in (
        [{"crew_id": "C-1042"}, {"crew_id": "C-1042"}],
        [{"crew_id": "C-1042", "date": "2026-10-13"}, {"crew_id": "C-1042", "date": "2026-10-14"}],
    ):
        out = ok(tools, "joint_plan", events=events)
        assert [(a["pairing_id"], a["crew_id"]) for a in out["assignments"]] == [
            ("P-2291", single.crew_id)
        ]
    # A seat given without who is out cannot be matched, so asking for it twice is refused.
    seat = {"pairing_id": "P-2291", "role": "Captain"}
    code, message = err(tools, "joint_plan", events=[seat, seat])
    assert code == "INVALID_ARGUMENT" and "has 1 Captain seat" in message
    cabin = {"pairing_id": "P-2291", "role": "Cabin Crew"}
    assert ok(tools, "joint_plan", events=[cabin, cabin])  # three cabin seats on P-2291
    code, _ = err(tools, "joint_plan", events=[{"crew_id": "C-1042", "role": "Cabin Crew"}, seat])
    assert code == "INVALID_ARGUMENT"


def test_ranges_and_lists_on_list_fields(tools, seed):
    late = ok(tools, "lookup", entity="pairings", filters={"date": {"gte": "2026-10-17"}})
    expected = [p for p in seed.pairings if any(d.date >= date(2026, 10, 17) for d in p.days)]
    assert late["count"] == len(expected) > 0
    either = ok(tools, "lookup", entity="crew", filters={"ratings": ["A320", "ATR72"]})
    assert either["count"] == 150


@pytest.mark.parametrize(
    "filters",
    [
        {"ratings": []},
        {"seniority": [{}]},
        {"seniority": [{"foo": 1}]},
        {"base": None},
        {"seniority": {"gte": [1]}},
        {"days": "2026-10-13"},
    ],
)
def test_filters_that_would_crash_or_match_everything_are_refused(tools, filters):
    entity = "pairings" if "days" in filters else "crew"
    assert err(tools, "lookup", entity=entity, filters=filters)[0] in (
        "INVALID_ARGUMENT",
        "UNRESOLVED_ENTITY",
    )


def test_nested_fields_cannot_be_grouped_or_sorted(tools):
    for group in (["crew"], ["days"]):
        assert err(tools, "lookup", entity="pairings", group_by=group)[0] == "INVALID_ARGUMENT"
    assert err(tools, "lookup", entity="reserves", sort_by="oncall_window_utc")[0] == (
        "INVALID_ARGUMENT"
    )


def test_unknown_values_in_id_filters_are_errors(tools):
    code, message = err(tools, "lookup", entity="flights", filters={"flight_no": "DX999"})
    assert code == "UNRESOLVED_ENTITY" and "flight number" in message
    assert err(tools, "lookup", entity="crew", filters={"crew_id": ["C-9999"]})[0] == (
        "UNRESOLVED_ENTITY"
    )
    code, message = err(tools, "lookup", entity="crew", filters={"crew_id": "c-1024"})
    assert code == "NEEDS_CONFIRMATION" and "C-1042" in message
    assert err(tools, "lookup", entity="pairings", filters={"aircraft": "VT-DXZ"})[0] == (
        "UNRESOLVED_ENTITY"
    )


def test_lower_case_ids_are_the_same_ids(tools):
    assert ok(tools, "duty_clock", crew_id="c-1042")["crew_id"] == "C-1042"
    assert ok(tools, "explain_rule", rule_id="rule-rest-04")["rule_id"] == "RULE-REST-04"


def test_ripple_on_a_flight_hits_that_flight_and_puts_later_ones_at_risk(tools):
    out = ok(tools, "ripple", event={"flight_id": "DX402-2026-10-13"})
    (row,) = out["affected_pairings"]
    assert row["directly_affected_flights"] == ["DX402-2026-10-13"]
    assert row["at_risk_flights"] == ["DX403-2026-10-13", "DX404-2026-10-13"]
    assert out["passengers"] == tools.world.flights["DX402-2026-10-13"].booked == 134


def test_ripple_leaves_out_flights_already_flown(tools):
    out = ok(tools, "ripple", event={"flight_id": "DX401-2026-10-12"})
    assert out["affected_pairings"] == [] and out["passengers"] == 0
    assert "DX401-2026-10-12" in out["already_departed"] and "already departed" in out["note"]
    out = ok(tools, "ripple", event={"crew_id": "C-3983"})
    assert out["affected_pairings"] == [] and "no flights left" in out["note"]


def test_ripple_names_one_disruption_with_a_time(tools):
    code, _ = err(tools, "ripple", event={"pairing_id": "P-2291", "crew_id": "C-9999"})
    assert code == "INVALID_ARGUMENT"
    assert err(tools, "ripple", event={"aircraft": "VT-DXA"})[0] == "AMBIGUOUS_QUERY"
    code, _ = err(tools, "ripple", event={"crew_id": "C-1042", "from_utc": "2026-10-13T00:00:00Z"})
    assert code == "INVALID_ARGUMENT"


def test_gate_schedule_for_a_date(tools):
    out = ok(tools, "check_gate", boarding_gate_number="BLR-G1", date="2026-10-14")
    assert out["date"] == "2026-10-14" and "occupied" not in out
    assert out["schedule"] and all(s["flight_id"].endswith("2026-10-14") for s in out["schedule"])
    assert err(tools, "check_gate", boarding_gate_number="BLR-G1", delay_minutes=30)[0] == (
        "INVALID_ARGUMENT"
    )


def test_conflicting_role_with_pairing_and_crew_is_refused(tools):
    code, _ = err(tools, "find_options", pairing_id="P-2291", crew_id="C-1042", role="Cabin Crew")
    assert code == "INVALID_ARGUMENT"


def test_a_wrong_id_among_real_ones_is_not_dropped(tools):
    code, _ = err(tools, "lookup", entity="crew", filters={"crew_id": ["C-1042", "C-9999"]})
    assert code == "UNRESOLVED_ENTITY"
    assert err(tools, "lookup", entity="flights", filters={"flight_no": ["DX401", "DX999"]})[0] == (
        "UNRESOLVED_ENTITY"
    )
    assert err(tools, "lookup", entity="crew", filters={"crew_id": 5})[0] == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    "filters",
    [
        {"ratings": {"gte": 3}},
        {"reachability_minutes": {"lte": "60 minutes"}},
        {"seniority": {"gte": True}},
    ],
)
def test_ranges_mixing_numbers_and_text_are_refused(tools, filters):
    assert err(tools, "lookup", entity="crew", filters=filters)[0] == "INVALID_ARGUMENT"


def test_numeric_text_bounds_still_work(tools):
    out = ok(tools, "lookup", entity="crew", filters={"reachability_minutes": {"lte": "45"}})
    assert out["count"] > 0 and all(r["reachability_minutes"] <= 45 for r in out["rows"])


def test_gate_and_a_different_station_is_refused(tools):
    code, _ = err(tools, "check_gate", boarding_gate_number="BLR-G1", station="DEL")
    assert code == "INVALID_ARGUMENT"
