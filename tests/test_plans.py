"""Plans end to end against moto's DynamoDB: submit, contention, four eyes, re-check, commit."""

from datetime import UTC, datetime, timedelta

import pytest
from test_reports import report

from crewops.analysis.runner import Operation, analyse_by_id
from crewops.domain import Rank, Report, Role
from crewops.engine import TripCancelled, find_options
from crewops.ingestion.seed import load_seed
from crewops.plans.service import PlanError, Plans
from crewops.store import plans as store
from crewops.store.ops_store import OpsStore
from crewops.store.reports import get_disruption, store_report
from crewops.tools import Caller

AT = datetime.now(UTC) - timedelta(hours=1)
"""When the test's reports arrived: real time, just before the analysis and the plan
actions (which use the real clock), so the audit trail reads in the order things happened.
The reports themselves are about the operating week."""
ROHIT = Caller("rohit", frozenset({Role.CONTROLLER}), desk="desk-2")
ANANYA = Caller("ananya", frozenset({Role.CONTROLLER}), desk="desk-1")
MEERA = Caller("meera", frozenset({Role.APPROVER}))
KARAN = Caller("karan", frozenset({Role.APPROVER}))
PRIYA = Caller("priya", frozenset({Role.ADMIN}))


@pytest.fixture(scope="module")
def seed():
    return load_seed()


@pytest.fixture
def table(ops_table, seed):
    OpsStore(ops_table).put_seed(seed)
    return ops_table


def disruption(table, report_id, crew_id, from_date, to_date=None):
    details = {"crew_id": crew_id, "from_date": from_date, "reason": "sick"}
    if to_date:
        details["to_date"] = to_date
    store_report(table, Report.from_dict(report(report_id=report_id, details=details)), AT)
    assert analyse_by_id(table, report_id, Operation.load(table)).outcome == "stored"


@pytest.fixture
def plans(table):
    disruption(table, "r-sick1042", "C-1042", "2026-10-13", "2026-10-14")  # P-2291, VT-DXC
    return Plans(table)  # the real clock: audit events sort after the analysis's


def crew_of(table, pairing_id):
    item = table.get_item(Key=store.pairing_key(pairing_id))["Item"]
    return {(c["role"], c["crew_id"]) for c in item["crew"]}


# -- Submitting ---------------------------------------------------------------------------


def test_controller_submits_the_recommendation(plans, table):
    out = plans.submit(ANANYA, "r-sick1042", note="reserve is closest")
    plan = out.plan
    # The only disruption, so the least-loaded desk got it: desk-1.
    assert (
        out.status == "SUBMITTED" and plan["submitted_by"] == "ananya" and plan["desk"] == "desk-1"
    )
    (a,) = plan["assignments"]
    # Reserve callout 18,500 + seniority 9 x 450 + travel 30 min (3 x 180).
    assert (a["crew_id"], a["replaces"], a["cost_inr"]) == ("C-3310", "C-1042", 23090)
    assert get_disruption(table, "r-sick1042")["status"] == "SUBMITTED"
    assert [q["plan_id"] for q in plans.queue(MEERA)] == [plan["plan_id"]]
    actions = [e["action"] for e in store.audit_trail(table, "r-sick1042")]
    assert actions == ["REPORTED", "OPTIONS_READY", "SUBMITTED"]


def test_a_chosen_legal_option_is_used(plans):
    out = plans.submit(ANANYA, "r-sick1042", {"P-2291/Captain": "C-1526"})
    # Day-off callout 24,000 + seniority 16 x 450 + travel 110 min (11 x 180).
    assert out.plan["assignments"][0]["cost_inr"] == 24000 + 7200 + 1980


def test_an_illegal_choice_is_refused_with_its_reason(plans):
    with pytest.raises(PlanError) as err:
        plans.submit(ANANYA, "r-sick1042", {"P-2291/Captain": "C-2087"})
    assert err.value.code == "NOT_LEGAL"
    assert "C-2087 is not a legal option" in err.value.message and "60h/7d" in err.value.message


@pytest.mark.parametrize(
    ("caller", "code"),
    [(ROHIT, "FORBIDDEN"), (MEERA, "FORBIDDEN"), (PRIYA, "FORBIDDEN")],
)
def test_only_the_desks_controller_submits(plans, caller, code):
    with pytest.raises(PlanError) as err:
        plans.submit(caller, "r-sick1042")
    assert err.value.code == code


