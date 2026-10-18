"""From a report to a decision-ready analysis, for every report type."""

import json

import pytest

from crewops.analysis.disruption import analyse
from crewops.domain import Report
from crewops.engine import build_world
from crewops.ingestion.seed import load_seed


@pytest.fixture(scope="module")
def seed():
    return load_seed()


@pytest.fixture(scope="module")
def world(seed):
    return build_world(seed)


def run(world, seed, type_, details):
    report = Report.from_dict(
        {
            "report_id": "r-analysis-1",
            "type": type_,
            "reported_by": {"user_id": "u-1", "roles": ["reporter"]},
            "reported_utc": "2026-10-13T05:00:00Z",
            "details": details,
        }
    )
    analysis = analyse(world, report, seed.gates, seed.contacts)
    json.dumps(analysis.to_dict())  # always storable and sendable as JSON
    return analysis


def test_sick_captain(world, seed):
    a = run(
        world,
        seed,
        "crew_unavailable",
        {"crew_id": "C-1042", "from_date": "2026-10-13", "reason": "sick"},
    )
    (seat,) = a.seats
    doc = seat.to_dict()
    assert (doc["pairing_id"], doc["role"], doc["crew_out"]) == ("P-2291", "Captain", "C-1042")
    assert doc["status"] == "options" and doc["recommended"]["crew_id"] == "C-3310"
    assert doc["impact"]["passengers"] == 397  # booked on the three uncrewed flights
    assert doc["cancellation_multiple"] == 65
    assert len(doc["options"]) <= 5 and doc["legal_options"] == 4
    assert a.joint is None
    assert "D. Reddy (C-3310)" in a.summary and "₹23,090" in a.summary


def test_unavailable_with_no_duty(world, seed):
    a = run(
        world,
        seed,
        "crew_unavailable",
        {"crew_id": "C-3310", "from_date": "2026-10-13", "reason": "sick"},
    )
    assert a.seats == () and "nothing to cover" in a.summary


def test_week_off_needs_a_joint_plan_and_skips_started_trips(world, seed):
    # C-5837 flies P-2201 (12 Oct, already flown), P-2204 (15 Oct) and P-2207 (18 Oct).
    a = run(
        world,
        seed,
        "crew_unavailable",
        {
            "crew_id": "C-5837",
            "from_date": "2026-10-12",
            "to_date": "2026-10-18",
            "reason": "personal",
        },
    )
    statuses = {s.seat.pairing_id: s.to_dict()["status"] for s in a.seats}
    assert statuses["P-2201"] == "already_started"
    assert set(statuses) == {"P-2201", "P-2204", "P-2207"}
    assert a.joint is not None
    covered = {s.pairing_id for s, _ in a.joint.assignments}
    assert covered == {"P-2204", "P-2207"}
    people = [o.crew_id for _, o in a.joint.assignments]
    assert len(set(people)) == len(people)


def test_certification_lapse(world, seed):
    a = run(
        world,
        seed,
        "certification_lapse",
        {"crew_id": "C-5417", "cert_type": "recurrent_training", "valid_to": "2026-10-15"},
    )
    (seat,) = a.seats
    assert seat.seat.pairing_id == "P-2213" and seat.options.recommended.crew_id == "C-4809"


def test_delay_beyond_the_duty_limit(world, seed):
    a = run(world, seed, "flight_delay", {"flight_id": "DX401-2026-10-14", "delay_minutes": 90})
    assert a.seats == ()
    (delay,) = a.delays
    assert delay.legs_to_drop == ("DX404-2026-10-14",)
    assert "DX404 must come off this crew" in a.summary and "₹250,000" in a.summary


def test_delay_that_breaks_overnight_rest_lists_seats(world, seed):
    a = run(world, seed, "flight_delay", {"flight_id": "KS412-2026-10-13", "delay_minutes": 180})
    assert len(a.seats) == 6
    # Whoever covers must also fly the delayed day, so the short overnight hits them too.
    assert all(s.to_dict()["status"] == "no_legal_cover" for s in a.seats)


def test_aircraft_fault_sets_delay_from_first_affected_departure(world, seed):
    a = run(
        world,
        seed,
        "aircraft_technical",
        {
            "aircraft": "VT-DXA",
            "from_utc": "2026-10-14T02:00:00Z",
            "to_utc": "2026-10-14T04:00:00Z",
        },
    )
    (delay,) = a.delays
    assert (delay.pairing_id, delay.delay_hours) == ("P-2203", 1.5)
    assert a.affected_flights == ("DX401-2026-10-14",)


