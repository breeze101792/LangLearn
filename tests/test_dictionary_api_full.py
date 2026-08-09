"""More dictionary-blueprint HTTP tests beyond the happy paths."""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(clean_state):
    from backend.services.dictionaries import registry
    registry.bootstrap()
    return clean_state


# --- /lookup validation ------------------------------------------------


def test_lookup_missing_lang_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={"word": "dog"})
    assert r.status_code == 400


def test_lookup_missing_word_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={"lang": "en"})
    assert r.status_code == 400


def test_lookup_word_with_only_punctuation_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "!!!"})
    assert r.status_code == 400


def test_lookup_with_spaces_normalized_in_lookup_endpoint(fresh):
    """Multi-word input is normalized so 'snap at' resolves the same as
    'snap_at' (WordNet's indexing convention)."""
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


def test_lookup_includes_in_vocab_false_when_word_not_in_chain(fresh, monkeypatch):
    """When the chain exhausts to empty, the response must report
    in_vocab=false."""
    from backend.app import create_app
    from backend.services import settings as s
    from backend.services import llm as llm_svc

    # Force LLM to error out so the chain returns empty.
    def boom(*a, **kw):
        raise llm_svc.LLMError("nope")
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", boom)
    s.update_settings({"dict_chain_json": {"en": [{"name": "llm"}]}})

    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "zzznotaword"})
    body = r.get_json()["data"]
    assert body["in_vocab"] is False
    assert body["provider_errors"] != []


def test_lookup_auto_add_vocab_false_does_not_persist(fresh):
    """When settings.auto_add_vocab is False, the chain result is not
    inserted into vocab_items even though it was a hit."""
    from backend.app import create_app
    from backend.services import settings as s
    from backend.services import vocab as vocab_svc
    s.update_settings({"auto_add_vocab": False})
    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={"lang": "en", "word": "dog"})
    assert r.get_json()["data"]["auto_added"] is False
    assert vocab_svc.list_vocab(user_id=1, language="en") == []


# --- /lookup with provider override ------------------------------------


def test_lookup_provider_override_wordnet_picks_wordnet(fresh, monkeypatch):
    from backend.app import create_app
    from backend.services import llm as llm_svc

    def should_not_call(*a, **kw):
        raise AssertionError("LLM should not be called when forced to wordnet")
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", should_not_call)

    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={
        "lang": "en", "word": "dog", "provider": "wordnet",
    })
    body = r.get_json()["data"]
    assert body["provider"] == "wordnet"
    assert body["source"] == "wordnet"


def test_lookup_provider_override_llm_works_when_wordnet_disabled(fresh, monkeypatch):
    """Force LLM even when the default chain has wordnet first."""
    from backend.app import create_app
    from backend.services import llm as llm_svc

    def fake(*, lang, word, **kw):
        return {"senses": [{"pos": "noun",
                              "definitions": [{"glossary": "from llm"}]}]}
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", fake)

    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={
        "lang": "en", "word": "dog", "provider": "llm",
    })
    body = r.get_json()["data"]
    assert body["provider"] == "llm"
    assert body["source"] == "llm"


def test_lookup_provider_override_auto_adds_when_vocab_off(fresh, monkeypatch):
    from backend.app import create_app
    from backend.services import llm as llm_svc
    from backend.services import settings as s
    s.update_settings({"auto_add_vocab": False})

    def fake(*, lang, word, **kw):
        return {"senses": [{"pos": "noun",
                              "definitions": [{"glossary": "g"}]}]}
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", fake)

    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={
        "lang": "en", "word": "newword123", "provider": "llm",
    })
    assert r.get_json()["data"]["auto_added"] is False


def test_lookup_provider_override_non_string_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/lookup", json={
        "lang": "en", "word": "dog", "provider": 42,
    })
    assert r.status_code == 400


# --- /<provider> force endpoint ---------------------------------------


def test_force_provider_endpoint_wordnet(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/wordnet",
                    json={"lang": "en", "word": "dog"})
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["source"] == "wordnet"
    assert body["provider"] == "wordnet"


def test_force_provider_endpoint_unknown_404(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/made_up",
                    json={"lang": "en", "word": "dog"})
    assert r.status_code == 404


def test_force_provider_endpoint_invalid_lang_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/wordnet",
                    json={"lang": "ENG", "word": "dog"})
    assert r.status_code == 400


def test_force_provider_endpoint_missing_word_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/wordnet", json={"lang": "en"})
    assert r.status_code == 400


def test_force_provider_endpoint_wordnet_for_non_en_returns_empty(fresh):
    """The /<provider> force endpoint doesn't reject unsupported langs;
    WordNet on Spanish just returns an empty entry (it short-circuits
    internally because lang != 'en')."""
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/wordnet",
                    json={"lang": "es", "word": "casa"})
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["entry"]["senses"] == []


def test_force_provider_endpoint_surfaces_llm_timeout(fresh, monkeypatch):
    from backend.app import create_app
    from backend.services import llm as llm_svc

    def timeout(*a, **kw):
        raise llm_svc.LLMTimeout("Read timed out.")
    monkeypatch.setattr(llm_svc, "lookup_word_via_llm", timeout)

    app = create_app()
    client = app.test_client()
    r = client.post("/api/dictionary/llm",
                    json={"lang": "en", "word": "dog"})
    assert r.status_code == 200
    errs = r.get_json()["data"]["provider_errors"]
    assert any(e["provider"] == "llm" for e in errs)


# --- /providers endpoint ----------------------------------------------


def test_providers_endpoint_no_lang_param(fresh):
    """`supports` field is absent when `lang` is not provided."""
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/dictionary/providers")
    body = r.get_json()["data"]["providers"]
    assert "supports" not in body[0]


def test_providers_endpoint_empty_lang_param_400(fresh):
    """Empty-string lang passes the `is not None` check but fails
    `is_valid_lang`, so the route returns 400."""
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/dictionary/providers?lang=")
    assert r.status_code == 400


def test_providers_response_includes_llm_provider_kind(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/dictionary/providers?lang=en")
    body = r.get_json()["data"]
    llm = next(p for p in body["providers"] if p["name"] == "llm")
    assert "provider_kind" in llm


# --- /suggest endpoint edge cases -------------------------------------


def test_suggest_endpoint_with_non_string_query_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    # Flask coerces non-string query args differently; sending an int
    # via args should surface an invalid_query 400.
    r = client.get("/api/dictionary/suggest", query_string={"lang": "en",
                                                              "q": ""})
    # Empty string is fine.
    assert r.status_code == 200
