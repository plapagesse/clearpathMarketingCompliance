import io
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from backend.db.seed import seed
from backend.ingest import load_submissions

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures"
SUBMISSIONS_CSV = FIXTURES_DIR / "submissions.csv"


# --------------------------------------------------------------------------- #
# Census-robust expectations
#
# These tests seed from fixtures/, so the fixture manifest IS the ground truth
# for what the endpoint must return. Deriving expectations by parsing it — the
# same parser the seeder uses — instead of hard-coding ids and counts means the
# fixture set can grow without the tests going stale. (It went 10 -> 16 when the
# nerdwallet and lendingtree partner mocks landed, which is what stranded the
# literals that used to live here.)
# --------------------------------------------------------------------------- #


def _census():
    """Every submission the seeder will load, keyed by submission_id."""
    return {s.submission_id: s for s in load_submissions(SUBMISSIONS_CSV)}


def _input_type_of(sub) -> str:
    """Mirrors the API's mapping: verification is 'production', all else 'proposed'."""
    return "production" if sub.mode.value == "verification" else "proposed"


def _expected_ids(product: str = "", partner: str = "", input_type: str = "") -> set[str]:
    """The ids the endpoint owes us for a filter combination, per the manifest."""
    return {
        sid
        for sid, sub in _census().items()
        if (not product or sub.product.value == product)
        and (not partner or sub.partner == partner)
        and (not input_type or _input_type_of(sub) == input_type)
    }


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
    """The endpoint returns the seeded census, and each row reports its own row."""
    census = _census()
    rows = client.get("/api/review/submissions").get_json()
    by_id = {r["submission_id"]: r for r in rows}

    assert set(by_id) == set(census)

    # Every row's own fields agree with the manifest row it came from — a
    # relation that holds however many fixtures there are.
    for sid, row in by_id.items():
        sub = census[sid]
        assert row["partner"] == sub.partner
        assert row["product"] == sub.product.value
        assert row["input_type"] == _input_type_of(sub)
        assert row["date_submitted"] == sub.date_submitted.isoformat()
        assert row["sla_due"] == (sub.sla_due.isoformat() if sub.sla_due else None)

    # image_url points at the PNG asset, never the sibling HTML.
    for sid, row in by_id.items():
        pngs = [f for f in census[sid].asset_files if f.lower().endswith(".png")]
        if pngs:
            assert row["image_url"] == f"/api/review/evidence/{pngs[0]}"

    # Both input types are actually represented, so the mapping is exercised.
    assert {row["input_type"] for row in rows} == {"proposed", "production"}


def test_list_is_oldest_first_and_reports_age(client):
    """UI iteration 2: the grid shows age and leads with the longest-waiting input."""
    rows = client.get("/api/review/submissions").get_json()

    dates = [r["date_submitted"] for r in rows]
    assert dates == sorted(dates)
    assert rows[0]["date_submitted"] == min(
        s.date_submitted for s in _census().values()
    ).isoformat()

    oldest = rows[0]
    expected = (date.today() - date.fromisoformat(oldest["date_submitted"])).days
    assert oldest["days_ago"] == expected


def test_list_carries_the_same_ai_summary_the_queue_items_have(client):
    """Change #3: every card says whether the AI has looked at it, and how it went."""
    # Two arbitrary seeded submissions, taken from the manifest rather than
    # named, for the same reason the filter tests derive their expectations.
    checked_id, untouched_id = sorted(_census())[:2]
    _add_check_run(checked_id, ["low", "critical"])

    by_id = {r["submission_id"]: r for r in client.get("/api/review/submissions").get_json()}

    processed = by_id[checked_id]
    assert processed["ai_status"] == "processed"
    assert processed["attention"] == "high_attention"
    assert processed["max_severity"] == "critical"
    assert processed["findings_count"] == 2
    assert processed["latest_check_run_id"] == "cr-review-test"

    untouched = by_id[untouched_id]
    assert untouched["ai_status"] == "unprocessed"
    assert "attention" not in untouched
    assert "max_severity" not in untouched

    # The queue's item for the same submission agrees field for field.
    queue_item = client.get("/api/queue/submission/" + checked_id).get_json()
    for key in ("ai_status", "attention", "max_severity", "findings_count", "latest_check_run_id"):
        assert queue_item[key] == processed[key]