def test_station_closure(world, seed):
    a = run(
        world,
        seed,
        "station_disruption",
        {
            "station": "BLR",
            "from_utc": "2026-10-15T08:00:00Z",
            "to_utc": "2026-10-15T14:00:00Z",
            "reason": "fuel-farm incident",
        },
    )
    assert len(a.affected_flights) == 13
    by_pairing = {d.pairing_id: (d.delay_hours, d.fdp_hours) for d in a.delays}
    assert by_pairing["P-2204"] == (5.75, 17.0)
    assert by_pairing["P-2232"] == (5.75, 11.0)


def test_station_closure_with_no_flights(world, seed):
    a = run(
        world,
        seed,
        "station_disruption",
        {
            "station": "GOI",
            "from_utc": "2026-10-12T19:00:00Z",
            "to_utc": "2026-10-12T20:00:00Z",
            "reason": "x",
        },
    )
    assert a.delays == () and "no rostered flights affected" in a.summary


def test_gate_change_clash_and_free_gate(world, seed):
    clash = run(world, seed, "gate_change", {"flight_id": "DX401-2026-10-13", "new_gate": "BLR-G1"})
    assert "held by NG451" in clash.summary and clash.notes
    free = run(world, seed, "gate_change", {"flight_id": "DX401-2026-10-13", "new_gate": "BLR-G9"})
    assert "the gate is free" in free.summary


def test_gate_change_for_flight_without_slot(world, seed):
    a = run(world, seed, "gate_change", {"flight_id": "DX999-2026-10-13", "new_gate": "BLR-G1"})
    assert "no boarding slot on record" in a.summary


# -- Regressions -----------------------------------------------------------------------


def test_joint_plan_agrees_with_per_seat_answers_under_a_delay(world, seed):
    # 3h on KS412 breaks the overnight rest for every rostered crew member and for anyone
    # who would cover them. No seat has legal cover, so the joint plan assigns nobody.
    a = run(world, seed, "flight_delay", {"flight_id": "KS412-2026-10-13", "delay_minutes": 180})
    assert len(a.seats) == 6
    assert all(s.to_dict()["status"] == "no_legal_cover" for s in a.seats)
    assert a.joint is None or a.joint.assignments == ()


def test_summary_states_pairing_cancellation_once_and_names_the_cause(world, seed):
    a = run(world, seed, "flight_delay", {"flight_id": "KS412-2026-10-13", "delay_minutes": 180})
    assert a.summary.count("cancelling P-2291") == 1
    assert "rest between 2026-10-13 and 2026-10-14" in a.summary


def test_mid_day_delay_drops_only_legs_from_the_late_flight_on(world, seed):
    # 15h on DX404 (the day's last leg): DX401-DX403 flew on time, so only DX404 goes.
    a = run(world, seed, "flight_delay", {"flight_id": "DX404-2026-10-15", "delay_minutes": 900})
    (delay,) = a.delays
    assert delay.legs_to_drop == ("DX404-2026-10-15",)
    assert delay.cancellation_cost_inr == 250_000


def test_aircraft_window_ending_seconds_after_departure_does_not_crash(world, seed):
    a = run(
        world,
        seed,
        "aircraft_technical",
        {
            "aircraft": "VT-DXA",
            "from_utc": "2026-10-14T02:00:00Z",
            "to_utc": "2026-10-14T02:30:10Z",
        },
    )
    (delay,) = a.delays
    assert delay.delay_hours == round(1 / 60, 2)  # rounded up to one minute, not to zero


def test_station_closure_slips_from_the_earliest_affected_leg(world, seed):
    a = run(
        world,
        seed,
        "station_disruption",
        {
            "station": "BLR",
            "from_utc": "2026-10-15T08:00:00Z",
            "to_utc": "2026-10-15T14:00:00Z",
            "reason": "x",
        },
    )
    p2204 = next(d for d in a.delays if d.pairing_id == "P-2204")
    # DX401 (02:30-05:15) flew before the closure, so it is kept.
    assert "DX401-2026-10-15" not in p2204.legs_to_drop


