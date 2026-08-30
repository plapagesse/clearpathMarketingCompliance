"""Flask entrypoint. Serves the API under /api/* and the built React bundle.

Local dev:  PORT=5001 python -m backend.app   (5000 is taken by macOS AirPlay)
Production: gunicorn backend.app:app
"""

import os
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)

    from backend.api.queue import queue_bp
    from backend.api.review import review_bp
    from backend.prequal.api import prequal_bp

    app.register_blueprint(prequal_bp)
    app.register_blueprint(review_bp)
    app.register_blueprint(queue_bp)

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    # --- static serving of the built frontend (catch-all, API takes precedence) ---

    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def frontend(path: str):
        if not FRONTEND_DIST.exists():
            return "frontend not built — run `npm run build` in /frontend", 200
        target = FRONTEND_DIST / path
        if path and target.is_file():
            return send_from_directory(FRONTEND_DIST, path)
        return send_from_directory(FRONTEND_DIST, "index.html")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "5001")), debug=True)
