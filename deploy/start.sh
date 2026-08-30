#!/usr/bin/env sh
# Production entrypoint: seed a fresh demo DB on every boot, then exec gunicorn.
#
# Re-seeding per restart is DELIBERATE — every deploy/restart starts from clean
# demo state (16 fixture submissions + current offer matrix). Uploaded evidence
# and review decisions do not survive a restart; that is the demo contract.
#
# Env:
#   PORT          port gunicorn binds (default 8000; Railway/Fly inject this)
#   CLEARPATH_DB  SQLAlchemy URL (sqlite:////abs/path.db) or a bare file path —
#                 bare paths are normalized to a sqlite:/// URL below.
set -eu

cd "$(dirname "$0")/.."

# Accept either a full SQLAlchemy URL or a plain filesystem path.
if [ -n "${CLEARPATH_DB:-}" ]; then
  case "$CLEARPATH_DB" in
    *://*) ;; # already a URL
    *) export CLEARPATH_DB="sqlite:///$CLEARPATH_DB" ;; # /abs/p.db -> sqlite:////abs/p.db
  esac
fi

python -m backend.db.seed

exec gunicorn --threads 8 -b "0.0.0.0:${PORT:-8000}" backend.app:app