def test_stored_output_is_capped_and_counts_what_it_left_out(world, seed, monkeypatch):
    import crewops.analysis.disruption as module

    monkeypatch.setattr(module, "MAX_FLIGHTS", 5)
    a = run(
        world,
        seed,
        "station_disruption",
        {
            "station": "BLR",
            "from_utc": "2026-10-15T08:00:00Z",
            "to_utc": "2026-10-15T14:00:00Z",
            "reason": "x",
        },
    )
    doc = a.to_dict()
    assert len(doc["affected_flights"]) == 5
    assert len(doc["late_departures"]) == 5
    assert doc["omitted"] == {"affected_flights": 8, "late_departures": 8}


# -- Gates, notices and how each disruption gets resolved -------------------------------


def teams(a):
    return {(n["team"], n["scope"]) for n in a.notices}


def test_gate_closure_regates_onto_free_then_emergency_gates(world, seed):
    a = run(
        world,
        seed,
        "gate_closure",
        {
            "gate": "BLR-G2",
            "from_utc": "2026-10-13T00:00:00Z",
            "to_utc": "2026-10-13T08:00:00Z",
            "reason": "jet bridge fault",
        },
    )
    moves = {m.flight_no: m for m in a.stands}
    # KS433 finds an ordinary free gate. KS431's overnight slot fits no ordinary BLR gate,
    # so it takes the station's emergency gate rather than a remote stand. Nobody slips.
    assert moves["KS433"].to_gate == "BLR-G3" and not moves["KS433"].emergency
    assert moves["KS431"].to_gate == "BLR-E1" and moves["KS431"].emergency
    assert moves["KS431"].delay_minutes == 0 and moves["KS431"].alternatives == ("BLR-E2",)
    assert "BLR-G4" in moves["KS433"].alternatives  # other gates free for the controller
    assert moves["KS433"].booked == 141
    assert a.seats == () and a.late == () and a.handling["kind"] == "internal"
    assert "No re-crew required" in a.handling["text"] and "(emergency gate)" in a.summary
    assert {("gate_control", "BLR"), ("station_ops", "BLR"), ("flight_info", "NETWORK")} <= teams(a)
    assert ("ops_monitoring", "NETWORK") not in teams(a)  # no departure slips
    gate = next(n for n in a.notices if n["team"] == "gate_control")
    assert gate["contact"] == "Sameer Hegde"


def test_with_every_gate_taken_a_departure_boards_by_bus(world, seed):
    from dataclasses import replace

    # No emergency gates at BLR, so KS431 gets a remote stand, 15 minutes later,
    # and operations monitoring tells its captain.
    a = run(
        replace(world, emergency_gates={}),
        seed,
        "gate_closure",
        {
            "gate": "BLR-G2",
            "from_utc": "2026-10-13T00:00:00Z",
            "to_utc": "2026-10-13T08:00:00Z",
            "reason": "jet bridge fault",
        },
    )
    bused = next(m for m in a.stands if m.flight_no == "KS431")
    assert bused.to_gate == "remote stand" and bused.delay_minutes == 15
    assert ("ops_monitoring", "NETWORK") in teams(a)


def test_an_outside_cause_readies_the_emergency_gates(world, seed):
    a = run(
        world,
        seed,
        "gate_closure",
        {
            "gate": "BLR-G2",
            "from_utc": "2026-10-13T00:00:00Z",
            "to_utc": "2026-10-13T08:00:00Z",
            "reason": "lightning strike",
            "cause": "external",
        },
    )
    ready = [n for n in a.notices if n["why"].startswith("ready the emergency gates")]
    assert [(n["team"], n["scope"]) for n in ready] == [("station_ops", "BLR")]
    assert ("ops_monitoring", "NETWORK") in teams(a)


def test_gate_closure_with_no_departure_moves_nothing(world, seed):
    a = run(
        world,
        seed,
        "gate_closure",
        {
            "gate": "GOI-G1",
            "from_utc": "2026-10-12T18:00:00Z",
            "to_utc": "2026-10-12T18:30:00Z",
            "reason": "cleaning",
        },
    )
    # Nothing moves, so only the station's gate control and duty manager need to know.
    assert a.stands == () and teams(a) == {("gate_control", "GOI"), ("station_ops", "GOI")}
    assert a.handling["kind"] == "internal" and "nothing moves" in a.summary


