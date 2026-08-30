"""Flask blueprint: the rulebook, readable, at /api/rulebook.

  GET  /api/rulebook             the loaded rulebook, flattened for humans
  GET  /api/rulebook/proposals   rules someone has proposed, newest first
  POST /api/rulebook/proposals   propose one

The rules come from ``load_rulebook`` — the same loader the checker runs — so
the page can never drift from what the engine actually enforces. Each rule is
reduced to the five things a compliance officer asks: what does it check, on
which product, how bad is a violation, which authority says so, and where can I
read that authority.

Proposals are a separate table on purpose. The rulebook is versioned data on
disk; a proposal is a request, promoted into a new rulebook version by a person.
Nothing posted here is ever executed against a submission.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from backend.api.common import SEVERITY_RANK, open_session
from backend.contracts import Product
from backend.db.models import RuleProposalRow

rulebook_bp = Blueprint("rulebook", __name__, url_prefix="/api/rulebook")

REPO_ROOT = Path(__file__).resolve().parents[2]
RULEBOOK_DIR = REPO_ROOT / "rulebook"
CLAIM_TYPES_JSON = RULEBOOK_DIR / "claim_types_legal_map.json"

PRODUCTS = tuple(p.value for p in Product)
SEVERITIES = tuple(SEVERITY_RANK)

TITLE_MAX = 200


def _first_sentence(text: str) -> str:
    """The judge's question, minus the coaching that follows it."""
    text = (text or "").strip()
    return re.split(r"(?<=[.?!])\s+", text, maxsplit=1)[0] if text else ""


def _description(entry) -> str:
    """The plain-English line. Deterministic rules carry one; judged rules ask a question.

    `check_description` is required on every deterministic rule precisely so a
    non-engineer never has to read `parameters` to know what a rule does.
    """
    described = (entry.parameters.get("check_description") or "").strip()
    return described or _first_sentence(entry.parameters.get("judge_focus") or "")


def _rule(entry) -> dict:
    primary = entry.authorities[0]  # authorities are ordered, primary first
    return {
        "rule_id": entry.rule_id,
        "product": entry.product.value,
        "severity": entry.severity.value,
        "kind": entry.check_kind.value,
        "description": _description(entry),
        "citation": f"{primary.body} — {primary.citation}",
        "url": primary.url,
    }


def _claim_types() -> list[dict]:
    """Claim types as the extractor's own definition file states them."""
    raw = json.loads(CLAIM_TYPES_JSON.read_text())
    out = []
    for name, spec in raw.get("claim_types", {}).items():
        fields = []
        for field_name, field_spec in (spec.get("normalized_fields") or {}).items():
            field = {
                "name": field_name,
                "type": field_spec.get("type", "string"),
                "optional": bool(field_spec.get("optional", False)),
            }
            if field_spec.get("values"):
                field["values"] = list(field_spec["values"])
            fields.append(field)
        out.append({"name": name, "definition": spec.get("definition", ""), "fields": fields})
    return out


@rulebook_bp.get("")
def rulebook():
    """Everything the Rulebook page renders, in one request."""
    from backend.engine.checker import load_rulebook

    loaded = load_rulebook(RULEBOOK_DIR)
    return jsonify(
        {
            "version": loaded.version,
            "claim_types": _claim_types(),
            "rules": [_rule(e) for e in loaded.entries],
        }
    )


def _serialize_proposal(row: RuleProposalRow) -> dict:
    return {
        "id": row.id,
        "product": row.product,
        "title": row.title,
        "description": row.description,
        "severity": row.severity,
        "citation_url": row.citation_url,
        "rationale": row.rationale,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
    }


@rulebook_bp.get("/proposals")
def list_proposals():
    """Newest first — a proposal is a conversation starter, not a backlog."""
    session = open_session()
    try:
        rows = session.execute(
            select(RuleProposalRow).order_by(
                RuleProposalRow.created_at.desc(), RuleProposalRow.id.desc()
            )
        ).scalars().all()
        return jsonify([_serialize_proposal(row) for row in rows])
    finally:
        session.close()


@rulebook_bp.post("/proposals")
def create_proposal():
    """Body: {product, title, description?, severity, citation_url?, rationale?}."""
    body = request.get_json(silent=True) or {}
    product = (body.get("product") or "").strip()
    title = (body.get("title") or "").strip()
    severity = (body.get("severity") or "").strip()

    if product not in PRODUCTS:
        return jsonify({"error": f"product must be one of {sorted(PRODUCTS)}"}), 400
    if not title:
        return jsonify({"error": "title is required"}), 400
    if len(title) > TITLE_MAX:
        return jsonify({"error": f"title must be at most {TITLE_MAX} characters"}), 400
    if severity not in SEVERITIES:
        return jsonify({"error": f"severity must be one of {sorted(SEVERITIES)}"}), 400

    row = RuleProposalRow(
        id=f"rp-{uuid.uuid4().hex[:12]}",
        product=product,
        title=title,
        description=(body.get("description") or "").strip(),
        severity=severity,
        citation_url=(body.get("citation_url") or "").strip() or None,
        rationale=(body.get("rationale") or "").strip(),
        status="pending",
        created_at=datetime.now(timezone.utc),
    )

    session = open_session()
    try:
        session.add(row)
        session.commit()
        return jsonify(_serialize_proposal(row)), 201
    finally:
        session.close()