def _ids(client, query: str = "") -> set[str]:
    return {r["submission_id"] for r in client.get("/api/review/submissions" + query).get_json()}


def test_list_filters_by_ai_status(client):
    """The fourth selector: which cards the AI has looked at, and how it went.

    One submission per bucket, then assert the four buckets partition the census
    — so a card can be reached by exactly one setting of the selector.
    """
    census = set(_census())
    issues_id, review_id, clean_id = sorted(census)[:3]
    _add_check_run(issues_id, ["low", "high"], run_id="cr-ai-issues")
    _add_check_run(review_id, ["medium"], run_id="cr-ai-review")
    _add_check_run(clean_id, ["info"], run_id="cr-ai-clean")

    assert _ids(client, "?ai_status=issues") == {issues_id}
    assert _ids(client, "?ai_status=review") == {review_id}
    assert _ids(client, "?ai_status=clean") == {clean_id}
    assert _ids(client, "?ai_status=not_checked") == census - {issues_id, review_id, clean_id}
    assert _ids(client) == census

    # Same closed-vocabulary contract as input_type: a typo is a 400, not a
    # silently unfiltered list.
    assert client.get("/api/review/submissions?ai_status=banana").status_code == 400


def test_ai_status_composes_with_the_other_selectors(client):
    """ai_status narrows what the other three already selected, not the whole table."""
    checked_id = sorted(_census())[0]
    _add_check_run(checked_id, ["critical"], run_id="cr-ai-combo")
    partner = _census()[checked_id].partner

    assert _ids(client, f"?ai_status=issues&partner={partner}") == {checked_id}
    # ...and a partner that owns no flagged card comes back empty rather than
    # falling through to the unfiltered list.
    other = next(p for p in {s.partner for s in _census().values()} if p != partner)
    assert _ids(client, f"?ai_status=issues&partner={other}") == set()


def test_list_filters_by_input_type(client):
    for wanted in ("proposed", "production"):
        rows = client.get(f"/api/review/submissions?input_type={wanted}").get_json()
        expected = _expected_ids(input_type=wanted)

        assert expected, f"the fixture set has no {wanted} submissions to filter for"
        assert {r["submission_id"] for r in rows} == expected
        # the returned rows really are of the requested type
        assert all(r["input_type"] == wanted for r in rows)

    # The three selectors compose.
    product = next(
        s.product.value for s in _census().values() if _input_type_of(s) == "production"
    )
    combo = client.get(
        f"/api/review/submissions?input_type=production&product={product}"
    ).get_json()
    assert {r["submission_id"] for r in combo} == _expected_ids(
        product=product, input_type="production"
    )

    assert client.get("/api/review/submissions?input_type=banana").status_code == 400


def test_filters_by_product_and_partner(client):
    census = _census()

    for product in sorted({s.product.value for s in census.values()}):
        rows = client.get(f"/api/review/submissions?product={product}").get_json()
        assert {r["submission_id"] for r in rows} == _expected_ids(product=product)
        assert all(r["product"] == product for r in rows)

    for partner in sorted({s.partner for s in census.values()}):
        rows = client.get(f"/api/review/submissions?partner={partner}").get_json()
        assert {r["submission_id"] for r in rows} == _expected_ids(partner=partner)
        assert all(r["partner"] == partner for r in rows)

    assert client.get("/api/review/submissions?partner=nobody").get_json() == []

    # A product/partner pair that the fixture set actually populates.
    sample = next(iter(census.values()))
    both = client.get(
        f"/api/review/submissions?product={sample.product.value}&partner={sample.partner}"
    ).get_json()
    assert {r["submission_id"] for r in both} == _expected_ids(
        product=sample.product.value, partner=sample.partner
    )
    assert sample.submission_id in {r["submission_id"] for r in both}


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


