"""Auth blueprint: login, logout, whoami.

Login expects a single shared password from the env var
``LANGLEARN_PASSWORD``. The env var is read on every login attempt (it is
never persisted). Sessions are signed cookies issued by ``auth_gate``.

When ``LANGLEARN_PASSWORD`` is unset the app behaves as before: every
endpoint is reachable without a login, and ``whoami`` returns the default
user. This preserves the local-dev single-user workflow.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, make_response, request

from .. import config
from ..services import auth_gate
from ..util import err, ok

log = logging.getLogger("langlearn.auth")

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@bp.get("/status")
def status():
    """Public — tells the frontend whether the app is password-gated. Lets the
    SPA skip rendering the login screen entirely when the user has chosen
    not to set a password (e.g. local development)."""
    return jsonify(ok({"auth_required": auth_gate.is_auth_enabled()}))


@bp.get("/whoami")
def whoami():
    return jsonify(ok({"user_id": config.DEFAULT_USER_ID, "username": "me"}))


@bp.post("/login")
def login():
    if not auth_gate.is_auth_enabled():
        return jsonify(ok({"user_id": config.DEFAULT_USER_ID, "already_open": True}))

    if not auth_gate.check_login_rate_limit():
        resp = jsonify(err("too_many_attempts", code="rate_limited"))
        resp.status_code = 429
        return resp

    body = request.get_json(silent=True) or {}
    password = body.get("password")
    if not isinstance(password, str) or not password:
        resp = jsonify(err("password_required", code="invalid_input"))
        resp.status_code = 400
        return resp

    if not auth_gate.verify_password(password):
        log.info("login failed from %s", auth_gate._client_ip())
        resp = jsonify(err("invalid_password", code="invalid_credentials"))
        resp.status_code = 401
        return resp

    auth_gate.reset_login_rate_limit()
    resp = make_response(jsonify(ok({"user_id": config.DEFAULT_USER_ID})))
    auth_gate.issue_session(resp)
    return resp


@bp.post("/logout")
def logout():
    resp = make_response(jsonify(ok({"logged_out": True})))
    auth_gate.clear_session(resp)
    return resp
