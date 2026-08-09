"""Tests for the dictionary suggestion helpers and /suggest endpoint."""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(clean_state):
    from backend.services.dictionaries import registry
    registry.bootstrap()
    return clean_state


# --- prefix suggestions ---------------------------------------------------

def test_prefix_returns_words_starting_with_query(fresh):
    from backend.services.dictionaries import suggest

    out = suggest.prefix("en", 1, "ap", limit=10)
    assert out, "expected at least one word starting with 'ap'"
    assert all(w.startswith("ap") for w in out)
    # Sorted shortest-first; "ap" itself, if present, must be first.
    assert out == sorted(out, key=lambda w: (len(w), w))


def test_prefix_case_insensitive(fresh):
    from backend.services.dictionaries import suggest

    out_lower = suggest.prefix("en", 1, "ap", limit=5)
    out_upper = suggest.prefix("en", 1, "AP", limit=5)
    assert out_lower == out_upper


def test_prefix_empty_query_returns_empty(fresh):
    from backend.services.dictionaries import suggest

    assert suggest.prefix("en", 1, "", limit=8) == []
    assert suggest.prefix("en", 1, "   ", limit=8) == []


def test_prefix_respects_limit(fresh):
    from backend.services.dictionaries import suggest

    out = suggest.prefix("en", 1, "a", limit=3)
    assert len(out) <= 3


def test_prefix_uses_user_vocab_when_no_wordnet(fresh):
    """For non-English langs the user can still get suggestions from their own vocab."""
    from backend.db import transaction

    with transaction() as conn:
        conn.execute(
            "INSERT INTO vocab_items (user_id, language, word, source, glossary) "
            "VALUES (?, ?, ?, 'llm', 'g')",
            (1, "es", "manzana"),
        )
        conn.execute(
            "INSERT INTO vocab_items (user_id, language, word, source, glossary) "
            "VALUES (?, ?, ?, 'llm', 'g')",
            (1, "es", "mango"),
        )
    from backend.services.dictionaries import suggest

    out = suggest.prefix("es", 1, "man", limit=8)
    assert "manzana" in out
    # 'mango' also starts with 'man', so both should be in the list —
    # this just confirms the helper returns the user's words, not just any.
    assert "mango" in out


def test_prefix_excludes_words_not_starting_with_query(fresh):
    from backend.db import transaction
    from backend.services.dictionaries import suggest

    with transaction() as conn:
        conn.execute(
            "INSERT INTO vocab_items (user_id, language, word, source, glossary) "
            "VALUES (?, ?, ?, 'llm', 'g')",
            (1, "fr", "maison"),
        )
        conn.execute(
            "INSERT INTO vocab_items (user_id, language, word, source, glossary) "
            "VALUES (?, ?, ?, 'llm', 'g')",
            (1, "fr", "pomme"),
        )
    out = suggest.prefix("fr", 1, "pomm", limit=8)
    assert out == ["pomme"]


def test_prefix_no_candidates_returns_empty(fresh):
    from backend.services.dictionaries import suggest

    assert suggest.prefix("es", 1, "zzz", limit=8) == []


# --- fuzzy suggestions ----------------------------------------------------

def test_fuzzy_returns_close_match(fresh):
    from backend.services.dictionaries import suggest

    out = suggest.fuzzy("en", 1, "dogg", limit=5)
    assert "dog" in out


def test_fuzzy_short_query_returns_empty(fresh):
    """Single-character / 2-char queries return [] to avoid noise."""
    from backend.services.dictionaries import suggest

    assert suggest.fuzzy("en", 1, "a", limit=5) == []
    assert suggest.fuzzy("en", 1, "do", limit=5) == []


def test_fuzzy_ranked_by_distance(fresh):
    from backend.services.dictionaries import suggest

    out = suggest.fuzzy("en", 1, "aple", limit=8)
    # 'apple' is distance 2 from 'aple' (insertion + substitution) — should
    # appear in suggestions. The 'aple' string itself must not.
    assert "aple" not in out
    assert "apple" in out
    # 'ape' is distance 1 — should rank before 'apple' if present.
    if "ape" in out and "apple" in out:
        assert out.index("ape") < out.index("apple")


