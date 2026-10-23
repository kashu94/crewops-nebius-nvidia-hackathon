"""The verifier: every claim in an answer must come from this turn's tool results."""

import pytest

from crewops.agent.verifier import normalise_clock, verify
from crewops.tools import TraceEntry


def entry(tool, result, args=None):
    return TraceEntry(tool, args or {}, result=result)


OPTIONS = entry(
    "find_options",
    {
        "pairing_id": "P-2291",
        "recommended": {"crew_id": "C-3310", "name": "D. Reddy", "cost_inr": 18500},
        "options": [{"crew_id": "C-1526", "name": "N. Sen", "cost_inr": 24000}],
    },
)
LEGALITY = entry(
    "check_legality",
    {
        "crew_id": "C-2087",
        "name": "R. Iyer",
        "pairing_id": "P-2291",
        "legal": False,
        "verdicts": [
            {"rule_id": "RULE-DUTY-02", "status": "FAIL", "detail": "61.33h"},
            {"rule_id": "RULE-FDP-01", "status": "PASS", "detail": "9.5h of 12.5h"},
        ],
        "report_utc": "2026-10-13T06:00:00Z",
    },
)


def test_sourced_ids_numbers_and_names_pass():
    result = verify("D. Reddy (C-3310) covers P-2291 for ₹18,500.", [OPTIONS])
    assert result.ok, result.summary()


def test_invented_id_or_number_fails():
    assert not verify("C-9999 covers P-2291 for ₹18,500.", [OPTIONS]).ok
    assert not verify("C-3310 covers P-2291 for ₹17,500.", [OPTIONS]).ok


def test_a_difference_of_two_sourced_numbers_is_allowed_and_labelled():
    result = verify("The next option costs ₹5,500 more.", [OPTIONS])
    assert result.ok and result.claims[-1].derivation == "24000 - 18500"


def test_wrong_name_for_a_real_id_fails():
    result = verify("Ananya Reddy (C-3310) covers P-2291.", [OPTIONS])
    assert not result.ok and result.unsupported[0].kind == "crew_name"
    assert not verify("S. Reddy (C-3310) covers P-2291.", [OPTIONS]).ok


def test_rule_attributed_the_wrong_way_fails():
    assert verify("C-2087 breaches RULE-DUTY-02.", [LEGALITY]).ok
    bad = verify("C-2087 breaches RULE-FDP-01.", [LEGALITY])
    assert not bad.ok and bad.unsupported[0].kind == "rule_context"


def test_times_match_their_timestamps():
    assert verify("Report at 06:00Z.", [LEGALITY]).ok
    assert not verify("Report at 07:00Z.", [LEGALITY]).ok
    assert normalise_clock("06:00:00+00:00") == "06:00" and normalise_clock("06:00+05:30") is None


def test_small_prose_numbers_and_calendar_words_are_not_claims():
    assert verify(
        "All 7 rules checked for 13 Oct over the next 30 days; C-2087 breaches RULE-DUTY-02.",
        [LEGALITY],
    ).ok


def test_no_trace_is_never_verified():
    assert not verify("Nothing to report.", []).ok


def test_uncrewed_needs_an_impact_result():
    assert not verify("KS412 is uncrewed.", [LEGALITY]).ok


def test_headcount_must_match_the_lookup_count():
    rows = [{"crew_id": f"C-{1000 + i}", "seniority": 26} for i in range(27)]
    trace = [entry("lookup", {"entity": "crew", "count": 27, "rows": rows})]
    assert verify("There are 27 captains.", trace).ok
    assert not verify("There are 26 captains.", trace).ok


def test_naming_only_some_affected_flights_fails():
    ripple = entry(
        "ripple",
        {
            "affected_pairings": [
                {"directly_affected_flights": ["KS412-2026-10-13", "KS413-2026-10-13"]}
            ]
        },
    )
    assert verify("KS412 and KS413 are affected.", [ripple]).ok
    assert not verify("KS412 is affected.", [ripple]).ok


def test_made_up_flight_shape_fails():
    assert not verify("Put them on flight X134.", [OPTIONS]).ok


# -- Regressions -----------------------------------------------------------------------


def test_full_timestamps_are_checked():
    assert verify("Report at 2026-10-13T06:00:00Z.", [LEGALITY]).ok
    assert not verify("Report at 2100-01-28T23:59Z.", [LEGALITY]).ok
    assert not verify("Report at 2026-10-13T07:00Z.", [LEGALITY]).ok


