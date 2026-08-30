"""Engine/session helpers and offer-matrix import with content-hash versioning."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.models import Base, MatrixImport, OfferCellRow
from backend.ingest import load_offer_matrix

DEFAULT_DB_PATH = Path(__file__).parent / "clearpath.db"


def _db_url() -> str:
    return os.environ.get("CLEARPATH_DB", f"sqlite:///{DEFAULT_DB_PATH}")


_engine = None
_session_factory = None


def _apply_sqlite_pragmas(engine) -> None:
    # Parallel /process requests commit CheckRuns concurrently; WAL + busy_timeout
    # prevent "database is locked" under the dev server's threaded mode.
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def get_engine():
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine(_db_url())
        _apply_sqlite_pragmas(_engine)
        _session_factory = sessionmaker(bind=_engine)
    return _engine


def get_session() -> Session:
    get_engine()
    return _session_factory()


def init_db(reset: bool = False) -> None:
    engine = get_engine()
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def matrix_version_for(csv_path: str | Path) -> str:
    """Deterministic version id: content hash of the CSV file."""
    digest = hashlib.sha256(Path(csv_path).read_bytes()).hexdigest()
    return f"omx-{digest[:10]}"


def import_offer_matrix(session: Session, csv_path: str | Path) -> str:
    """Ingest an offer-matrix CSV under its content-hash version.

    Idempotent: re-importing an identical file is a no-op returning the same
    version; a changed file lands under a new version, leaving prior versions
    untouched (CheckRuns reference the version they ran against).
    """
    version = matrix_version_for(csv_path)
    existing = session.get(MatrixImport, version)
    if existing is not None:
        return version

    session.add(
        MatrixImport(
            offer_matrix_version=version,
            source_path=str(csv_path),
            imported_at=datetime.now(timezone.utc),
        )
    )
    for cell in load_offer_matrix(csv_path):
        session.add(OfferCellRow.from_contract(cell, version))
    session.commit()
    return version


def latest_matrix_version(session: Session) -> str | None:
    row = session.execute(
        select(MatrixImport).order_by(MatrixImport.imported_at.desc()).limit(1)
    ).scalar_one_or_none()
    return row.offer_matrix_version if row else None
