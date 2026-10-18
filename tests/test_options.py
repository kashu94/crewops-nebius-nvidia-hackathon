"""Candidate search and cost ranking against the seed week."""

import pytest

from crewops.domain import Rank
from crewops.engine import AlreadyStarted, UnknownEntity, build_world
from crewops.engine.options import Strategy, find_options
from crewops.ingestion.seed import load_seed


@pytest.fixture(scope="module")
def world():
    return build_world(load_seed())


@pytest.fixture(scope="module")
def sick_captain(world):
    """Captain C-1042 sick on the two-day pairing P-2291."""
    return find_options(world, "P-2291", Rank.CAPTAIN)


def ranked(result):
    return [(o.crew_id, o.cost_inr, o.delay_hours) for o in result.options]


def test_ranked_by_cost_with_cancellation_last(sick_captain):
    # Konkan Skyways' captains only (P-2291 flies VT-DXC). Callout, seniority allowance and
    # travel by reach time make each cost its own.
    assert ranked(sick_captain) == [
        ("C-3310", 23090, 0.0),
        ("C-3983", 30660, 0.0),
        ("C-5566", 31740, 0.0),
        ("C-1526", 33180, 0.0),
        (None, 1500000, 0.0),
    ]


def test_recommendation_and_precomputed_figures(sick_captain):
    rec = sick_captain.recommended
    assert rec.action == "Assign Captain D. Reddy (C-3310) (reserve callout)"
    assert sick_captain.next_tier_premium_inr == 30660 - 23090
    assert sick_captain.equal_cost_alternatives == 0
    assert sick_captain.cancellation_multiple == round(1500000 / 23090) == 65
    assert sick_captain.cancellation.name == "Cancel all 6 flights of the pairing"


def test_positioning_option_is_costed_in_full(world):
    result = find_options(world, "P-2213", Rank.CABIN_CREW)
    pos = next(o for o in result.options if o.crew_id == "C-4839")
    assert pos.strategy is Strategy.POSITIONING
    # A day-off callout from Delhi: no travel allowance (the fare replaces it), and the
    # 7-hour slip waiting for the positioning flight is costed per duty hour.
    assert pos.cost_breakdown == {
        "callout": 12500,
        "seniority_allowance": 2250,
        "positioning": 6500,
        "delay": 37800,
    }
    assert pos.positioning_flight == "DX402-2026-10-17"
    assert "positioning from DEL" in pos.action
    assert [o.strategy for o in result.strategies] == [
        Strategy.RESERVE_CALLOUT,
        Strategy.DAY_OFF_CALLOUT,
        Strategy.POSITIONING,
        Strategy.CANCEL,
    ]


def test_one_option_per_strategy(sick_captain):
    assert [o.strategy for o in sick_captain.strategies] == [
        Strategy.RESERVE_CALLOUT,
        Strategy.DAY_OFF_CALLOUT,
        Strategy.CANCEL,
    ]


def test_positioning_that_breaks_rest_is_excluded(world):
    # C-2341, a Delhi reserve: positioning delays day one, which cuts the overnight rest.
    result = find_options(world, "P-2291", Rank.FIRST_OFFICER)
    c2341 = next(e for e in result.excluded if e.crew_id == "C-2341")
    assert c2341.stage == "rested" and "9.5h rest" in c2341.reasons[0]


def test_pairing_that_already_reported_cannot_be_recrewed(world):
    with pytest.raises(AlreadyStarted, match="P-2201 reported at 2026-10-12 01:30Z"):
        find_options(world, "P-2201", Rank.CAPTAIN)


def test_excluded_carry_stage_and_reason(sick_captain):
    by_id = {e.crew_id: e for e in sick_captain.excluded}
    assert by_id["C-2087"].stage == "within limits"
    assert by_id["C-2087"].reasons[0].startswith("would exceed 60h/7d by 1h20m")
    assert by_id["C-5015"].stage == "qualified"  # on leave
    assert by_id["C-1443"].stage == "rested"  # already flying P-2216 that day


def test_funnel_adds_up(sick_captain):
    stages = sick_captain.funnel
    assert stages[0].stage == "considered" and stages[-1].stage == "legal"
    assert stages[0].remaining - sum(s.dropped for s in stages) == stages[-1].remaining
    assert stages[-1].remaining == len(sick_captain.crew_options)


def test_incumbents_never_offered(world, sick_captain):
    rostered = set(world.pairing("P-2291").holder(Rank.CAPTAIN))
    offered = {o.crew_id for o in sick_captain.options}
    excluded = {e.crew_id for e in sick_captain.excluded}
    assert not rostered & (offered | excluded)


def test_crew_already_on_the_pairing_cannot_fill_another_seat(world):
    result = find_options(world, "P-2213", Rank.CABIN_CREW)
    already_on_board = set(world.pairing("P-2213").holder(Rank.CABIN_CREW))
    assert {"C-2840", "C-4588"} <= already_on_board
    assert not already_on_board & {o.crew_id for o in result.options}
    assert result.recommended.crew_id == "C-4809"
    assert result.recommended.cost_inr == 9500 + 1050 + 360  # callout, 7 years, 20 minutes