def test_arithmetic_is_only_within_one_result():
    duty = entry("duty_clock", {"crew_id": "C-1042", "duty_hours_7d": 13.0, "duty_limit_7d": 60.0})
    other = entry("lookup", {"entity": "crew", "count": 60, "rows": []})
    # Only rupee amounts combine; headroom is a field the tool returns itself.
    assert not verify("C-1042 has 47 hours of headroom.", [duty]).ok
    assert not verify("C-1042 has 73 hours.", [other, entry("x", {"v": 13})]).ok
    assert verify("Both together cost 42,500.", [OPTIONS]).ok  # 18500 + 24000, one result
    assert not verify("The options differ by 61,000.", [OPTIONS]).ok


def test_legality_verdicts_and_negation_are_checked():
    assert verify("C-2087 is not legal for P-2291: RULE-DUTY-02 fails.", [LEGALITY]).ok
    assert not verify("C-2087 is legal for P-2291.", [LEGALITY]).ok
    assert not verify("RULE-DUTY-02 is not breached.", [LEGALITY]).ok
    assert verify("RULE-FDP-01 is not breached.", [LEGALITY]).ok
    assert not verify("RULE-DUTY-02: fine.", [LEGALITY]).ok


def test_rule_verdicts_do_not_cross_clauses():
    assert verify("RULE-FDP-01 passes; RULE-DUTY-02 fails.", [LEGALITY]).ok


def test_numbers_come_only_from_results():
    call = TraceEntry("lookup", {"entity": "crew", "limit": 137}, result={"count": 2, "rows": []})
    assert not verify("137 passengers are affected.", [call]).ok
    failed = TraceEntry(
        "duty_clock", {"crew_id": "C-7777"}, error_code="UNRESOLVED_ENTITY", error="no crew C-7777"
    )
    assert verify("There is no crew C-7777.", [failed]).ok
    assert not verify("C-7777 is legal for everything.", [failed]).ok


def test_units_signs_and_durations_are_claims():
    for text in ("C-3310 has 47h of headroom.", "It costs ₹45k.", "A 120min delay.", "-18,500"):
        assert not verify(text, [OPTIONS]).ok, text
    assert verify("It exceeds by 1h20m.", [entry("x", {"detail": "exceeds by 1h20m"})]).ok


def test_names_are_checked_wherever_they_appear():
    assert not verify("Captain R. Sharma will cover.", [OPTIONS]).ok
    assert not verify("C-3310 (R. Sharma) will cover.", [OPTIONS]).ok
    assert not verify("R. Sharma, C-3310 will cover.", [OPTIONS]).ok
    assert verify("C-3310 (D. Reddy) will cover.", [OPTIONS]).ok


def test_flight_hours_are_not_a_headcount():
    trace = [entry("lookup", {"entity": "duty_clocks", "count": 1, "rows": [{"v": 64.27}]})]
    assert verify("64.27 flight hours.", trace).ok


def test_second_round_findings():
    # "No," before the id does not negate the claim that follows it.
    assert not verify("No, C-2087 is legal for P-2291.", [LEGALITY]).ok
    assert not verify("C-2087 can cover P-2291.", [LEGALITY]).ok
    assert not verify("Yes, C-2087 is cleared to fly P-2291.", [LEGALITY]).ok
    assert verify("C-2087 cannot cover P-2291.", [LEGALITY]).ok
    # Spaced Indian units and names in other forms.
    assert not verify("Cancelling costs ₹5 lakh.", [OPTIONS]).ok
    assert not verify("Ravi Reddy will cover.", [OPTIONS]).ok
    assert not verify("Captain Sharma can take it instead.", [OPTIONS]).ok
    assert verify("Captain Reddy can take it.", [OPTIONS]).ok
    # Each rule takes the verdict word after it.
    assert verify("RULE-FDP-01 passes but RULE-DUTY-02 fails.", [LEGALITY]).ok
    # Counts from impact results, and "100 flight (block) hours" is not a headcount.
    ripple = entry("ripple", {"directly_affected_flights": 21, "affected_pairings": []})
    look = entry("lookup", {"entity": "crew", "count": 2, "rows": []})
    assert verify("21 flights are directly affected.", [look, ripple]).ok
    rule = entry("explain_rule", {"rule_id": "RULE-FLT-03", "text": "Max 100 flight (block) hours"})
    assert verify("Max 100 flight (block) hours.", [look, rule]).ok
    # Crew who stop being legal in a what-if.
    sim = entry("simulate", {"changed": [{"crew_id": "C-3187", "legal_after": False}]})
    assert verify("After the delay, C-3187 is no longer legal.", [sim]).ok
    # A list: an id followed by someone else's name is not a pairing.
    nair = entry("x", {"crew_id": "C-1042", "name": "A. Nair"})
    assert verify("Out: C-1042, D. Reddy (C-3310) covers for ₹18,500.", [OPTIONS, nair]).ok
    # Names learned from a did-you-mean.
    err = TraceEntry(
        "duty_clock",
        {},
        error_code="NEEDS_CONFIRMATION",
        error="Did you mean C-1938 (N. Reddy, Captain, BLR)?",
    )
    assert verify("Did you mean N. Reddy (C-1938)?", [err]).ok
    # A controller's name said as the desk tool gave it.
    desks = entry("list_controllers", {"desks": [{"controller": "Ananya Iyer"}]})
    crew = entry("x", {"crew_id": "C-2087", "name": "R. Iyer"})
    assert verify("Ananya Iyer covers it; R. Iyer (C-2087) flies.", [desks, crew]).ok


