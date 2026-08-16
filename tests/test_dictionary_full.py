"""Tests for the dictionary providers and chain executor internals.

test_dictionary.py covers the chain happy paths and provider-error
recording. This file pins base.WordEntry / Sense / Definition, the
provider lookup normalizers (WordNet empty on non-en, llm provider's
sense mapping), and registry edge cases.
"""

from __future__ import annotations

import json as _json
from unittest import mock

import pytest


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    from backend import db
    db.init_schema()
    from backend.services.dictionaries import registry
    registry.bootstrap()
    return tmp_path


# --- base dataclasses ---------------------------------------------------


def test_word_entry_empty_factory():
    from backend.services.dictionaries.base import WordEntry
    e = WordEntry.empty("dog", "en")
    assert e.word == "dog"
    assert e.language == "en"
    assert e.is_empty
    assert e.source == ""


def test_word_entry_is_empty_when_no_senses():
    from backend.services.dictionaries.base import WordEntry, Sense
    e = WordEntry(word="x", language="en", senses=[], source="llm")
    assert e.is_empty


def test_word_entry_to_dict_shape():
    from backend.services.dictionaries.base import Definition, Sense, WordEntry
    e = WordEntry(
        word="dog", language="en", source="llm",
        senses=[Sense(pos="noun",
                       definitions=[Definition(glossary="animal", example="the dog")],
                       explanations={"primary": "an animal"})],
    )
    d = e.to_dict()
    assert d["word"] == "dog"
    assert d["language"] == "en"
    assert d["source"] == "llm"
    assert d["senses"][0]["pos"] == "noun"
    assert d["senses"][0]["definitions"][0]["glossary"] == "animal"


def test_sense_to_dict_shape():
    from backend.services.dictionaries.base import Definition, Sense
    s = Sense(pos="verb",
               definitions=[Definition(glossary="to run", example="run fast")])
    d = s.to_dict()
    assert d["pos"] == "verb"
    assert d["definitions"][0]["glossary"] == "to run"


# --- WordNet provider internals -----------------------------------------


def test_wordnet_lookup_ignores_explanation_kwargs(fresh):
    """The chain executor passes `explanation_primary`/`explanation_secondary`
    to every provider; WordNet must accept them without crashing."""
    from backend.services.dictionaries import wordnet
    entry = wordnet.lookup("dog", "en", explanation_primary="en",
                            explanation_secondary="zh")
    assert not entry.is_empty


def test_wordnet_lookup_empty_returns_proper_word_lang(fresh):
    from backend.services.dictionaries import wordnet
    entry = wordnet.lookup("zzznotaword", "en")
    assert entry.is_empty
    assert entry.word == "zzznotaword"
    assert entry.language == "en"


def test_wordnet_lookup_non_english_short_circuits(fresh):
    """WordNet must short-circuit on non-English even if a word happens
    to exist in the corpus, because lemmas vary per language."""
    from backend.services.dictionaries import wordnet
    entry = wordnet.lookup("dog", "es")
    assert entry.is_empty
    assert entry.language == "es"


def test_wordnet_supports_only_en():
    from backend.services.dictionaries import wordnet
    assert wordnet.supports("en") is True
    assert wordnet.supports("es") is False
    assert wordnet.supports("ja") is False


# --- LLM provider internals ---------------------------------------------


def test_llm_provider_normalizes_response(fresh, monkeypatch):
    from backend.services.dictionaries import llm as llm_provider
    from backend.services import llm as llm_svc

    payload = {"senses": [
        {"pos": "noun",
         "definitions": [{"glossary": "A house.", "example": "Mi casa."}],
         "explanations": {"primary": "House.", "secondary": "房子。"}},
    ]}

    def fake(*, lang, word, explanation_primary=None, explanation_secondary=None):
        return payload
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", fake)

    entry = llm_provider.lookup("casa", "es", explanation_primary="en",
                                  explanation_secondary="zh")
    assert not entry.is_empty
    assert entry.source == "llm"
    assert entry.senses[0].pos == "noun"
    assert entry.senses[0].definitions[0].glossary == "A house."
    assert entry.senses[0].explanations["primary"] == "House."


def test_llm_provider_skips_definitions_with_empty_glossary(fresh, monkeypatch):
    from backend.services.dictionaries import llm as llm_provider
    from backend.services import llm as llm_svc

    payload = {"senses": [
        {"pos": "noun", "definitions": [
            {"glossary": "  "},                # empty -> skip
            {"glossary": "real gloss"},          # keep
        ]},
    ]}

    def fake(*, lang, word, **kw):
        return payload
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", fake)

    entry = llm_provider.lookup("x", "en")
    assert len(entry.senses) == 1
    assert len(entry.senses[0].definitions) == 1


