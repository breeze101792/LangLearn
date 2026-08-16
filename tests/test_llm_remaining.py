"""Targeted tests for remaining LLM normalizer / batch branches.

test_llm_normalizers.py covers the main normalizer branches. This file pins
a few remaining edge cases:

- ``_normalize_dict_word``: a flat sense with no definition drops the example
- ``_normalize_dict_word``: non-dict definitions are skipped
- ``_normalize_translate``: legacy flat shape backfills source from the
  per-call stash
- ``fill_structures_via_llm`` / ``fill_phrases_via_llm``: empty partials
  short-circuit without calling the LLM
- ``_build_fill_user_prompt``: keep_secondary branch
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
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def test_normalize_dict_word_flat_sense_no_definition(fresh):
    """A flat sense with no definition drops the example key."""
    from backend.services import llm
    data = {"senses": [
        {"pos": "noun", "example": "orphan example", "definition": None},
    ]}
    out = llm._normalize_dict_word(data)
    sense = out["senses"][0]
    assert "example" not in sense
    assert "definitions" not in sense


def test_normalize_dict_word_skips_non_dict_definitions(fresh):
    """Non-dict definitions are left as-is (the normalizer only promotes
    glossary on dict entries); the strict validator rejects them later."""
    from backend.services import llm
    data = {"senses": [
        {"pos": "noun", "definitions": ["bogus", {"glossary": "real"}]},
    ]}
    out = llm._normalize_dict_word(data)
    defs = out["senses"][0]["definitions"]
    assert defs == ["bogus", {"glossary": "real"}]


def test_normalize_translate_legacy_flat_backfills_source(fresh):
    """The legacy flat shape backfills `source` from the per-call stash."""
    from backend.services import llm
    llm._TRANSLATE_SOURCE_TEXT = "I'd like a coffee."
    try:
        data = {
            "translation": "Je voudrais un café.",
            "alternatives": [],
            "breakdown": [],
            "notes": "x",
        }
        out = llm._normalize_translate(data)
        assert out["sentences"][0]["source"] == "I'd like a coffee."
    finally:
        llm._TRANSLATE_SOURCE_TEXT = ""


def test_fill_structures_empty_short_circuits(fresh, monkeypatch):
    """Empty partials return [] without calling the LLM."""
    from backend.services import llm
    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _mock_openai_response("{}")
    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    assert llm.fill_structures_via_llm(lang="es", partials=[]) == []
    assert called["n"] == 0


def test_fill_phrases_empty_short_circuits(fresh, monkeypatch):
    from backend.services import llm
    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _mock_openai_response("{}")
    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    assert llm.fill_phrases_via_llm(lang="es", partials=[]) == []
    assert called["n"] == 0


def test_fill_structures_rejects_zero_batch(fresh):
    from backend.services import llm
    with pytest.raises(ValueError, match="batch_size"):
        llm.fill_structures_via_llm(lang="es", partials=[{}], batch_size=0)


def test_fill_phrases_rejects_zero_batch(fresh):
    from backend.services import llm
    with pytest.raises(ValueError, match="batch_size"):
        llm.fill_phrases_via_llm(lang="es", partials=[{}], batch_size=0)


def test_build_fill_user_prompt_keep_secondary(fresh):
    from backend.services import llm
    prompt = llm._build_fill_user_prompt(
        kind="structure", lang="es", partials=[{}],
        target_name="Spanish", primary_name="English",
        secondary_name="Chinese", keep_primary=True, keep_secondary=True,
    )
    assert "Fill explanation_primary in English." in prompt
    assert "Fill explanation_secondary in Chinese." in prompt