def test_bad_requests(plans):
    with pytest.raises(PlanError, match="no disruption"):
        plans.submit(ANANYA, "r-missing1")
    with pytest.raises(PlanError, match="no such seat"):
        plans.submit(ANANYA, "r-sick1042", {"P-9999/Captain": "C-3310"})
    plans.submit(ANANYA, "r-sick1042")
    with pytest.raises(PlanError) as err:
        plans.submit(ANANYA, "r-sick1042")
    assert err.value.code == "WRONG_STATE"


# -- Deciding -----------------------------------------------------------------------------


def test_approval_commits_the_roster_in_one_step(plans, table):
    plan = plans.submit(ANANYA, "r-sick1042").plan
    out = plans.approve(MEERA, "r-sick1042", plan["plan_id"])
    assert out.status == "COMMITTED"
    crew = crew_of(table, "P-2291")
    assert ("Captain", "C-3310") in crew and ("Captain", "C-1042") not in crew
    assert get_disruption(table, "r-sick1042")["status"] == "COMMITTED"
    actions = [e["action"] for e in store.audit_trail(table, "r-sick1042")]
    assert actions[-2:] == ["APPROVED", "COMMITTED"]
    # The roster as stored is what every later answer sees, and the sick crew stays out.
    world = Operation.load(table).world
    assert world.pairings["P-2291"].holder(Rank.CAPTAIN) == ("C-3310",)
    assert "C-1042" in world.unavailable
    with pytest.raises(PlanError) as err:
        plans.approve(KARAN, "r-sick1042", plan["plan_id"])
    assert err.value.code == "WRONG_STATE"


def test_four_eyes(plans):
    both = Caller("ananya", frozenset({Role.CONTROLLER, Role.APPROVER}), desk="desk-1")
    plan = plans.submit(both, "r-sick1042").plan
    for decide in (plans.approve, lambda c, d, p: plans.reject(c, d, p, "no")):
        with pytest.raises(PlanError) as err:
            decide(both, "r-sick1042", plan["plan_id"])
        assert err.value.code == "FOUR_EYES"
    with pytest.raises(PlanError) as err:
        plans.approve(PRIYA, "r-sick1042", plan["plan_id"])
    assert err.value.code == "FORBIDDEN"  # admins configure; they do not decide


def test_reject_sends_it_back_and_a_new_plan_can_follow(plans, table):
    plan = plans.submit(ANANYA, "r-sick1042").plan
    with pytest.raises(PlanError, match="say why"):
        plans.reject(MEERA, "r-sick1042", plan["plan_id"], "  ")
    out = plans.reject(MEERA, "r-sick1042", plan["plan_id"], "use a day-off captain")
    assert out.status == "REJECTED"
    assert get_disruption(table, "r-sick1042")["status"] == "OPTIONS_READY"
    back = [q for q in plans.queue(ANANYA) if q["kind"] == "sent_back_rejected"]
    assert back[0]["reason"] == "use a day-off captain"
    again = plans.submit(ANANYA, "r-sick1042", {"P-2291/Captain": "C-1526"})
    assert again.status == "SUBMITTED"


def test_only_the_submitter_withdraws(plans, table):
    plan = plans.submit(ANANYA, "r-sick1042").plan
    other = Caller("divya", frozenset({Role.CONTROLLER}), desk="desk-3")
    with pytest.raises(PlanError) as err:
        plans.withdraw(other, "r-sick1042", plan["plan_id"])
    assert err.value.code == "FORBIDDEN"
    assert plans.withdraw(ANANYA, "r-sick1042", plan["plan_id"]).status == "WITHDRAWN"
    assert get_disruption(table, "r-sick1042")["status"] == "OPTIONS_READY"


# -- Re-check at approval -----------------------------------------------------------------


def test_cover_reported_sick_after_submission_makes_the_plan_stale(plans, table):
    plan = plans.submit(ANANYA, "r-sick1042").plan
    disruption(table, "r-sick3310", "C-3310", "2026-10-13")
    out = plans.approve(MEERA, "r-sick1042", plan["plan_id"])
    assert out.status == "STALE" and "C-3310 is no longer legal" in out.reasons[0]
    assert ("Captain", "C-1042") in crew_of(table, "P-2291")  # nothing written
    assert get_disruption(table, "r-sick1042")["status"] == "OPTIONS_READY"
    assert [q["kind"] for q in plans.queue(ANANYA)][-1] == "sent_back_stale"


def test_a_roster_changed_after_submission_makes_the_plan_stale(plans, table):
    plan = plans.submit(ANANYA, "r-sick1042").plan
    item = table.get_item(Key=store.pairing_key("P-2291"))["Item"]
    item["crew"] = [c for c in item["crew"] if c["role"] != "Cabin Crew"][:5] + item["crew"][-1:]
    table.put_item(Item=item)
    out = plans.approve(MEERA, "r-sick1042", plan["plan_id"])
    assert out.status == "STALE" and "crew of P-2291 changed" in out.reasons[0]


