"""Mock decisioning engine simulating ClearPath's prequalification API.

Design principle (see build plan): the offer matrix is the ENVELOPE; this
engine prices a POINT inside it — consistency by construction. Every offer is
derived from the offer-cell rows in the DB (latest matrix version); nothing is
hardcoded. The result echoes offer_cell_id + offer_matrix_version so a
personalized rendering can later be verified against exactly what was served.

Determinism: no randomness, no wall-clock dependence in pricing. The APR is a
pure function of (profile, cell): credit score sets the position inside the
cell's [apr_min, apr_max] band (better score → lower APR; the floor is only
reachable at top scores), and a stable hash of the non-score profile fields
adds a small fixed spread so different applicants with the same score don't
all price identically. Effective-date eligibility uses `as_of` (defaults to
today for the live API; tests pass a fixed date for reproducibility).
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Iterable

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.contracts import BadgeDesignation, OfferCell, Product
from backend.db.models import OfferCellRow
from backend.db.session import get_session, latest_matrix_version

# Share of the APR band controlled by credit score vs. the profile-hash spread.
SCORE_WEIGHT = 0.9
HASH_WEIGHT = 0.1
TOP_SCORE = 850


class ApplicantProfile(BaseModel):
    credit_score: int = Field(ge=300, le=850)
    annual_income: float = Field(gt=0)
    state: str = Field(min_length=2, max_length=2, description="Two-letter state code")
    requested_amount: float | None = Field(default=None, gt=0)


class PrequalResult(BaseModel):
    decision: str  # "approved" | "declined"
    product: Product
    offer_cell_id: str | None = None
    offer_matrix_version: str | None = None
    apr: float | None = None
    amount: float | None = None
    term_months_options: list[int] = Field(default_factory=list)
    origination_fee_pct: float | None = None
    intro_apr_pct: float | None = None
    intro_period_months: int | None = None
    annual_fee: float | None = None
    monthly_payment_example: float | None = None
    badge_designation: BadgeDesignation | None = None
    decline_reason: str | None = None
    drift_injected: bool = False


def _stable_unit_hash(*parts: object) -> float:
    """Deterministic value in [0, 1) from the given parts."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def _price_apr(profile: ApplicantProfile, cell: OfferCell) -> float:
    """Position in the cell's APR band: score-driven, hash-jittered, monotone in score."""
    if cell.apr_min is None or cell.apr_max is None:
        raise ValueError(f"cell {cell.offer_id} has no APR band")
    floor_score = cell.min_credit_score or 640
    span = max(TOP_SCORE - floor_score, 1)
    score_pos = min(max((profile.credit_score - floor_score) / span, 0.0), 1.0)
    # Jitter is independent of credit_score so pricing stays monotone in score.
    jitter = _stable_unit_hash(profile.annual_income, profile.state, profile.requested_amount, cell.offer_id)
    blend = (1.0 - score_pos) * SCORE_WEIGHT + jitter * HASH_WEIGHT
    return round(cell.apr_min + (cell.apr_max - cell.apr_min) * blend, 2)


def _fee_pct(profile: ApplicantProfile, cell: OfferCell) -> float | None:
    if not cell.origination_fee_pct:
        return None
    lo, _, hi = cell.origination_fee_pct.partition("-")
    lo_f, hi_f = float(lo), float(hi or lo)
    jitter = _stable_unit_hash(profile.annual_income, profile.state, cell.offer_id, "fee")
    return round(lo_f + (hi_f - lo_f) * jitter, 1)


def _monthly_payment(principal: float, apr: float, term_months: int) -> float:
    r = apr / 100 / 12
    if r == 0:
        return round(principal / term_months, 2)
    return round(principal * r * (1 + r) ** term_months / ((1 + r) ** term_months - 1), 2)


def _eligible(cells: Iterable[OfferCell], profile: ApplicantProfile, as_of: date) -> tuple[list[OfferCell], str | None]:
    """Filter to eligible cells; on empty, return the most informative decline reason."""
    cells = list(cells)
    # Firm-offer prescreen cells are mail-campaign inventory, not real-time
    # prequal inventory — the marketplace API never serves them.
    cells = [c for c in cells if not c.is_firm_offer]
    cells = [c for c in cells if c.effective_start <= as_of <= c.effective_end]
    if not cells:
        return [], "no_offers_currently_effective"
    in_state = [c for c in cells if profile.state.upper() not in c.states_excluded]
    if not in_state:
        return [], "state_not_available"
    scored = [c for c in in_state if c.min_credit_score is None or profile.credit_score >= c.min_credit_score]
    if not scored:
        return [], "credit_score_below_minimum"
    if profile.requested_amount is not None:
        sized = [
            c
            for c in scored
            if (c.amount_min is None or profile.requested_amount >= c.amount_min)
            and (c.amount_max is None or profile.requested_amount <= c.amount_max)
        ]
        if not sized:
            return [], "amount_out_of_range"
        return sized, None
    return scored, None


def prequalify(
    profile: ApplicantProfile,
    product: Product,
    *,
    as_of: date | None = None,
    inject_drift: bool = False,
    session: Session | None = None,
) -> PrequalResult:
    """Price a prequalification decision from the current offer matrix.

    inject_drift=True is a DEMO/TEST HOOK ONLY: it returns an offer priced
    below the cell's apr_min — deliberately outside the matrix envelope — so
    the checker's truthfulness detection can be exercised. Never enable it in
    a real flow.
    """
    as_of = as_of or date.today()
    owns_session = session is None
    session = session or get_session()
    try:
        version = latest_matrix_version(session)
        if version is None:
            return PrequalResult(decision="declined", product=product, decline_reason="no_offer_matrix_loaded")
        rows = session.execute(
            select(OfferCellRow).where(
                OfferCellRow.offer_matrix_version == version,
                OfferCellRow.product == product.value,
            )
        ).scalars()
        eligible, reason = _eligible((r.to_contract() for r in rows), profile, as_of)
        if not eligible:
            return PrequalResult(decision="declined", product=product, decline_reason=reason)

        # Best offer for the consumer: lowest reachable APR band; stable tiebreak.
        cell = sorted(eligible, key=lambda c: (c.apr_min if c.apr_min is not None else 99.0, c.offer_id))[0]
        apr = _price_apr(profile, cell)
        if inject_drift:
            apr = round((cell.apr_min or 1.0) - 1.0, 2)

        amount = profile.requested_amount or cell.amount_max
        terms = sorted({c.term_months for c in eligible if c.term_months and c.offer_name.split()[0] == cell.offer_name.split()[0]})
        payment = None
        if product != Product.CREDIT_CARD and amount and cell.term_months:
            payment = _monthly_payment(amount, apr, cell.term_months)

        return PrequalResult(
            decision="approved",
            product=product,
            offer_cell_id=cell.offer_id,
            offer_matrix_version=version,
            apr=apr,
            amount=amount,
            term_months_options=terms or ([cell.term_months] if cell.term_months else []),
            origination_fee_pct=_fee_pct(profile, cell),
            intro_apr_pct=cell.intro_apr_pct,
            intro_period_months=cell.intro_period_months,
            annual_fee=cell.annual_fee,
            monthly_payment_example=payment,
            badge_designation=cell.badge_designation_allowed,
            drift_injected=inject_drift,
        )
    finally:
        if owns_session:
            session.close()
