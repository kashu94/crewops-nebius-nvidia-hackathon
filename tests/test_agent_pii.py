"""Personal data is removed at the boundary; operational identity is kept."""

from crewops.agent.pii import redact, redact_text


def test_contact_and_government_ids_are_removed():
    text = (
        "Mail a.nair@example.com or +91 98765 43210; PAN ABCDE1234F, passport A1234567, "
        "Aadhaar 1234 5678 9012, card 4111 1111 1111 1111, host 10.0.0.12"
    )
    out = redact_text(text)
    for secret in ("a.nair@", "98765", "ABCDE1234F", "A1234567", "5678", "4111", "10.0.0.12"):
        assert secret not in out, secret


def test_operational_data_is_kept():
    text = "A. Nair (C-1042) on P-2291, KS412-2026-10-13 at 06:00Z, ₹18,500, VT-DXC, 1500000"
    assert redact_text(text) == text


def test_a_long_number_that_fails_luhn_is_not_a_card():
    assert redact_text("ref 1234 5678 9012 3456") == "ref 1234 5678 9012 3456"


def test_sensitive_fields_are_removed_at_any_depth():
    out = redact({"crew": [{"name": "A. Nair", "home_address": "12 MG Road", "dob": "1980-01-01"}]})
    assert out == {"crew": [{"name": "A. Nair", "home_address": "[removed]", "dob": "[removed]"}]}


def test_dates_and_flight_ids_are_never_taken_for_cards():
    for text in (
        "2026-10-12 2026-10-14",
        "2026-10-13 2026-10-13",
        "DX404-2026-10-14 2026-10-14T08:30:00Z",
    ):
        assert redact_text(text) == text


def test_indian_phone_formats():
    for number in ("98765-43210", "022-2345-6789", "080 2345 6789"):
        assert number not in redact_text(f"call {number} now"), number


def test_plain_amounts_are_not_phones():
    assert redact_text("costs 18500 24000") == "costs 18500 24000"


def test_mobile_needs_a_phone_word():
    assert redact_text("totals 70000 80000") == "totals 70000 80000"
    assert "98765" not in redact_text("call him on 98765 43210")
