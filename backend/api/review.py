"""Flask blueprint backing the reviewer input view at /api/review.

Three endpoints, all thin wrappers over the seeded submissions table:
  GET  /api/review/submissions            list (optional product/partner/input_type/ai_status)
  POST /api/review/submissions            multipart upload -> new SubmissionRow
  GET  /api/review/evidence/<filename>    serve a screenshot (uploads/, then fixtures/)

Every listed card carries the same AI-status summary the queue items do (see
backend/api/common.py), so a reviewer can tell at a glance which inputs the
engine has already looked at without opening them one by one.

Seeding is unchanged (``python -m backend.db.seed``); this module only makes
sure the tables exist so a fresh checkout answers instead of 500-ing.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory
from sqlalchemy import select

from backend.api.common import (
    ai_status_filter,
    ai_summary,
    days_ago,
    filter_by_input_type,
    input_type,
    open_session,
)
from backend.contracts import Product, SubmissionMode
from backend.db.models import SubmissionRow

review_bp = Blueprint("review", __name__, url_prefix="/api/review")

REPO_ROOT = Path(__file__).resolve().parents[2]
UPLOADS_DIR = REPO_ROOT / "uploads"
FIXTURES_DIR = REPO_ROOT / "fixtures"

SLA_DAYS = 5
DEFAULT_SURFACE = "manual_upload"
INPUT_TYPES = {"proposed": SubmissionMode.PRE_PUBLICATION, "production": SubmissionMode.VERIFICATION}


def _image_url(row: SubmissionRow) -> str | None:
    """First image asset of the submission, as an evidence URL."""
    images = [f for f in (row.asset_files or []) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
    return f"/api/review/evidence/{images[0]}" if images else None


def _serialize(session, row: SubmissionRow) -> dict:
    card = {
        "submission_id": row.submission_id,
        "product": row.product,
        "partner": row.partner,
        "surface": row.surface,
        "mode": row.mode,
        "date_submitted": row.date_submitted.isoformat() if row.date_submitted else None,
        # The card shows age now; sla_due stays in the payload as data, unused.
        "days_ago": days_ago(row.date_submitted),
        "sla_due": row.sla_due.isoformat() if row.sla_due else None,
        "image_url": _image_url(row),
        "input_type": input_type(row.mode),
    }
    card.update(ai_summary(session, row))
    return card


@review_bp.get("/submissions")
def list_submissions():
    """Query params: product, partner, input_type, ai_status (all optional, exact match).

    Oldest first, matching the queue: the grid and the queue walk the backlog in
    the same order, so "the next thing to look at" means the same in both views.

    Three of the four filters narrow the query; ai_status can't, because a
    submission's bucket is a property of its latest CheckRun, computed per row by
    ai_summary(). It is applied to the serialized cards instead, which keeps one
    definition of "what the AI thinks of this" rather than a second one in SQL.
    """
    product = (request.args.get("product") or "").strip()
    partner = (request.args.get("partner") or "").strip()
    wanted_type = (request.args.get("input_type") or "").strip()
    wanted_ai = (request.args.get("ai_status") or "").strip()

    stmt = select(SubmissionRow)
    if product:
        stmt = stmt.where(SubmissionRow.product == product)
    if partner:
        stmt = stmt.where(SubmissionRow.partner == partner)
    try:
        stmt = filter_by_input_type(stmt, wanted_type)
        keep = ai_status_filter(wanted_ai)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    stmt = stmt.order_by(SubmissionRow.date_submitted.asc(), SubmissionRow.submission_id)

    session = open_session()
    try:
        rows = session.execute(stmt).scalars().all()
        cards = (_serialize(session, row) for row in rows)
        return jsonify([card for card in cards if keep(card)])
    finally:
        session.close()


@review_bp.post("/submissions")
def create_submission():
    """Multipart form: file (required), product, partner, surface?, input_type?, notes?."""
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "file is required"}), 400

    product = (request.form.get("product") or "").strip()
    partner = (request.form.get("partner") or "").strip()
    if product not in {p.value for p in Product}:
        return jsonify({"error": f"product must be one of {sorted(p.value for p in Product)}"}), 400
    if not partner:
        return jsonify({"error": "partner is required"}), 400

    wanted_type = (request.form.get("input_type") or "proposed").strip()
    if wanted_type not in INPUT_TYPES:
        return jsonify({"error": "input_type must be 'proposed' or 'production'"}), 400

    surface = (request.form.get("surface") or "").strip() or DEFAULT_SURFACE
    notes = (request.form.get("notes") or "").strip()

    submission_id = f"SUB-UI-{uuid.uuid4().hex[:8]}"
    filename = f"{submission_id}.png"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    upload.save(UPLOADS_DIR / filename)

    today = date.today()
    row = SubmissionRow(
        id=submission_id,
        submission_id=submission_id,
        partner=partner,
        date_submitted=today,
        surface=surface,
        product=product,
        template_id="UI-UPLOAD",
        template_version="v1",
        offer_ids=[],
        asset_files=[filename],
        states_targeted="ALL",
        change_summary=notes,
        status="pending_review",
        sla_due=today + timedelta(days=SLA_DAYS),
        mode=INPUT_TYPES[wanted_type].value,
    )

    session = open_session()
    try:
        session.add(row)
        session.commit()
        return jsonify(_serialize(session, row)), 201
    finally:
        session.close()


@review_bp.get("/evidence/<path:filename>")
def evidence(filename: str):
    """Serve a screenshot: uploads/ first (user uploads), then fixtures/ (seeded)."""
    safe = os.path.basename(filename)
    if not safe or safe != filename:
        return jsonify({"error": "invalid filename"}), 400

    for directory in (UPLOADS_DIR, FIXTURES_DIR):
        if (directory / safe).is_file():
            return send_from_directory(directory, safe)
    return jsonify({"error": "not found"}), 404
