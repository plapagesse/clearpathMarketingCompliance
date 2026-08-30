"""Queue API tests. Every test runs against a throwaway SQLite file, so the
reviewer's seeded database is never touched.
"""

from __future__ import annotations

import importlib
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.contracts import Submission


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A Flask test client bound to an empty, isolated database."""
    monkeypatch.setenv("CLEARPATH_DB", f"sqlite:///{tmp_path / 'queue-test.db'}")

    from backend.db import session as session_mod

    importlib.reload(session_mod)
    session_mod.init_db(reset=True)

    from backend.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _session():
    from backend.db.session import get_session

    return get_session()


def _add_submission(
    submission_id: str,
    sla_due: date | None,
    *,
    product: str = "personal_loan",
    partner: str = "credit_karma",
    mode: str = "pre_publication",
    asset_files: list[str] | None = None,
) -> None:
    from backend.db.models import SubmissionRow

    sub = Submission(
        id=submission_id,
        submission_id=submission_id,
        partner=partner,
        date_submitted=date(2026, 8, 20),
        surface="marketplace_offer_card",
        product=product,
        template_id="CK-PL-CARD",
        template_version="v7",
        offer_ids=["PL-36-A"],
        proposed_headline="You're prequalified",
        badge_text="Prequalified",
        asset_files=asset_files if asset_files is not None else ["mock_pl_card_compliant.png"],
        sla_due=sla_due,
        mode=mode,
    )
    session = _session()
    try:
        session.add(SubmissionRow.from_contract(sub))
        session.commit()
    finally:
        session.close()


def _add_check_run(submission_id: str, findings: list[tuple[str, str]], run_id: str = "cr-test1"):
    """findings: list of (rule_id, severity)."""
    from backend.db.models import CheckRunRow, FindingRow

    session = _session()
    try:
        run = CheckRunRow(
            id=run_id,
            submission_id=submission_id,
            rulebook_version="2026.08.4",
            offer_matrix_version="ui",
            mode="pre_publication",
            created_at=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
        )
        run.findings = [
            FindingRow(
                id=f"{run_id}-f{i}",
                check_run_id=run_id,
                check_class="legality",
                severity=severity,
                rule_id=rule_id,
                summary=f"{rule_id} summary",
                explanation="x" * 400,
                citation_url="https://example.gov/reg-z",
                suggested_redline="Say 'prequalified' instead.",
            )
            for i, (rule_id, severity) in enumerate(findings)
        ]
        session.add(run)
        session.commit()
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# GET /api/queue
# --------------------------------------------------------------------------- #


def test_queue_orders_by_sla_due_ascending(client):
    _add_submission("SUB-B", date(2026, 9, 5))
    _add_submission("SUB-A", date(2026, 8, 29))
    _add_submission("SUB-C", date(2026, 9, 1))

    body = client.get("/api/queue").get_json()

    assert body["remaining"] == 3
    assert [i["submission_id"] for i in body["items"]] == ["SUB-A", "SUB-C", "SUB-B"]


def test_queue_puts_undated_submissions_last(client):
    _add_submission("SUB-NODATE", None)
    _add_submission("SUB-DATED", date(2026, 9, 9))

    items = client.get("/api/queue").get_json()["items"]

    assert [i["submission_id"] for i in items] == ["SUB-DATED", "SUB-NODATE"]
    assert items[1]["sla_due"] is None and items[1]["days_left"] is None


def test_queue_filters_by_product_and_partner(client):
    _add_submission("SUB-PL", date(2026, 9, 1), product="personal_loan", partner="credit_karma")
    _add_submission("SUB-CC", date(2026, 9, 2), product="credit_card", partner="credit_karma")
    _add_submission("SUB-OTHER", date(2026, 9, 3), product="personal_loan", partner="nerdwallet")

    body = client.get("/api/queue?product=personal_loan&partner=credit_karma").get_json()

    assert [i["submission_id"] for i in body["items"]] == ["SUB-PL"]
    assert body["remaining"] == 1


def test_item_reports_input_type_days_left_and_image_url(client):
    due = date.today() + timedelta(days=3)
    _add_submission("SUB-PROPOSED", due, mode="pre_publication")
    _add_submission("SUB-PROD", due + timedelta(days=1), mode="verification")

    items = client.get("/api/queue").get_json()["items"]

    assert items[0]["input_type"] == "proposed"
    assert items[0]["days_left"] == 3
    assert items[0]["image_url"] == "/api/queue/evidence/mock_pl_card_compliant.png"
    assert items[1]["input_type"] == "production"


def test_unprocessed_item_carries_no_ai_fields(client):
    _add_submission("SUB-RAW", date(2026, 9, 1))

    item = client.get("/api/queue").get_json()["items"][0]

    assert item["ai_status"] == "unprocessed"
    assert "max_severity" not in item
    assert "latest_check_run_id" not in item


@pytest.mark.parametrize(
    "severities, expected_attention, expected_max",
    [
        ([], "quick_check", None),
        ([("R1", "info"), ("R2", "low")], "quick_check", "low"),
        ([("R1", "low"), ("R2", "medium")], "needs_attention", "medium"),
        ([("R1", "medium"), ("R2", "high")], "high_attention", "high"),
        ([("R1", "critical")], "high_attention", "critical"),
    ],
)
def test_attention_bucket_tracks_worst_severity(
    client, severities, expected_attention, expected_max
):
    _add_submission("SUB-P", date(2026, 9, 1))
    _add_check_run("SUB-P", severities)

    item = client.get("/api/queue").get_json()["items"][0]

    assert item["ai_status"] == "processed"
    assert item["attention"] == expected_attention
    assert item["max_severity"] == expected_max
    assert item["findings_count"] == len(severities)
    assert item["latest_check_run_id"] == "cr-test1"


# --------------------------------------------------------------------------- #
# decisions
# --------------------------------------------------------------------------- #


def test_decision_removes_item_from_queue(client):
    _add_submission("SUB-1", date(2026, 8, 29))
    _add_submission("SUB-2", date(2026, 8, 30))
    assert client.get("/api/queue").get_json()["remaining"] == 2

    resp = client.post("/api/queue/submission/SUB-1/decision", json={"decision": "approved"})
    assert resp.status_code == 200
    assert resp.get_json()["decision"] == "approved"

    body = client.get("/api/queue").get_json()
    assert body["remaining"] == 1
    assert [i["submission_id"] for i in body["items"]] == ["SUB-2"]


def test_decision_rejects_unknown_verdict(client):
    _add_submission("SUB-1", date(2026, 8, 29))

    resp = client.post("/api/queue/submission/SUB-1/decision", json={"decision": "maybe"})

    assert resp.status_code == 400
    assert client.get("/api/queue").get_json()["remaining"] == 1


def test_decision_on_unknown_submission_is_404(client):
    resp = client.post("/api/queue/submission/NOPE/decision", json={"decision": "approved"})
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# GET /api/queue/submission/<id>
# --------------------------------------------------------------------------- #


def test_detail_returns_findings_from_latest_check_run(client):
    _add_submission("SUB-D", date(2026, 9, 1))
    _add_check_run("SUB-D", [("RZ-TRIGGER-01", "high"), ("RZ-APR-02", "low")])

    body = client.get("/api/queue/submission/SUB-D").get_json()

    assert body["ai_status"] == "processed"
    assert body["attention"] == "high_attention"
    assert [f["rule_id"] for f in body["findings"]] == ["RZ-TRIGGER-01", "RZ-APR-02"]
    top = body["findings"][0]
    assert top["severity"] == "high"
    assert top["check_class"] == "legality"
    assert top["citation_url"] == "https://example.gov/reg-z"
    assert top["suggested_redline"] == "Say 'prequalified' instead."
    assert len(top["explanation"]) <= 200


def test_detail_uses_the_most_recent_run(client):
    _add_submission("SUB-D", date(2026, 9, 1))
    _add_check_run("SUB-D", [("OLD-RULE", "low")], run_id="cr-old")

    from backend.db.models import CheckRunRow

    session = _session()
    try:
        session.get(CheckRunRow, "cr-old").created_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        session.commit()
    finally:
        session.close()

    _add_check_run("SUB-D", [("NEW-RULE", "critical")], run_id="cr-new")

    body = client.get("/api/queue/submission/SUB-D").get_json()

    assert body["latest_check_run_id"] == "cr-new"
    assert [f["rule_id"] for f in body["findings"]] == ["NEW-RULE"]


def test_detail_history_is_chronological(client):
    _add_submission("SUB-H", date(2026, 9, 1))
    _add_check_run("SUB-H", [("R1", "medium")])
    client.post(
        "/api/queue/submission/SUB-H/decision",
        json={"decision": "rejected", "note": "APR floor is stale"},
    )

    history = client.get("/api/queue/submission/SUB-H").get_json()["history"]

    assert [h["event"] for h in history][:2] == [
        "submitted",
        "AI processing run — 1 finding",
    ]
    assert history[2]["event"].startswith("rejected by reviewer")
    assert "APR floor is stale" in history[2]["event"]
    assert [h["when"] for h in history] == sorted(h["when"] for h in history)


def test_detail_of_unprocessed_submission_has_empty_findings(client):
    _add_submission("SUB-R", date(2026, 9, 1))

    body = client.get("/api/queue/submission/SUB-R").get_json()

    assert body["ai_status"] == "unprocessed"
    assert body["findings"] == []
    assert [h["event"] for h in body["history"]] == ["submitted"]


def test_detail_of_unknown_submission_is_404(client):
    assert client.get("/api/queue/submission/NOPE").status_code == 404


# --------------------------------------------------------------------------- #
# evidence + process
# --------------------------------------------------------------------------- #


def test_evidence_serves_a_fixture_png(client):
    resp = client.get("/api/queue/evidence/mock_pl_card_compliant.png")

    assert resp.status_code == 200
    assert resp.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_evidence_ignores_path_traversal(client):
    assert client.get("/api/queue/evidence/../../backend/app.py").status_code == 404


def test_process_returns_503_without_api_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _add_submission("SUB-P", date(2026, 9, 1))

    resp = client.post("/api/queue/submission/SUB-P/process")

    assert resp.status_code == 503
    assert resp.get_json() == {"error": "no API key configured"}


def test_process_404s_when_the_submission_has_no_png(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    _add_submission("SUB-NOPNG", date(2026, 9, 1), asset_files=["notes.html"])

    resp = client.post("/api/queue/submission/SUB-NOPNG/process")

    assert resp.status_code == 404


def test_process_wraps_engine_failure_as_502(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    _add_submission("SUB-BOOM", date(2026, 9, 1))

    import backend.engine.extractor.extract as extract_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("upstream API is down")

    monkeypatch.setattr(extract_mod, "extract", _boom)

    resp = client.post("/api/queue/submission/SUB-BOOM/process")

    assert resp.status_code == 502
    assert "upstream API is down" in resp.get_json()["error"]
    # A failed run must not leave a half-written CheckRun behind.
    assert client.get("/api/queue/submission/SUB-BOOM").get_json()["ai_status"] == "unprocessed"


def test_filters_lists_present_products_and_partners(client):
    _add_submission("SUB-1", date(2026, 9, 1), product="personal_loan", partner="credit_karma")
    _add_submission("SUB-2", date(2026, 9, 2), product="credit_card", partner="nerdwallet")

    body = client.get("/api/queue/filters").get_json()

    assert body["products"] == ["credit_card", "personal_loan"]
    assert body["partners"] == ["credit_karma", "nerdwallet"]
