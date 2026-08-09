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