"""Tests for the Translate blueprint and ``llm.translate_text_via_llm``."""

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


def _two_sentence_payload():
    return {
        "sentences": [
            {
                "source": "I'd like a coffee.",
                "translation": "Je voudrais un café.",
                "alternatives": [
                    {"text": "Un café, s'il vous plaît.", "nuance": "More direct."},
                ],
                "breakdown": [
                    {"target": "Je voudrais", "source": "I'd like", "note": "Conditional, polite."},
                    {"target": "un café", "source": "a coffee", "note": None},
                ],
                "notes": "The conditional softens the request.",
            },
            {
                "source": "But not too hot.",
                "translation": "Mais pas trop chaud.",
                "alternatives": [
                    {"text": "Mais pas brûlant.", "nuance": "Uses 'brûlant' for 'scalding hot'."},
                ],
                "breakdown": [
                    {"target": "pas trop chaud", "source": "not too hot", "note": None},
                ],
                "notes": "Adjective follows the noun here.",
            },
        ],
        "notes": "Polite requests in French lean on the conditional.",
        "notes_primary": "French uses the conditional for polite requests.",
        "notes_secondary": "法語用條件式表達禮貌的請求。",
    }


# --- LLM service ---------------------------------------------------------


def test_translate_text_happy_path(fresh, monkeypatch):
    from backend.services import llm

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(_two_sentence_payload()))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.translate_text_via_llm(
        target_lang="fr",
        text="I'd like a coffee. But not too hot.",
        primary="en", secondary="zh",
    )
    assert len(out["sentences"]) == 2
    s0 = out["sentences"][0]
    assert s0["source"] == "I'd like a coffee."
    assert s0["translation"] == "Je voudrais un café."
    assert s0["alternatives"][0]["text"] == "Un café, s'il vous plaît."
    assert s0["alternatives"][0]["nuance"] == "More direct."
    assert s0["breakdown"][0]["target"] == "Je voudrais"
    assert s0["notes"].startswith("The conditional")
    assert out["notes"].startswith("Polite requests")
    assert out["notes_primary"] == "French uses the conditional for polite requests."


