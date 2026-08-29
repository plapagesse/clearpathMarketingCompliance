"""Seed the database from a fixtures directory.

Usage: python -m backend.db.seed [--fixtures-dir PATH]

Defaults to ./fixtures when present, else backend/ingest/testdata. Resets the
schema on every run so re-seeding is clean and reviewers always start fresh.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from backend.db.models import SubmissionRow
from backend.db.session import get_session, import_offer_matrix, init_db
from backend.ingest import load_submissions

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTDATA_DIR = REPO_ROOT / "backend" / "ingest" / "testdata"
FIXTURES_DIR = REPO_ROOT / "fixtures"


def _find_one(directory: Path, patterns: list[str]) -> Path:
    for pattern in patterns:
        matches = sorted(directory.glob(pattern))
        if matches:
            return matches[0]
    raise FileNotFoundError(f"none of {patterns} found in {directory}")


def seed(fixtures_dir: Path | None = None) -> dict:
    directory = fixtures_dir or (FIXTURES_DIR if FIXTURES_DIR.is_dir() else TESTDATA_DIR)
    matrix_csv = _find_one(directory, ["*offer_matrix*.csv"])
    submissions_csv = _find_one(directory, ["*submission*.csv"])

    init_db(reset=True)
    session = get_session()
    try:
        version = import_offer_matrix(session, matrix_csv)
        submissions = load_submissions(submissions_csv)
        for sub in submissions:
            session.add(SubmissionRow.from_contract(sub))
        session.commit()
        return {
            "fixtures_dir": str(directory),
            "offer_matrix_version": version,
            "submissions": len(submissions),
        }
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures-dir", type=Path, default=None)
    args = parser.parse_args()
    summary = seed(args.fixtures_dir)
    print(
        f"seeded from {summary['fixtures_dir']}: "
        f"offer matrix {summary['offer_matrix_version']}, "
        f"{summary['submissions']} submissions"
    )


if __name__ == "__main__":
    main()
