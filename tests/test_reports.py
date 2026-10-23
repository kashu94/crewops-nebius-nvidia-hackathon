"""Report parsing and the role rules. Pure, no AWS."""

import pytest

from crewops.domain import DataError
from crewops.domain.reports import Report, ReportType, Role, authorize, crew_out_of


def report(type_="crew_unavailable", details=None, roles=("reporter",), crew_id=None, **extra):
    doc = {
        "report_id": "r-00000001",
        "type": type_,
        "reported_by": {"user_id": "u-1", "roles": list(roles), "crew_id": crew_id},
        "reported_utc": "2026-10-13T05:00:00Z",
        "details": details
        if details is not None
        else {"crew_id": "C-1042", "from_date": "2026-10-13", "reason": "sick"},
    }
    doc.update(extra)
    return doc


VALID_DETAILS = {
    "crew_unavailable": {"crew_id": "C-1042", "from_date": "2026-10-13", "reason": "sick"},
    "flight_delay": {"flight_id": "DX402-2026-10-13", "delay_minutes": 90},
    "aircraft_technical": {
        "aircraft": "VT-DXA",
        "from_utc": "2026-10-14T06:00:00Z",
        "to_utc": "2026-10-14T14:00:00Z",
    },
    "station_disruption": {
        "station": "BLR",
        "from_utc": "2026-10-15T08:00:00Z",
        "to_utc": "2026-10-15T14:00:00Z",
        "reason": "weather",
    },
    "gate_change": {"flight_id": "DX401-2026-10-13", "new_gate": "BLR-G6"},
    "certification_lapse": {
        "crew_id": "C-5417",
        "cert_type": "recurrent_training",
        "valid_to": "2026-10-15",
    },
    "multiple_crew_unavailable": {
        "crew_ids": ["C-1938", "C-1042"],
        "from_date": "2026-10-13",
        "reason": "sick",
    },
    "gate_closure": {
        "gate": "BLR-G2",
        "from_utc": "2026-10-13T00:00:00Z",
        "to_utc": "2026-10-13T08:00:00Z",
        "reason": "jet bridge fault",
    },
}


@pytest.mark.parametrize("type_", list(VALID_DETAILS))
def test_every_type_parses_and_round_trips(type_):
    r = Report.from_dict(report(type_, VALID_DETAILS[type_]))
    assert r.report_type is ReportType(type_)
    assert Report.from_dict(r.to_dict()) == r


def test_details_are_normalised():
    r = Report.from_dict(
        report(details={"crew_id": " C-1042 ", "from_date": "2026-10-13", "reason": "sick"})
    )
    assert r.details == {
        "crew_id": "C-1042",
        "from_date": "2026-10-13",
        "to_date": "2026-10-13",
        "reason": "sick",
    }


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        (report(report_id="short"), "report_id"),
        (report(report_id="has spaces in it"), "report_id"),
        (report(type_="volcano"), "type: unknown"),
        (report(roles=()), "roles: expected a non-empty list"),
        (report(roles=("pilot",)), "unknown role"),
        (report(reported_utc="2026-10-13T05:00:00"), "no timezone"),
        (
            report(details={"crew_id": "C-1", "from_date": "2026-10-13", "reason": "bored"}),
            "reason",
        ),
        (
            report(
                details={
                    "crew_id": "C-1",
                    "from_date": "2026-10-13",
                    "to_date": "2026-10-12",
                    "reason": "sick",
                }
            ),
            "before from_date",
        ),
        (report("flight_delay", {"flight_id": "X", "delay_minutes": 0}), "1 to 1440"),
        (report("flight_delay", {"flight_id": "X", "delay_minutes": 1.5}), "whole number"),
        (report("flight_delay", {"flight_id": "X", "delay_minutes": True}), "whole number"),
        (
            report(
                "aircraft_technical",
                {
                    "aircraft": "A",
                    "from_utc": "2026-10-14T06:00:00Z",
                    "to_utc": "2026-10-14T05:00:00Z",
                },
            ),
            "after from_utc",
        ),
        (
            report(
                "station_disruption",
                {
                    "station": "Bengaluru",
                    "from_utc": "2026-10-15T08:00:00Z",
                    "to_utc": "2026-10-15T14:00:00Z",
                    "reason": "x",
                },
            ),
            "3-letter",
        ),
        (
            report(
                "certification_lapse",
                {"crew_id": "C-1", "cert_type": "visa", "valid_to": "2026-10-15"},
            ),
            "cert_type: unknown",
        ),
        (report(narrative="x" * 1001), "narrative"),
        (report(details=[]), "details: expected an object"),
    ],
)
def test_bad_reports_are_refused_with_the_field_named(doc, message):
    with pytest.raises(DataError, match=message):
        Report.from_dict(doc)


