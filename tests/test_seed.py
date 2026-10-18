import json
import shutil
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from crewops.domain import DataError, PairingSeat, Rank
from crewops.ingestion.seed import (
    SEED_FILES,
    Severity,
    default_seed_dir,
    errors,
    load_seed,
    validate,
)


@pytest.fixture(scope="module")
def seed():
    return load_seed()


def test_seed_counts(seed):
    assert len(seed.crew) == 150
    assert len(seed.flights) == 147
    assert len(seed.pairings) == 39
    assert len(seed.reserves) == 16
    assert len(seed.duty_clocks) == 150
    assert len(seed.rules) == 7
    assert len(seed.gates) == 147
    assert seed.snapshot_utc.isoformat() == "2026-10-12T18:00:00+00:00"


def test_seed_has_no_errors(seed):
    assert errors(validate(seed)) == []


def test_known_certificate_oddity_is_a_warning(seed):
    warnings = [i for i in validate(seed) if i.severity is Severity.WARNING]
    assert any(i.entity == "C-2087" and "valid_from" in i.message for i in warnings)


def test_flagged_roster_exception_is_loaded(seed):
    assert [(e.crew_id, e.rule_id) for e in seed.exceptions] == [("C-5417", "RULE-CERT-06")]


def test_role_rank_mismatch_is_an_error(seed):
    first = seed.pairings[0]
    captain = first.holder(Rank.CAPTAIN)[0]
    seats = tuple(
        PairingSeat(s.crew_id, Rank.CABIN_CREW) if s.crew_id == captain else s for s in first.crew
    )
    broken = replace(seed, pairings=(replace(first, crew=seats), *seed.pairings[1:]))
    assert any("rostered as Cabin Crew" in i.message for i in errors(validate(broken)))


def test_double_booking_is_an_error(seed):
    a, b = seed.pairings[0], seed.pairings[1]
    intruder = a.holder(Rank.CAPTAIN)[0]
    day = b.first_day
    overlapping = replace(
        b,
        days=(
            replace(day, report_utc=a.first_day.report_utc, release_utc=a.first_day.release_utc),
        ),
        crew=(PairingSeat(intruder, Rank.CAPTAIN),),
    )
    broken = replace(seed, pairings=(a, overlapping, *seed.pairings[2:]))
    assert any(
        i.entity == intruder and "double-booked" in i.message for i in errors(validate(broken))
    )


def test_unknown_flight_in_pairing_is_an_error(seed):
    p = seed.pairings[0]
    day = replace(p.first_day, flight_ids=("DX999-2026-10-12",))
    broken = replace(seed, pairings=(replace(p, days=(day,)), *seed.pairings[1:]))
    assert any("unknown flight DX999" in i.message for i in errors(validate(broken)))


def test_late_report_time_is_a_warning(seed):
    p = seed.pairings[0]
    day = replace(p.first_day, report_utc=p.first_day.report_utc + timedelta(minutes=15))
    broken = replace(seed, pairings=(replace(p, days=(day, *p.days[1:])), *seed.pairings[1:]))
    issues = validate(broken)
    assert errors(issues) == []
    assert any("report is not 60 min" in i.message for i in issues)


def test_missing_file_names_it(tmp_path):
    with pytest.raises(DataError, match="crew.json: file not found"):
        load_seed(tmp_path)


def test_bad_record_names_file_and_index(tmp_path: Path):
    for name in SEED_FILES:
        shutil.copy(default_seed_dir() / name, tmp_path / name)
    crew = json.loads((tmp_path / "crew.json").read_text())
    crew[3]["rank"] = "Pilot"
    (tmp_path / "crew.json").write_text(json.dumps(crew))
    with pytest.raises(DataError, match=r"crew\[3\]\.rank: unknown value 'Pilot'"):
        load_seed(tmp_path)


def test_invalid_json_names_line(tmp_path: Path):
    for name in SEED_FILES:
        shutil.copy(default_seed_dir() / name, tmp_path / name)
    (tmp_path / "costs.json").write_text("{ not json")
    with pytest.raises(DataError, match="costs.json: invalid JSON at line 1"):
        load_seed(tmp_path)


def test_duplicate_keyed_records_are_errors(seed):
    cert = seed.certifications[0]
    broken = replace(seed, certifications=(*seed.certifications, cert))
    assert any("duplicate certification" in i.message for i in errors(validate(broken)))


def test_three_airlines_each_with_its_own_aircraft_and_crew(seed):
    assert [a.code for a in seed.airlines] == ["DX", "KS", "NG"]
    owner = {tail: a.code for a in seed.airlines for tail in a.aircraft}
    assert all(f.flight_no.startswith(owner[f.aircraft]) for f in seed.flights)
    crew = {c.crew_id: c for c in seed.crew}
    for p in seed.pairings:
        assert {crew[s.crew_id].airline for s in p.crew} == {owner[p.aircraft]}, p.pairing_id
    assert {g.station for g in seed.emergency_gates} == {f.dep_station for f in seed.flights}


def test_crew_rostered_for_another_airline_is_an_error(seed):
    p = seed.pairings[0]  # a Deccan Express trip
    stranger = next(c for c in seed.crew if c.airline == "KS" and c.rank is Rank.CAPTAIN)
    seats = (PairingSeat(stranger.crew_id, Rank.CAPTAIN), *p.crew[1:])
    broken = replace(seed, pairings=(replace(p, crew=seats), *seed.pairings[1:]))
    assert any("works for KS, not DX" in i.message for i in errors(validate(broken)))


def test_a_command_upgrade_is_only_for_first_officers(seed):
    captain = next(c for c in seed.crew if c.rank is Rank.CAPTAIN)
    broken = replace(
        seed,
        crew=tuple(replace(c, command_qualified=True) if c is captain else c for c in seed.crew),
    )
    assert any("only a first officer" in i.message for i in errors(validate(broken)))
