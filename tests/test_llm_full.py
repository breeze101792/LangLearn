"""More LLM-service tests covering helpers beyond lookup_word_via_llm.

test_llm.py covers the lookup path. This file pins generate_seed_payload,
fill_structure_via_llm, fill_phrase_via_llm, and the low-level _post_json
HTTP plumbing (timeouts, HTTP errors, non-JSON, malformed content).
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
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    from backend import db
    db.init_schema()
    return tmp_path


def _mock_openai_response(content: str, status_code: int = 200):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return resp


# --- generate_seed_payload ----------------------------------------------


def test_generate_seed_payload_happy_path(fresh, monkeypatch):
    from backend.services import llm

    # New behavior: structures and phrases are generated in separate
    # LLM calls, batched internally. The mock returns one batch payload
    # per call.
    struct_payload = {
        "structures": [{"pattern": "S V O",
                         "explanation": "Target-language usage note.",
                         "explanation_primary": "Basic",
                         "example_sentence": "She reads."}],
    }
    phrase_payload = {
        "phrases": [{"phrase": "Hi", "explanation": "Target-language usage note.",
                      "explanation_primary": "Hello",
                      "example_sentence": "Greeting word."}],
    }
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        # Call 1: structures. Call 2: phrases.
        if calls["n"] == 1:
            return _mock_openai_response(_json.dumps(struct_payload))
        return _mock_openai_response(_json.dumps(phrase_payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.generate_seed_payload(lang="es", n_structures=1, n_phrases=1)
    assert calls["n"] == 2
    assert out["structures"][0]["pattern"] == "S V O"
    assert out["phrases"][0]["phrase"] == "Hi"


def test_generate_seed_payload_retries_on_invalid_json(fresh, monkeypatch):
    from backend.services import llm

    struct_good = {"structures": [{"pattern": "S V O", "explanation_primary": "ok",
                                    "explanation": "Target-language usage note.",
                                    "example_sentence": "She reads."}]}
    phrase_good = {"phrases": [{"phrase": "Hi", "explanation_primary": "ok",
                                 "explanation": "Target-language usage note.",
                                 "example_sentence": "Greeting word."}]}
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        # First call (structures) returns invalid JSON once, then good.
        # Second call (phrases) returns good.
        if calls["n"] == 1:
            return _mock_openai_response("not json")
        if calls["n"] == 2:
            return _mock_openai_response(_json.dumps(struct_good))
        return _mock_openai_response(_json.dumps(phrase_good))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.generate_seed_payload(lang="es", n_structures=1, n_phrases=1)
    assert calls["n"] == 3
    assert out["structures"][0]["pattern"] == "S V O"


def test_generate_seed_payload_raises_after_two_failures(fresh, monkeypatch):
    from backend.services import llm

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response("not json at all")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMSchemaError):
        llm.generate_seed_payload(lang="es", n_structures=1, n_phrases=1)


# --- fill_structure_via_llm ----------------------------------------------


def test_fill_structure_via_llm_happy_path(fresh, monkeypatch):
    from backend.services import llm

    payload = {"structures": [{"pattern": "S V O", "example_sentence": "She reads.",
                                "explanation": "Target-language usage note.",
                                "explanation_primary": "Basic",
                                "explanation_secondary": None}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.fill_structure_via_llm(lang="es", partial={"pattern": None})
    assert out["pattern"] == "S V O"


def test_fill_structure_via_llm_returns_nulls_for_missing(fresh, monkeypatch):
    """When the LLM returns all-null fields, the response still validates."""
    from backend.services import llm

    payload = {"structures": [{"pattern": None, "example_sentence": None,
                                "explanation": "Target-language usage note.",
                                "explanation_primary": None,
                                "explanation_secondary": None}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.fill_structure_via_llm(lang="es", partial={})
    assert out["pattern"] is None
    assert out["example_sentence"] is None


def test_fill_structure_via_llm_retries_on_schema_error(fresh, monkeypatch):
    from backend.services import llm

    bad = {"structures": [{"unknown_field": "x"}]}  # fails strict schema
    good = {"structures": [{"pattern": "S V O", "example_sentence": None,
                            "explanation": "Target-language usage note.",
                            "explanation_primary": "Basic",
                            "explanation_secondary": None}]}
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _mock_openai_response(_json.dumps(bad))
        return _mock_openai_response(_json.dumps(good))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.fill_structure_via_llm(lang="es", partial={"pattern": None})
    assert calls["n"] == 2
    assert out["pattern"] == "S V O"


def test_fill_structure_via_llm_raises_after_two_failures(fresh, monkeypatch):
    from backend.services import llm

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response("garbage")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMSchemaError):
        llm.fill_structure_via_llm(lang="es", partial={"pattern": None})


# --- fill_phrase_via_llm -------------------------------------------------


def test_fill_phrase_via_llm_happy_path(fresh, monkeypatch):
    from backend.services import llm

    payload = {"phrases": [{"example_sentence": "good night",
                              "explanation": "Target-language usage note.",
                              "explanation_primary": "Farewell.",
                              "explanation_secondary": None}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.fill_phrase_via_llm(lang="es", partial={})
    assert out["example_sentence"] == "good night"


def test_fill_phrase_via_llm_retries_on_invalid_json(fresh, monkeypatch):
    from backend.services import llm

    good = {"phrases": [{"example_sentence": "good night",
                          "explanation": "Target-language usage note.",
                          "explanation_primary": "Farewell.",
                          "explanation_secondary": None}]}
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _mock_openai_response("{not-json")
        return _mock_openai_response(_json.dumps(good))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.fill_phrase_via_llm(lang="es", partial={})
    assert calls["n"] == 2
    assert out["example_sentence"] == "good night"


def test_fill_phrase_via_llm_raises_after_two_failures(fresh, monkeypatch):
    from backend.services import llm

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response("not json")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMSchemaError):
        llm.fill_phrase_via_llm(lang="es", partial={})


# --- _post_json low-level plumbing ---------------------------------------


def test_post_json_timeout_maps_to_llm_timeout(fresh, monkeypatch):
    import requests
    from backend.services import llm

    def fake_post(url, json=None, headers=None, timeout=None):
        raise requests.Timeout("timed out")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMTimeout) as exc:
        llm.lookup_word_via_llm(lang="en", word="dog",
                                  explanation_primary="en", explanation_secondary=None)
    # Error message should mention the configurable timeout so the user
    # knows how to bump it.
    assert "LLM_TIMEOUT_SECONDS" in str(exc.value)
    assert "OPENAI_TIMEOUT_SECONDS" in str(exc.value)


def test_schema_error_includes_schema_name_and_last_response(fresh, monkeypatch):
    """When the LLM response fails validation, the LLMSchemaError
    message should include the schema name, the last validation error,
    and a sample of the last response — so the user can diagnose
    strict-schema mismatches against non-OpenAI proxies without
    enabling DEBUG logging."""
    from backend.services import llm

    def fake_post(url, json=None, headers=None, timeout=None):
        # Return a response that is valid JSON but fails the strict
        # schema: a structure item missing the required `explanation`
        # field. (Extra keys are now repaired by the seed normalizer,
        # so we use a missing-required-field failure to exercise the
        # error-reporting path.)
        return _mock_openai_response(
            _json.dumps({"structures": [{"pattern": "S V O",
                                         "example_sentence": "She reads.",
                                         "explanation_primary": "Basic"}],
                              "phrases": []})
        )

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMSchemaError) as exc:
        llm.generate_seed_payload(lang="es", n_structures=1, n_phrases=0,
                                  primary="en", secondary=None)
    msg = str(exc.value)
    # Schema name is included.
    assert "seed_structures" in msg
    # Last validation error is included.
    assert "required" in msg or "explanation" in msg
    # A sample of the last response is included.
    assert "She reads." in msg or "Basic" in msg


def test_schema_error_surfaces_json_parse_failure(fresh, monkeypatch):
    """When the LLM returns text that isn't valid JSON, the error
    should include the parser message and the raw response."""
    from backend.services import llm

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response("oops not json {")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMSchemaError) as exc:
        llm.fill_phrase_via_llm(lang="es", partial={})
    msg = str(exc.value)
    assert "not valid JSON" in msg
    # Sample includes the broken text.
    assert "oops not json" in msg


def test_post_json_400_status_raises_llm_error(fresh, monkeypatch):
    from backend.services import llm

    def fake_post(url, json=None, headers=None, timeout=None):
        resp = mock.Mock()
        resp.status_code = 401
        resp.text = "unauthorized"
        return resp

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMError, match="HTTP 401"):
        llm.lookup_word_via_llm(lang="en", word="dog",
                                  explanation_primary="en", explanation_secondary=None)


def test_post_json_non_json_body_raises_llm_error(fresh, monkeypatch):
    from backend.services import llm

    def fake_post(url, json=None, headers=None, timeout=None):
        resp = mock.Mock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        resp.text = "raw body"
        return resp

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMError, match="non-JSON"):
        llm.lookup_word_via_llm(lang="en", word="dog",
                                  explanation_primary="en", explanation_secondary=None)


def test_post_json_non_string_content_raises_llm_error(fresh, monkeypatch):
    """If the LLM returns a non-string content (e.g. null or list), the
    client must surface an error rather than passing it through to
    json.loads."""
    from backend.services import llm

    def fake_post(url, json=None, headers=None, timeout=None):
        resp = mock.Mock()
        resp.status_code = 200
        resp.json.return_value = {"choices": [{"message": {"content": None}}]}
        return resp

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMError, match="unexpected content type"):
        llm.lookup_word_via_llm(lang="en", word="dog",
                                  explanation_primary="en", explanation_secondary=None)


def test_post_json_uses_env_api_key_at_request_time(fresh, monkeypatch):
    """The OpenAICompatClient reads OPENAI_API_KEY per call, not at import.
    This test pins that contract — important for test fixtures that mutate
    env after import."""
    from backend.services import llm

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["headers"] = headers
        return _mock_openai_response(_json.dumps({"senses": [
            {"pos": "noun", "definitions": [{"glossary": "g"}]}
        ]}))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    monkeypatch.setenv("OPENAI_API_KEY", "fresh-key")
    llm.lookup_word_via_llm(lang="en", word="dog",
                             explanation_primary="en", explanation_secondary=None)
    assert captured["headers"]["Authorization"] == "Bearer fresh-key"


def test_openai_url_requires_key_for_openai_host(monkeypatch, tmp_path):
    """The OpenAI host URL explicitly requires an API key; self-hosted
    endpoints don't."""
    from backend.services import llm

    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    client = llm.OpenAICompatClient()
    with pytest.raises(llm.LLMError, match="API_KEY"):
        client.chat(system="s", user="u", schema={}, schema_name="x",
                    temperature=0.2)


