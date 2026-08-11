"""Tests for the password-gate auth flow.

Covers:
- status, whoami, login, logout endpoints
- gating of /api/* when LANGLEARN_PASSWORD is set
- SPA route serving login.html vs index.html
- in-memory login rate limit
- env-var password rotation (env re-read on each verify, hash cache invalidation)
- "auth disabled when LANGLEARN_PASSWORD is unset" path (existing tests live
  under the same conftest, so this is the default)
"""

from __future__ import annotations

import pytest


@pytest.fixture
def gated(monkeypatch, tmp_path):
    """Per-test data dir + LANGLEARN_PASSWORD set."""
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LANGLEARN_PASSWORD", "topsecret")
    from backend import db
    db.init_schema()
    return tmp_path


def _login(client, password="topsecret"):
    return client.post("/api/auth/login", json={"password": password})


def _session_cookie(resp):
    raw = resp.headers.get("Set-Cookie", "")
    assert raw.startswith("ll_session=")
    return raw.split(";")[0].split("=", 1)[1]


# --- /api/auth/status -----------------------------------------------------


def test_status_reports_auth_required_when_password_set(gated):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.get_json()["data"]["auth_required"] is True


def test_status_reports_open_when_password_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LANGLEARN_PASSWORD", raising=False)
    from backend import db
    db.init_schema()
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/auth/status")
    assert r.get_json()["data"]["auth_required"] is False


# --- /api/auth/login ------------------------------------------------------


def test_login_with_correct_password_sets_cookie(gated):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = _login(client)
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert "ll_session=" in r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in r.headers.get("Set-Cookie", "")
    assert "SameSite=Strict" in r.headers.get("Set-Cookie", "")
    # Werkzeug's test client synthesizes Host=localhost on bare requests;
    # the backend treats that as a local-network request → no Secure flag.
    assert "Secure" not in r.headers.get("Set-Cookie", "")


def test_login_cookie_not_secure_for_local_network_host(gated):
    """Plain-HTTP request from a private IP / localhost / .lan host should
    NOT get the Secure flag, so the cookie works on the LAN."""
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    for host in (
        "192.168.1.10", "192.168.1.10:5056",
        "10.0.0.5", "10.31.1.9",
        "127.0.0.1", "localhost", "raspi.lan", "mybox",
    ):
        r = client.post(
            "/api/auth/login",
            json={"password": "topsecret"},
            headers={"Host": host},
        )
        assert r.status_code == 200, host
        assert "Secure" not in r.headers.get("Set-Cookie", ""), host


def test_login_cookie_secure_for_public_domain_host(gated):
    """A public-looking Host header gets the Secure flag even on plain HTTP
    (so the cookie is not silently dropped on the public origin)."""
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    for host in ("langlearn.example.com", "langlearn.io"):
        r = client.post(
            "/api/auth/login",
            json={"password": "topsecret"},
            headers={"Host": host},
        )
        assert r.status_code == 200, host
        assert "Secure" in r.headers.get("Set-Cookie", ""), host


def test_login_cookie_secure_when_x_forwarded_proto_https(gated):
    """Reverse proxy in front of the app sends X-Forwarded-Proto=https;
    the backend trusts it without any env-var opt-in."""
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post(
        "/api/auth/login",
        json={"password": "topsecret"},
        headers={"Host": "langlearn.example.com", "X-Forwarded-Proto": "https"},
    )
    assert r.status_code == 200
    assert "Secure" in r.headers.get("Set-Cookie", "")


def test_login_with_wrong_password_returns_401(gated):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = _login(client, password="nope")
    assert r.status_code == 401
    body = r.get_json()
    assert body["code"] == "invalid_credentials"
    assert "ll_session=" not in r.headers.get("Set-Cookie", "")


def test_login_with_empty_password_returns_400(gated):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/auth/login", json={"password": ""})
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


def test_login_with_missing_password_returns_400(gated):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/auth/login", json={})
    assert r.status_code == 400


# --- /api/auth/logout -----------------------------------------------------


def test_logout_clears_session_cookie(gated):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = _login(client)
    client.set_cookie("ll_session", _session_cookie(r))
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    set_cookie = r.headers.get("Set-Cookie", "")
    assert "ll_session=" in set_cookie
    assert "Max-Age=0" in set_cookie or "Expires=" in set_cookie


# --- Gating of /api/* -----------------------------------------------------


def test_settings_endpoint_requires_auth(gated):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/settings")
    assert r.status_code == 401
    assert r.get_json()["code"] == "unauthorized"


def test_settings_endpoint_passes_with_cookie(gated):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = _login(client)
    client.set_cookie("ll_session", _session_cookie(r))
    r = client.get("/api/settings")
    assert r.status_code == 200


