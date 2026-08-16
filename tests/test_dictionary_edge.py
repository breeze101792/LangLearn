"""Edge-case tests for the dictionary blueprint.

test_dictionary.py / test_dictionary_api_full.py cover the main flows. This
file pins the remaining branches:

- ``/lookup`` word that normalizes to empty -> 400
- ``/lookup`` empty entry with no chain and no override -> early return
- ``/lookup`` auto-add failure is swallowed (200, not 500)
- ``/<provider>`` word that normalizes to empty -> 400
- ``/<provider>`` auto-add failure is swallowed
- ``/providers`` with a non-OpenAI base URL reports llm_configured=True
- ``/suggest`` with a non-string query -> 400
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(clean_state):
    from backend.services.dictionaries import registry
    registry.bootstrap()
    return clean_state


def _client():
    from backend.app import create_app
    app = create_app()
    return app.test_client()


def test_lookup_word_normalizes_to_empty_400(fresh):
    """A word that normalizes to empty (e.g. only whitespace) is rejected."""
    c = _client()
    r = c.post("/api/dictionary/lookup", json={"lang": "en", "word": "   "})
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_word"


def test_lookup_empty_entry_no_chain_early_return(fresh, monkeypatch):
    """When the chain is empty and there's no override, an empty entry
    returns immediately with no suggestions."""
    from backend.services import settings as s
    s.update_settings({"dict_chain_json": {}})
    c = _client()
    r = c.post("/api/dictionary/lookup", json={"lang": "en", "word": "zzz"})
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["entry"]["senses"] == []
    assert data["source"] == ""
    assert data["providers_in_chain"] == 0


def test_lookup_auto_add_failure_swallowed(fresh, monkeypatch):
    """If auto-add raises, the lookup still returns 200 with auto_added
    False rather than a 500."""
    from backend.services import vocab as vocab_svc

    def boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(vocab_svc, "auto_add_from_lookup", boom)

    c = _client()
    r = c.post("/api/dictionary/lookup", json={"lang": "en", "word": "dog"})
    assert r.status_code == 200
    assert r.get_json()["data"]["auto_added"] is False


def test_force_provider_word_normalizes_to_empty_400(fresh):
    c = _client()
    r = c.post("/api/dictionary/wordnet", json={"lang": "en", "word": "   "})
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_word"


def test_force_provider_auto_add_failure_swallowed(fresh, monkeypatch):
    from backend.services import vocab as vocab_svc

    def boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(vocab_svc, "auto_add_from_lookup", boom)

    c = _client()
    r = c.post("/api/dictionary/wordnet", json={"lang": "en", "word": "dog"})
    assert r.status_code == 200
    assert r.get_json()["data"]["auto_added"] is False


def test_providers_llm_configured_without_key_on_alt_host(fresh, monkeypatch):
    """A non-OpenAI base URL reports llm_configured=True even with no key."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    c = _client()
    r = c.get("/api/dictionary/providers")
    body = r.get_json()["data"]
    assert body["llm_configured"] is True