def test_openai_url_optional_key_for_alternate_host(monkeypatch, tmp_path):
    """Self-hosted OpenAI-compatible endpoints (Ollama etc.) often skip
    auth; the client must not insist on a key."""
    from backend.services import llm

    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["headers"] = headers
        captured["url"] = url
        return _mock_openai_response(_json.dumps({"senses": [
            {"pos": "noun", "definitions": [{"glossary": "g"}]}
        ]}))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    client = llm.OpenAICompatClient()
    client.chat(system="s", user="u", schema={}, schema_name="x",
                temperature=0.2)
    assert "Authorization" not in captured["headers"]
    assert captured["url"].endswith("/chat/completions")


def test_openai_url_strips_trailing_slash(monkeypatch, tmp_path):
    """A base URL with a trailing '/' must not produce '//chat/completions'."""
    from backend.services import llm

    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1/")

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        return _mock_openai_response(_json.dumps({"senses": [
            {"pos": "noun", "definitions": [{"glossary": "g"}]}
        ]}))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    client = llm.OpenAICompatClient()
    client.chat(system="s", user="u", schema={}, schema_name="x",
                temperature=0.2)
    assert "//chat" not in captured["url"]


# --- schema validation specifics ----------------------------------------


def test_lookup_word_accepts_empty_senses(fresh, monkeypatch):
    """An empty `senses` array is a valid (empty) result, not a schema
    failure. The chain executor treats the resulting empty WordEntry as
    'no result' and falls through to the next provider."""
    from backend.services import llm

    bad = {"senses": []}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(bad))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    data = llm.lookup_word_via_llm(lang="en", word="dog",
                                   explanation_primary="en", explanation_secondary=None)
    assert data == {"senses": []}


