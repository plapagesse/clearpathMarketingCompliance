"""Flask blueprint: the reviewer queue at /api/queue.

One submission at a time, oldest first — the queue is worked in arrival order,
so the longest-waiting partner is never the last one looked at. A submission
leaves the queue the moment a ReviewDecisionRow exists for it, so `remaining` is
the honest count of what is still on the reviewer's plate.

Screenshots are served by the input view's /api/review/evidence/<file> route;
this module only resolves the on-disk path when it needs to feed the engine.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, time, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from backend.api.common import (
    SEVERITY_RANK,
    ai_summary,
    days_ago,
    filter_by_input_type,
    input_type,
    latest_check_run,
)
from backend.db.models import CheckRunRow, FindingRow, ReviewDecisionRow, SubmissionRow
from backend.db.session import get_session

queue_bp = Blueprint("queue", __name__, url_prefix="/api/queue")

REPO_ROOT = Path(__file__).resolve().parents[2]
UPLOADS_DIR = REPO_ROOT / "uploads"
FIXTURES_DIR = REPO_ROOT / "fixtures"
RULEBOOK_DIR = REPO_ROOT / "rulebook"
OFFER_MATRIX_CSV = FIXTURES_DIR / "offer_matrix.csv"

EXCERPT_CHARS = 200


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _excerpt(text: str | None) -> str:
    text = (text or "").strip()
    return text if len(text) <= EXCERPT_CHARS else text[: EXCERPT_CHARS - 1] + "…"


def _evidence_dir(filename: str) -> Path | None:
    """uploads/ wins over fixtures/ — a re-upload supersedes the shipped mock."""
    for directory in (UPLOADS_DIR, FIXTURES_DIR):
        if (directory / filename).is_file():
            return directory
    return None


def _evidence_file(sub: SubmissionRow) -> str | None:
    """The PNG among a submission's assets (the HTML twin is not the evidence)."""
    for name in sub.asset_files or []:
        if str(name).lower().endswith(".png"):
            return Path(str(name)).name
    return None


def _evidence_path(sub: SubmissionRow) -> Path | None:
    filename = _evidence_file(sub)
    if not filename:
        return None
    directory = _evidence_dir(filename)
    return directory / filename if directory else None


def _days_left(sla_due: date | None) -> int | None:
    if sla_due is None:
        return None
    return (sla_due - date.today()).days


def _item(session, sub: SubmissionRow) -> dict:
    filename = _evidence_file(sub)
    item = {
        "submission_id": sub.submission_id,
        "product": sub.product,
        "partner": sub.partner,
        "surface": sub.surface,
        "proposed_headline": sub.proposed_headline,
        # Served by the input view's blueprint — same uploads/-then-fixtures/
        # lookup, so there is no reason for a second copy of that route here.
        "image_url": f"/api/review/evidence/{filename}" if filename else None,
        "date_submitted": sub.date_submitted.isoformat() if sub.date_submitted else None,
        # What the UI shows now. sla_due/days_left stay in the payload: the SLA
        # is still real data, it just isn't what the reviewer is steered by.
        "days_ago": days_ago(sub.date_submitted),
        "sla_due": sub.sla_due.isoformat() if sub.sla_due else None,
        "days_left": _days_left(sub.sla_due),
        "input_type": input_type(sub.mode),
    }
    item.update(ai_summary(session, sub))
    return item


def _find_submission(session, sid: str) -> SubmissionRow | None:
    """Accept either the business submission_id or the row pk (seed makes them equal)."""
    sub = session.execute(
        select(SubmissionRow).where(SubmissionRow.submission_id == sid)
    ).scalar_one_or_none()
    return sub if sub is not None else session.get(SubmissionRow, sid)


def _decided_ids(session) -> list[str]:
    return list(session.execute(select(ReviewDecisionRow.submission_id)).scalars().all())


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #


