from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.contracts import (
    CheckClass,
    CheckRun,
    Finding,
    FindingStatus,
    Severity,
    SubmissionMode,
)
from backend.db import (
    CheckRunRow,
    OfferCellRow,
    SubmissionRow,
    get_session,
    import_offer_matrix,
    init_db,
    latest_matrix_version,
)
from backend.ingest import load_submissions

TESTDATA = Path(__file__).resolve().parents[1] / "ingest" / "testdata"
MATRIX_CSV = TESTDATA / "clearpath_offer_matrix.csv"
SUBMISSIONS_CSV = TESTDATA / "ck_placement_submissions.csv"


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARPATH_DB", f"sqlite:///{tmp_path}/test.db")
    # reset the module-level engine cache so the env var takes effect
    import backend.db.session as dbs

    monkeypatch.setattr(dbs, "_engine", None)
    monkeypatch.setattr(dbs, "_session_factory", None)
    init_db(reset=True)
    s = get_session()
    yield s
    s.close()


def test_import_offer_matrix_is_idempotent(session):
    v1 = import_offer_matrix(session, MATRIX_CSV)
    v2 = import_offer_matrix(session, MATRIX_CSV)
    assert v1 == v2
    assert v1.startswith("omx-")
    rows = session.query(OfferCellRow).all()
    assert len(rows) == 7  # not duplicated on re-import
    assert latest_matrix_version(session) == v1

    pl60 = session.get(OfferCellRow, ("PL-60-A", v1))
    cell = pl60.to_contract()
    assert "IL" in cell.states_excluded
    assert cell.notes.startswith("IL excluded")


def test_submission_round_trip(session):
    subs = load_submissions(SUBMISSIONS_CSV)
    for sub in subs:
        session.add(SubmissionRow.from_contract(sub))
    session.commit()

    row = session.get(SubmissionRow, "SUB-2026-0145")
    restored = row.to_contract()
    original = next(s for s in subs if s.submission_id == "SUB-2026-0145")
    assert restored == original


def test_check_run_round_trip_with_findings(session):
    subs = load_submissions(SUBMISSIONS_CSV)
    session.add(SubmissionRow.from_contract(subs[0]))
    session.commit()

    run = CheckRun(
        id="run-001",
        submission_id=subs[0].id,
        rulebook_version="rb-2026.08",
        offer_matrix_version="omx-deadbeef00",
        mode=SubmissionMode.PRE_PUBLICATION,
        created_at=datetime(2026, 8, 29, 12, 0, 0),
        findings=[
            Finding(
                id="f-1",
                check_class=CheckClass.LEGALITY,
                severity=Severity.HIGH,
                rule_id="regz-trigger-24d",
                claim_id="c-1",
                summary="Payment amount shown without companion disclosures",
                explanation="'$509/mo' is a Reg Z trigger term.",
                citation_url="https://www.consumerfinance.gov/rules-policy/regulations/1026/24/",
                suggested_redline="Add repayment terms and APR.",
            ),
            Finding(
                id="f-2",
                check_class=CheckClass.JUDGMENT,
                severity=Severity.MEDIUM,
                summary="Approval-odds phrasing may overstate certainty",
                explanation="Net impression review recommended.",
                status=FindingStatus.OVERRIDDEN,
            ),
        ],
    )
    session.add(CheckRunRow.from_contract(run))
    session.commit()

    restored = session.get(CheckRunRow, "run-001").to_contract()
    assert restored == run
    assert restored.findings[1].status == FindingStatus.OVERRIDDEN
