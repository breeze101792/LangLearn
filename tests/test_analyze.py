"""Tests for the Analyze blueprint and ``llm.analyze_text_via_llm``."""

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


def test_analyze_text_happy_path(fresh, monkeypatch):
    from backend.services import llm

    payload = {
        "structures": [
            {
                "pattern": "would rather X than Y",
                "example_sentence": "She would rather stay home than go out.",
                "explanation": "Expresses preference between two options.",
                "explanation_primary": "Expresses preference.",
                "explanation_secondary": "表示偏好。",
            },
        ],
        "phrases": [
            {
                "phrase": "stay home",
                "example_sentence": "She would rather stay home.",
                "explanation": "Remain at home instead of going out.",
                "explanation_primary": "Remain at home.",
                "explanation_secondary": "待在家。",
            },
        ],
        "words": [
            {
                "word": "rather",
                "pos": "adverb",
                "glossary": "Used to express preference.",
                "example": "I would rather walk.",
                "explanation_primary": "Preferably.",
                "explanation_secondary": "寧可。",
            },
        ],
        "analysis": {
            "explanation": "The sentence expresses a preference between two options.",
            "alternatives": [
                {
                    "text": "She'd sooner stay home than go out in the rain.",
                    "nuance": "More informal.",
                },
            ],
            "translation_primary": "她寧可待在家，也不願冒雨出門。",
            "translation_secondary": "她寧可待在家，也不願冒雨出門。",
        },
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.analyze_text_via_llm(
        lang="en", text="She would rather stay home than go out.",
        primary="en", secondary="zh",
    )
    assert out["structures"][0]["pattern"] == "would rather X than Y"
    assert out["phrases"][0]["phrase"] == "stay home"
    assert out["words"][0]["word"] == "rather"
    assert out["analysis"]["explanation"].startswith("The sentence expresses")
    assert out["analysis"]["alternatives"][0]["text"].startswith("She'd sooner")
    # primary == target language, so the native translation is redundant
    # and must be nulled out by apply_explanation_rules.
    assert out["analysis"]["translation_primary"] is None


def test_analyze_keeps_analysis_translation_when_primary_differs(fresh, monkeypatch):
    """When the user's primary native differs from the target language,
    ``analysis.translation_primary`` survives the explanation rules."""
    from backend.services import llm

    payload = {
        "structures": [],
        "phrases": [],
        "words": [],
        "analysis": {
            "explanation": "Expresses a preference.",
            "alternatives": [{"text": "She'd sooner stay home.", "nuance": "Informal."}],
            "translation_primary": "她寧可待在家。",
            "translation_secondary": None,
        },
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.analyze_text_via_llm(
        lang="en", text="She would rather stay home.",
        primary="zh", secondary=None,
    )
    assert out["analysis"]["translation_primary"] == "她寧可待在家。"
    assert out["analysis"]["translation_secondary"] is None


def test_analyze_normalizes_analysis_aliases(fresh, monkeypatch):
    """The normalizer should repair common field-name variants the model
    produces for the ``analysis`` block so strict validation passes."""
    from backend.services import llm

    payload = {
        "structures": [],
        "phrases": [],
        "words": [],
        "analysis": {
            "summary": "Expresses a preference.",
            "native_alternatives": [
                {"sentence": "She'd sooner stay home.", "note": "Informal."},
            ],
            "translation": "她寧可待在家。",
        },
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.analyze_text_via_llm(
        lang="en", text="She would rather stay home.",
        primary="zh", secondary=None,
    )
    analysis = out["analysis"]
    assert analysis["explanation"] == "Expresses a preference."
    assert analysis["alternatives"][0]["text"] == "She'd sooner stay home."
    assert analysis["alternatives"][0]["nuance"] == "Informal."
    assert analysis["translation_primary"] == "她寧可待在家。"


def test_analyze_text_nulls_redundant_primary(fresh, monkeypatch):
    """If the target language equals the user's primary native, the LLM
    may still return ``explanation_primary``; ``apply_explanation_rules``
    must null it out so the rules hold even when the model is chatty."""
    from backend.services import llm

    payload = {
        "structures": [{
            "pattern": "X is Y", "example_sentence": "X is Y.",
            "explanation": "ex.", "explanation_primary": "should-be-nulled",
        }],
        "phrases": [{
            "phrase": "p", "example_sentence": "p.",
            "explanation": "ex.", "explanation_primary": "should-be-nulled",
        }],
        "words": [{
            "word": "w", "pos": "noun", "glossary": "g",
            "explanation_primary": "should-be-nulled",
        }],
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.analyze_text_via_llm(
        lang="en", text="X is Y.", primary="en", secondary=None,
    )
    assert out["structures"][0]["explanation_primary"] is None
    assert out["phrases"][0]["explanation_primary"] is None
    assert out["words"][0]["explanation_primary"] is None


def test_analyze_text_empty_text_raises(fresh):
    from backend.services import llm
    with pytest.raises(ValueError):
        llm.analyze_text_via_llm(
            lang="en", text="   ", primary="en", secondary=None,
        )


def test_analyze_uses_dedicated_timeout_and_no_retries(fresh, monkeypatch):
    """Analyze should call complete_json with a long, dedicated timeout
    and ``max_retries=0`` so a slow model fails fast instead of burning
    twice the global LLM timeout on a single user click."""
    from backend.services import llm

    captured = {}

    real_complete_json = llm.complete_json

    def spy_complete_json(**kwargs):
        captured["timeout"] = kwargs.get("timeout")
        captured["max_retries"] = kwargs.get("max_retries")
        return {
            "structures": [], "phrases": [], "words": [],
        }

    monkeypatch.setattr(llm, "complete_json", spy_complete_json)

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(
            _json.dumps({"structures": [], "phrases": [], "words": []})
        )

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.analyze_text_via_llm(
        lang="en", text="Hello.", primary="en", secondary=None,
    )
    assert out == {"structures": [], "phrases": [], "words": []}
    assert captured["max_retries"] == 0
    assert captured["timeout"] == llm.ANALYZE_TIMEOUT_SECONDS
    assert captured["timeout"] >= 180  # at least the global default


def test_analyze_llm_error_maps_to_502(monkeypatch):
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
    r = client.post("/api/analyze",
                    json={"language": "en", "text": "Hello."})
    assert r.status_code == 502
    body = r.get_json()
    assert body["ok"] is False
    assert body["code"] == "llm_error"


# --- HTTP layer ----------------------------------------------------------


def test_analyze_endpoint_rejects_invalid_lang():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/analyze",
                    json={"language": "klingon", "text": "Hello."})
    assert r.status_code == 400
    body = r.get_json()
    assert body["ok"] is False
    assert body["code"] == "invalid_lang"


def test_analyze_endpoint_rejects_blank_text():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/analyze",
                    json={"language": "en", "text": "   "})
    assert r.status_code == 400
    body = r.get_json()
    assert body["code"] == "invalid_input"


def test_analyze_endpoint_rejects_oversized_text():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/analyze",
                    json={"language": "en", "text": "a" * 5000})
    assert r.status_code == 400
    body = r.get_json()
    assert body["code"] == "invalid_input"


def test_analyze_endpoint_returns_extracted_items(monkeypatch):
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import settings as settings_svc
    init_schema()
    # Override the conftest default which points the LLM at OpenAI's
    # real endpoint (and therefore insists on a key). A non-OpenAI host
    # is enough to opt out of the key check.
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    settings_svc.update_settings({
        "active_language": "en",
        "explanation_primary": "zh",
        "explanation_secondary": None,
    })
    payload = {
        "structures": [
            {"pattern": "S V O", "example_sentence": "Cats eat fish.",
             "explanation": "Basic subject-verb-object.", "explanation_primary": "主謂賓。"}
        ],
        "phrases": [
            {"phrase": "eat fish", "example_sentence": "Cats eat fish.",
             "explanation": "Consume fish.", "explanation_primary": "吃魚。"}
        ],
        "words": [
            {"word": "cat", "pos": "noun", "glossary": "A small furry animal.",
             "explanation_primary": "貓。"}
        ],
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    app = create_app()
    client = app.test_client()
    r = client.post("/api/analyze",
                    json={"language": "en",
                          "text": "Cats eat fish in the morning."})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["ok"] is True
    data = body["data"]
    assert len(data["structures"]) == 1
    assert data["structures"][0]["pattern"] == "S V O"
    assert data["structures"][0]["explanation_primary"] == "主謂賓。"
    assert data["phrases"][0]["phrase"] == "eat fish"
    assert data["words"][0]["word"] == "cat"


def test_analyze_save_structure_round_trip(monkeypatch):
    """Hitting the per-item save endpoints (which the frontend uses) on
    structures extracted by Analyze should land in the DB and be visible
    through the structures list endpoint."""
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import settings as settings_svc
    init_schema()
    # Set the primary native to a non-English language so the
    # explanation-language rules let ``explanation_primary`` survive
    # (target=en, primary=zh, secondary=None).
    settings_svc.update_settings({
        "active_language": "en",
        "explanation_primary": "zh",
        "explanation_secondary": None,
    })
    app = create_app()
    client = app.test_client()

    item = {
        "pattern": "would rather X than Y",
        "example_sentence": "She would rather stay home than go out.",
        "explanation": "Expresses a preference between two options.",
        "explanation_primary": "Expresses preference.",
    }
    r = client.post("/api/structures", json={"language": "en", **item, "source": "llm"})
    assert r.status_code == 200
    new_id = r.get_json()["data"]["id"]

    items = client.get("/api/structures?lang=en").get_json()["data"]["items"]
    row = next(i for i in items if i["id"] == new_id)
    assert row["pattern"] == "would rather X than Y"
    assert row["explanation_primary"] == "Expresses preference."


def test_analyze_save_phrase_round_trip():
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import settings as settings_svc
    init_schema()
    settings_svc.update_settings({
        "active_language": "en",
        "explanation_primary": "zh",
        "explanation_secondary": None,
    })
    app = create_app()
    client = app.test_client()
    item = {
        "phrase": "stay home",
        "example_sentence": "She would rather stay home than go out.",
        "explanation": "Remain at home.",
        "explanation_primary": "Remain at home.",
    }
    r = client.post("/api/phrases", json={"language": "en", **item, "source": "llm"})
    assert r.status_code == 200
    new_id = r.get_json()["data"]["id"]
    items = client.get("/api/phrases?lang=en").get_json()["data"]["items"]
    row = next(i for i in items if i["id"] == new_id)
    assert row["phrase"] == "stay home"
    assert row["explanation_primary"] == "Remain at home."


def test_analyze_save_word_round_trip():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/add-from-entry", json={
        "lang": "en",
        "word": "rather",
        "source": "llm",
        "pos": "adverb",
        "glossary": "Used to express preference.",
        "example": "I would rather walk.",
        "explanation_primary": "Preferably.",
    })
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["created"] is True
    items = client.get("/api/vocab?lang=en").get_json()["data"]["items"]
    row = next(i for i in items if i["id"] == data["id"])
    assert row["word"] == "rather"
    assert row["pos"] == "adverb"