def test_a_roster_change_between_check_and_write_is_caught(plans, table, monkeypatch):
    plan = plans.submit(ANANYA, "r-sick1042").plan
    real = store.commit

    def racing(table_, plan_, rosters, versions, actor, at):
        item = table_.get_item(Key=store.pairing_key("P-2291"))["Item"]
        table_.put_item(Item=item | {"crew": item["crew"][:-1]})  # someone else wrote first
        real(table_, plan_, rosters, versions, actor, at)

    monkeypatch.setattr(store, "commit", racing)
    out = plans.approve(MEERA, "r-sick1042", plan["plan_id"])
    assert out.status == "STALE"
    assert get_disruption(table, "r-sick1042")["status"] == "OPTIONS_READY"


# -- Contention ---------------------------------------------------------------------------


def test_no_contention_when_the_dates_do_not_overlap(plans, table):
    disruption(table, "r-sick5566", "C-5566", "2026-10-15", "2026-10-16")  # P-2293, 15-16 Oct
    first = plans.submit(ANANYA, "r-sick1042")  # P-2291, 13-14 Oct, also C-3310
    second = plans.submit(ROHIT, "r-sick5566")  # the second disruption: the next desk
    assert second.plan["assignments"][0]["crew_id"] == first.plan["assignments"][0]["crew_id"]
    assert first.warnings == () and second.warnings == ()


def test_approved_cover_leaves_other_options(plans, table):
    plan = plans.submit(ANANYA, "r-sick1042").plan
    plans.approve(MEERA, "r-sick1042", plan["plan_id"])
    world = Operation.load(table).world
    other = find_options(world, "P-2291", "First Officer")
    assert "C-3310" not in [o.crew_id for o in other.crew_options]  # a captain, and now busy


# -- The desk command ---------------------------------------------------------------------


def test_desk_command_end_to_end(plans, table, monkeypatch, capsys):
    from crewops.plans import desk
    from crewops.store.users import profile_item, put_profile

    put_profile(table, profile_item("ananya", "Ananya Iyer", ["controller"], None, "desk-1"))
    put_profile(table, profile_item("meera", "Meera Bhatt", ["approver"], None, None))
    monkeypatch.setattr("crewops.config.load_env_file", lambda: {})
    monkeypatch.setenv("AWS_PROFILE", "crewops")
    monkeypatch.setattr("crewops.store.ops_store.table_for", lambda settings: table)

    assert desk.main(["--as", "ananya", "queue"]) == 0
    assert "[ready_for_plan] r-sick1042" in capsys.readouterr().out
    assert desk.main(["--as", "ananya", "submit", "r-sick1042"]) == 0
    plan_id = capsys.readouterr().out.split()[1].rstrip(",")
    assert desk.main(["--as", "ananya", "approve", "r-sick1042", plan_id]) == 1
    assert "FORBIDDEN" in capsys.readouterr().err
    assert desk.main(["--as", "meera", "approve", "r-sick1042", plan_id]) == 0
    assert f"{plan_id}: COMMITTED" in capsys.readouterr().out
    assert desk.main(["--as", "meera", "show", "r-sick1042"]) == 0
    shown = capsys.readouterr().out
    assert "r-sick1042: COMMITTED" in shown and "COMMITTED by meera" in shown
    with pytest.raises(SystemExit, match="No team member"):
        desk.main(["--as", "nobody", "queue"])


# -- Regressions --------------------------------------------------------------------------

from crewops.store.reports import store_analysis  # noqa: E402


def crafted(table, report_id, seats, crew_id="C-1042", desk="desk-1"):
    """A disruption whose analysis is exactly ``seats``: for cases the seed week does not
    produce on its own. Stored as the runner stores one, with the desk it was assigned."""
    details = {"crew_id": crew_id, "from_date": "2026-10-13", "reason": "sick"}
    store_report(table, Report.from_dict(report(report_id=report_id, details=details)), AT)
    store_analysis(table, report_id, {"summary": "crafted", "seats": seats, "desk": desk}, AT)


def seat(world, pairing_id, role, crew_out, status="options", skip=()):
    out = {"pairing_id": pairing_id, "role": role, "crew_out": crew_out, "status": status}
    if status == "options":
        options = find_options(world, pairing_id, role).crew_options
        pick = next(o for o in options if o.crew_id not in skip)
        out["recommended"] = {"crew_id": pick.crew_id, "cost_inr": pick.cost_inr}
    return out