def test_translate_text_nulls_redundant_primary(fresh, monkeypatch):
    """When the target language equals the user's primary, notes_primary
    is nulled out by the shared explanation-language rules."""
    from backend.services import llm

    payload = {
        "sentences": [
            {
                "source": "我要一杯咖啡。",
                "translation": "I'd like a coffee.",
                "alternatives": [
                    {"text": "A coffee, please.", "nuance": None},
                ],
                "breakdown": [
                    {"target": "I'd like", "source": "我想", "note": None},
                ],
                "notes": "Use 'would like' for polite requests.",
            },
        ],
        "notes": "Use 'would like' for polite requests.",
        "notes_primary": "should-be-nulled",
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.translate_text_via_llm(
        target_lang="en",
        text="我要一杯咖啡。",
        primary="en", secondary=None,
    )
    assert out["notes_primary"] is None


def test_translate_text_promotes_legacy_flat_shape(fresh, monkeypatch):
    """A model that returns the old flat shape (top-level translation/
    alternatives/breakdown) should be promoted into a one-element
    `sentences` array by the normalizer."""
    from backend.services import llm

    payload = {
        "translation": "Je voudrais un café.",
        "alternatives": ["Un café, svp."],
        "breakdown": [
            {"to": "Je voudrais", "from": "I'd like", "comment": "polite"},
        ],
        "notes": "Polite request.",
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.translate_text_via_llm(
        target_lang="fr",
        text="I'd like a coffee.",
        primary="en", secondary=None,
    )
    assert len(out["sentences"]) == 1
    s = out["sentences"][0]
    assert s["translation"] == "Je voudrais un café."
    # string alternatives are coerced into {text, nuance} objects.
    assert s["alternatives"][0]["text"] == "Un café, svp."
    assert s["alternatives"][0]["nuance"] is None
    # breakdown aliases normalized.
    assert s["breakdown"][0]["target"] == "Je voudrais"
    assert s["breakdown"][0]["source"] == "I'd like"
    assert s["breakdown"][0]["note"] == "polite"


def test_translate_text_normalizes_sentence_aliases(fresh, monkeypatch):
    """A model that returns `items` instead of `sentences`, `original`
    instead of `source`, and string alternatives should all validate."""
    from backend.services import llm

    payload = {
        "items": [
            {
                "original": "Hello.",
                "translated": "Bonjour.",
                "options": ["Salut."],
                "gloss": [
                    {"to": "Bonjour", "from": "Hello", "comment": "greeting"},
                ],
                "note": "A simple greeting.",
            },
        ],
        "notes": "Greetings vary by register.",
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.translate_text_via_llm(
        target_lang="fr", text="Hello.",
        primary="en", secondary=None,
    )
    assert len(out["sentences"]) == 1
    s = out["sentences"][0]
    assert s["source"] == "Hello."
    assert s["translation"] == "Bonjour."
    assert s["alternatives"][0]["text"] == "Salut."
    assert s["breakdown"][0]["note"] == "greeting"
    assert s["notes"] == "A simple greeting."


def test_translate_text_normalizes_alternative_nuance_aliases(fresh, monkeypatch):
    """`why` / `difference` aliases for `nuance` are repaired."""
    from backend.services import llm

    payload = {
        "sentences": [
            {
                "source": "Hi.",
                "translation": "Salut.",
                "alternatives": [
                    {"phrase": "Bonjour.", "why": "more formal"},
                ],
                "breakdown": [],
                "notes": "x",
            },
        ],
        "notes": "x",
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.translate_text_via_llm(
        target_lang="fr", text="Hi.",
        primary="en", secondary=None,
    )
    a = out["sentences"][0]["alternatives"][0]
    assert a["text"] == "Bonjour."
    assert a["nuance"] == "more formal"


def test_translate_text_empty_text_raises(fresh):
    from backend.services import llm
    with pytest.raises(ValueError):
        llm.translate_text_via_llm(
            target_lang="fr", text="   ",
            primary="en", secondary=None,
        )


def test_translate_uses_dedicated_timeout_and_no_retries(fresh, monkeypatch):
    """Same policy as Analyze/Refine: dedicated timeout, no retries."""
    from backend.services import llm

    captured = {}

    def spy_complete_json(**kwargs):
        captured["timeout"] = kwargs.get("timeout")
        captured["max_retries"] = kwargs.get("max_retries")
        return {
            "sentences": [
                {
                    "source": "x", "translation": "x",
                    "alternatives": [], "breakdown": [], "notes": "x",
                },
            ],
            "notes": "x",
        }

    monkeypatch.setattr(llm, "complete_json", spy_complete_json)
    monkeypatch.setattr("backend.services.llm.requests.post", lambda *a, **k: _mock_openai_response(_json.dumps({
        "sentences": [
            {"source": "x", "translation": "x",
             "alternatives": [], "breakdown": [], "notes": "x"},
        ],
        "notes": "x",
    })))
    out = llm.translate_text_via_llm(
        target_lang="fr", text="Hello.",
        primary="en", secondary=None,
    )
    assert out["sentences"][0]["translation"] == "x"
    assert captured["max_retries"] == 0
    assert captured["timeout"] == llm.TRANSLATE_TIMEOUT_SECONDS
    assert captured["timeout"] >= 180


def test_translate_prompt_asks_model_to_split_sentences(fresh, monkeypatch):
    """The prompt must tell the model to split into sentences and
    produce a per-sentence breakdown, and to detect the source language."""
    from backend.services import llm

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        body = json or {}
        captured["user_msg"] = body["messages"][1]["content"]
        return _mock_openai_response(_json.dumps({
            "sentences": [
                {"source": "x", "translation": "x",
                 "alternatives": [], "breakdown": [], "notes": "x"},
            ],
            "notes": "x",
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.translate_text_via_llm(
        target_lang="fr", text="Hello.",
        primary="en", secondary=None,
    )
    assert "detect it yourself" in captured["user_msg"]
    assert "Split the input into sentences" in captured["user_msg"]
    assert "nuance" in captured["user_msg"]
    assert "French (fr)" in captured["user_msg"]


# --- HTTP layer ----------------------------------------------------------


def test_translate_endpoint_rejects_invalid_target_lang():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/translate",
                    json={"target_language": "klingon",
                          "text": "Hello."})
    assert r.status_code == 400
    body = r.get_json()
    assert body["ok"] is False
    assert body["code"] == "invalid_lang"


def test_translate_endpoint_rejects_blank_text():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/translate", json={"text": "   "})
    assert r.status_code == 400
    body = r.get_json()
    assert body["code"] == "invalid_input"


def test_translate_endpoint_rejects_oversized_text():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/translate", json={"text": "a" * 5000})
    assert r.status_code == 400
    body = r.get_json()
    assert body["code"] == "invalid_input"


def test_translate_endpoint_defaults_target_to_active_language(monkeypatch):
    """When ``target_language`` is omitted, the endpoint falls back to
    the user's active language from settings."""
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import settings as settings_svc
    init_schema()
    settings_svc.update_settings({
        "active_language": "fr",
        "explanation_primary": "en",
        "explanation_secondary": None,
    })
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        body = json or {}
        captured["user_msg"] = body["messages"][1]["content"]
        return _mock_openai_response(_json.dumps({
            "sentences": [
                {"source": "Hello.", "translation": "Bonjour.",
                 "alternatives": [], "breakdown": [], "notes": "x"},
            ],
            "notes": "x",
        }))

    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    app = create_app()
    client = app.test_client()
    r = client.post("/api/translate", json={"text": "Hello."})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"]["sentences"][0]["translation"] == "Bonjour."
    assert "French (fr)" in captured["user_msg"]


def test_translate_llm_error_maps_to_502(monkeypatch):
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
    r = client.post("/api/translate", json={"text": "你好。"})
    assert r.status_code == 502
    body = r.get_json()
    assert body["ok"] is False
    assert body["code"] == "llm_error"


def test_translate_endpoint_returns_per_sentence_translation(monkeypatch):
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import settings as settings_svc
    init_schema()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    settings_svc.update_settings({
        "active_language": "fr",
        "explanation_primary": "zh",
        "explanation_secondary": None,
    })

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(_two_sentence_payload()))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    app = create_app()
    client = app.test_client()
    r = client.post("/api/translate",
                    json={"text": "I'd like a coffee. But not too hot."})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    data = body["data"]
    assert len(data["sentences"]) == 2
    assert data["sentences"][0]["translation"] == "Je voudrais un café."
    assert data["sentences"][0]["alternatives"][0]["nuance"] == "More direct."
    assert data["sentences"][1]["breakdown"][0]["target"] == "pas trop chaud"
    assert data["notes_primary"] == "French uses the conditional for polite requests."