"""Edge-case tests for auth_gate helpers and the dictionary blueprint.

test_auth_gate.py covers the HTTP auth flow. This file pins the lower-level
helpers that were not exercised there:

- ``_is_private_or_local_host`` boundary cases (empty, public, IPv6)
- ``_signer`` RuntimeError when SECRET_KEY is missing
- ``constant_time_str_eq``
- ``require_auth`` decorator (auth enabled + authenticated / not)
- ``_request_is_secure`` via X-Forwarded-Proto

And dictionary blueprint edge cases:
- ``/lookup`` with a provider override that is an empty string (falls back
  to the chain)
- ``/lookup`` auto-add failure is swallowed (returns 200, not 500)
- ``/providers`` with a non-OpenAI base URL reports llm_configured=True
  even without a key
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(clean_state):
    """Re-export of the autouse clean_state fixture."""
    return clean_state


# ---------- auth_gate helpers ----------


def test_is_private_or_local_host_cases(fresh):
    from backend.services import auth_gate
    assert auth_gate._is_private_or_local_host("localhost") is True
    assert auth_gate._is_private_or_local_host("192.168.1.1") is True
    assert auth_gate._is_private_or_local_host("10.0.0.5") is True
    assert auth_gate._is_private_or_local_host("raspi.lan") is True
    assert auth_gate._is_private_or_local_host("mybox") is True
    assert auth_gate._is_private_or_local_host("::1") is True
    assert auth_gate._is_private_or_local_host("example.com") is False
    assert auth_gate._is_private_or_local_host("") is False
    # A malformed IP that isn't a hostname either.
    assert auth_gate._is_private_or_local_host("999.999.999.999") is False


def test_constant_time_str_eq(fresh):
    from backend.services import auth_gate
    assert auth_gate.constant_time_str_eq("abc", "abc") is True
    assert auth_gate.constant_time_str_eq("abc", "abd") is False
    assert auth_gate.constant_time_str_eq("", "") is True


def test_signer_raises_without_secret_key(fresh):
    from backend.app import create_app
    from backend.services import auth_gate
    app = create_app()
    app.config["SECRET_KEY"] = None
    with app.test_request_context("/"):
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            auth_gate._signer()


def test_request_is_secure_via_forwarded_proto(fresh):
    from backend.app import create_app
    from backend.services import auth_gate
    app = create_app()
    with app.test_request_context("/", headers={"X-Forwarded-Proto": "https"}):
        assert auth_gate._request_is_secure() is True


def test_request_is_secure_false_for_local_http(fresh):
    from backend.app import create_app
    from backend.services import auth_gate
    app = create_app()
    with app.test_request_context("/", headers={"Host": "localhost"}):
        assert auth_gate._request_is_secure() is False


# ---------- require_auth decorator ----------


def test_require_auth_passes_when_authenticated(fresh, monkeypatch):
    from backend.app import create_app
    from backend.services import auth_gate
    app = create_app()

    @auth_gate.require_auth
    def view():
        return "ok"

    monkeypatch.setenv("LANGLEARN_PASSWORD", "secret")
    # Simulate an authenticated request by patching is_authenticated.
    monkeypatch.setattr(auth_gate, "is_authenticated", lambda *a, **k: True)
    with app.test_request_context("/"):
        assert view() == "ok"


def test_require_auth_returns_401_when_not_authenticated(fresh, monkeypatch):
    from backend.app import create_app
    from backend.services import auth_gate
    app = create_app()

    @auth_gate.require_auth
    def view():
        return "ok"

    monkeypatch.setenv("LANGLEARN_PASSWORD", "secret")
    monkeypatch.setattr(auth_gate, "is_authenticated", lambda *a, **k: False)
    with app.test_request_context("/"):
        resp = view()
    assert resp.status_code == 401
    assert resp.get_json()["code"] == "unauthorized"


def test_require_auth_skips_when_disabled(fresh, monkeypatch):
    from backend.app import create_app
    from backend.services import auth_gate
    app = create_app()

    @auth_gate.require_auth
    def view():
        return "ok"

    monkeypatch.delenv("LANGLEARN_PASSWORD", raising=False)
    with app.test_request_context("/"):
        assert view() == "ok"


# ---------- dictionary blueprint edge cases ----------


def test_lookup_provider_override_empty_string_falls_back(fresh, monkeypatch):
    """An empty-string provider override behaves as 'no override' and uses
    the chain."""
    from backend.app import create_app
    from backend.services import llm as llm_svc

    def fake_llm(*, lang, word, **kwargs):
        return {"senses": [{"pos": "noun",
                            "definitions": [{"glossary": "x"}]}]}
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", fake_llm)

    app = create_app()
    client = app.test_client()
    # 'asdfqwer' has no WordNet entry, so the chain falls through to LLM.
    r = client.post("/api/dictionary/lookup", json={
        "lang": "en", "word": "asdfqwer", "provider": "",
    })
    assert r.status_code == 200
    assert r.get_json()["data"]["source"] == "llm"


def test_lookup_auto_add_failure_swallowed(fresh, monkeypatch):
    """If auto-add raises, the lookup still returns 200 with auto_added
    False rather than a 500."""
    from backend.app import create_app
    from backend.services import vocab as vocab_svc

    def boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(vocab_svc, "auto_add_from_lookup", boom)

    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "dog"})
    assert r.status_code == 200
    assert r.get_json()["data"]["auto_added"] is False


def test_providers_llm_configured_without_key_on_alt_host(fresh, monkeypatch):
    """A non-OpenAI base URL reports llm_configured=True even with no key."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/dictionary/providers")
    body = r.get_json()["data"]
    assert body["llm_configured"] is True
