"""Auth blueprint — stub for v1 (single-user). Ready for future multi-user."""

from __future__ import annotations

from flask import Blueprint

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@bp.get("/whoami")
def whoami():
    return {"ok": True, "data": {"user_id": 1, "username": "me"}}