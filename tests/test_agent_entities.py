"""Entity extraction: exact patterns, never similarity."""

from agent_fixtures import vocab

from crewops.agent.entities import extract, stated_bases, stated_ranks, stated_ratings


def ents(text):
    return extract(text, vocab())


def test_vocabulary_comes_from_the_roster():
    v = vocab()
    assert "BLR" in v.stations and "VT-DXA" in v.aircraft
    assert str(v.week_start) == "2026-10-12" and str(v.week_end) == "2026-10-18"
    assert str(v.today) == "2026-10-12"


def test_ids_dates_roles_and_stations():
    e = ents("Can Captain C-1042 cover P-2291 on 13 Oct out of BLR?")
    assert e.crew_ids == ["C-1042"] and e.pairing_ids == ["P-2291"]
    assert e.dates == ["2026-10-13"] and e.stations == ["BLR"] and e.roles == ["Captain"]


def test_flight_id_is_not_also_a_bare_flight_number():
    e = ents("Move C-2087 onto KS412-2026-10-13")
    assert e.flight_ids == ["KS412-2026-10-13"] and e.flight_nos == []


def test_malformed_ids_are_kept_apart():
    e = ents("Is C-10 on P-22 or DX9999?")
    assert e.crew_ids == [] and e.malformed_crew_ids == ["C-10"]
    assert e.malformed_pairing_ids == ["P-22"] and e.malformed_flight_nos == ["DX9999"]


def test_cities_tails_and_unknown_codes():
    e = ents("Flights from Mumbai to XYZ on VT-DXB")
    assert e.stations == ["BOM"] and e.unknown_stations == ["XYZ"] and e.aircraft == ["VT-DXB"]
    assert "DXB" not in e.unknown_stations  # the letters of a tail, not a station


def test_date_forms():
    assert ents("on 2026-10-14").dates == ["2026-10-14"]
    assert ents("on Oct 15").dates == ["2026-10-15"]
    assert ents("on the 16th").dates == ["2026-10-16"]
    assert ents("out 13–14 Oct").dates == ["2026-10-13", "2026-10-14"]
    assert ents("who is free today").dates == ["2026-10-12"]
    assert ents("and tomorrow?").dates == ["2026-10-13"]
    assert ents("on 31 Sep").dates == []


def test_a_comparison_word_must_sit_beside_the_date():
    assert ents("flights before 17 Oct").date_range == {"lt": "2026-10-17"}
    assert ents("between 13 Oct and 15 Oct").date_range == {
        "gte": "2026-10-13",
        "lte": "2026-10-15",
    }
    assert ents("VT-DXA is delayed 90 minutes before DX401 on 14 Oct").date_range is None


def test_delay_horizon_times_gates_and_certs():
    question = "If DX401 is delayed by 2 hours at 06:30Z, does BLR-G2 clear? "
    e = ents(question + "Licence expiring in the next 30 days")
    assert e.delay_minutes == 120 and e.times == ["06:30"] and e.gate_numbers == ["BLR-G2"]
    assert e.horizon_days == 30 and e.cert_types == ["licence"]
    assert ents("a 90-minute delay").delay_minutes == 90


def test_names_are_candidates_only():
    assert ents("Is A. Nair paired with Menon?").names == ["A. Nair", "Menon"]
    assert ents("Who is the Captain on Monday?").names == []  # a weekday, not a person
    assert ents("May I see Menon's hours?").names == ["Menon"]


def test_stated_attributes():
    assert stated_ranks("If FO C-2087 moves") == [("C-2087", "First Officer")]
    assert stated_ranks("cover P-2291 as Captain") == []
    v = vocab()
    assert stated_bases("Can C-2210 (DEL base) cover", v) == [("C-2210", "DEL")]
    assert stated_bases("the DEL-based captain C-1042", v) == [("C-1042", "DEL")]
    assert stated_ratings("C-3316 is A320-rated") == [("C-3316", "A320")]


def test_ordinary_capitals_are_not_attempted_stations():
    for text in (
        "Which captains are rated on ATR-72?",
        "Who covers KS412? FYI the CEO asked",
        "Send an SMS at 10:00 GMT",
    ):
        assert ents(text).unknown_stations == [], text
    assert ents("flights from BLX").unknown_stations == ["BLX"]
    assert ents("flights to XYZ").unknown_stations == ["XYZ"]


def test_dates_review_cases():
    e = ents("Who is on reserve at BLR between 13 and 15 Oct?")
    assert e.dates == ["2026-10-13", "2026-10-15"]
    assert e.date_range == {"gte": "2026-10-13", "lte": "2026-10-15"}
    assert ents("Who may 3 captains replace?").dates == []
    assert ents("on 3 May").dates == ["2026-05-03"]
    assert ents("Report at 2026-10-14T08:30:00Z").dates == ["2026-10-14"]


def test_currently_does_not_turn_tomorrow_into_today():
    assert ents("Which flights are currently scheduled for tomorrow?").dates == ["2026-10-13"]
    assert ents("Who is on reserve right now?").dates == ["2026-10-12"]
    assert ents("today, not tomorrow").dates == ["2026-10-12"]
