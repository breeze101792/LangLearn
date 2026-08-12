"""Tests for the app-level JSON error handlers.

The app registers three Flask ``errorhandler``s — for 404 (not_found),
405 (method_not_allowed), and 500 (internal_error). Each must return the
``{ok: false, error: ..., code: ...}`` envelope used everywhere else.

Individual blueprints assert their own 400/404/502 paths, but the
generic Flask handlers in ``backend/app.py`` are not exercised
elsewhere. This file pins that contract.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def client():
    from backend.app import create_app
    app = create_app()
    return app.test_client()


def test_404_returns_err_envelope(client):
    """A request to an unknown non-/api route (so it bypasses the auth
    gate's pre-request hook) hits the generic 404 handler."""
    r = client.get("/some-totally-unknown-page")
    assert r.status_code == 404
    body = r.get_json()
    assert body["ok"] is False
    assert body["error"] == "not_found"
    assert body["code"] == "not_found"


def test_404_for_static_asset_path(client):
    """An unknown /api/* path falls through to the generic 404 handler
    when the auth gate is not enabled (no LANGLEARN_PASSWORD)."""
    r = client.get("/api/vocabulary/study/refresh/wat")
    assert r.status_code == 404
    body = r.get_json()
    assert body["ok"] is False
    assert body["code"] == "not_found"


def test_404_for_unknown_api_path_when_auth_enabled(client, monkeypatch):
    """When the auth gate is enabled (``LANGLEARN_PASSWORD`` set), an
    unknown /api/* path is intercepted by the gate before the 404
    handler. The response is 401 (unauthorized) — that avoids leaking
    the route catalog. This is the auth-first contract that
    ``_register_auth_gate`` in ``app.py`` establishes."""
    monkeypatch.setenv("LANGLEARN_PASSWORD", "secret")
    from backend.app import create_app
    app = create_app()
    c = app.test_client()
    r = c.get("/api/something-unknown")
    assert r.status_code == 401
    body = r.get_json()
    assert body["ok"] is False
    assert body["code"] == "unauthorized"


def test_405_returns_err_envelope(client):
    """Method Not Allowed for an existing route with the wrong verb.
    GET on a POST-only route is a 405."""
    # /api/languages is GET-only for listing (POST also exists);
    # PATCH should hit the generic 405 handler.
    from backend.app import create_app
    app = create_app()
    c = app.test_client()
    r = c.patch("/api/languages")
    assert r.status_code == 405
    body = r.get_json()
    assert body["ok"] is False
    assert body["error"] == "method_not_allowed"
    assert body["code"] == "method_not_allowed"


def test_500_returns_err_envelope(monkeypatch):
    """An unhandled exception in a route bubbles up to the generic 500
    handler, which must return the JSON envelope (not a HTML stack
    trace)."""
    from backend.app import create_app

    app = create_app()

    def boom():
        raise RuntimeError("kaboom")

    # Add a deliberately broken route that we can trigger.
    app.add_url_rule("/api/_test_boom", view_func=boom, methods=["GET"])

    client = app.test_client()
    r = client.get("/api/_test_boom")
    assert r.status_code == 500
    body = r.get_json()
    assert body["ok"] is False
    assert body["error"] == "internal_error"
    assert body["code"] == "internal_error"