def test_llm_provider_skips_sense_with_no_definitions(fresh, monkeypatch):
    from backend.services.dictionaries import llm as llm_provider
    from backend.services import llm as llm_svc

    payload = {"senses": [
        {"pos": "noun", "definitions": []},   # all filtered -> skip sense
        {"pos": "verb", "definitions": [{"glossary": "to act"}]},
    ]}

    def fake(*, lang, word, **kw):
        return payload
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", fake)

    entry = llm_provider.lookup("x", "en")
    assert len(entry.senses) == 1
    assert entry.senses[0].pos == "verb"


def test_llm_provider_defaults_null_pos(fresh, monkeypatch):
    """A sense with a null pos (now allowed by the schema) is kept and
    its pos defaults to the placeholder instead of failing the lookup."""
    from backend.services.dictionaries import llm as llm_provider
    from backend.services import llm as llm_svc

    payload = {"senses": [
        {"pos": None, "definitions": [{"glossary": "a gloss"}]},
    ]}

    def fake(*, lang, word, **kw):
        return payload
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", fake)

    entry = llm_provider.lookup("x", "en")
    assert len(entry.senses) == 1
    assert entry.senses[0].pos == "—"


def test_llm_provider_supports_all_languages():
    from backend.services.dictionaries import llm
    assert llm.supports("en") is True
    assert llm.supports("es") is True
    assert llm.supports("xx") is True


def test_llm_provider_propagates_llm_errors(fresh, monkeypatch):
    """The LLM provider re-raises so the chain records the error."""
    from backend.services.dictionaries import llm as llm_provider
    from backend.services import llm as llm_svc

    def boom(*, lang, word, **kw):
        raise llm_svc.LLMError("nope")
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", boom)

    with pytest.raises(llm_svc.LLMError):
        llm_provider.lookup("x", "en")


def test_llm_provider_propagates_schema_errors_with_diagnostic_message(fresh, monkeypatch):
    """The LLM provider re-raises LLMSchemaError. The error message
    must include the schema name and a sample of the last response so
    the user can debug a non-OpenAI proxy that returns malformed
    output."""
    from backend.services.dictionaries import llm as llm_provider
    from backend.services import llm as llm_svc

    def boom(*, lang, word, **kw):
        raise llm_svc.LLMSchemaError(
            "LLM did not produce valid JSON for schema 'dict_word' after 2 "
            "attempts. Last error: Additional properties are not allowed "
            "('language' was unexpected). Last response (truncated to 400 "
            "chars): '{\"language\": \"en\", \"senses\": [{}]}'"
        )
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", boom)

    with pytest.raises(llm_svc.LLMSchemaError) as exc:
        llm_provider.lookup("press", "en")
    msg = str(exc.value)
    # The schema name is included so the user knows which call failed.
    assert "dict_word" in msg
    # The specific validation error is included.
    assert "Additional properties are not allowed" in msg
    # A sample of the bad response is included.
    assert "language" in msg


def test_chain_continues_when_llm_provider_schema_errors(fresh, monkeypatch):
    """If the LLM provider raises a schema error, the chain should
    record it in `errors` and continue. A non-OpenAI proxy that
    misbehaves on the LLM step must not break the wordnet fallback."""
    from backend.services.dictionaries import registry
    from backend.services import llm as llm_svc

    def boom(*, lang, word, **kw):
        raise llm_svc.LLMSchemaError("bad JSON")
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", boom)

    # Single-element chain, only the LLM provider. It fails, so the
    # result is empty with one error recorded.
    result = registry.lookup_via_chain(
        word="press", lang="en",
        chain=[{"name": "llm", "enabled": True}],
    )
    assert result.entry.is_empty
    assert len(result.errors) == 1
    assert result.errors[0]["provider"] == "llm"
    assert "bad JSON" in result.errors[0]["error"]


# --- registry helpers --------------------------------------------------


def test_supports_provider_default_true_for_unknown():
    """A provider without an explicit supports() fn defaults to True."""
    from backend.services.dictionaries import registry
    # monkeypatch: register a provider without a `supports` callback.
    registry.register("noop", lambda *a, **kw: None)
    try:
        assert registry.supports_provider("noop", "en") is True
        assert registry.supports_provider("noop", "xx") is True
    finally:
        # Cleanup so we don't pollute subsequent tests' bootstrap.
        registry.PROVIDERS.pop("noop", None)