def test_invalid_session_cookie_returns_401(gated):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    client.set_cookie("ll_session", "not-a-real-token")
    r = client.get("/api/settings")
    assert r.status_code == 401


# --- Auth-free routes stay reachable --------------------------------------


def test_auth_blueprint_endpoints_are_exempt(gated):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    # /api/auth/* must always work without a cookie so users can log in.
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    r = client.get("/api/auth/whoami")
    assert r.status_code == 200


def test_static_assets_unaffected_by_auth(gated):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/static/css/tokens.css")
    assert r.status_code == 200
    r = client.get("/manifest.json")
    assert r.status_code == 200


# --- SPA route ------------------------------------------------------------


def test_root_serves_login_html_when_not_authed(gated):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/")
    assert r.status_code == 200
    assert b'id="password"' in r.data


def test_root_serves_app_when_authed(gated):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = _login(client)
    client.set_cookie("ll_session", _session_cookie(r))
    r = client.get("/")
    assert r.status_code == 200
    assert b"main.js" in r.data


def test_root_serves_app_when_auth_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LANGLEARN_PASSWORD", raising=False)
    from backend import db
    db.init_schema()
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/")
    assert r.status_code == 200
    assert b"main.js" in r.data


# --- Rate limiting --------------------------------------------------------


def test_login_rate_limit_blocks_after_threshold(gated):
    from backend.app import create_app
    from backend.services import auth_gate
    app = create_app()
    client = app.test_client()
    # Clear any state from prior tests in the same process.
    auth_gate._login_attempts.clear()
    for _ in range(5):
        r = _login(client, password="wrong")
        assert r.status_code == 401
    r = _login(client, password="topsecret")
    assert r.status_code == 429
    assert r.get_json()["code"] == "rate_limited"


def test_login_rate_limit_resets_on_success(gated):
    from backend.app import create_app
    from backend.services import auth_gate
    app = create_app()
    client = app.test_client()
    auth_gate._login_attempts.clear()
    for _ in range(4):
        r = _login(client, password="wrong")
        assert r.status_code == 401
    r = _login(client, password="topsecret")
    assert r.status_code == 200
    # Now we should have a fresh budget.
    r = client.post("/api/auth/login", json={"password": "wrong"})
    assert r.status_code == 401
    r = _login(client, password="topsecret")
    assert r.status_code == 200


# --- Password rotation (env var re-read) ----------------------------------


def test_password_change_takes_effect_after_cache_invalidation(gated):
    """The cached hash is keyed by the raw env value. If the env var is
    changed at runtime (e.g. operator edits .env and signals the process),
    the cache miss on next verify picks up the new hash."""
    import os
    from backend.app import create_app
    from backend.services import auth_gate
    app = create_app()
    client = app.test_client()
    # Old password works
    r = _login(client, password="topsecret")
    assert r.status_code == 200
    # Rotate
    os.environ["LANGLEARN_PASSWORD"] = "rotated"
    r = _login(client, password="topsecret")
    assert r.status_code == 401
    r = _login(client, password="rotated")
    assert r.status_code == 200


def test_password_is_not_written_to_disk(gated):
    """The raw password must never appear in the SQLite file or the session
    secret file."""
    from backend.app import create_app
    import sqlite3
    from backend import config
    app = create_app()
    client = app.test_client()
    _login(client, password="topsecret")
    db_file = config.db_path()
    secret_file = config.data_dir() / "session.secret"
    if db_file.exists():
        with sqlite3.connect(str(db_file)) as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            for (name,) in rows:
                try:
                    data = "\n".join(str(r) for r in conn.execute(f"SELECT * FROM {name}").fetchall())
                except Exception:
                    continue
                assert "topsecret" not in data
    if secret_file.exists():
        assert "topsecret" not in secret_file.read_text()


# --- Auth disabled: existing behavior preserved ---------------------------


def test_no_password_means_open_app(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LANGLEARN_PASSWORD", raising=False)
    from backend import db
    db.init_schema()
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/settings")
    assert r.status_code == 200
    r = client.post("/api/auth/login", json={"password": "anything"})
    assert r.status_code == 200
    assert r.get_json()["data"]["already_open"] is True


# --- Session secret persistence -------------------------------------------


def test_session_secret_persists_across_restarts(gated):
    from backend.app import create_app
    from backend import config
    app = create_app()
    secret_path = config.data_dir() / "session.secret"
    assert secret_path.exists()
    first = secret_path.read_text()
    # A second app build should reuse the same secret (cookies survive).
    app2 = create_app()
    second = app2.config["SECRET_KEY"]
    assert second == first
