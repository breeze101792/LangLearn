"""More edge-case tests for auth_gate internals.

test_auth_gate.py / test_auth_gate_edge.py cover the main flows. This file
pins the remaining branches:

- ``_cached_hash`` returns None when no password is set
- ``verify_password`` returns False when no hash is cached
- ``_request_is_secure`` returns True when the request is HTTPS
- ``is_authenticated`` returns False on an expired signature
- ``_client_ip`` falls back to remote_addr
- ``check_login_rate_limit`` returns True when rate limiting is disabled
- rate-limit window cleanup (expired attempts are pruned)
"""

from __future__ import annotations

import time

import pytest


@pytest.fixture
def fresh(clean_state):
    """Re-export of the autouse clean_state fixture."""
    return clean_state


def test_cached_hash_none_without_password(fresh, monkeypatch):
    from backend.app import create_app
    from backend.services import auth_gate
    monkeypatch.delenv("LANGLEARN_PASSWORD", raising=False)
    app = create_app()
    with app.test_request_context("/"):
        assert auth_gate._cached_hash() is None


def test_verify_password_false_without_hash(fresh, monkeypatch):
    from backend.app import create_app
    from backend.services import auth_gate
    monkeypatch.delenv("LANGLEARN_PASSWORD", raising=False)
    app = create_app()
    with app.test_request_context("/"):
        assert auth_gate.verify_password("anything") is False


def test_request_is_secure_when_https(fresh):
    from backend.app import create_app
    from backend.services import auth_gate
    app = create_app()
    with app.test_request_context("/", environ_overrides={"wsgi.url_scheme": "https"}):
        assert auth_gate._request_is_secure() is True


def test_is_authenticated_expired_signature(fresh, monkeypatch):
    """An expired session token is treated as unauthenticated."""
    from backend.app import create_app
    from backend.services import auth_gate
    from itsdangerous import SignatureExpired
    app = create_app()
    with app.app_context():
        signer = auth_gate._signer()
        expired = signer.sign(b"v1").decode("ascii")
        # Force unsign to raise SignatureExpired.
        def fake_unsign(token, max_age=None):
            raise SignatureExpired("expired")
        monkeypatch.setattr(signer, "unsign", fake_unsign)
        monkeypatch.setattr(auth_gate, "_signer", lambda: signer)
        with app.test_request_context("/"):
            assert auth_gate.is_authenticated() is False


def test_client_ip_falls_back_to_remote_addr(fresh):
    from backend.app import create_app
    from backend.services import auth_gate
    app = create_app()
    with app.test_request_context("/", environ_overrides={"REMOTE_ADDR": "10.0.0.9"}):
        assert auth_gate._client_ip() == "10.0.0.9"


def test_client_ip_uses_forwarded_for(fresh):
    from backend.app import create_app
    from backend.services import auth_gate
    app = create_app()
    with app.test_request_context("/", headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}):
        assert auth_gate._client_ip() == "1.2.3.4"


def test_check_login_rate_limit_disabled(fresh):
    from backend.app import create_app
    from backend.services import auth_gate
    app = create_app()
    app.config["LL_AUTH_RATE_LIMIT"] = False
    with app.test_request_context("/"):
        assert auth_gate.check_login_rate_limit() is True


def test_rate_limit_window_prunes_expired(fresh):
    """Attempts older than the window are pruned before the count check."""
    from backend.app import create_app
    from backend.services import auth_gate
    app = create_app()
    with app.test_request_context("/"):
        # Seed the deque with an old attempt.
        old = time.monotonic() - auth_gate.LOGIN_WINDOW_SECONDS - 10
        auth_gate._login_attempts["10.0.0.1"] = __import__("collections").deque([old])
        # A fresh attempt should be allowed (old one pruned).
        assert auth_gate.check_login_rate_limit() is True
        assert len(auth_gate._login_attempts["10.0.0.1"]) == 1