def test_lookup_word_rejects_extra_properties(fresh, monkeypatch):
    """The raw schema sets additionalProperties=false, so a sense carrying
    an unknown key fails the validator when no normalization is applied."""
    from backend.services import llm

    bad = {"senses": [{"pos": "noun",
                        "definitions": [{"glossary": "g"}],
                        "extra": "x"}]}

    validator = llm.Draft202012Validator(llm.DICT_WORD_SCHEMA)
    with pytest.raises(Exception):
        validator.validate(bad)


def test_lookup_word_strips_unknown_properties_when_normalized(fresh, monkeypatch):
    """complete_json's `normalize` runs before strict validation and drops
    unknown keys, so a proxy that emits stray keys still passes."""
    from backend.services import llm

    out = llm._normalize_dict_word({"senses": [
        {"pos": "noun", "definitions": [{"glossary": "g"}], "extra": "x"},
    ]})
    assert out == {"senses": [
        {"pos": "noun", "definitions": [{"glossary": "g"}]},
    ]}

    bad = {"senses": [{"pos": "noun",
                        "definitions": [{"glossary": "g"}],
                        "extra": "x"}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(bad))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    data = llm.lookup_word_via_llm(lang="en", word="dog",
                                   explanation_primary="en", explanation_secondary=None)
    assert data == {"senses": [
        {"pos": "noun", "definitions": [{"glossary": "g"}]},
    ]}


def test_lookup_word_normalizes_common_aliases(fresh, monkeypatch):
    """Proxies rename fields; the normalizer maps the most common aliases
    back to canonical schema names."""
    from backend.services import llm

    messy = {"senses": [
        {"part_of_speech": "noun",
         "glosses": [
             {"text": "g1",
              "explanation": "usage note",
              "sentence": "s1"},
             {"translation": "g2",
              "usage_example": "s2"},
         ],
         "explanations": {
             "primary": "p",
             "secondary": "s",
             "note": "stray",
         }},
        {"pos": "verb",
         "meanings": [{"glossary": "g3", "example": "s3"}]},
    ]}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(messy))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    data = llm.lookup_word_via_llm(lang="en", word="applecart",
                                   explanation_primary="en", explanation_secondary=None)
    assert data == {"senses": [
        {"pos": "noun",
         "definitions": [
             {"glossary": "g1", "example": "s1"},
             {"glossary": "g2", "example": "s2"},
         ],
         "explanations": {"primary": "p", "secondary": "s"}},
        {"pos": "verb",
         "definitions": [{"glossary": "g3", "example": "s3"}]},
    ]}


