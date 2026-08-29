from pathlib import Path

import pytest

from backend.contracts import BadgeDesignation, Product
from backend.ingest import (
    load_offer_matrix,
    load_submissions,
    normalize_states_targeted,
    parse_pct_range,
)
from backend.ingest.parsers import US_STATES

TESTDATA = Path(__file__).parent / "testdata"
MATRIX_CSV = TESTDATA / "clearpath_offer_matrix.csv"
SUBMISSIONS_CSV = TESTDATA / "ck_placement_submissions.csv"


def test_offer_matrix_rows_and_spot_values():
    cells = load_offer_matrix(MATRIX_CSV)
    assert len(cells) == 7
    by_id = {c.offer_id: c for c in cells}

    pl60 = by_id["PL-60-A"]
    assert pl60.product == Product.PERSONAL_LOAN
    assert "IL" in pl60.states_excluded
    assert pl60.apr_max == 35.99
    assert pl60.fee_deducted_from_proceeds is True

    prescreen = by_id["CC-PLAT-PS"]
    assert prescreen.is_firm_offer is True
    assert prescreen.badge_designation_allowed == BadgeDesignation.PRE_APPROVED
    assert prescreen.intro_apr_pct == 0.0
    assert prescreen.intro_period_months == 15

    card = by_id["CC-PLAT"]
    assert card.term_months is None  # empty cell -> None
    assert card.annual_fee == 0.0
    assert card.states_excluded == []  # "none" -> []

    arm = by_id["MTG-ARM"]
    assert arm.apr_type == "variable"
    assert arm.effective_start.isoformat() == "2026-08-28"


def test_submissions_rows_and_spot_values():
    subs = load_submissions(SUBMISSIONS_CSV)
    assert len(subs) == 4
    by_id = {s.submission_id: s for s in subs}

    loan = by_id["SUB-2026-0142"]
    assert loan.offer_ids == ["PL-36-A", "PL-60-A"]
    assert loan.product == Product.PERSONAL_LOAN
    assert "{{approval_odds}}" in loan.dynamic_slots
    assert loan.asset_files == ["mock_pl_card_v7.html", "mock_pl_card_v7.png"]
    assert loan.states_targeted == "ALL except IA;WV"
    assert loan.sla_due.isoformat() == "2026-08-29"

    prescreen = by_id["SUB-2026-0144"]
    assert prescreen.badge_text == "Pre-approved"
    assert "short-form opt-out notice" in prescreen.disclosures_included


def test_normalize_states_targeted():
    assert normalize_states_targeted("ALL") == US_STATES
    minus_two = normalize_states_targeted("ALL except IA;WV")
    assert len(minus_two) == len(US_STATES) - 2
    assert "IA" not in minus_two and "WV" not in minus_two
    assert "IL" in minus_two
    assert normalize_states_targeted("CA;TX") == ["CA", "TX"]
    assert normalize_states_targeted("") == []


def test_parse_pct_range():
    assert parse_pct_range("1.0-6.0") == (1.0, 6.0)
    assert parse_pct_range("4") == (4.0, 4.0)
    assert parse_pct_range("") is None
    assert parse_pct_range(None) is None


def test_us_states_count():
    assert len(US_STATES) == 51  # 50 states + DC
    assert len(set(US_STATES)) == 51