def test_a_seat_whose_crew_already_changed_is_not_covered_twice(plans, table):
    plan = plans.submit(ANANYA, "r-sick1042").plan
    plans.approve(MEERA, "r-sick1042", plan["plan_id"])
    world = Operation.load(table).world
    crafted(table, "r-stale0001", [seat(world, "P-2291", "Captain", "C-1042")])
    with pytest.raises(PlanError) as err:
        plans.submit(ANANYA, "r-stale0001", {"P-2291/Captain": "C-1526"})
    assert err.value.code == "NOTHING_TO_COVER"
    assert Operation.load(table).world.pairings["P-2291"].holder(Rank.CAPTAIN) == ("C-3310",)


def test_concurrent_approvals_cannot_double_book(table):
    # Two Deccan Express captains out the same day, on trips that can take the same cover
    # (cover only ever comes from the trip's own airline). The first goes to desk-1, the
    # second to desk-2: two controllers, one reserve.
    disruption(table, "r-sick3940", "C-3940", "2026-10-13")  # P-2202
    disruption(table, "r-sick1938", "C-1938", "2026-10-13")  # P-2209
    world = Operation.load(table).world
    common = set.intersection(
        *(
            {o.crew_id for o in find_options(world, pid, "Captain").crew_options}
            for pid in ("P-2202", "P-2209")
        )
    )
    shared = sorted(common)[0]
    ananya_plans = Plans(table)
    first = Plans(table).submit(ANANYA, "r-sick3940", {"P-2202/Captain": shared}).plan
    second = ananya_plans.submit(ROHIT, "r-sick1938", {"P-2209/Captain": shared})
    assert any(first["plan_id"] in w for w in second.warnings)

    def load_then_lose_the_race(t):
        op = Operation.load(t)  # B has read versions and the roster...
        Plans(t).approve(MEERA, "r-sick3940", first["plan_id"])  # ...then A commits first
        return op

    racing = Plans(table, load=load_then_lose_the_race)
    out = racing.approve(KARAN, "r-sick1938", second.plan["plan_id"])
    assert out.status == "STALE"
    duties = Operation.load(table).world.crew[shared].duties
    assert {d.pairing_id for d in duties} >= {"P-2202"} and "P-2209" not in {
        d.pairing_id for d in duties
    }


def test_two_seats_of_one_role_on_one_pairing(table):
    world = Operation.load(table).world
    first = seat(world, "P-2291", "Cabin Crew", "C-4395")
    second = seat(world, "P-2291", "Cabin Crew", "C-4273", skip={first["recommended"]["crew_id"]})
    crafted(table, "r-twocabin", [first, second])
    plans = Plans(table)
    with pytest.raises(PlanError, match="names several seats"):
        plans.submit(ANANYA, "r-twocabin", {"P-2291/Cabin Crew": "C-1021"})
    out = plans.submit(ANANYA, "r-twocabin")
    assert len({a["crew_id"] for a in out.plan["assignments"]}) == 2
    plans.approve(MEERA, "r-twocabin", out.plan["plan_id"])
    crew = Operation.load(table).world.pairings["P-2291"].crew
    assert len(crew) == 6 and len({s.crew_id for s in crew}) == 6
    assert not {"C-4395", "C-4273"} & {s.crew_id for s in crew}


def test_only_the_assigned_desk_plans_a_disruption(table):
    # Seats on two airlines' aircraft, assigned to desk-2. Desks don't own aircraft, so
    # only desk-2 sees it waiting and may plan it.
    world = Operation.load(table).world
    crafted(
        table,
        "r-twodesks",
        [seat(world, "P-2291", "Captain", "C-1042"), seat(world, "P-2204", "Captain", "C-5837")],
        desk="desk-2",
    )
    plans = Plans(table)
    assert [q["kind"] for q in plans.queue(ROHIT)] == ["ready_for_plan"]
    assert plans.queue(ANANYA) == []
    with pytest.raises(PlanError) as err:
        plans.submit(ANANYA, "r-twodesks")
    assert err.value.code == "FORBIDDEN"
    out = plans.submit(ROHIT, "r-twodesks")
    assert out.status == "SUBMITTED" and len(out.plan["assignments"]) == 2


