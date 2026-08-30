"""HTTP API blueprints for the reviewer UI."""

from backend.api.queue import queue_bp
from backend.api.review import review_bp

__all__ = ["queue_bp", "review_bp"]