def test_fuzzy_respects_first_letter_and_length_window(fresh):
    from backend.services.dictionaries import suggest

    out = suggest.fuzzy("en", 1, "dogg", limit=10)
    for w in out:
        assert w.startswith("d")
        assert 2 <= len(w) <= 6  # query len 4 +/- 2


def test_fuzzy_no_candidates_returns_empty(fresh):
    from backend.services.dictionaries import suggest

    assert suggest.fuzzy("es", 1, "zzzzz", limit=5) == []


# --- HTTP endpoint --------------------------------------------------------

def test_suggest_endpoint_returns_prefix_matches(fresh):
    from backend.app import create_app

    app = create_app()
    client = app.test_client()
    r = client.get("/api/dictionary/suggest?lang=en&q=ap&limit=5")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["data"]["query"] == "ap"
    assert data["data"]["lang"] == "en"
    assert all(w.startswith("ap") for w in data["data"]["suggestions"])
    assert len(data["data"]["suggestions"]) <= 5


def test_suggest_endpoint_invalid_lang(fresh):
    from backend.app import create_app

    app = create_app()
    client = app.test_client()
    r = client.get("/api/dictionary/suggest?lang=INVALID&q=ap")
    assert r.status_code == 400
    body = r.get_json()
    assert body["ok"] is False
    assert body["code"] == "invalid_lang"


def test_suggest_endpoint_invalid_limit(fresh):
    from backend.app import create_app

    app = create_app()
    client = app.test_client()
    r = client.get("/api/dictionary/suggest?lang=en&q=ap&limit=abc")
    assert r.status_code == 400
    body = r.get_json()
    assert body["code"] == "invalid_limit"


def test_suggest_endpoint_clamps_huge_limit(fresh):
    from backend.app import create_app

    app = create_app()
    client = app.test_client()
    r = client.get("/api/dictionary/suggest?lang=en&q=a&limit=999")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["data"]["suggestions"]) <= 25


def test_suggest_endpoint_empty_query(fresh):
    from backend.app import create_app

    app = create_app()
    client = app.test_client()
    r = client.get("/api/dictionary/suggest?lang=en&q=")
    assert r.status_code == 200
    data = r.get_json()
    assert data["data"]["suggestions"] == []


def test_lookup_includes_suggestions_on_no_entry(fresh):
    """When the chain returns empty, /lookup should include 'suggestions'."""
    from backend.app import create_app

    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "dogg"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["data"]["entry"]["senses"] == []
    assert "suggestions" in data["data"]
    assert "dog" in data["data"]["suggestions"]


def test_lookup_omits_suggestions_on_hit(fresh):
    """A successful lookup should not carry a 'suggestions' field."""
    from backend.app import create_app

    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "dog"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["data"]["entry"]["senses"], "expected dog to be found"
    assert "suggestions" not in data["data"]


# --- spaces-in-word normalization ----------------------------------------

def test_lookup_with_spaces_normalizes_to_underscore(fresh):
    """Typing 'snap at' must hit the same entry as 'snap_at'."""
    from backend.app import create_app

    app = create_app()
    client = app.test_client()
    r1 = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "snap at"})
    r2 = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "snap_at"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    d1 = r1.get_json()["data"]
    d2 = r2.get_json()["data"]
    assert len(d1["entry"]["senses"]) == len(d2["entry"]["senses"])
    assert d1["entry"]["senses"] == d2["entry"]["senses"]


def test_lookup_collapses_multiple_spaces(fresh):
    from backend.app import create_app

    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "  snap   at  "})
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["entry"]["senses"]


def test_suggest_prefix_normalizes_spaces(fresh):
    from backend.app import create_app

    app = create_app()
    client = app.test_client()
    r = client.get("/api/dictionary/suggest?lang=en&q=snap+at&limit=5")
    assert r.status_code == 200
    assert r.get_json()["data"]["suggestions"] == ["snap_at"]