def test_uncovered_seats_must_be_acknowledged_and_a_hopeless_one_closed(table):
    world = Operation.load(table).world
    covered = seat(world, "P-2291", "Captain", "C-1042")
    hopeless = {
        "pairing_id": "P-2291",
        "role": "First Officer",
        "crew_out": "C-1694",
        "status": "no_legal_cover",
    }
    crafted(table, "r-partial1", [covered, hopeless])
    plans = Plans(table)
    with pytest.raises(PlanError) as err:
        plans.submit(ANANYA, "r-partial1")
    assert err.value.code == "UNCOVERED" and "no legal cover" in err.value.message
    out = plans.submit(ANANYA, "r-partial1", accept_uncovered=True)
    assert out.plan["uncovered"] == [
        {"seat": "P-2291/First Officer/C-1694", "reason": "no legal cover"}
    ]
    crafted(table, "r-hopeless", [hopeless])
    with pytest.raises(PlanError) as err:
        plans.submit(ANANYA, "r-hopeless")
    assert err.value.code == "NOTHING_TO_COVER"
    with pytest.raises(PlanError) as err:
        plans.close(MEERA, "r-hopeless", "cancelled")
    assert err.value.code == "FORBIDDEN"
    # The controller resolves it; an approver signs it off.
    assert plans.close(ANANYA, "r-hopeless", "KS589 cancelled").status == "RESOLVING"
    assert plans.confirm_resolution(MEERA, "r-hopeless").status == "CLOSED"
    assert get_disruption(table, "r-hopeless")["status"] == "CLOSED"


def test_a_resolution_is_signed_off_or_sent_back_by_another_person(table):
    hopeless = {
        "pairing_id": "P-2291",
        "role": "First Officer",
        "crew_out": "C-1694",
        "status": "no_legal_cover",
    }
    crafted(table, "r-resolve1", [hopeless])
    plans = Plans(table)
    plans.close(ANANYA, "r-resolve1", "KS412 cancelled with operations")
    assert get_disruption(table, "r-resolve1")["status"] == "RESOLVING"
    # In the approver's queue, with the controller's remark; never in the sender's.
    (item,) = [q for q in plans.queue(MEERA) if q["kind"] == "to_confirm"]
    assert item["remark"] == "KS412 cancelled with operations" and item["submitted_by"] == "ananya"
    both = Caller("ananya", frozenset({Role.CONTROLLER, Role.APPROVER}), desk="desk-1")
    assert not [q for q in plans.queue(both) if q["kind"] == "to_confirm"]
    for who, code in ((ANANYA, "FORBIDDEN"), (PRIYA, "FORBIDDEN"), (both, "FOUR_EYES")):
        with pytest.raises(PlanError) as err:
            plans.confirm_resolution(who, "r-resolve1")
        assert err.value.code == code
    # Sent back with a reason: on the desk again, ready to resolve or plan.
    with pytest.raises(PlanError) as err:
        plans.return_resolution(MEERA, "r-resolve1", " ")
    assert err.value.code == "INVALID"
    assert plans.return_resolution(MEERA, "r-resolve1", "check the crew hotel").status == (
        "OPTIONS_READY"
    )
    with pytest.raises(PlanError) as err:
        plans.confirm_resolution(KARAN, "r-resolve1")
    assert err.value.code == "WRONG_STATE"
    # Resolved again and signed off: closed, with what the controller did as its reason.
    plans.close(ANANYA, "r-resolve1", "KS412 cancelled; crew stood down")
    plans.confirm_resolution(KARAN, "r-resolve1")
    closed = [e for e in store.audit_trail(table, "r-resolve1") if e["action"] == "CLOSED"]
    assert closed[-1]["reason"] == "KS412 cancelled; crew stood down"
    assert closed[-1]["actor"] == "karan" and closed[-1]["handled_by"] == "ananya"


def test_sent_back_plans_leave_the_queue_once_resolved(plans):
    plan = plans.submit(ANANYA, "r-sick1042").plan
    plans.reject(MEERA, "r-sick1042", plan["plan_id"], "try another")
    assert "sent_back_rejected" in [q["kind"] for q in plans.queue(ANANYA)]
    again = plans.submit(ANANYA, "r-sick1042", {"P-2291/Captain": "C-1526"}).plan
    plans.approve(MEERA, "r-sick1042", again["plan_id"])
    assert plans.queue(ANANYA) == []


def test_a_transient_clash_asks_for_a_retry(plans, monkeypatch):
    plan = plans.submit(ANANYA, "r-sick1042").plan

    def clash(*args):
        raise store.Conflict("busy", ("None", "TransactionConflict"))

    monkeypatch.setattr(store, "commit", clash)
    with pytest.raises(PlanError) as err:
        plans.approve(MEERA, "r-sick1042", plan["plan_id"])
    assert err.value.code == "RETRY"
    assert store.get_plan(plans.table, "r-sick1042", plan["plan_id"])["status"] == "SUBMITTED"