@queue_bp.get("")
def queue():
    """Undecided submissions for a product/partner/input-type combo, oldest first."""
    product = (request.args.get("product") or "").strip()
    partner = (request.args.get("partner") or "").strip()
    wanted_type = (request.args.get("input_type") or "").strip()

    session = get_session()
    try:
        stmt = select(SubmissionRow)
        if product:
            stmt = stmt.where(SubmissionRow.product == product)
        if partner:
            stmt = stmt.where(SubmissionRow.partner == partner)
        try:
            stmt = filter_by_input_type(stmt, wanted_type)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        decided = _decided_ids(session)
        if decided:
            stmt = stmt.where(SubmissionRow.id.notin_(decided))
        # Oldest first: the reviewer works the backlog in arrival order, so the
        # submission that has waited longest is the one on screen. Ids break ties.
        stmt = stmt.order_by(
            SubmissionRow.date_submitted.asc(),
            SubmissionRow.submission_id.asc(),
        )
        subs = list(session.execute(stmt).scalars().all())
        items = [_item(session, s) for s in subs]
        return jsonify({"remaining": len(items), "items": items})
    finally:
        session.close()


@queue_bp.get("/filters")
def filters():
    """The product/partner values actually present, so the selects aren't hardcoded."""
    session = get_session()
    try:
        products = sorted(set(session.execute(select(SubmissionRow.product)).scalars().all()))
        partners = sorted(set(session.execute(select(SubmissionRow.partner)).scalars().all()))
        return jsonify({"products": products, "partners": partners})
    finally:
        session.close()


@queue_bp.get("/submission/<sid>")
def detail(sid: str):
    """Full review payload: the item, the latest run's findings, and the history."""
    session = get_session()
    try:
        sub = _find_submission(session, sid)
        if sub is None:
            return jsonify({"error": f"unknown submission: {sid}"}), 404

        item = _item(session, sub)

        findings = []
        run = latest_check_run(session, sub)
        if run is not None:
            findings = [
                {
                    "id": f.id,
                    "rule_id": f.rule_id,
                    "severity": f.severity,
                    "check_class": f.check_class,
                    "summary": _excerpt(f.summary),
                    "explanation": _excerpt(f.explanation),
                    "citation_url": f.citation_url,
                    "suggested_redline": f.suggested_redline,
                }
                for f in sorted(
                    run.findings,
                    key=lambda f: -SEVERITY_RANK.get(f.severity, 0),
                )
            ]

        item["findings"] = findings
        item["history"] = _history(session, sub)
        return jsonify(item)
    finally:
        session.close()


def _history(session, sub: SubmissionRow) -> list[dict]:
    """Everything that has happened to this submission, oldest first."""
    events: list[tuple[datetime, str]] = []

    if sub.date_submitted:
        events.append((datetime.combine(sub.date_submitted, time.min), "submitted"))

    runs = session.execute(
        select(CheckRunRow).where(CheckRunRow.submission_id == sub.id)
    ).scalars().all()
    for run in runs:
        count = len(run.findings)
        events.append(
            (run.created_at, f"AI processing run — {count} finding{'' if count == 1 else 's'}")
        )

    decisions = session.execute(
        select(ReviewDecisionRow).where(ReviewDecisionRow.submission_id == sub.id)
    ).scalars().all()
    for d in decisions:
        label = f"{d.decision} by {d.decided_by}"
        if d.note:
            label = f"{label} — {_excerpt(d.note)}"
        events.append((d.decided_at, label))

    events.sort(key=lambda e: e[0])
    return [{"when": when.isoformat(), "event": event} for when, event in events]