def test_supports_provider_uses_registered_fn():
    from backend.services.dictionaries import registry

    def only_en(lang):
        return lang == "en"
    registry.register("only_en", lambda *a, **kw: None, supports=only_en)
    try:
        assert registry.supports_provider("only_en", "en") is True
        assert registry.supports_provider("only_en", "es") is False
    finally:
        registry.PROVIDERS.pop("only_en", None)
        registry.PROVIDER_SUPPORTS.pop("only_en", None)


def test_supports_provider_returns_true_on_fn_exception():
    """A buggy supports() fn must not crash; default to True."""
    from backend.services.dictionaries import registry

    def broken(lang):
        raise RuntimeError("oops")
    registry.register("broken", lambda *a, **kw: None, supports=broken)
    try:
        assert registry.supports_provider("broken", "en") is True
    finally:
        registry.PROVIDERS.pop("broken", None)
        registry.PROVIDER_SUPPORTS.pop("broken", None)


def test_provider_info_returns_metadata():
    from backend.services.dictionaries import registry
    info = registry.provider_info("wordnet")
    assert info["name"] == "wordnet"
    assert info["kind"] == "builtin"


def test_provider_info_returns_none_for_unknown():
    from backend.services.dictionaries import registry
    assert registry.provider_info("made_up") is None


def test_available_providers_detailed_includes_known():
    from backend.services.dictionaries import registry
    items = registry.available_providers_detailed()
    names = [i["name"] for i in items]
    assert "wordnet" in names
    assert "llm" in names


def test_lookup_via_chain_skips_disabled_entries(fresh, monkeypatch):
    """An `enabled=False` entry must be silently skipped (no error)."""
    from backend.services.dictionaries import registry

    calls = []

    def tracker(*args, **kw):
        calls.append(args)
        from backend.services.dictionaries.base import WordEntry
        return WordEntry.empty(args[0] if args else "", "en")

    registry.register("tracker", tracker)
    try:
        chain = [{"name": "tracker", "enabled": False}]
        registry.lookup_via_chain(word="dog", lang="en", chain=chain)
        assert calls == []
    finally:
        registry.PROVIDERS.pop("tracker", None)


def test_lookup_via_chain_skips_non_dict_entries(fresh):
    from backend.services.dictionaries import registry
    chain = [None, "string", 42, {"name": "wordnet", "enabled": True}]
    # Must not raise.
    result = registry.lookup_via_chain(word="dog", lang="en", chain=chain)
    assert result.entry is not None


def test_lookup_via_chain_handles_invalid_lang(fresh):
    from backend.services.dictionaries import registry
    result = registry.lookup_via_chain(word="dog", lang="INVALID!", chain=[])
    assert result.entry.is_empty


def test_lookup_via_chain_handles_non_list_chain(fresh):
    """If the chain is not a list (e.g. legacy data), return empty."""
    from backend.services.dictionaries import registry
    result = registry.lookup_via_chain(word="dog", lang="en", chain="not-a-list")
    assert result.entry.is_empty


def test_lookup_via_chain_skips_unknown_provider(fresh):
    from backend.services.dictionaries import registry
    chain = [{"name": "made_up", "enabled": True},
             {"name": "wordnet", "enabled": True}]
    result = registry.lookup_via_chain(word="dog", lang="en", chain=chain)
    # Falls through to wordnet.
    assert result.entry.source == "wordnet"


def test_lookup_via_chain_continues_after_provider_exception(fresh, monkeypatch):
    from backend.services.dictionaries import registry
    from backend.services import llm as llm_svc

    def boom(*, lang, word, **kw):
        raise llm_svc.LLMTimeout("network")
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", boom)

    chain = [{"name": "llm", "enabled": True},
             {"name": "wordnet", "enabled": True}]
    result = registry.lookup_via_chain(word="dog", lang="en", chain=chain)
    assert result.entry.source == "wordnet"
    assert any(e["provider"] == "llm" for e in result.errors)


def test_lookup_with_provider_unknown_returns_empty(fresh):
    from backend.services.dictionaries import registry
    result = registry.lookup_with_provider(word="x", lang="en",
                                            provider_name="nope")
    assert result.entry.is_empty
    assert result.errors == []


def test_bootstrap_is_idempotent(fresh):
    """Calling bootstrap() repeatedly must not double-register providers."""
    from backend.services.dictionaries import registry
    n_before = len(registry.available_providers())
    registry.bootstrap()
    registry.bootstrap()
    n_after = len(registry.available_providers())
    assert n_before == n_after