def test_a_report_taking_crew_out_moves_their_roster_version(table):
    disruption(table, "r-sick3310", "C-3310", "2026-10-13")
    assert store.read_versions(table, {"C-3310", "C-1526"}) == {"C-3310": 1, "C-1526": 0}


def test_approver_sees_contention_in_the_queue(plans, table):
    disruption(table, "r-sick5566", "C-5566", "2026-10-15", "2026-10-16")
    plans.submit(ANANYA, "r-sick1042")
    (item,) = [q for q in plans.queue(MEERA) if q["kind"] == "to_decide"]
    assert item["warnings"] == []  # P-2291 (15-16) and P-2293 (17-18) do not overlap


def test_a_disruption_with_cover_cannot_be_closed_and_closing_keeps_crew_out(plans, table):
    with pytest.raises(PlanError) as err:
        plans.close(ANANYA, "r-sick1042", "flights cancelled")
    assert err.value.code == "WRONG_STATE" and "submit a plan instead" in err.value.message
    world = Operation.load(table).world
    hopeless = {
        "pairing_id": "P-2291",
        "role": "First Officer",
        "crew_out": "C-1694",
        "status": "no_legal_cover",
    }
    crafted(table, "r-nocover1", [hopeless], crew_id="C-1694")
    plans.close(ANANYA, "r-nocover1", "KS412 cancelled")
    assert "C-1694" in Operation.load(table).world.unavailable  # still sick after closing
    assert world is not None


def test_a_plan_too_large_to_commit_is_refused(table, monkeypatch):
    from crewops.plans import service

    world = Operation.load(table).world
    crafted(table, "r-toolarge", [seat(world, "P-2291", "Captain", "C-1042")])
    monkeypatch.setattr(service, "MAX_SEATS", 0)
    with pytest.raises(PlanError) as err:
        Plans(table).submit(ANANYA, "r-toolarge")
    assert err.value.code == "TOO_LARGE"


def test_a_report_of_several_sick_crew_moves_each_ones_roster_version(table):
    from crewops.store.versions import read_versions

    details = {"crew_ids": ["C-1042", "C-1938"], "from_date": "2026-10-13", "reason": "sick"}
    doc = report("multiple_crew_unavailable", details, report_id="r-sick-both")
    store_report(table, Report.from_dict(doc), AT)
    assert read_versions(table, {"C-1042", "C-1938", "C-3310"}) == {
        "C-1042": 1,
        "C-1938": 1,
        "C-3310": 0,
    }


# -- Operating short, cancelling, and who plans -------------------------------------------


def test_a_cabin_seat_can_be_planned_one_short_and_the_seat_is_left_empty(table):
    # C-1970 is the fifth cabin crew member on P-2202 (an A320 needs four for 162 seats).
    disruption(table, "r-sick1970", "C-1970", "2026-10-13")
    plans = Plans(table)
    plan = plans.submit(ANANYA, "r-sick1970", {"P-2202/Cabin Crew": "SHORT"}, "4 still legal").plan
    (a,) = plan["assignments"]
    assert (a["crew_id"], a["strategy"], a["cost_inr"]) == (None, "short_crew", 0)
    plans.approve(MEERA, "r-sick1970", plan["plan_id"])
    crew = crew_of(table, "P-2202")
    assert ("Cabin Crew", "C-1970") not in crew
    assert sum(1 for role, _ in crew if role in ("Cabin Crew", "Senior Cabin Crew")) == 4


def test_operating_short_below_the_minimum_is_refused(plans):
    with pytest.raises(PlanError) as err:
        plans.submit(ANANYA, "r-sick1042", {"P-2291/Captain": "SHORT"})
    assert err.value.code == "NOT_LEGAL" and "minimum cabin crew" in err.value.message


