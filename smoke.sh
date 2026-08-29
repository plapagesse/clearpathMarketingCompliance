#!/usr/bin/env bash
# Smoke test: boot Flask, assert /api/health, tear down.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-5001}"
PORT="$PORT" .venv/bin/python -m backend.app >/dev/null 2>&1 &
PID=$!
trap 'kill "$PID" 2>/dev/null || true' EXIT
for i in $(seq 1 20); do
  sleep 0.5
  if curl -sf "http://localhost:${PORT}/api/health" | grep -q '"ok"'; then
    echo "smoke: OK (health endpoint on :${PORT})"
    exit 0
  fi
done
echo "smoke: FAIL — /api/health never returned ok" >&2
exit 1
