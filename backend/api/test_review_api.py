import io
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from backend.db.seed import seed

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """App wired to a throwaway DB seeded from fixtures/, uploads in tmp_path."""
    monkeypatch.setenv("CLEARPATH_DB", f"sqlite:///{tmp_path}/test.db")
    import backend.db.session as dbs

    monkeypatch.setattr(dbs, "_engine", None)
    monkeypatch.setattr(dbs, "_session_factory", None)

    import backend.api.review as review

    monkeypatch.setattr(review, "UPLOADS_DIR", tmp_path / "uploads")

    seed(FIXTURES_DIR)

    from backend.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _add_check_run(submission_id: str, severities: list[str], run_id: str = "cr-review-test"):
    """Attach one AI run to a seeded submission, so the card carries a status."""
    from backend.db.models import CheckRunRow, FindingRow
    from backend.db.session import get_session

    session = get_session()
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
                rule_id=f"R{i}",
                summary="summary",
                explanation="explanation",
            )
            for i, severity in enumerate(severities)
        ]
        session.add(run)
        session.commit()
    finally:
        session.close()


def test_list_returns_seeded_submissions(client):
    rows = client.get("/api/review/submissions").get_json()
    assert len(rows) == 10
    by_id = {r["submission_id"]: r for r in rows}

    pre_pub = by_id["SUB-2026-0142"]
    assert pre_pub["partner"] == "credit_karma"
    assert pre_pub["product"] == "personal_loan"
    assert pre_pub["input_type"] == "proposed"
    assert pre_pub["sla_due"] == "2026-08-29"
    # image_url points at the PNG asset, not the sibling HTML
    assert pre_pub["image_url"] == "/api/review/evidence/mock_pl_card_compliant.png"

    assert by_id["SUB-2026-0151"]["input_type"] == "production"


def test_list_is_oldest_first_and_reports_age(client):
    """UI iteration 2: the grid shows age and leads with the longest-waiting input."""
    rows = client.get("/api/review/submissions").get_json()

    dates = [r["date_submitted"] for r in rows]
    assert dates == sorted(dates)
    assert rows[0]["date_submitted"] == "2026-08-24"

    oldest = rows[0]
    expected = (date.today() - date.fromisoformat(oldest["date_submitted"])).days
    assert oldest["days_ago"] == expected


def test_list_carries_the_same_ai_summary_the_queue_items_have(client):
    """Change #3: every card says whether the AI has looked at it, and how it went."""
    _add_check_run("SUB-2026-0142", ["low", "critical"])

    by_id = {r["submission_id"]: r for r in client.get("/api/review/submissions").get_json()}

    processed = by_id["SUB-2026-0142"]
    assert processed["ai_status"] == "processed"
    assert processed["attention"] == "high_attention"
    assert processed["max_severity"] == "critical"
    assert processed["findings_count"] == 2
    assert processed["latest_check_run_id"] == "cr-review-test"

    untouched = by_id["SUB-2026-0143"]
    assert untouched["ai_status"] == "unprocessed"
    assert "attention" not in untouched
    assert "max_severity" not in untouched

    # The queue's item for the same submission agrees field for field.
    queue_item = client.get("/api/queue/submission/SUB-2026-0142").get_json()
    for key in ("ai_status", "attention", "max_severity", "findings_count", "latest_check_run_id"):
        assert queue_item[key] == processed[key]


def test_list_filters_by_input_type(client):
    proposed = client.get("/api/review/submissions?input_type=proposed").get_json()
    production = client.get("/api/review/submissions?input_type=production").get_json()

    assert {r["submission_id"] for r in production} == {"SUB-2026-0151"}
    assert len(proposed) == 9
    assert all(r["input_type"] == "proposed" for r in proposed)
    # combines with the other two selectors
    combo = client.get(
        "/api/review/submissions?input_type=production&product=personal_loan"
    ).get_json()
    assert [r["submission_id"] for r in combo] == ["SUB-2026-0151"]

    assert client.get("/api/review/submissions?input_type=banana").status_code == 400


def test_filters_by_product_and_partner(client):
    cards = client.get("/api/review/submissions?product=credit_card").get_json()
    assert {r["submission_id"] for r in cards} == {
        "SUB-2026-0143",
        "SUB-2026-0144",
        "SUB-2026-0149",
    }

    assert len(client.get("/api/review/submissions?partner=credit_karma").get_json()) == 10
    assert client.get("/api/review/submissions?partner=nobody").get_json() == []

    both = client.get(
        "/api/review/submissions?product=mortgage_prequal&partner=credit_karma"
    ).get_json()
    assert {r["submission_id"] for r in both} == {"SUB-2026-0145", "SUB-2026-0150"}


def test_post_creates_row_and_saves_file(client, tmp_path):
    response = client.post(
        "/api/review/submissions",
        data={
            "file": (io.BytesIO(b"fake-png-bytes"), "screenshot.png"),
            "product": "personal_loan",
            "partner": "acme_partners",
            "input_type": "production",
            "notes": "spotted in the wild",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    created = response.get_json()
    assert created["submission_id"].startswith("SUB-UI-")
    assert created["partner"] == "acme_partners"
    assert created["input_type"] == "production"
    assert created["surface"] == "manual_upload"  # default

    saved = tmp_path / "uploads" / f"{created['submission_id']}.png"
    assert saved.read_bytes() == b"fake-png-bytes"

    # it shows up in the list, and its own image is servable
    listed = client.get("/api/review/submissions?partner=acme_partners").get_json()
    assert [r["submission_id"] for r in listed] == [created["submission_id"]]
    assert client.get(created["image_url"]).data == b"fake-png-bytes"


def test_post_rejects_missing_file_and_bad_product(client):
    missing_file = client.post(
        "/api/review/submissions",
        data={"product": "personal_loan", "partner": "acme"},
        content_type="multipart/form-data",
    )
    assert missing_file.status_code == 400

    bad_product = client.post(
        "/api/review/submissions",
        data={
            "file": (io.BytesIO(b"x"), "a.png"),
            "product": "car_loan",
            "partner": "acme",
        },
        content_type="multipart/form-data",
    )
    assert bad_product.status_code == 400


def test_evidence_serves_fixtures_and_rejects_traversal(client):
    ok = client.get("/api/review/evidence/mock_pl_card_compliant.png")
    assert ok.status_code == 200
    assert ok.data[:8] == b"\x89PNG\r\n\x1a\n"

    assert client.get("/api/review/evidence/does_not_exist.png").status_code == 404
    assert client.get("/api/review/evidence/../backend/contracts.py").status_code == 400
    assert client.get("/api/review/evidence/..%2Fbackend%2Fcontracts.py").status_code == 400
    assert client.get("/api/review/evidence/subdir/file.png").status_code == 400
