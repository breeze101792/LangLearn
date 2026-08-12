"""Tests for the Refine blueprint and ``llm.refine_text_via_llm``."""

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
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


# --- LLM service ---------------------------------------------------------


def test_refine_text_happy_path(fresh, monkeypatch):
    from backend.services import llm

    payload = {
        "corrected": "I went to the store yesterday because I wanted to buy some apples.",
        "native": "Yesterday I popped into the store to grab some apples.",
        "edits": [
            {
                "original": "I am go",
                "suggested": "I went",
                "reason": "Past tense, not present.",
            },
            {
                "original": "want buy",
                "suggested": "wanted to buy",
                "reason": "Past tense + to-infinitive.",
            },
            {
                "original": "apple",
                "suggested": "apples",
                "reason": "Plural for the general sense.",
            },
        ],
        "explanation": "Watch tense agreement: was/wanted pair with past actions.",
        "explanation_primary": "Watch tense agreement.",
        "explanation_secondary": "注意時態一致。",
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.refine_text_via_llm(
        lang="en",
        text="I am go to the store yesterday because I want buy some apple.",
        primary="en", secondary="zh",
    )
    assert out["corrected"].startswith("I went")
    assert out["native"].startswith("Yesterday")
    assert len(out["edits"]) == 3
    assert out["edits"][0]["original"] == "I am go"


def test_refine_text_nulls_redundant_primary(fresh, monkeypatch):
    from backend.services import llm

    payload = {
        "corrected": "I went home.",
        "native": "I went home.",
        "edits": [],
        "explanation": "All good.",
        "explanation_primary": "should-be-nulled",
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.refine_text_via_llm(
        lang="en", text="I go home.", primary="en", secondary=None,
    )
    assert out["explanation_primary"] is None


def test_refine_text_normalizes_field_aliases(fresh, monkeypatch):
    """Models that use ``from``/``to``/``changes`` etc. should still
    produce a schema-valid refine response after the normalizer runs."""
    from backend.services import llm

    payload = {
        "corrected": "I went home.",
        "native": "I went home.",
        "changes": [
            {"from": "I go", "to": "I went", "note": "past tense"},
        ],
        "explanation": "Use past tense.",
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.refine_text_via_llm(
        lang="en", text="I go home.", primary="en", secondary=None,
    )
    assert out["edits"][0]["original"] == "I go"
    assert out["edits"][0]["suggested"] == "I went"
    assert out["edits"][0]["reason"] == "past tense"


def test_refine_text_empty_text_raises(fresh):
    from backend.services import llm
    with pytest.raises(ValueError):
        llm.refine_text_via_llm(
            lang="en", text="   ", primary="en", secondary=None,
        )


def test_refine_uses_dedicated_timeout_and_no_retries(fresh, monkeypatch):
    """Same policy as Analyze: dedicated timeout, no retries, fail
    fast on a slow proxy instead of burning 2x the global LLM timeout."""
    from backend.services import llm

    captured = {}

    def spy_complete_json(**kwargs):
        captured["timeout"] = kwargs.get("timeout")
        captured["max_retries"] = kwargs.get("max_retries")
        return {
            "corrected": "x", "native": "x", "edits": [],
            "explanation": "x",
        }

    monkeypatch.setattr(llm, "complete_json", spy_complete_json)

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps({
            "corrected": "x", "native": "x", "edits": [],
            "explanation": "x",
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.refine_text_via_llm(
        lang="en", text="Hello.", primary="en", secondary=None,
    )
    assert out["corrected"] == "x"
    assert captured["max_retries"] == 0
    assert captured["timeout"] == llm.REFINE_TIMEOUT_SECONDS
    assert captured["timeout"] >= 180  # at least the global default


# --- HTTP layer ----------------------------------------------------------


def test_refine_endpoint_rejects_invalid_lang():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/refine",
                    json={"language": "klingon", "text": "Hello."})
    assert r.status_code == 400
    body = r.get_json()
    assert body["ok"] is False
    assert body["code"] == "invalid_lang"


def test_refine_endpoint_rejects_blank_text():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/refine",
                    json={"language": "en", "text": "   "})
    assert r.status_code == 400
    body = r.get_json()
    assert body["code"] == "invalid_input"


def test_refine_endpoint_rejects_oversized_text():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/refine",
                    json={"language": "en", "text": "a" * 5000})
    assert r.status_code == 400
    body = r.get_json()
    assert body["code"] == "invalid_input"


def test_refine_llm_error_maps_to_502(monkeypatch):
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    app = create_app()
    client = app.test_client()
    import requests

    def fake_post(url, json=None, headers=None, timeout=None):
        raise requests.RequestException("boom")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    r = client.post("/api/refine",
                    json={"language": "en", "text": "Hello."})
    assert r.status_code == 502
    body = r.get_json()
    assert body["ok"] is False
    assert body["code"] == "llm_error"


def test_refine_endpoint_returns_corrections(monkeypatch):
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import settings as settings_svc
    init_schema()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    settings_svc.update_settings({
        "active_language": "en",
        "explanation_primary": "zh",
        "explanation_secondary": None,
    })
    payload = {
        "corrected": "I went to the store.",
        "native": "I popped into the store.",
        "edits": [
            {"original": "I go", "suggested": "I went", "reason": "past tense"},
        ],
        "explanation": "Use past tense.",
        "explanation_primary": "用過去式。",
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    app = create_app()
    client = app.test_client()
    r = client.post("/api/refine",
                    json={"language": "en",
                          "text": "I go to the store."})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    data = body["data"]
    assert data["corrected"] == "I went to the store."
    assert data["native"] == "I popped into the store."
    assert data["edits"][0]["original"] == "I go"
    assert data["edits"][0]["suggested"] == "I went"
    assert data["explanation_primary"] == "用過去式。"