def test_cancelling_is_a_plan_an_approver_must_agree(plans, table):
    plan = plans.submit(
        ANANYA, "r-sick1042", {"P-2291/Captain": "CANCEL"}, "no cover acceptable"
    ).plan
    (a,) = plan["assignments"]
    assert a["strategy"] == "cancel" and a["crew_id"] is None and a["cost_inr"] == 1_500_000
    assert a["flights"][:3] == ["KS412-2026-10-13", "KS413-2026-10-13", "KS588-2026-10-13"]
    assert plans.approve(MEERA, "r-sick1042", plan["plan_id"]).status == "COMMITTED"
    # The trip is cancelled, so its whole crew is released: never a first officer flying alone.
    assert crew_of(table, "P-2291") == set()
    world = Operation.load(table).world  # the operation still loads, the trip with no crew
    assert world.pairings["P-2291"].crew == ()
    assert set(a["flights"]) <= world.cancelled
    with pytest.raises(TripCancelled):
        find_options(world, "P-2291", Rank.CAPTAIN)  # nothing left to re-crew
    # Its flights no longer carry anyone: before, C-2210 positioned on KS589 for P-2203.
    positioned = {
        o.positioning_flight for o in find_options(world, "P-2203", Rank.CAPTAIN).crew_options
    }
    assert "KS589-2026-10-14" not in positioned and not positioned & world.cancelled


def test_before_any_cancellation_ks589_is_a_positioning_flight(table):
    # The control for the test above: the same search uses KS589 while P-2291 still flies.
    world = Operation.load(table).world
    positioned = {
        o.positioning_flight for o in find_options(world, "P-2203", Rank.CAPTAIN).crew_options
    }
    assert "KS589-2026-10-14" in positioned


def test_falling_ill_after_the_trip_still_counts_for_its_cabin_minimum(table):
    # C-3988 falls ill at 20:00Z, after P-2202's last leg (DX404, 11:15Z): they flew it all.
    details = {
        "crew_id": "C-3988",
        "from_date": "2026-10-13",
        "from_utc": "2026-10-13T20:00:00Z",
        "reason": "sick",
    }
    store_report(table, Report.from_dict(report(report_id="r-late3988", details=details)), AT)
    assert analyse_by_id(table, "r-late3988", Operation.load(table)).outcome == "stored"
    # Nothing left to cover: recorded, on no desk, and still counted as out that day.
    assert get_disruption(table, "r-late3988")["status"] == "CLOSED"
    # C-1970 is then out for the day: four cabin crew still fly P-2202, its minimum.
    disruption(table, "r-sick1970", "C-1970", "2026-10-13")
    desk = get_disruption(table, "r-sick1970")["analysis"]["desk"]
    owner = {"desk-1": ANANYA, "desk-2": ROHIT}[desk]
    plans = Plans(table)
    plan = plans.submit(owner, "r-sick1970", {"P-2202/Cabin Crew": "SHORT"}, "4 remain").plan
    assert plans.approve(MEERA, "r-sick1970", plan["plan_id"]).status == "COMMITTED"


def several_out(table, report_id, crew_ids, on="2026-10-13"):
    details = {"crew_ids": crew_ids, "from_date": on, "to_date": on, "reason": "sick"}
    doc = report("multiple_crew_unavailable", details, report_id=report_id)
    store_report(table, Report.from_dict(doc), AT)
    assert analyse_by_id(table, report_id, Operation.load(table)).outcome == "stored"


def test_two_cabin_crew_out_on_one_trip_cannot_both_be_flown_short(table):
    # P-2202 has five cabin crew and needs four (162 seats): one short is legal, two is not.
    several_out(table, "r-twoshort", ["C-1569", "C-2907"])
    plans = Plans(table)
    with pytest.raises(PlanError) as err:
        plans.submit(
            ANANYA,
            "r-twoshort",
            {"P-2202/Cabin Crew/C-1569": "SHORT", "P-2202/Cabin Crew/C-2907": "SHORT"},
        )
    assert err.value.code == "NOT_LEGAL" and "minimum cabin crew" in err.value.message
    assert ("Cabin Crew", "C-1569") in crew_of(table, "P-2202")  # nothing written


def test_flying_short_goes_stale_when_another_cabin_crew_member_falls_ill(table):
    disruption(table, "r-sick1970", "C-1970", "2026-10-13")
    plans = Plans(table)
    plan = plans.submit(ANANYA, "r-sick1970", {"P-2202/Cabin Crew": "SHORT"}, "4 still legal").plan
    disruption(table, "r-sick1569", "C-1569", "2026-10-13")  # same trip, another desk's report
    out = plans.approve(MEERA, "r-sick1970", plan["plan_id"])
    assert out.status == "STALE"
    assert out.reasons == (
        "P-2202 would fly with 3 cabin crew, below the minimum of 4 (one per 50 seats)",
    )
    assert ("Cabin Crew", "C-1970") in crew_of(table, "P-2202")


