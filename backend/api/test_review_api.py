import io
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
