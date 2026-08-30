"""Serialization helpers shared by the /api/review and /api/queue blueprints.

The AI-status summary (ai_status / max_severity / findings_count / attention /
latest_check_run_id) started life inside the queue blueprint. The input grid now
puts the same chip on every card, so the logic lives here and both blueprints
call it: one definition of "what the AI thinks of this submission", so the two
views can never disagree.

The human verdict (human_status / decided_by / decided_at) is here for the same
reason: the input grid, the review list and the detail view all wear the chip
that says whether a person has signed off, and one query answers all three.

Age and input-type live here too — both lists show them.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import CheckRunRow, ReviewDecisionRow, SubmissionRow
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


# The four buckets the input grid's AI-status selector offers, mapped onto the
# attention value ai_summary() produces — so the selector and the chip on the
# card are two readings of one number, and can never disagree.
AI_STATUSES = ("not_checked", "clean", "review", "issues")
ATTENTION_FOR_STATUS = {
    "clean": "quick_check",
    "review": "needs_attention",
    "issues": "high_attention",
}


def ai_status_filter(value: str):
    """A card predicate for one AI-status bucket. Empty value keeps everything.

    Returns a predicate rather than narrowing a select, because the bucket is
    derived from the submission's latest CheckRun by ai_summary() — it only
    exists once the row has been serialized. Validating here still raises before
    the query runs, and outside the closed vocabulary it raises ValueError like
    filter_by_input_type, so a typo'd query string is a 400 rather than a
    silently unfiltered list.
    """
    if not value:
        return lambda card: True
    if value not in AI_STATUSES:
        raise ValueError(f"ai_status must be one of {list(AI_STATUSES)}")
    if value == "not_checked":
        return lambda card: card.get("ai_status") != "processed"
    wanted = ATTENTION_FOR_STATUS[value]
    return lambda card: card.get("ai_status") == "processed" and card.get("attention") == wanted


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


def latest_decision(session: Session, sub: SubmissionRow) -> ReviewDecisionRow | None:
    """The reviewer's most recent call on this submission, if anyone has made one."""
    return session.execute(
        select(ReviewDecisionRow)
        .where(ReviewDecisionRow.submission_id == sub.id)
        .order_by(ReviewDecisionRow.decided_at.desc(), ReviewDecisionRow.id.desc())
        .limit(1)
    ).scalars().first()


def human_summary(session: Session, sub: SubmissionRow) -> dict:
    """The human-verdict block, alongside ai_summary's.

    Unlike ai_summary, the fields are always present: "none" is a state the chip
    renders ("Human: —"), not an absence, and a caller sorting or filtering on
    human_status should never have to guess whether a missing key means
    undecided or means the server forgot.
    """
    row = latest_decision(session, sub)
    if row is None:
        return {"human_status": "none", "decided_by": None, "decided_at": None}
    return {
        "human_status": row.decision,
        "decided_by": row.decided_by,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
    }