def test_cancelling_one_seat_cancels_the_trip_for_every_seat_of_it(table):
    several_out(table, "r-twopilots", ["C-1042", "C-1694"])  # both pilots of P-2291
    plans = Plans(table)
    with pytest.raises(PlanError) as err:
        plans.submit(ANANYA, "r-twopilots", {"P-2291/Captain": "CANCEL"})
    assert err.value.code == "INVALID" and "every flight of P-2291" in err.value.message
    plan = plans.submit(
        ANANYA, "r-twopilots", {"P-2291/Captain": "CANCEL", "P-2291/First Officer": "CANCEL"}
    ).plan
    # One trip, cancelled once: 6 flights at the per-flight cancellation cost.
    assert [a["cost_inr"] for a in plan["assignments"]] == [1_500_000, 0]
    assert plan["total_cost_inr"] == 1_500_000


def test_a_seat_with_no_legal_crew_may_still_be_cancelled(table):
    world = Operation.load(table).world
    crafted(
        table, "r-nocover1", [seat(world, "P-2291", "Captain", "C-1042", status="no_legal_cover")]
    )
    plans = Plans(table)
    with pytest.raises(PlanError) as err:
        plans.submit(ANANYA, "r-nocover1")
    assert err.value.code == "NOTHING_TO_COVER"
    plan = plans.submit(ANANYA, "r-nocover1", {"P-2291/Captain": "CANCEL"}, "nobody legal").plan
    (a,) = plan["assignments"]
    assert (a["strategy"], a["cost_inr"], a["replaces"]) == ("cancel", 1_500_000, "C-1042")


def test_the_least_loaded_desk_gets_the_next_disruption():
    from crewops.domain.desks import assign_desk

    assert assign_desk({}, {}).desk_id == "desk-1"
    assert assign_desk({"desk-1": 1}, {"desk-1": 1}).desk_id == "desk-2"
    assert assign_desk({"desk-1": 1, "desk-2": 1}, {"desk-1": 1, "desk-2": 1}).desk_id == "desk-3"
    # Equal open load: the one given fewest overall takes it, so ties take turns.
    assert assign_desk({}, {"desk-1": 4, "desk-2": 2, "desk-3": 3}).desk_id == "desk-2"


def test_a_choice_for_one_trip_is_re_judged_for_the_next_days_trip(table):
    # Two different seats on adjacent days: C-1042's P-2291 (13-14 Oct) and C-5820's P-2218
    # (15 Oct), both Konkan captains, both ranking C-3983. Planned separately.
    disruption(table, "r-sick1042", "C-1042", "2026-10-13", "2026-10-14")  # desk-1
    disruption(table, "r-sick5820", "C-5820", "2026-10-15")  # desk-2
    plans = Plans(table)
    world = Operation.load(table).world
    assert "C-3983" in [o.crew_id for o in find_options(world, "P-2218", Rank.CAPTAIN).crew_options]
    first = plans.submit(ANANYA, "r-sick1042", {"P-2291/Captain": "C-3983"}, "closest").plan
    # The other desk also picks C-3983 before the first plan is decided.
    second = plans.submit(ROHIT, "r-sick5820", {"P-2218/Captain": "C-3983"}, "rested").plan
    assert plans.approve(MEERA, "r-sick1042", first["plan_id"]).status == "COMMITTED"
    # C-3983 now flies P-2291 until 14:45Z on the 14th: re-judged for the 15th with that
    # trip included, the rest falls short, so the second approval writes nothing.
    out = plans.approve(KARAN, "r-sick5820", second["plan_id"])
    assert out.status == "STALE"
    assert out.reasons == (
        "C-3983 is no longer legal for P-2218: only 11.75h rest after P-2291 on 2026-10-14",
    )
    # And they are no longer offered for P-2218, with the reason, when it is planned again.
    result = find_options(Operation.load(table).world, "P-2218", Rank.CAPTAIN)
    assert "C-3983" not in [o.crew_id for o in result.crew_options]
    (gone,) = [e for e in result.excluded if e.crew_id == "C-3983"]
    assert "only 11.75h rest after P-2291 on 2026-10-14" in gone.reasons


def test_a_trip_that_started_before_approval_is_not_re_crewed(plans, table):
    """Approval re-runs the search done at submission: a trip under way can't be changed."""
    plan = plans.submit(ANANYA, "r-sick1042").plan
    first_report = Operation.load(table).world.days_of("P-2291")[0].report_utc
    later = Plans(table, load=lambda t: Operation.load(t, now=first_report + timedelta(hours=3)))
    out = later.approve(MEERA, "r-sick1042", plan["plan_id"])
    assert out.status != "COMMITTED"
    assert any("can no longer be re-crewed" in r for r in out.reasons), out.reasons
    assert ("Captain", "C-1042") in crew_of(table, "P-2291")  # the roster is untouched
