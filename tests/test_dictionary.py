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
    entry = registry.lookup_via_chain(word="dog", lang="en", chain=chain)
    assert entry.source == "wordnet"


def test_chain_skips_disabled(fresh, monkeypatch):
    from backend.services.dictionaries import registry
    from backend.services import llm as llm_svc

    def fake_llm(*, lang, word, **kwargs):
        return {"senses": [{"pos": "noun", "definitions": [{"glossary": "fake"}]}]}
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", fake_llm)

    chain = [{"name": "wordnet", "enabled": False},
             {"name": "llm", "enabled": True}]
    entry = registry.lookup_via_chain(word="dog", lang="en", chain=chain)
    assert entry.source == "llm"


def test_chain_handles_provider_error(fresh, monkeypatch):
    from backend.services.dictionaries import registry
    from backend.services import llm as llm_svc

    def bad(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", bad)

    chain = [{"name": "llm", "enabled": True}]
    entry = registry.lookup_via_chain(word="dog", lang="en", chain=chain)
    assert entry.is_empty


def test_chain_empty_returns_empty(fresh):
    from backend.services.dictionaries import registry

    entry = registry.lookup_via_chain(word="dog", lang="en", chain=[])
    assert entry.is_empty


def test_chain_invalid_lang_returns_empty(fresh):
    from backend.services.dictionaries import registry

    entry = registry.lookup_via_chain(word="dog", lang="invalid!", chain=[])
    assert entry.is_empty


def test_force_provider(fresh, monkeypatch):
    from backend.services.dictionaries import registry
    from backend.services import llm as llm_svc

    def fake_llm(*, lang, word, **kwargs):
        return {"senses": [{"pos": "noun", "definitions": [{"glossary": "forced"}]}]}
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", fake_llm)

    entry = registry.lookup_with_provider(word="dog", lang="es", provider_name="llm")
    assert entry.source == "llm"


def test_force_unknown_provider_returns_empty(fresh):
    from backend.services.dictionaries import registry

    entry = registry.lookup_with_provider(word="dog", lang="es", provider_name="nonexistent")
    assert entry.is_empty