def test_reset_restores_the_seeded_fixtures(client, tmp_path, monkeypatch):
    """The demo reset button: back to the manifest, with every trace of a run gone."""
    from backend.db.models import CheckRunRow, FindingRow, ReviewDecisionRow, SubmissionRow
    from backend.db.session import get_session

    monkeypatch.setenv("CLEARPATH_DEMO", "1")
    census = set(_census())
    checked_id = sorted(census)[0]

    # A demo's worth of accumulated state: an AI run with findings, a reviewer's
    # decision on it, and an uploaded submission with evidence on disk.
    _add_check_run(checked_id, ["low", "high"], run_id="cr-reset-test")
    decided = client.post(
        f"/api/queue/submission/{checked_id}/decision", json={"decision": "approved"}
    )
    assert decided.status_code == 200

    created = client.post(
        "/api/review/submissions",
        data={
            "file": (io.BytesIO(b"upload-bytes"), "shot.png"),
            "product": "personal_loan",
            "partner": "acme_partners",
        },
        content_type="multipart/form-data",
    ).get_json()
    uploaded = tmp_path / "uploads" / f"{created['submission_id']}.png"
    assert uploaded.is_file()
    assert _ids(client) == census | {created["submission_id"]}

    response = client.post("/api/review/reset")
    assert response.status_code == 200
    assert response.get_json() == {"status": "reset", "submissions": len(census)}

    # The list is exactly the manifest again, and the upload's file went with it.
    assert _ids(client) == census
    assert not uploaded.exists()

    # Nothing survives in the tables the demo writes to.
    session = get_session()
    try:
        assert session.query(CheckRunRow).count() == 0
        assert session.query(FindingRow).count() == 0
        assert session.query(ReviewDecisionRow).count() == 0
        assert session.get(SubmissionRow, created["submission_id"]) is None
        assert session.query(SubmissionRow).count() == len(census)
    finally:
        session.close()

    # And the reseeded rows are usable: a fresh run can be recorded again.
    assert client.get("/api/review/submissions").get_json()[0]["ai_status"] == "unprocessed"


def test_reset_is_gated_off_by_default(client, monkeypatch):
    """Unset CLEARPATH_DEMO on a non-debug process: the endpoint is not on offer.

    The gate is the only thing standing between a deployment and an
    unauthenticated wipe, so assert the closed state as well as the open one —
    and that a refused call leaves the data alone.
    """
    monkeypatch.delenv("CLEARPATH_DEMO", raising=False)
    checked_id = sorted(_census())[0]
    _add_check_run(checked_id, ["high"], run_id="cr-gate-test")

    assert client.post("/api/review/reset").status_code == 404

    # Refused, not half-done: the run that existed before the call still does.
    from backend.db.models import CheckRunRow
    from backend.db.session import get_session

    session = get_session()
    try:
        assert session.get(CheckRunRow, "cr-gate-test") is not None
    finally:
        session.close()

    # An explicit falsey value is off too, so CLEARPATH_DEMO=0 cannot read as on.
    monkeypatch.setenv("CLEARPATH_DEMO", "0")
    assert client.post("/api/review/reset").status_code == 404

    # ...but the Flask dev server keeps the button with no env change at all,
    # which is what `python -m backend.app` (debug=True) gives a local reviewer.
    monkeypatch.delenv("CLEARPATH_DEMO", raising=False)
    monkeypatch.setattr(client.application, "debug", True)
    assert client.post("/api/review/reset").status_code == 200

    # An explicit opt-in wins over a non-debug process, which is the deploy path.
    monkeypatch.setattr(client.application, "debug", False)
    monkeypatch.setenv("CLEARPATH_DEMO", "1")
    assert client.post("/api/review/reset").status_code == 200


def test_evidence_serves_fixtures_and_rejects_traversal(client):
    ok = client.get("/api/review/evidence/mock_pl_card_compliant.png")
    assert ok.status_code == 200
    assert ok.data[:8] == b"\x89PNG\r\n\x1a\n"

    assert client.get("/api/review/evidence/does_not_exist.png").status_code == 404
    assert client.get("/api/review/evidence/../backend/contracts.py").status_code == 400
    assert client.get("/api/review/evidence/..%2Fbackend%2Fcontracts.py").status_code == 400
    assert client.get("/api/review/evidence/subdir/file.png").status_code == 400
