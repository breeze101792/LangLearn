"""Tests for the per-language CEFR proficiency level feature.

Covers:
- ``language_levels_json`` settings key (defaults, coercion, validation,
  round-trip through the HTTP API, ``_row_to_dict`` tolerance for a
  pre-010 DB row).
- ``get_language_level`` getter.
- ``llm._level_directive`` returns "" for unset/unknown and a concrete
  paragraph for a known level.
- The directive is appended to the system prompt on every target-
  language generation path (Analyze, Refine, Translate, Describe, seed,
  fill, dictionary lookup, apply-explanations) when a level is set,
  and omitted when it is not.
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
    from backend.services.dictionaries import registry
    registry.bootstrap()
    return tmp_path


# --- config --------------------------------------------------------------


def test_cefr_levels_constant_order():
    from backend import config
    assert config.CEFR_LEVELS == ["A1", "A2", "B1", "B2", "C1", "C2"]


# --- settings service ----------------------------------------------------


def test_language_levels_default_empty(fresh):
    from backend.services import settings as s
    out = s.get_settings()
    assert out["language_levels_json"] == {}


def test_update_language_levels_round_trip(fresh):
    from backend.services import settings as s
    out = s.update_settings({"language_levels_json": {"es": "B1", "ja": "A2"}})
    assert out["language_levels_json"] == {"es": "B1", "ja": "A2"}
    assert s.get_language_level("es") == "B1"
    assert s.get_language_level("ja") == "A2"


def test_get_language_level_unset_returns_none(fresh):
    from backend.services import settings as s
    s.update_settings({"language_levels_json": {"es": "B1"}})
    assert s.get_language_level("es") == "B1"
    assert s.get_language_level("en") is None


def test_update_language_levels_normalizes_case(fresh):
    from backend.services import settings as s
    out = s.update_settings({"language_levels_json": {"es": "b1"}})
    assert out["language_levels_json"] == {"es": "B1"}


def test_update_language_levels_null_drops_entry(fresh):
    """Setting a level to null/empty removes it from the map (unset)."""
    from backend.services import settings as s
    s.update_settings({"language_levels_json": {"es": "B1"}})
    s.update_settings({"language_levels_json": {"es": None}})
    assert s.get_settings()["language_levels_json"] == {}
    assert s.get_language_level("es") is None


def test_update_language_levels_rejects_non_dict(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s.update_settings({"language_levels_json": ["B1"]})


def test_update_language_levels_rejects_unknown_lang(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s.update_settings({"language_levels_json": {"ZZ": "B1"}})


def test_update_language_levels_rejects_unknown_level(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s.update_settings({"language_levels_json": {"es": "X9"}})


def test_update_language_levels_rejects_non_string_level(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s.update_settings({"language_levels_json": {"es": 42}})


def test_row_to_dict_tolerates_missing_language_levels(fresh):
    """A pre-010 DB row without the language_levels_json column falls
    back to the default (empty map) rather than raising."""
    from backend.services import settings as s
    from backend.db import get_conn

    class FakeRow:
        def __getitem__(self, key):
            if key == "language_levels_json":
                raise KeyError("language_levels_json")
            return {
                "user_id": 1, "active_language": "en", "auto_add_vocab": 1,
                "page_size": 20, "explanation_primary": "en",
                "explanation_secondary": None, "dict_chain_json": "{}",
                "theme": "auto", "show_readings": 1, "tts_provider": "google",
                "review_session_size": 30,
            }[key]

    out = s._row_to_dict(FakeRow())
    assert out["language_levels_json"] == {}


# --- HTTP API ------------------------------------------------------------


def test_api_get_settings_includes_language_levels(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/settings")
    data = r.get_json()["data"]
    assert "language_levels_json" in data
    assert data["language_levels_json"] == {}


def test_api_put_language_levels_round_trip(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"language_levels_json": {"es": "C1"}})
    assert r.status_code == 200
    assert r.get_json()["data"]["language_levels_json"] == {"es": "C1"}


def test_api_put_language_levels_rejects_unknown_level(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"language_levels_json": {"es": "Z9"}})
    assert r.status_code == 400


def test_api_put_language_levels_rejects_non_dict(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"language_levels_json": "not an object"})
    assert r.status_code == 400


# --- llm._level_directive ------------------------------------------------


def test_level_directive_unset_returns_empty():
    from backend.services import llm
    assert llm._level_directive(None) == ""
    assert llm._level_directive("") == ""


def test_level_directive_unknown_returns_empty():
    from backend.services import llm
    assert llm._level_directive("X9") == ""


def test_level_directive_known_returns_guidance():
    from backend.services import llm
    text = llm._level_directive("B1")
    assert text.startswith(" ")
    assert "intermediate" in text.lower() or "B1" in text


def test_level_directive_case_insensitive():
    from backend.services import llm
    assert llm._level_directive("b1") == llm._level_directive("B1")


# --- LLM calls receive the directive -------------------------------------


def _mock_openai_response(content: str):
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def test_analyze_threads_level_into_system_prompt(fresh, monkeypatch):
    from backend.services import llm
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        body = json or {}
        captured["system"] = body["messages"][0]["content"]
        return _mock_openai_response(_json.dumps({
            "structures": [], "phrases": [], "words": [],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.analyze_text_via_llm(
        lang="en", text="hello", primary="zh", secondary=None, level="C2",
    )
    assert "C2" in captured["system"]
    assert "proficient" in captured["system"].lower() or "do not simplify" in captured["system"].lower()


def test_analyze_omits_directive_when_level_unset(fresh, monkeypatch):
    from backend.services import llm
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        body = json or {}
        captured["system"] = body["messages"][0]["content"]
        return _mock_openai_response(_json.dumps({
            "structures": [], "phrases": [], "words": [],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.analyze_text_via_llm(
        lang="en", text="hello", primary="zh", secondary=None, level=None,
    )
    assert "CEFR" not in captured["system"]
    assert "proficient" not in captured["system"].lower()


def test_refine_threads_level_into_system_prompt(fresh, monkeypatch):
    from backend.services import llm
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        body = json or {}
        captured["system"] = body["messages"][0]["content"]
        return _mock_openai_response(_json.dumps({
            "corrected": "x", "native": "y", "edits": [], "explanation": "z",
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.refine_text_via_llm(
        lang="en", text="I is here", primary="zh", secondary=None, level="A1",
    )
    assert "A1" in captured["system"]
    assert "beginner" in captured["system"].lower()


def test_translate_threads_level_into_system_prompt(fresh, monkeypatch):
    from backend.services import llm
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        body = json or {}
        captured["system"] = body["messages"][0]["content"]
        return _mock_openai_response(_json.dumps({
            "sentences": [{"source": "x", "translation": "y",
                            "alternatives": [], "breakdown": [], "notes": "n"}],
            "notes": "n",
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.translate_text_via_llm(
        target_lang="en", text="bonjour", primary="zh", secondary=None, level="B2",
    )
    assert "B2" in captured["system"]


def test_describe_threads_level_into_system_prompt(fresh, monkeypatch):
    import base64
    from backend.services import llm
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        body = json or {}
        captured["system"] = body["messages"][0]["content"]
        return _mock_openai_response(_json.dumps({
            "description": "x", "words": [],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.describe_image_via_llm(
        target_lang="en", image_bytes=png, mime_type="image/png",
        primary="zh", secondary=None, level="C1",
    )
    assert "C1" in captured["system"]


def test_lookup_word_threads_level_into_system_prompt(fresh, monkeypatch):
    from backend.services import llm
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        body = json or {}
        captured["system"] = body["messages"][0]["content"]
        return _mock_openai_response(_json.dumps({
            "senses": [{"pos": "noun", "definitions": [{"glossary": "g"}]}],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.lookup_word_via_llm(
        lang="en", word="house", explanation_primary="zh",
        explanation_secondary=None, level="A2",
    )
    assert "A2" in captured["system"]


def test_seed_threads_level_into_system_prompt(fresh, monkeypatch):
    from backend.services import llm
    captured = []

    def fake_post(url, json=None, headers=None, timeout=None):
        body = json or {}
        captured.append(body["messages"][0]["content"])
        return _mock_openai_response(_json.dumps({
            "structures": [{"pattern": "p", "example_sentence": "e",
                            "explanation": "x", "explanation_primary": "p"}],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.generate_structures_via_llm(
        lang="en", n=1, primary="zh", secondary=None, level="B1",
    )
    assert any("B1" in s for s in captured)


def test_fill_structure_threads_level_into_system_prompt(fresh, monkeypatch):
    from backend.services import llm
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        body = json or {}
        captured["system"] = body["messages"][0]["content"]
        return _mock_openai_response(_json.dumps({
            "structures": [{"pattern": "p", "example_sentence": "e",
                            "explanation": "x"}],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.fill_structure_via_llm(
        lang="en", partial={"pattern": "p"}, primary="zh",
        secondary=None, level="C2",
    )
    assert "C2" in captured["system"]


def test_apply_explanations_threads_level_into_system_prompt(fresh, monkeypatch):
    from backend.services import llm
    captured = []

    def fake_post(url, json=None, headers=None, timeout=None):
        body = json or {}
        captured.append(body["messages"][0]["content"])
        return _mock_openai_response(_json.dumps({
            "structures": [{"id": 1, "explanation": "e",
                            "explanation_primary": "p",
                            "explanation_secondary": None}],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.apply_explanations_via_llm(
        lang="en", structures=[{"id": 1, "pattern": "p",
                                 "example_sentence": "e", "explanation": "x"}],
        phrases=[], primary="zh", secondary=None, level="A1",
    )
    assert any("A1" in s for s in captured)


# --- end-to-end: blueprint reads level from settings ---------------------


def test_analyze_endpoint_reads_level_from_settings(monkeypatch):
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import settings as settings_svc
    init_schema()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    settings_svc.update_settings({
        "active_language": "en",
        "explanation_primary": "zh",
        "explanation_secondary": None,
        "language_levels_json": {"en": "B2"},
    })
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        body = json or {}
        captured["system"] = body["messages"][0]["content"]
        return _mock_openai_response(_json.dumps({
            "structures": [], "phrases": [], "words": [],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    app = create_app()
    client = app.test_client()
    r = client.post("/api/analyze", json={"language": "en", "text": "hello"})
    assert r.status_code == 200
    assert "B2" in captured["system"]