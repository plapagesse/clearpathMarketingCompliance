"""In-process platform client for the prequal engine.

Engine/checker layers call this directly (no HTTP round-trip inside the
process); the /api/prequal endpoint wraps the same function.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from backend.contracts import Product
from backend.prequal.engine import ApplicantProfile, prequalify


def prequalify_client(
    profile: dict,
    product: str | Product,
    *,
    as_of: date | None = None,
    inject_drift: bool = False,
    session: Session | None = None,
) -> dict:
    """Validate a raw profile dict, run the engine, return a JSON-ready dict."""
    result = prequalify(
        ApplicantProfile.model_validate(profile),
        Product(product),
        as_of=as_of,
        inject_drift=inject_drift,
        session=session,
    )
    return result.model_dump(mode="json")