def test_third_round_findings():
    assert not verify("Captain Reddy (C-1526) is cheapest at ₹18,500.", [OPTIONS]).ok
    assert verify("Captain Reddy (C-3310) is cheapest at ₹18,500.", [OPTIONS]).ok
    assert verify("C-2087 can't take P-2291: RULE-DUTY-02 fails.", [LEGALITY]).ok
    out = entry(
        "find_options",
        {
            "crew_out": "C-1042",
            "pairing_id": "P-2291",
            "recommended": {"crew_id": "C-3310", "name": "D. Reddy"},
        },
    )
    assert verify("C-1042 cannot fly P-2291, so D. Reddy (C-3310) can cover it.", [out]).ok
    assert verify("C-2087 breaches RULE-DUTY-02 on Tuesday the 13th.", [LEGALITY]).ok


FOUND = entry("find_options", {"pairing_id": "P-2291", "legal_options": 4, "options": []})
NONE_FOUND = entry("find_options", {"pairing_id": "P-2291", "legal_options": 0, "options": []})


@pytest.mark.parametrize(
    "draft",
    [
        "Nobody can cover P-2291; cancel it.",
        "There is no legal reserve for P-2291, so cancel it.",
        "For P-2291 no crew is free.",
        "P-2291 cannot be covered.",
        "For P-2291, cancel rather than call anyone out.",
    ],
)
def test_no_cover_and_cancel_verdicts_need_evidence(draft):
    assert not verify(draft, [FOUND]).ok
    other = entry("lookup", {"entity": "pairings", "count": 1, "rows": [{"pairing_id": "P-2291"}]})
    assert not verify(draft, [other]).ok  # no search for cover at all is not evidence either
    assert verify(draft, [NONE_FOUND]).ok


def test_quoting_the_cost_of_cancelling_is_not_advice():
    trace = [
        entry(
            "find_options",
            {"pairing_id": "P-2291", "legal_options": 4, "cancellation": {"cost_inr": 1500000}},
        )
    ]
    assert verify("For P-2291, cancelling instead costs ₹1,500,000.", trace).ok


def test_a_rewording_may_neither_drop_nor_add_a_fact():
    from crewops.agent.verifier import keeps_every_fact

    original = "D. Reddy (C-3310) covers P-2291 for ₹18,500."
    assert keeps_every_fact(original, "For ₹18,500, D. Reddy (C-3310) covers P-2291.")
    assert not keeps_every_fact(original, "D. Reddy (C-3310) covers it.")
    assert not keeps_every_fact(original, original + " Or C-1526 for ₹24,000.")


def test_an_id_written_loosely_is_still_checked():
    assert not verify("c-9999 covers P-2291.", [OPTIONS]).ok
    assert not verify("C9999 covers P-2291.", [OPTIONS]).ok
    assert not verify("C-3310 covers P-2291 on flight dx999.", [OPTIONS]).ok
    assert verify("c-3310 covers P-2291.", [OPTIONS]).ok


def test_a_verdict_without_a_rule_id_must_match_the_engine():
    legal = entry("check_legality", {"crew_id": "C-3310", "pairing_id": "P-2291", "legal": True})
    assert not verify("C-3310 would breach duty limits on P-2291.", [legal]).ok
    assert verify("C-3310 does not breach any limit on P-2291.", [legal]).ok
    assert not verify("C-2087 passes every rule for P-2291.", [LEGALITY]).ok
    assert verify("C-2087 exceeds the duty limit on P-2291.", [LEGALITY]).ok