def test_ranked_by_what_each_costs_and_risk_never_ranks(world, sick_captain):
    # Seniority ranks only through its allowance, which is part of the cost; the provided
    # risk score is shown and never changes the order.
    crew = sick_captain.crew_options
    assert [o.cost_inr for o in crew] == sorted(o.cost_inr for o in crew)
    assert all(o.seniority is not None and o.risk_score is not None for o in crew)
    assert all(
        o.cost_breakdown.get("seniority_allowance") == 450 * o.seniority
        for o in crew
        if o.seniority
    )


def test_policies(sick_captain):
    assert sick_captain.policies["cheapest"].crew_id == "C-3310"
    assert sick_captain.policies["least_delay"].delay_hours == 0.0
    # C-3310 is one of three captains on call at BLR that day, so using them gives up a
    # third of the reserve cover. A day-off callout keeps all of it.
    assert sick_captain.recommended.resilience == 66.7
    assert sick_captain.policies["keeps_most_reserve_cover"].strategy is Strategy.DAY_OFF_CALLOUT


def test_role_accepts_string_and_rejects_unknown(world):
    assert find_options(world, "P-2291", "Captain").recommended.crew_id == "C-3310"
    with pytest.raises(ValueError):
        find_options(world, "P-2291", "Pilot")
    with pytest.raises(UnknownEntity):
        find_options(world, "P-0000", Rank.CAPTAIN)


# -- Airlines, substitutes and operating short ------------------------------------------


def _without(world, crew_ids, day):
    """The world with these crew reported unavailable on ``day``."""
    from dataclasses import replace

    return replace(world, unavailable={c: ((day, "r-test"),) for c in crew_ids})


def test_cover_comes_only_from_the_trips_airline(world, sick_captain):
    airline = world.airline_of_pairing("P-2291")
    assert airline == "KS"
    assert all(world.airline_of_crew[o.crew_id] == airline for o in sick_captain.crew_options)
    assert all(world.airline_of_crew[e.crew_id] == airline for e in sick_captain.excluded)


def test_too_few_captains_brings_in_command_qualified_first_officers(world):
    from datetime import date

    # Take out every Konkan captain who could cover P-2291 but one: fewer than three are
    # legal, so command-qualified first officers follow, most experienced first.
    legal = [o.crew_id for o in find_options(world, "P-2291", Rank.CAPTAIN).crew_options]
    short_of_captains = _without(world, legal[1:], date(2026, 10, 13))
    result = find_options(short_of_captains, "P-2291", Rank.CAPTAIN)
    strategies = [o.strategy for o in result.crew_options]
    assert strategies[0] is not Strategy.COMMAND_UPGRADE  # the one captain left comes first
    upgrades = [o for o in result.crew_options if o.strategy is Strategy.COMMAND_UPGRADE]
    assert upgrades and strategies.index(Strategy.COMMAND_UPGRADE) == 1
    assert all(o.crew_id in world.command_qualified for o in upgrades)
    assert all(world.airline_of_crew[o.crew_id] == "KS" for o in upgrades)
    years = [o.experience_years for o in upgrades]
    assert years == sorted(years, reverse=True)
    assert "in command" in upgrades[0].action


def test_enough_captains_means_no_substitutes(sick_captain):
    assert len(sick_captain.crew_options) >= 3
    assert {o.strategy for o in sick_captain.crew_options} <= {
        Strategy.RESERVE_CALLOUT,
        Strategy.DAY_OFF_CALLOUT,
        Strategy.POSITIONING,
    }


def test_too_few_first_officers_brings_in_captains_in_the_right_seat(world):
    from datetime import date

    legal = [o.crew_id for o in find_options(world, "P-2291", Rank.FIRST_OFFICER).crew_options]
    result = find_options(_without(world, legal, date(2026, 10, 13)), "P-2291", Rank.FIRST_OFFICER)
    assert result.crew_options, "a captain current in the right seat may fly as first officer"
    assert all(o.strategy is Strategy.RIGHT_SEAT for o in result.crew_options)
    assert all(o.crew_id in world.right_seat_qualified for o in result.crew_options)
    # Never one pilot. The last resort for a pilot seat is cancelling, not flying short.
    assert result.short_crew is None and result.cancellation.strategy is Strategy.CANCEL


def test_a_cabin_seat_may_operate_short_only_above_the_minimum(world):
    # P-2202 carries a fifth cabin crew member on an A320 (162 seats: minimum four).
    roomy = find_options(world, "P-2202", Rank.CABIN_CREW)
    assert roomy.short_crew is not None and roomy.short_crew.cost_inr == 0
    assert roomy.options[-1].strategy is Strategy.CANCEL  # still the explicit last resort
    # P-2291 carries exactly the minimum: one fewer would be illegal.
    assert find_options(world, "P-2291", Rank.CABIN_CREW).short_crew is None
    # A first officer is never "short": two pilots always.
    assert find_options(world, "P-2202", Rank.FIRST_OFFICER).short_crew is None
