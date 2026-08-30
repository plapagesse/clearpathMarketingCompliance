"""Serialization helpers shared by the /api/review and /api/queue blueprints.

The AI-status summary (ai_status / max_severity / findings_count / attention /
latest_check_run_id) started life inside the queue blueprint. The input grid now
puts the same chip on every card, so the logic lives here and both blueprints
call it: one definition of "what the AI thinks of this submission", so the two
views can never disagree.

Age and input-type live here for the same reason — both lists show them.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import CheckRunRow, SubmissionRow
from backend.db.session import get_session, init_db

# Rank severities so "the worst finding in the run" is a max(), not a lookup table.
SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

# The two values the UI's input-type selector offers. "production" is the
# verification mode (live-placement evidence); everything else is a proposal.
PRODUCTION_MODE = "verification"
INPUT_TYPES = ("proposed", "production")


def open_session() -> Session:
    """Session against the app DB, creating tables if this is a fresh file."""
    init_db()  # create_all is a no-op once the schema is there; never resets data
    return get_session()


def input_type(mode: str) -> str:
    return "production" if mode == PRODUCTION_MODE else "proposed"


def filter_by_input_type(stmt, value: str):
    """Narrow a SubmissionRow select to one input type. Empty value = no filter.

    Raises ValueError outside the closed vocabulary, so a typo'd query string
    gets a 400 instead of quietly returning everything.
    """
    if not value:
        return stmt
    if value not in INPUT_TYPES:
        raise ValueError(f"input_type must be one of {list(INPUT_TYPES)}")
    if value == "production":
        return stmt.where(SubmissionRow.mode == PRODUCTION_MODE)
    return stmt.where(SubmissionRow.mode != PRODUCTION_MODE)


def days_ago(day: date | None) -> int | None:
    """Whole days since the submission landed. 0 is today; None if undated."""
    if day is None:
        return None
    return (date.today() - day).days


def attention(severities: list[str]) -> str:
    """Three buckets, so the reviewer knows how hard to look before they look."""
    worst = max((SEVERITY_RANK.get(s, 0) for s in severities), default=-1)
    if worst >= SEVERITY_RANK["high"]:
        return "high_attention"
    if worst >= SEVERITY_RANK["medium"]:
        return "needs_attention"
    return "quick_check"


def latest_check_run(session: Session, sub: SubmissionRow) -> CheckRunRow | None:
    return session.execute(
        select(CheckRunRow)
        .where(CheckRunRow.submission_id == sub.id)
        .order_by(CheckRunRow.created_at.desc(), CheckRunRow.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def ai_summary(session: Session, sub: SubmissionRow) -> dict:
    """The AI-status block both views render — a chip on a card, a banner in review.

    Unprocessed submissions carry ai_status alone: absent fields say "never run"
    more honestly than zeros would.
    """
    run = latest_check_run(session, sub)
    if run is None:
        return {"ai_status": "unprocessed"}

    severities = [f.severity for f in run.findings]
    return {
        "ai_status": "processed",
        "max_severity": max(severities, key=lambda s: SEVERITY_RANK.get(s, 0))
        if severities
        else None,
        "findings_count": len(severities),
        "attention": attention(severities),
        "latest_check_run_id": run.id,
    }
