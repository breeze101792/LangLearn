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

    payload = {
        "structures": [{"pattern": "S V O",
                         "explanation_primary": "Basic",
                         "example_sentence": "She reads."}],
        "phrases": [{"phrase": "Hi", "explanation_primary": "Hello"}],
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.generate_seed_payload(lang="es", n_structures=1, n_phrases=1)
    assert out["structures"][0]["pattern"] == "S V O"
    assert out["phrases"][0]["phrase"] == "Hi"


def test_generate_seed_payload_retries_on_invalid_json(fresh, monkeypatch):
    from backend.services import llm

    good = {"structures": [{"pattern": "S V O", "explanation_primary": "ok"}],
            "phrases": [{"phrase": "Hi", "explanation_primary": "ok"}]}
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _mock_openai_response("not json")
        return _mock_openai_response(_json.dumps(good))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.generate_seed_payload(lang="es", n_structures=1, n_phrases=1)
    assert calls["n"] == 2
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

    payload = {"pattern": "S V O", "example_sentence": "She reads.",
               "explanation_primary": "Basic",
               "explanation_secondary": None}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.fill_structure_via_llm(lang="es", partial={"pattern": None})
    assert out["pattern"] == "S V O"


def test_fill_structure_via_llm_returns_nulls_for_missing(fresh, monkeypatch):
    """When the LLM returns all-null fields, the response still validates."""
    from backend.services import llm

    payload = {"pattern": None, "example_sentence": None,
               "explanation_primary": None, "explanation_secondary": None}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.fill_structure_via_llm(lang="es", partial={})
    assert out["pattern"] is None
    assert out["example_sentence"] is None


def test_fill_structure_via_llm_retries_on_schema_error(fresh, monkeypatch):
    from backend.services import llm

    bad = {"unknown_field": "x"}  # extra properties — fails strict schema
    good = {"pattern": "S V O", "example_sentence": None,
            "explanation_primary": "Basic", "explanation_secondary": None}
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

    payload = {"literal_translation": "good night",
               "explanation_primary": "Farewell.",
               "explanation_secondary": None}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.fill_phrase_via_llm(lang="es", partial={})
    assert out["literal_translation"] == "good night"


def test_fill_phrase_via_llm_retries_on_invalid_json(fresh, monkeypatch):
    from backend.services import llm

    good = {"literal_translation": "good night",
            "explanation_primary": "Farewell.",
            "explanation_secondary": None}
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _mock_openai_response("{not-json")
        return _mock_openai_response(_json.dumps(good))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.fill_phrase_via_llm(lang="es", partial={})
    assert calls["n"] == 2
    assert out["literal_translation"] == "good night"


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
    with pytest.raises(llm.LLMTimeout):
        llm.lookup_word_via_llm(lang="en", word="dog",
                                  explanation_primary="en", explanation_secondary=None)


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


def test_lookup_word_rejects_empty_senses(fresh, monkeypatch):
    from backend.services import llm

    bad = {"senses": []}  # minItems: 1

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(bad))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMSchemaError):
        llm.lookup_word_via_llm(lang="en", word="dog",
                                  explanation_primary="en", explanation_secondary=None)


def test_lookup_word_rejects_extra_properties(fresh, monkeypatch):
    """strict=True means additionalProperties must be false."""
    from backend.services import llm

    bad = {"senses": [{"pos": "noun",
                        "definitions": [{"glossary": "g"}],
                        "extra": "x"}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(bad))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMSchemaError):
        llm.lookup_word_via_llm(lang="en", word="dog",
                                  explanation_primary="en", explanation_secondary=None)


def test_lookup_word_rejects_missing_glossary(fresh, monkeypatch):
    from backend.services import llm

    bad = {"senses": [{"pos": "noun", "definitions": [{}]}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(bad))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMSchemaError):
        llm.lookup_word_via_llm(lang="en", word="dog",
                                  explanation_primary="en", explanation_secondary=None)


def test_llm_error_is_exception_subclass():
    from backend.services import llm
    assert issubclass(llm.LLMError, Exception)
    assert issubclass(llm.LLMTimeout, llm.LLMError)
    assert issubclass(llm.LLMSchemaError, llm.LLMError)