@pytest.mark.parametrize("role", ["reporter", "controller", "approver"])
def test_operational_roles_may_report_anything(role):
    for type_, details in VALID_DETAILS.items():
        assert authorize(Report.from_dict(report(type_, details, roles=(role,)))) is None


@pytest.mark.parametrize("role", ["auditor", "admin", "super_admin"])
def test_observers_and_admins_may_not_report(role):
    reason = authorize(Report.from_dict(report(roles=(role,))))
    assert reason and "may not file reports" in reason


def test_crew_may_report_only_their_own_unavailability():
    own = report(roles=("crew",), crew_id="C-1042")
    assert authorize(Report.from_dict(own)) is None

    someone_else = report(roles=("crew",), crew_id="C-2087")
    assert "their own" in authorize(Report.from_dict(someone_else))

    no_id = report(roles=("crew",))
    assert "no crew id" in authorize(Report.from_dict(no_id))

    other_type = report(
        "flight_delay", VALID_DETAILS["flight_delay"], roles=("crew",), crew_id="C-1042"
    )
    assert "their own" in authorize(Report.from_dict(other_type))


def test_any_operational_role_wins_over_others():
    mixed = report(roles=("admin", "controller"))
    assert authorize(Report.from_dict(mixed)) is None
    assert Role.ADMIN in Report.from_dict(mixed).reported_by.roles


def test_several_crew_in_one_report():
    r = Report.from_dict(
        report("multiple_crew_unavailable", VALID_DETAILS["multiple_crew_unavailable"])
    )
    assert r.details == {
        "crew_ids": ["C-1042", "C-1938"],
        "from_date": "2026-10-13",
        "to_date": "2026-10-13",
        "reason": "sick",
    }
    assert crew_out_of(r.report_type, r.details) == ("C-1042", "C-1938")
    for bad, message in (
        (["C-1042"], "2 to 12"),
        ("C-1042", "2 to 12"),
        (["C-1042", "C-1042"], "listed twice"),
        ([f"C-{i}" for i in range(13)], "2 to 12"),
    ):
        details = {**VALID_DETAILS["multiple_crew_unavailable"], "crew_ids": bad}
        with pytest.raises(DataError, match=message):
            Report.from_dict(report("multiple_crew_unavailable", details))


def test_crew_may_not_report_others_together_with_themselves():
    details = {**VALID_DETAILS["multiple_crew_unavailable"], "crew_ids": ["C-1042", "C-2087"]}
    doc = report("multiple_crew_unavailable", details, roles=("crew",), crew_id="C-1042")
    assert "their own" in authorize(Report.from_dict(doc))


def test_a_gate_is_named_by_station_and_number():
    details = {**VALID_DETAILS["gate_closure"], "gate": "Gate 2"}
    with pytest.raises(DataError, match="like BLR-G2"):
        Report.from_dict(report("gate_closure", details))
    late = {**VALID_DETAILS["gate_closure"], "to_utc": "2026-10-12T00:00:00Z"}
    with pytest.raises(DataError, match="after from_utc"):
        Report.from_dict(report("gate_closure", late))
    assert crew_out_of("gate_closure", VALID_DETAILS["gate_closure"]) == ()


def test_unavailable_from_part_way_through_the_first_day():
    details = {**VALID_DETAILS["crew_unavailable"], "from_utc": "2026-10-13T09:00:00Z"}
    r = Report.from_dict(report("crew_unavailable", details))
    assert r.details["from_utc"] == "2026-10-13T09:00:00Z"
    wrong_day = {**details, "from_utc": "2026-10-14T09:00:00Z"}
    with pytest.raises(DataError, match="must be on from_date"):
        Report.from_dict(report("crew_unavailable", wrong_day))


def test_a_gate_closure_says_whether_the_cause_is_internal_or_external():
    r = Report.from_dict(report("gate_closure", VALID_DETAILS["gate_closure"]))
    assert r.details["cause"] == "internal"  # by default
    out = {**VALID_DETAILS["gate_closure"], "cause": "external"}
    assert Report.from_dict(report("gate_closure", out)).details["cause"] == "external"
    with pytest.raises(DataError, match="cause"):
        Report.from_dict(report("gate_closure", {**out, "cause": "aliens"}))
