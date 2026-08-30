"""Parsers for the two intake CSV formats.

Column formats follow fixtures/clearpath_offer_matrix.csv and
fixtures/ck_placement_submissions.csv:
- semicolon-delimited lists ("IA;WV", "PL-36-A;PL-60-A")
- percent ranges as strings ("1.0-6.0")
- "ALL" / "ALL except X;Y" state-targeting syntax
- empty cells for fields that don't apply to a product
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from backend.contracts import OfferCell, Submission, SubmissionMode

US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
]


def _semilist(raw: str | None) -> list[str]:
    if raw is None:
        return []
    raw = raw.strip()
    if not raw or raw.lower() == "none":
        return []
    return [part.strip() for part in raw.split(";") if part.strip()]


def _opt_float(raw: str | None) -> float | None:
    raw = (raw or "").strip()
    return float(raw) if raw else None


def _opt_int(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw else None


def _opt_bool(raw: str | None) -> bool | None:
    raw = (raw or "").strip().upper()
    if not raw:
        return None
    return raw == "TRUE"


def _opt_date(raw: str | None) -> date | None:
    raw = (raw or "").strip()
    return date.fromisoformat(raw) if raw else None


def parse_pct_range(raw: str | None) -> tuple[float, float] | None:
    """"1.0-6.0" -> (1.0, 6.0); "4" -> (4.0, 4.0); empty -> None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if "-" in raw:
        lo, hi = raw.split("-", 1)
        return (float(lo), float(hi))
    value = float(raw)
    return (value, value)


def normalize_states_targeted(raw: str | None) -> list[str]:
    """Expand targeting syntax to an explicit state list.

    "ALL" -> all 50 states + DC; "ALL except IA;WV" -> everything but those;
    otherwise treated as a plain semicolon list ("CA;TX").
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    lowered = raw.lower()
    if lowered == "all":
        return list(US_STATES)
    if lowered.startswith("all except"):
        excluded = {s.upper() for s in _semilist(raw[len("ALL except"):])}
        return [s for s in US_STATES if s not in excluded]
    return [s.upper() for s in _semilist(raw)]


def load_offer_matrix(csv_path: str | Path) -> list[OfferCell]:
    """Parse an offer-matrix CSV into contract OfferCell models."""
    cells: list[OfferCell] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            cells.append(
                OfferCell(
                    offer_id=row["offer_id"].strip(),
                    product=row["product"].strip(),
                    offer_name=row["offer_name"].strip(),
                    apr_min=_opt_float(row.get("apr_min")),
                    apr_max=_opt_float(row.get("apr_max")),
                    apr_type=row.get("apr_type", "").strip() or None,
                    term_months=_opt_int(row.get("term_months")),
                    amount_min=_opt_float(row.get("amount_min")),
                    amount_max=_opt_float(row.get("amount_max")),
                    origination_fee_pct=row.get("origination_fee_pct", "").strip() or None,
                    fee_deducted_from_proceeds=_opt_bool(row.get("fee_deducted_from_proceeds")),
                    intro_apr_pct=_opt_float(row.get("intro_apr_pct")),
                    intro_period_months=_opt_int(row.get("intro_period_months")),
                    annual_fee=_opt_float(row.get("annual_fee")),
                    badge_designation_allowed=row["badge_designation_allowed"].strip(),
                    is_firm_offer=_opt_bool(row.get("is_firm_offer")) or False,
                    min_credit_score=_opt_int(row.get("min_credit_score")),
                    states_excluded=_semilist(row.get("states_excluded")),
                    effective_start=_opt_date(row.get("effective_start")),
                    effective_end=_opt_date(row.get("effective_end")),
                    notes=row.get("notes", "").strip(),
                )
            )
    return cells


def load_submissions(csv_path: str | Path) -> list[Submission]:
    """Parse a placement-submission manifest CSV into contract Submissions.

    The internal ``id`` is set to the partner-facing ``submission_id``; the
    ``states_targeted`` raw string is preserved as-is per the frozen contract
    (use :func:`normalize_states_targeted` when a list is needed).

    Contract fields with defaults are still READ from the CSV when the column
    is present ("mode", "status", "baseline_submission_id"); the default
    applies only when the column is absent or the cell is empty — never
    silently overriding a populated cell.
    """
    submissions: list[Submission] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            submission_id = row["submission_id"].strip()
            mode = (row.get("mode") or "").strip() or SubmissionMode.PRE_PUBLICATION
            baseline = (row.get("baseline_submission_id") or "").strip() or None
            submissions.append(
                Submission(
                    id=submission_id,
                    submission_id=submission_id,
                    partner=row["partner"].strip(),
                    date_submitted=_opt_date(row.get("date_submitted")),
                    surface=row["surface"].strip(),
                    product=row["product"].strip(),
                    template_id=row["template_id"].strip(),
                    template_version=row["template_version"].strip(),
                    offer_ids=_semilist(row.get("offer_ids")),
                    proposed_headline=row.get("proposed_headline", "").strip(),
                    badge_text=row.get("badge_text", "").strip(),
                    dynamic_slots=_semilist(row.get("dynamic_slots")),
                    disclosures_included=_semilist(row.get("disclosures_included")),
                    asset_files=_semilist(row.get("asset_files")),
                    states_targeted=row.get("states_targeted", "").strip(),
                    requested_launch=_opt_date(row.get("requested_launch")),
                    change_summary=row.get("change_summary", "").strip(),
                    status=row.get("status", "").strip() or "pending_review",
                    sla_due=_opt_date(row.get("sla_due")),
                    mode=mode,
                    baseline_submission_id=baseline,
                )
            )
    return submissions
