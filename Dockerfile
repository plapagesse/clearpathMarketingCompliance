# syntax=docker/dockerfile:1
# Single-service image: Flask serves the API and the built React bundle.

# ---- Stage 1: build the frontend ----
FROM node:20-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY rulebook/ rulebook/
COPY fixtures/ fixtures/
COPY scripts/ scripts/
COPY deploy/start.sh deploy/start.sh
COPY --from=frontend /build/frontend/dist frontend/dist

# Non-root runtime user; writable dirs for the SQLite DB and evidence uploads.
RUN useradd --create-home appuser \
    && mkdir -p /app/data /app/uploads \
    && chown appuser:appuser /app/data /app/uploads
USER appuser

# DB lives on a writable path; start.sh re-seeds it on every boot (deliberate).
ENV CLEARPATH_DB=sqlite:////app/data/clearpath.db

EXPOSE 8000
ENTRYPOINT ["/bin/sh", "deploy/start.sh"]
