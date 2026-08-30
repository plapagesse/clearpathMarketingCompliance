"""Flask blueprint exposing the mock prequal engine at /api/prequal."""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from backend.prequal.client import prequalify_client

prequal_bp = Blueprint("prequal", __name__, url_prefix="/api/prequal")


@prequal_bp.get("/health")
def health():
    return jsonify({"status": "ok"})


@prequal_bp.post("")
def prequal():
    """Body: {"product": "personal_loan", "profile": {credit_score, annual_income, state, requested_amount?}}"""
    body = request.get_json(silent=True) or {}
    try:
        result = prequalify_client(
            body.get("profile") or {},
            body.get("product", ""),
            inject_drift=bool(body.get("inject_drift", False)),
        )
    except (ValidationError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)