def test_suggest_prefix_preserves_hyphens(fresh):
    """Hyphens are part of single-token lemmas and must NOT be replaced."""
    from backend.util import normalize_word
    assert normalize_word("snap-at") == "snap-at"
    assert normalize_word("don't snap") == "don't_snap"
    assert normalize_word("hello") == "hello"
    assert normalize_word("  snap   at  ") == "snap_at"
    assert normalize_word("") == ""


# --- /providers endpoint --------------------------------------------------

def test_providers_endpoint_returns_metadata(fresh):
    from backend.app import create_app

    app = create_app()
    client = app.test_client()
    r = client.get("/api/dictionary/providers")
    assert r.status_code == 200
    data = r.get_json()["data"]
    names = [p["name"] for p in data["providers"]]
    assert "wordnet" in names
    assert "llm" in names
    for p in data["providers"]:
        assert "display_name" in p
        assert "description" in p
        assert "kind" in p


def test_providers_endpoint_marks_unsupported_for_lang(fresh):
    """For a non-English language, WordNet should be marked supports=False."""
    from backend.app import create_app

    app = create_app()
    client = app.test_client()
    r = client.get("/api/dictionary/providers?lang=es")
    assert r.status_code == 200
    data = r.get_json()["data"]["providers"]
    by_name = {p["name"]: p for p in data}
    assert by_name["wordnet"]["supports"] is False
    assert by_name["llm"]["supports"] is True


def test_providers_endpoint_invalid_lang(fresh):
    from backend.app import create_app

    app = create_app()
    client = app.test_client()
    r = client.get("/api/dictionary/providers?lang=ZZ")
    assert r.status_code == 400


# --- /lookup with provider override ---------------------------------------

def test_lookup_provider_override_forces_provider(fresh, monkeypatch):
    """A `provider` field on /lookup must bypass the chain and use that
    provider, even if the chain would have picked a different one."""
    from backend.app import create_app

    def should_not_call(*a, **kw):
        raise AssertionError("LLM should not be called when wordnet is forced")
    from backend.services import llm as llm_svc
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", should_not_call)

    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "dog", "provider": "wordnet"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"]["source"] == "wordnet"
    assert body["data"]["provider"] == "wordnet"


def test_lookup_provider_override_unsupported_lang(fresh):
    """Forcing WordNet for Spanish must return 400 provider_unsupported_lang."""
    from backend.app import create_app

    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={"lang": "es", "word": "casa", "provider": "wordnet"})
    assert r.status_code == 400
    assert r.get_json()["code"] == "provider_unsupported_lang"


def test_lookup_provider_override_unknown_provider(fresh):
    from backend.app import create_app

    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "dog", "provider": "nope"})
    assert r.status_code == 400
    assert r.get_json()["code"] == "unknown_provider"


def test_lookup_provider_override_empty_string_falls_back_to_chain(fresh, monkeypatch):
    """An empty-string provider override should behave as 'no override'."""
    from backend.app import create_app

    def fake_llm(*, lang, word, **kwargs):
        return {"senses": [{"pos": "noun", "definitions": [{"glossary": "x"}]}]}
    from backend.services import llm as llm_svc
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", fake_llm)

    app = create_app()
    client = app.test_client()
    # Default chain for en is [wordnet, llm]; 'asdfqwer' has no wordnet entry
    # so it falls through to llm.
    r = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "asdfqwer", "provider": ""})
    assert r.status_code == 200
    assert r.get_json()["data"]["source"] == "llm"


# --- providers llm_configured flag ---------------------------------------

def test_providers_reports_llm_configured_when_key_present(fresh, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/dictionary/providers")
    body = r.get_json()
    assert body["data"]["llm_configured"] is True
    assert body["data"]["llm_provider_kind"] == "openai-compat"
    llm = next(p for p in body["data"]["providers"] if p["name"] == "llm")
    assert llm["configured"] is True


def test_providers_reports_llm_unconfigured_when_key_missing(fresh, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    # Re-import config so the new env is read.
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/dictionary/providers")
    body = r.get_json()
    assert body["data"]["llm_configured"] is False
    llm = next(p for p in body["data"]["providers"] if p["name"] == "llm")
    assert llm["configured"] is False