def test_delay_tells_screens_captain_and_the_next_departure_at_the_gate(world, seed):
    a = run(world, seed, "flight_delay", {"flight_id": "KS412-2026-10-13", "delay_minutes": 60})
    assert {leg.flight_id for leg in a.late} == {
        "KS412-2026-10-13",
        "KS413-2026-10-13",
        "KS588-2026-10-13",
    }
    assert {("flight_info", "NETWORK"), ("ops_monitoring", "NETWORK")} <= teams(a)
    assert ("customer_care", "NETWORK") in teams(a)
    # KS588 leaves BLR-G2 an hour late; DX401's aircraft is due on that gate meanwhile.
    (nxt,) = a.next_at_gate
    assert (nxt.flight_no, nxt.gate, nxt.buffer_minutes) == ("DX401", "BLR-G2", -60)
    assert [(m.flight_no, m.to_gate) for m in a.stands] == [("DX401", "BLR-G6")]
    agent = [n for n in a.notices if n["about"] == "DX401-2026-10-14"]
    assert agent and "moves to BLR-G6" in agent[0]["message"]


def test_a_short_delay_leaves_the_next_departure_its_gate(world, seed):
    a = run(world, seed, "flight_delay", {"flight_id": "DX402-2026-10-13", "delay_minutes": 10})
    # DX404 leaves BLR-G1 ten minutes late; NG451 still boards there 65 minutes later.
    (nxt,) = a.next_at_gate
    assert (nxt.flight_no, nxt.gate, nxt.buffer_minutes) == ("NG451", "BLR-G1", 65)
    assert a.stands == () and a.handling["kind"] == "internal"
    agent = next(n for n in a.notices if n["about"] == nxt.flight_id)
    assert "free in time" in agent["message"]


def test_emails_never_name_crew(world, seed):
    for type_, details in (
        ("crew_unavailable", {"crew_id": "C-1042", "from_date": "2026-10-13", "reason": "sick"}),
        (
            "certification_lapse",
            {"crew_id": "C-5417", "cert_type": "recurrent_training", "valid_to": "2026-10-15"},
        ),
        ("flight_delay", {"flight_id": "KS412-2026-10-13", "delay_minutes": 180}),
    ):
        a = run(world, seed, type_, details)
        for n in a.notices:
            if n["channel"] == "email":
                assert "C-" not in n["message"] and "Krishnan" not in n["message"]


def test_certificate_lapse_tells_the_crew_member_in_app_and_crew_records(world, seed):
    a = run(
        world,
        seed,
        "certification_lapse",
        {"crew_id": "C-5417", "cert_type": "recurrent_training", "valid_to": "2026-10-15"},
    )
    crew = [n for n in a.notices if n["channel"] == "in_app"]
    assert [(n["about"], n["contact"]) for n in crew] == [("C-5417", "S. Krishnan")]
    assert "recurrent training expires on 2026-10-15" in crew[0]["message"]
    assert ("crew_records", "NETWORK") in teams(a)
    assert a.handling["kind"] == "recrew"


def test_several_sick_crew_are_covered_together(world, seed):
    from dataclasses import replace
    from datetime import date

    out = ("C-1042", "C-1938")
    # Open reports mark everyone they name unavailable before the analysis runs.
    sick = replace(world, unavailable={c: ((date(2026, 10, 13), "r-analysis-1"),) for c in out})
    a = run(
        sick,
        seed,
        "multiple_crew_unavailable",
        {"crew_ids": list(out), "from_date": "2026-10-13", "reason": "sick"},
    )
    assert {(s.seat.pairing_id, s.crew_out) for s in a.seats} == {
        ("P-2291", "C-1042"),
        ("P-2209", "C-1938"),
    }
    offered = {o.crew_id for s in a.seats for o in s.options.crew_options}
    assert not offered & set(out)  # neither is offered to cover the other
    picks = [o.crew_id for _, o in a.joint.assignments]
    assert len(picks) == len(set(picks)) == 2
    assert a.handling["kind"] == "recrew" and "2 seat(s) on 2 trip(s)" in a.summary


def test_a_refused_gate_change_keeps_the_gate_and_tells_only_the_station(world, seed):
    a = run(world, seed, "gate_change", {"flight_id": "DX401-2026-10-13", "new_gate": "BLR-G2"})
    assert "Keep it at BLR-G4" in a.summary and a.stands == ()
    assert teams(a) == {("gate_control", "BLR"), ("station_ops", "BLR")}


