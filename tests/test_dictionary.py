"""Tests for dictionary providers and chain."""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    from backend import db
    db.init_schema()
    from backend.services.dictionaries import registry
    registry.bootstrap()
    return tmp_path


def test_wordnet_english_returns_senses(fresh):
    from backend.services.dictionaries import wordnet

    entry = wordnet.lookup("dog", "en")
    assert not entry.is_empty
    assert entry.language == "en"
    assert entry.source == "wordnet"
    assert len(entry.senses) >= 1
    assert any(s.pos for s in entry.senses)


def test_wordnet_non_english_returns_empty(fresh):
    from backend.services.dictionaries import wordnet

    entry = wordnet.lookup("casa", "es")
    assert entry.is_empty


def test_wordnet_unknown_word_returns_empty(fresh):
    from backend.services.dictionaries import wordnet

    entry = wordnet.lookup("asdfqwer", "en")
    assert entry.is_empty


def test_chain_picks_wordnet_first(fresh, monkeypatch):
    from backend.services.dictionaries import registry
    from backend.services import llm as llm_svc

    def should_not_call(*a, **kw):
        raise AssertionError("LLM should not be called when WordNet succeeds")
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", should_not_call)

    chain = [{"name": "wordnet", "enabled": True},
             {"name": "llm", "enabled": True}]
    result = registry.lookup_via_chain(word="dog", lang="en", chain=chain)
    assert result.entry.source == "wordnet"
    assert result.errors == []


def test_chain_skips_disabled(fresh, monkeypatch):
    from backend.services.dictionaries import registry
    from backend.services import llm as llm_svc

    def fake_llm(*, lang, word, **kwargs):
        return {"senses": [{"pos": "noun", "definitions": [{"glossary": "fake"}]}]}
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", fake_llm)

    chain = [{"name": "wordnet", "enabled": False},
             {"name": "llm", "enabled": True}]
    result = registry.lookup_via_chain(word="dog", lang="en", chain=chain)
    assert result.entry.source == "llm"


def test_chain_handles_provider_error(fresh, monkeypatch):
    from backend.services.dictionaries import registry
    from backend.services import llm as llm_svc

    def bad(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", bad)

    chain = [{"name": "llm", "enabled": True}]
    result = registry.lookup_via_chain(word="dog", lang="en", chain=chain)
    assert result.entry.is_empty
    assert any(e["provider"] == "llm" for e in result.errors)


def test_chain_empty_returns_empty(fresh):
    from backend.services.dictionaries import registry

    result = registry.lookup_via_chain(word="dog", lang="en", chain=[])
    assert result.entry.is_empty


def test_chain_invalid_lang_returns_empty(fresh):
    from backend.services.dictionaries import registry

    result = registry.lookup_via_chain(word="dog", lang="invalid!", chain=[])
    assert result.entry.is_empty


def test_force_provider(fresh, monkeypatch):
    from backend.services.dictionaries import registry
    from backend.services import llm as llm_svc

    def fake_llm(*, lang, word, **kwargs):
        return {"senses": [{"pos": "noun", "definitions": [{"glossary": "forced"}]}]}
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", fake_llm)

    result = registry.lookup_with_provider(word="dog", lang="es", provider_name="llm")
    assert result.entry.source == "llm"
    assert result.errors == []


def test_force_unknown_provider_returns_empty(fresh):
    from backend.services.dictionaries import registry

    result = registry.lookup_with_provider(word="dog", lang="es", provider_name="nonexistent")
    assert result.entry.is_empty


def test_force_provider_records_error(fresh, monkeypatch):
    """When the forced provider raises, the chain result carries the error
    so the UI can surface 'AI is unreachable' instead of just 'no senses'."""
    from backend.services.dictionaries import registry
    from backend.services import llm as llm_svc

    def bad(*a, **kw):
        raise RuntimeError("network down")
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", bad)

    result = registry.lookup_with_provider(word="dog", lang="en", provider_name="llm")
    assert result.entry.is_empty
    assert any(e["provider"] == "llm" and "network down" in e["error"]
               for e in result.errors)


def test_chain_records_llm_timeout(fresh, monkeypatch):
    """Regression: the LLM dictionary provider used to swallow LLMTimeout
    and return an empty entry silently. The chain never saw the failure,
    so the UI couldn't tell the user AI was unreachable. The provider now
    re-raises; this test pins that contract end-to-end."""
    from backend.services.dictionaries import registry
    from backend.services import llm as llm_svc

    def timeout(*a, **kw):
        raise llm_svc.LLMTimeout("HTTPConnectionPool: Read timed out.")
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", timeout)

    chain = [{"name": "wordnet", "enabled": True},
             {"name": "llm", "enabled": True}]
    # Use a word WordNet doesn't know so the chain actually reaches LLM.
    result = registry.lookup_via_chain(word="zzznotaword", lang="en", chain=chain)
    assert result.entry.is_empty
    assert any(e["provider"] == "llm" and "Read timed out" in e["error"]
               for e in result.errors), f"expected LLM timeout in errors, got {result.errors}"


def test_chain_records_llm_timeout_via_force(fresh, monkeypatch):
    """When the user explicitly clicks 'AI' and LLM times out, the popup
    needs the exact error string to render its own provider-error card."""
    from backend.services.dictionaries import registry
    from backend.services import llm as llm_svc

    def timeout(*a, **kw):
        raise llm_svc.LLMTimeout("HTTPConnectionPool: Read timed out. (read timeout=20)")
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", timeout)

    result = registry.lookup_with_provider(word="typically", lang="en", provider_name="llm")
    assert result.entry.is_empty
    assert len(result.errors) == 1
    assert result.errors[0]["provider"] == "llm"
    assert "read timeout=20" in result.errors[0]["error"]


def test_lookup_endpoint_surfaces_llm_timeout(fresh, monkeypatch):
    """End-to-end: when LLM times out, the HTTP response carries
    `provider_errors` so the popup can show 'AI is unreachable'."""
    from backend.services import llm as llm_svc
    from backend.app import create_app

    def timeout(*a, **kw):
        raise llm_svc.LLMTimeout("HTTPConnectionPool: Read timed out. (read timeout=20)")
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", timeout)

    app = create_app()
    client = app.test_client()
    # Use a word WordNet doesn't have so the chain falls through to LLM.
    r = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "zzznotaword"})
    assert r.status_code == 200
    payload = r.get_json()
    assert payload["ok"] is True
    data = payload["data"]
    assert data["entry"]["senses"] == []
    errs = data["provider_errors"]
    assert any(e["provider"] == "llm" and "read timeout=20" in e["error"] for e in errs), \
        f"expected LLM timeout in provider_errors, got {errs}"
    # Suggestions should still come back for the user to recover from.
    assert "suggestions" in data


def test_force_provider_endpoint_surfaces_llm_timeout(fresh, monkeypatch):
    """End-to-end: forcing LLM explicitly must also surface the error."""
    from backend.services import llm as llm_svc
    from backend.app import create_app

    def timeout(*a, **kw):
        raise llm_svc.LLMTimeout("HTTPConnectionPool: Read timed out. (read timeout=20)")
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", timeout)

    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "typically",
                                                     "provider": "llm"})
    assert r.status_code == 200
    payload = r.get_json()
    assert payload["ok"] is True
    data = payload["data"]
    assert data["entry"]["senses"] == []
    errs = data["provider_errors"]
    assert len(errs) == 1
    assert errs[0]["provider"] == "llm"
    assert "read timeout=20" in errs[0]["error"]