def test_lookup_word_promotes_flat_sense_shape(fresh, monkeypatch):
    """Some models emit a flat sense like
    ``{"pos": ..., "definition": ..., "example": ...}`` instead of
    nesting the gloss under ``definitions``. The normalizer promotes
    that shape into the schema's required
    ``definitions: [{glossary, example?}]`` layout so strict validation
    passes."""
    from backend.services import llm

    flat = {"senses": [
        {"pos": "adjective",
         "definition": "Being in the early stage of life.",
         "example": "The young puppy played."},
        {"pos": "noun",
         "definition": "Young people collectively.",
         "example": "The young are our future."},
    ]}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(flat))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    data = llm.lookup_word_via_llm(lang="en", word="young",
                                   explanation_primary="en", explanation_secondary=None)
    assert data == {"senses": [
        {"pos": "adjective",
         "definitions": [{"glossary": "Being in the early stage of life.",
                          "example": "The young puppy played."}]},
        {"pos": "noun",
         "definitions": [{"glossary": "Young people collectively.",
                          "example": "The young are our future."}]},
    ]}


def test_lookup_word_promotes_flat_top_level_sense(fresh, monkeypatch):
    """Some models return a single flat sense object at the top level
    (``{"pos": ..., "definitions": [...], "explanations": {...}}``)
    instead of wrapping it in ``{"senses": [...]}``. The normalizer
    wraps it so strict validation passes."""
    from backend.services import llm

    flat = {"pos": "verb",
            "definitions": [
                {"glossary": "to throw something with force",
                 "example": "He cast the net into the sea."},
                {"glossary": "to choose an actor for a part",
                 "example": "The director cast her as the lead."},
            ],
            "explanations": {"primary": "To throw or to select someone."}}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(flat))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    data = llm.lookup_word_via_llm(lang="en", word="cast",
                                   explanation_primary="en", explanation_secondary=None)
    assert data == {"senses": [
        {"pos": "verb",
         "definitions": [
             {"glossary": "to throw something with force",
              "example": "He cast the net into the sea."},
             {"glossary": "to choose an actor for a part",
              "example": "The director cast her as the lead."},
         ],
         "explanations": {"primary": "To throw or to select someone."}},
    ]}


def test_lookup_word_accepts_missing_glossary(fresh, monkeypatch):
    """A definition with no glossary is tolerated by the schema instead of
    failing the lookup. The raw dict is returned; the provider's salvage
    logic drops the empty definition downstream."""
    from backend.services import llm

    bad = {"senses": [{"pos": "noun", "definitions": [{}]}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(bad))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    data = llm.lookup_word_via_llm(lang="en", word="dog",
                                   explanation_primary="en", explanation_secondary=None)
    assert data == {"senses": [
        {"pos": "noun", "definitions": [{"glossary": None}]},
    ]}


def test_lookup_word_salvages_partial_senses(fresh, monkeypatch):
    """A word where one sense is broken (missing pos, empty glossary) but
    another is complete should pass schema validation; the provider's
    salvage logic keeps the complete sense and drops the broken one."""
    from backend.services import llm

    partial = {"senses": [
        {"pos": "noun", "definitions": [{"glossary": "good sense"}]},
        {"pos": None, "definitions": [{"glossary": ""}]},
    ]}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(partial))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    data = llm.lookup_word_via_llm(lang="en", word="dog",
                                   explanation_primary="en", explanation_secondary=None)
    assert data == {"senses": [
        {"pos": "noun", "definitions": [{"glossary": "good sense"}]},
        {"pos": None, "definitions": [{"glossary": ""}]},
    ]}


def test_llm_error_is_exception_subclass():
    from backend.services import llm
    assert issubclass(llm.LLMError, Exception)
    assert issubclass(llm.LLMTimeout, llm.LLMError)
    assert issubclass(llm.LLMSchemaError, llm.LLMError)
