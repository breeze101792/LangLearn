"""Edge-case tests for LLM normalizers and the seed service.

test_llm_helpers.py / test_llm_full.py cover the main LLM paths. This file
pins the remaining normalizer branches and seed-service edge cases that were
not exercised:

- ``_normalize_analyze``: drops unknown keys, promotes definition->glossary,
  sentence->example, truncates word
- ``_normalize_refine``: alias mapping for native/rewrite/improved/changes,
  per-edit from/to/note aliases, non-string coercion
- ``_normalize_translate``: legacy flat shape promotion, string alternatives
  coercion, breakdown aliases
- ``_normalize_seed``: list-shaped payload, wrapper keys, non-dict items
- ``_lang_name``: empty / unknown code
- seed: ``load_builtin_seed`` on a corrupt JSON file returns None
- seed: ``apply_explanations`` with non-dict / non-int items is skipped
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


# ---------- _lang_name ----------


def test_lang_name_empty_and_unknown(fresh):
    from backend.services import llm
    assert llm._lang_name(None) == ""
    assert llm._lang_name("") == ""
    assert llm._lang_name("en") == "English"
    assert llm._lang_name("xx") == "xx"


# ---------- _normalize_analyze ----------


def test_normalize_analyze_drops_unknown_and_promotes(fresh):
    from backend.services import llm
    data = {
        "structures": [
            {"pattern": "S V O", "example_sentence": "x", "explanation": "e",
             "explanation_primary": "p", "stray": "drop"},
        ],
        "phrases": [
            {"phrase": "hi", "example_sentence": "x", "explanation": "e",
             "extra": 1},
        ],
        "words": [
            {"word": "  Rather  ", "pos": "adverb",
             "definition": "used to express preference",
             "sentence": "I would rather walk.",
             "unknown": "drop"},
        ],
    }
    out = llm._normalize_analyze(data)
    assert "stray" not in out["structures"][0]
    assert "extra" not in out["phrases"][0]
    w = out["words"][0]
    assert w["word"] == "Rather"
    assert w["glossary"] == "used to express preference"
    assert w["example"] == "I would rather walk."
    assert "unknown" not in w


def test_normalize_analyze_skips_non_dict_items(fresh):
    from backend.services import llm
    data = {
        "structures": ["bogus"],
        "phrases": [None],
        "words": [42],
    }
    out = llm._normalize_analyze(data)
    assert out["structures"] == ["bogus"]
    assert out["phrases"] == [None]
    assert out["words"] == [42]


# ---------- _normalize_refine ----------


def test_normalize_refine_aliases(fresh):
    from backend.services import llm
    data = {
        "rewrite": "corrected text",
        "native_version": "native text",
        "changes": [
            {"from": "I go", "to": "I went", "note": "past tense"},
        ],
        "explanation": "e",
        "stray": "drop",
    }
    out = llm._normalize_refine(data)
    assert out["corrected"] == "corrected text"
    assert out["native"] == "native text"
    assert out["edits"][0]["original"] == "I go"
    assert out["edits"][0]["suggested"] == "I went"
    assert out["edits"][0]["reason"] == "past tense"
    assert "stray" not in out


def test_normalize_refine_coerces_non_string_fields(fresh):
    from backend.services import llm
    data = {
        "corrected": "x", "native": "x",
        "edits": [{"original": 5, "suggested": None, "reason": 7}],
        "explanation": "e",
    }
    out = llm._normalize_refine(data)
    e = out["edits"][0]
    assert e["original"] == "5"
    assert e["suggested"] == ""
    assert e["reason"] == "7"


def test_normalize_refine_improved_and_more_native(fresh):
    from backend.services import llm
    data = {
        "improved": "x", "more_native": "y",
        "suggestions": [{"bad": "a", "fixed": "b", "why": "c"}],
        "explanation": "e",
    }
    out = llm._normalize_refine(data)
    assert out["corrected"] == "x"
    assert out["native"] == "y"
    assert out["edits"][0]["original"] == "a"
    assert out["edits"][0]["suggested"] == "b"
    assert out["edits"][0]["reason"] == "c"


# ---------- _normalize_translate ----------


def test_normalize_translate_legacy_flat_shape(fresh):
    from backend.services import llm
    data = {
        "translation": "Bonjour.",
        "alternatives": ["Salut."],
        "breakdown": [{"to": "Bonjour", "from": "Hello", "comment": "greeting"}],
        "notes": "A greeting.",
    }
    out = llm._normalize_translate(data)
    assert len(out["sentences"]) == 1
    s = out["sentences"][0]
    assert s["translation"] == "Bonjour."
    assert s["alternatives"][0]["text"] == "Salut."
    assert s["alternatives"][0]["nuance"] is None
    assert s["breakdown"][0]["target"] == "Bonjour"
    assert s["breakdown"][0]["source"] == "Hello"
    assert s["breakdown"][0]["note"] == "greeting"
    assert out["notes"] == "A greeting."


def test_normalize_translate_string_alternatives_and_aliases(fresh):
    from backend.services import llm
    data = {
        "sentences": [
            {
                "original": "Hello.",
                "translated": "Bonjour.",
                "options": ["Salut.", {"phrase": "Coucou", "why": "informal"}],
                "gloss": [{"to": "Bonjour", "from": "Hello", "comment": "g"}],
                "note": "A greeting.",
            },
        ],
        "notes": "x",
    }
    out = llm._normalize_translate(data)
    s = out["sentences"][0]
    assert s["source"] == "Hello."
    assert s["translation"] == "Bonjour."
    assert s["alternatives"][0]["text"] == "Salut."
    assert s["alternatives"][1]["text"] == "Coucou"
    assert s["alternatives"][1]["nuance"] == "informal"
    assert s["breakdown"][0]["note"] == "g"
    assert s["notes"] == "A greeting."


def test_normalize_translate_skips_non_dict_sentence(fresh):
    from backend.services import llm
    data = {"sentences": ["bogus"], "notes": "x"}
    out = llm._normalize_translate(data)
    assert out["sentences"] == ["bogus"]


# ---------- _normalize_seed ----------


def test_normalize_seed_list_shape(fresh):
    from backend.services import llm
    data = [
        {"pattern": "S V O", "example_sentence": "x", "explanation": "e",
         "explanation_primary": "p", "stray": "drop"},
    ]
    out = llm._normalize_seed_batch(data, array_name="structures")
    assert out == {"structures": [
        {"pattern": "S V O", "example_sentence": "x", "explanation": "e",
         "explanation_primary": "p"},
    ]}


def test_normalize_seed_wrapper_keys(fresh):
    from backend.services import llm
    data = {"items": [{"phrase": "hi", "example_sentence": "x",
                       "explanation": "e"}]}
    out = llm._normalize_seed_batch(data, array_name="phrases")
    assert out["phrases"][0]["phrase"] == "hi"


def test_normalize_seed_coerces_empty_explanations_to_none(fresh):
    from backend.services import llm
    data = {"structures": [
        {"pattern": "S V O", "example_sentence": "x", "explanation": "e",
         "explanation_primary": "", "explanation_secondary": 5},
    ]}
    out = llm._normalize_seed_batch(data, array_name="structures")
    item = out["structures"][0]
    assert item["explanation_primary"] is None
    assert item["explanation_secondary"] is None


def test_normalize_seed_non_dict_items_dropped(fresh):
    from backend.services import llm
    data = {"structures": ["bogus", {"pattern": "S V O", "example_sentence": "x",
                                     "explanation": "e"}]}
    out = llm._normalize_seed_batch(data, array_name="structures")
    assert len(out["structures"]) == 1


# ---------- seed service edge cases ----------


def test_load_builtin_seed_corrupt_json_returns_none(fresh, monkeypatch):
    """A corrupt built-in JSON file must not crash; load_builtin_seed
    returns None."""
    from backend.services import seed as s
    from backend import config
    from pathlib import Path
    bad = Path(config.BUILTIN_SEED_DIR) / "zz.json"
    bad.write_text("{not json")
    monkeypatch.setattr(config, "BUILTIN_SEED_DIR", config.BUILTIN_SEED_DIR)
    # Point get_seed_path at the corrupt file by creating a valid-lang file.
    # 'zz' is not a valid lang, so use a temp dir override instead.
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    (tmp / "es.json").write_text("{not json")
    monkeypatch.setattr(config, "BUILTIN_SEED_DIR", tmp)
    assert s.load_builtin_seed("es") is None


def test_apply_explanations_skips_non_dict_and_non_int_items(fresh, monkeypatch):
    """apply_explanations must skip non-dict / non-int-id items in the LLM
    payload rather than crashing."""
    from backend.services import seed as s
    from backend.services import llm as llm_svc
    from backend.db import transaction

    with transaction() as conn:
        conn.execute(
            "INSERT INTO structures (user_id, language, pattern, example_sentence,"
            " explanation, explanation_primary, explanation_secondary, source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'user')",
            (1, "en", "S V O", "x", "x", None, None),
        )

    def fake_apply_via_llm(*, lang, structures, phrases, primary, secondary,
                           batch_size=None, **_kw):
        return {
            "structures": [
                {"id": 1, "explanation_primary": "p",
                 "explanation_secondary": None, "explanation": "e"},
                "bogus",
                {"id": "not-an-int", "explanation_primary": "x",
                 "explanation_secondary": None, "explanation": "e"},
            ],
            "phrases": [],
        }
    monkeypatch.setattr(llm_svc, "apply_explanations_via_llm", fake_apply_via_llm)
    out = s.apply_explanations("en")
    assert out == {"structures": 1, "phrases": 0}