@queue_bp.post("/submission/<sid>/process")
def process(sid: str):
    """Run the real pipeline synchronously: extract -> run_checks -> run_judge.

    Two live model calls, so this takes 15-40s; the UI holds a processing state
    rather than us pretending it's fast.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "no API key configured"}), 503

    session = get_session()
    try:
        sub = _find_submission(session, sid)
        if sub is None:
            return jsonify({"error": f"unknown submission: {sid}"}), 404

        evidence_path = _evidence_path(sub)
        if evidence_path is None:
            return jsonify({"error": f"no evidence artifact for {sub.submission_id}"}), 404

        # Imported lazily: the engine pulls in the Anthropic SDK, and the queue
        # endpoints that don't process must stay importable without it.
        from backend.contracts import Product
        from backend.engine.checker import load_rulebook, run_checks
        from backend.engine.extractor.extract import ExtractionContext, extract
        from backend.engine.judge import run_judge
        from backend.ingest import load_offer_matrix

        submission = sub.to_contract()
        try:
            rulebook = load_rulebook(str(RULEBOOK_DIR))
            extraction = extract(
                str(evidence_path),
                ExtractionContext(
                    product=Product(sub.product),
                    surface=sub.surface,
                    partner=sub.partner,
                    evidence_id=sub.submission_id,
                ),
            )
            claims = list(extraction.claims)
            disclosures = list(extraction.disclosures)

            run = run_checks(
                submission=submission,
                claims=claims,
                disclosures=disclosures,
                offer_cells=load_offer_matrix(OFFER_MATRIX_CSV),
                offer_matrix_version="ui",
                rulebook=rulebook,
                artifact_text=None,
            )
            judged = run_judge(
                submission=submission,
                claims=claims,
                disclosures=disclosures,
                evidence_path=str(evidence_path),
                rulebook=rulebook,
                model="claude-haiku-4-5",
            )
        except Exception as exc:  # noqa: BLE001 — surface any engine/API failure verbatim
            return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502

        # ONE CheckRunRow carrying deterministic + judge findings. Ids are minted
        # here so re-processing a submission can never collide with an older run.
        run_id = f"cr-{uuid.uuid4().hex[:12]}"
        row = CheckRunRow(
            id=run_id,
            submission_id=sub.id,
            rulebook_version=run.rulebook_version,
            offer_matrix_version=run.offer_matrix_version,
            mode=sub.mode,
            created_at=datetime.now(timezone.utc),
            extracted_claims=[c.model_dump(mode="json") for c in claims],
            extracted_disclosures=[d.model_dump(mode="json") for d in disclosures],
        )
        row.findings = [
            FindingRow(
                id=f"{run_id}-f{i:03d}",
                check_run_id=run_id,
                check_class=_enum_value(f.check_class),
                severity=_enum_value(f.severity),
                rule_id=f.rule_id,
                claim_id=f.claim_id,
                summary=f.summary,
                explanation=f.explanation,
                citation_url=f.citation_url,
                suggested_redline=f.suggested_redline,
                status=_enum_value(f.status),
            )
            for i, f in enumerate(list(run.findings) + list(judged))
        ]
        session.add(row)
        session.commit()

        return jsonify(_item(session, sub))
    finally:
        session.close()


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


@queue_bp.post("/submission/<sid>/decision")
def decision(sid: str):
    """Body: {"decision": "approved"|"rejected", "note"?: str, "decided_by"?: str}."""
    body = request.get_json(silent=True) or {}
    verdict = (body.get("decision") or "").strip()
    if verdict not in ("approved", "rejected"):
        return jsonify({"error": "decision must be 'approved' or 'rejected'"}), 400

    session = get_session()
    try:
        sub = _find_submission(session, sid)
        if sub is None:
            return jsonify({"error": f"unknown submission: {sid}"}), 404

        session.add(
            ReviewDecisionRow(
                id=f"rd-{uuid.uuid4().hex[:12]}",
                submission_id=sub.id,
                decision=verdict,
                decided_by=(body.get("decided_by") or "reviewer").strip() or "reviewer",
                decided_at=datetime.now(timezone.utc),
                note=(body.get("note") or "").strip() or None,
            )
        )
        session.commit()

        item = _item(session, sub)
        item["decision"] = verdict
        return jsonify(item)
    finally:
        session.close()
