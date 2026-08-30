from datetime import date
from pathlib import Path

import pytest

from backend.contracts import BadgeDesignation, Product
from backend.db import get_session, import_offer_matrix, init_db
from backend.prequal.engine import ApplicantProfile, prequalify

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
AS_OF = date(2026, 8, 29)  # fixed: inside every fixture cell's effective window


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARPATH_DB", f"sqlite:///{tmp_path}/test.db")
    import backend.db.session as dbs

    monkeypatch.setattr(dbs, "_engine", None)
    monkeypatch.setattr(dbs, "_session_factory", None)
    init_db(reset=True)
    s = get_session()
    import_offer_matrix(s, FIXTURES / "offer_matrix.csv")
    yield s
    s.close()


def profile(**kw) -> ApplicantProfile:
    base = dict(credit_score=720, annual_income=90000, state="CA", requested_amount=15000)
    base.update(kw)
    return ApplicantProfile(**base)


def test_determinism(session):
    a = prequalify(profile(), Product.PERSONAL_LOAN, as_of=AS_OF, session=session)
    b = prequalify(profile(), Product.PERSONAL_LOAN, as_of=AS_OF, session=session)
    assert a == b
    assert a.decision == "approved"
    assert a.offer_matrix_version and a.offer_cell_id


def test_score_monotonicity(session):
    aprs = [
        prequalify(profile(credit_score=s), Product.PERSONAL_LOAN, as_of=AS_OF, session=session).apr
        for s in (640, 680, 720, 780, 850)
    ]
    assert aprs == sorted(aprs, reverse=True), f"APR must not rise with score: {aprs}"


def test_state_exclusion_il_never_gets_pl60(session):
    r = prequalify(profile(state="IL"), Product.PERSONAL_LOAN, as_of=AS_OF, session=session)
    assert r.decision == "approved"
    assert r.offer_cell_id != "PL-60-A"
    # IA is excluded from every PL cell → declined outright
    r_ia = prequalify(profile(state="IA"), Product.PERSONAL_LOAN, as_of=AS_OF, session=session)
    assert r_ia.decision == "declined"
    assert r_ia.decline_reason == "state_not_available"


def test_score_gating(session):
    r = prequalify(profile(credit_score=600), Product.PERSONAL_LOAN, as_of=AS_OF, session=session)
    assert r.decision == "declined"
    assert r.decline_reason == "credit_score_below_minimum"


def test_amount_bounds(session):
    r = prequalify(profile(requested_amount=1000), Product.PERSONAL_LOAN, as_of=AS_OF, session=session)
    assert r.decision == "declined"
    assert r.decline_reason == "amount_out_of_range"


def test_apr_always_within_cell_band(session):
    cells = {"PL-36-A": (8.99, 29.99), "PL-60-A": (11.49, 35.99)}
    for score in range(640, 851, 10):
        for income in (30000, 90000, 250000):
            r = prequalify(
                profile(credit_score=score, annual_income=income),
                Product.PERSONAL_LOAN,
                as_of=AS_OF,
                session=session,
            )
            lo, hi = cells[r.offer_cell_id]
            assert lo <= r.apr <= hi, f"{r.offer_cell_id}: {r.apr} outside [{lo}, {hi}]"


def test_drift_hook_prices_outside_matrix(session):
    r = prequalify(profile(), Product.PERSONAL_LOAN, as_of=AS_OF, session=session, inject_drift=True)
    assert r.drift_injected and r.decision == "approved"
    assert r.apr < 8.99  # below every PL cell's apr_min


def test_mortgage_never_preapproved_and_card_terms(session):
    m = prequalify(profile(requested_amount=400000), Product.MORTGAGE_PREQUAL, as_of=AS_OF, session=session)
    assert m.decision == "approved"
    assert m.badge_designation == BadgeDesignation.PREQUALIFIED
    assert m.monthly_payment_example and m.monthly_payment_example > 0

    c = prequalify(profile(requested_amount=None), Product.CREDIT_CARD, as_of=AS_OF, session=session)
    assert c.decision == "approved"
    # firm-offer prescreen cell must never be served by the real-time API
    assert c.offer_cell_id != "CC-PLAT-PS"
    assert c.intro_apr_pct == 0.0 and c.intro_period_months == 15
    assert c.monthly_payment_example is None
