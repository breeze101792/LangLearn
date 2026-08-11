"""Tests for the LLM service.

We monkeypatch the HTTP call so the tests don't need real network access.
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


def _mock_openai_response(content: str):
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return resp


def test_lookup_word_happy_path(fresh, monkeypatch):
    from backend.services import llm

    payload = {
        "senses": [
            {
                "pos": "noun",
                "definitions": [{"glossary": "A house.", "example": "Mi casa."}],
                "explanations": {"primary": "House.", "secondary": "房子。"},
            }
        ]
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.lookup_word_via_llm(
        lang="es", word="casa",
        explanation_primary="en", explanation_secondary="zh",
    )
    assert out["senses"][0]["definitions"][0]["glossary"] == "A house."


def test_lookup_word_prompt_specifies_target_lang_for_glossary_and_example(
    fresh, monkeypatch,
):
    """The user prompt must tell the model that `glossary` and `example`
    are written in the TARGET language (the word's language), not in the
    explanation languages — otherwise we get English glosses/example
    sentences for Chinese words, which is what prompted this fix."""
    from backend.services import llm

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["payload"] = json or {}
        return _mock_openai_response(_json.dumps({
            "senses": [{
                "pos": "noun",
                "definitions": [{"glossary": "ok"}],
            }],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.lookup_word_via_llm(
        lang="zh", word="從",
        explanation_primary="en", explanation_secondary="fr",
    )
    user_msg = next(
        (m for m in captured["payload"]["messages"] if m["role"] == "user"),
        None,
    )
    assert user_msg is not None
    text = user_msg["content"]
    # Glossary definition must be tied to the target language.
    assert "Traditional Chinese" in text
    # Example sentence must also be tied to the target language.
    # Appears at least twice (once for glossary context, once for example).
    assert text.count("Traditional Chinese") >= 2
    # The prompt must explicitly contrast glossary (target lang) with
    # explanations (other languages), so the model doesn't conflate them.
    assert "explanation" in text.lower()


def test_lookup_word_prompt_for_english_word(fresh, monkeypatch):
    """Same contract for English: glossary/example in English, explanations
    in the user's other languages."""
    from backend.services import llm

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["payload"] = json or {}
        return _mock_openai_response(_json.dumps({
            "senses": [{
                "pos": "noun",
                "definitions": [{"glossary": "ok"}],
            }],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.lookup_word_via_llm(
        lang="en", word="dog",
        explanation_primary="zh", explanation_secondary=None,
    )
    user_msg = next(
        (m for m in captured["payload"]["messages"] if m["role"] == "user"),
        None,
    )
    assert user_msg is not None
    assert "English" in user_msg["content"]


def test_lookup_word_retries_on_invalid_json(fresh, monkeypatch):
    from backend.services import llm

    good = {"senses": [{"pos": "noun", "definitions": [{"glossary": "ok"}]}]}
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _mock_openai_response("not json {")
        return _mock_openai_response(_json.dumps(good))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.lookup_word_via_llm(
        lang="es", word="casa",
        explanation_primary="en", explanation_secondary=None,
    )
    assert calls["n"] == 2
    assert out["senses"][0]["definitions"][0]["glossary"] == "ok"


def test_lookup_word_retries_on_schema_violation(fresh, monkeypatch):
    from backend.services import llm

    bad = {"wrong": "shape"}
    good = {"senses": [{"pos": "noun", "definitions": [{"glossary": "ok"}]}]}
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _mock_openai_response(_json.dumps(bad))
        return _mock_openai_response(_json.dumps(good))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.lookup_word_via_llm(
        lang="es", word="casa",
        explanation_primary="en", explanation_secondary=None,
    )
    assert calls["n"] == 2


def test_lookup_word_raises_after_two_failures(fresh, monkeypatch):
    from backend.services import llm

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response("not json")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMSchemaError):
        llm.lookup_word_via_llm(
            lang="es", word="casa",
            explanation_primary="en", explanation_secondary=None,
        )


def test_missing_api_key_raises(fresh, monkeypatch):
    from backend.services import llm

    monkeypatch.setattr(llm.config, "OPENAI_API_KEY", "")
    with pytest.raises(llm.LLMError):
        llm.lookup_word_via_llm(
            lang="es", word="casa",
            explanation_primary="en", explanation_secondary=None,
        )


def test_network_error_maps_to_llm_error(fresh, monkeypatch):
    from backend.services import llm
    import requests

    def fake_post(url, json=None, headers=None, timeout=None):
        raise requests.RequestException("boom")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMError):
        llm.lookup_word_via_llm(
            lang="es", word="casa",
            explanation_primary="en", explanation_secondary=None,
        )