def test_the_aircraft_touched_are_recorded(world, seed):
    a = run(
        world,
        seed,
        "gate_closure",
        {
            "gate": "DEL-G1",
            "from_utc": "2026-10-13T04:00:00Z",
            "to_utc": "2026-10-13T06:00:00Z",
            "reason": "security sweep",
        },
    )
    assert a.aircraft == ("VT-DXA",)


def _double_booked(gates, stands):
    """Gates held by two departures at once after the moves (late ones held to their new
    departure)."""
    from collections import defaultdict

    moved = {m.flight_id: m for m in stands}
    held = defaultdict(list)
    for g in gates:
        m = moved.get(g.flight_id)
        if m is None:
            held[g.gate].append((g.start_utc, g.end_utc, g.flight_id))
        elif m.to_gate != "remote stand":
            held[m.to_gate].append((m.start_utc, m.end_utc, g.flight_id))
    return [
        (gate, a[2], b[2])
        for gate, spans in held.items()
        for i, a in enumerate(spans)
        for b in spans[i + 1 :]
        if a[0] < b[1] and b[0] < a[1]
    ]


def test_a_long_delay_moves_no_flight_onto_its_own_gate_or_a_taken_one(world, seed):
    # A 400-minute delay to DX401: its own later legs leave late too, so they never clash
    # with it, and nothing may be "moved" to the gate it already has.
    a = run(world, seed, "flight_delay", {"flight_id": "DX401-2026-10-12", "delay_minutes": 400})
    late = {leg.flight_id for leg in a.late}
    assert all(m.from_gate != m.to_gate for m in a.stands)
    assert not any(m.flight_id in late and "late DX401" in m.why for m in a.stands)
    assert len({n.flight_id for n in a.next_at_gate}) == len(a.next_at_gate)
    assert a.aircraft == ("VT-DXA",)  # flights only re-gated stay their own desk's


@pytest.mark.parametrize("minutes", [60, 180, 300, 420])
def test_moves_never_double_book_a_gate(world, seed, minutes):
    from dataclasses import replace as shifted
    from datetime import timedelta

    for f in sorted(world.flights)[::3]:
        a = run(world, seed, "flight_delay", {"flight_id": f, "delay_minutes": minutes})
        late = {leg.flight_id: timedelta(minutes=leg.minutes) for leg in a.late}
        assert all(m.from_gate != m.to_gate for m in a.stands), f
        # A late departure boards and leaves later: its whole slot moves with it.
        gates = [
            shifted(
                g, start_utc=g.start_utc + late[g.flight_id], end_utc=g.end_utc + late[g.flight_id]
            )
            if g.flight_id in late
            else g
            for g in seed.gates
        ]
        stands = [
            shifted(m, start_utc=m.start_utc + late[m.flight_id]) if m.flight_id in late else m
            for m in a.stands
        ]
        assert _double_booked(gates, stands) == [], (f, minutes)


def test_falling_ill_mid_day_keeps_the_legs_already_flown(world, seed):
    a = run(
        world,
        seed,
        "crew_unavailable",
        {
            "crew_id": "C-1042",
            "from_date": "2026-10-13",
            "from_utc": "2026-10-13T08:00:00Z",
            "reason": "sick",
        },
    )
    (seat,) = a.seats
    # KS412 left BLR at 07:00Z, before they fell ill; the cover joins at BOM for KS413.
    assert seat.impact.flown_before == ("KS412-2026-10-13",)
    assert seat.impact.uncovered_flights[0] == "KS413-2026-10-13"
    assert "KS412-2026-10-13" not in seat.impact.uncovered_flights
    (note,) = a.notes
    assert note.startswith("C-1042 still flies KS412; the cover joins at BOM for KS413 at 09:30Z")


def test_falling_ill_after_the_last_leg_leaves_nothing_to_cover(world, seed):
    # P-2202's last leg, DX404, leaves at 11:15Z; C-1569 falls ill at 11:45Z.
    a = run(
        world,
        seed,
        "crew_unavailable",
        {
            "crew_id": "C-1569",
            "from_date": "2026-10-13",
            "from_utc": "2026-10-13T11:45:00Z",
            "reason": "sick",
        },
    )
    assert a.seats == () and "nothing to cover" in a.summary
    assert a.notes == (
        "C-1569 had already flown every leg of P-2202 before falling ill: nothing to cover there.",
    )
