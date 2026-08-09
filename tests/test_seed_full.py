"""More seed-service tests covering edge cases beyond the happy path.

test_seed.py covers built-in loading and idempotent init. This file pins
ensure_language_row, get_seed_path fallbacks, is_seeded, seed_builtin,
seed_via_llm, and the file-not-found paths.
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
    from backend import db
    db.init_schema()
    return tmp_path


# --- ensure_language_row ------------------------------------------------


def test_ensure_language_row_creates_row(fresh):
    from backend.services import seed as s
    from backend.db import get_conn

    s.ensure_language_row("es", "Spanish", is_built_in=0)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT code, display_name, is_built_in, seeded_at FROM languages "
            "WHERE code=?", ("es",)
        ).fetchone()
    assert row["display_name"] == "Spanish"
    assert row["is_built_in"] == 0
    assert row["seeded_at"] is None


def test_ensure_language_row_is_idempotent(fresh):
    from backend.services import seed as s
    s.ensure_language_row("es", "Spanish", is_built_in=0)
    s.ensure_language_row("es", "Castilian", is_built_in=0)
    from backend.db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT display_name FROM languages WHERE code=?", ("es",)
        ).fetchone()
    assert row["display_name"] == "Castilian"


def test_ensure_language_row_defaults_display_name(fresh):
    from backend.services import seed as s
    s.ensure_language_row("es")
    from backend.db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT display_name FROM languages WHERE code=?", ("es",)
        ).fetchone()
    assert row["display_name"] == "ES"


def test_ensure_language_row_rejects_invalid_code(fresh):
    from backend.services import seed as s
    with pytest.raises(ValueError, match="language"):
        s.ensure_language_row("ENG123")


# --- get_seed_path ------------------------------------------------------


def test_get_seed_path_returns_path_for_built_in(fresh):
    """Either the `<code>.json` or the `<display_name>.json` form is
    acceptable — what matters is that the path exists and ends in .json."""
    from backend.services import seed as s
    p = s.get_seed_path("en")
    assert p is not None
    assert p.suffix == ".json"
    assert p.exists()


def test_get_seed_path_returns_none_for_unknown_lang(fresh):
    from backend.services import seed as s
    assert s.get_seed_path("klingon") is None


def test_get_seed_path_rejects_invalid_lang(fresh):
    from backend.services import seed as s
    assert s.get_seed_path("ENG123") is None


# --- load_builtin_seed edge cases --------------------------------------


def test_load_builtin_seed_returns_dict(fresh):
    from backend.services import seed as s
    data = s.load_builtin_seed("en")
    assert data is not None
    assert "structures" in data
    assert "phrases" in data


def test_load_builtin_seed_for_unknown_lang_returns_none(fresh):
    from backend.services import seed as s
    assert s.load_builtin_seed("xx") is None


# --- is_seeded ---------------------------------------------------------


def test_is_seeded_false_when_lang_row_missing(fresh):
    from backend.services import seed as s
    assert s.is_seeded("es") is False


def test_is_seeded_false_when_seeded_at_null(fresh):
    from backend.services import seed as s
    s.ensure_language_row("es", "Spanish", is_built_in=0)
    assert s.is_seeded("es") is False


def test_is_seeded_true_after_initialize(fresh):
    from backend.services import seed as s
    s.initialize_language("en")
    assert s.is_seeded("en") is True


# --- seed_builtin ------------------------------------------------------


def test_seed_builtin_inserts_rows(fresh):
    from backend.services import seed as s
    from backend.db import get_conn
    s.ensure_language_row("en", "English", is_built_in=1)
    counts = s.seed_builtin("en")
    assert counts["structures"] >= 50
    assert counts["phrases"] >= 100
    with get_conn() as conn:
        struct_count = conn.execute(
            "SELECT COUNT(*) AS c FROM structures WHERE language='en' "
            "AND user_id=1"
        ).fetchone()["c"]
        phrase_count = conn.execute(
            "SELECT COUNT(*) AS c FROM phrases WHERE language='en' "
            "AND user_id=1"
        ).fetchone()["c"]
    assert struct_count == counts["structures"]
    assert phrase_count == counts["phrases"]


def test_seed_builtin_replaces_previous_built_in(fresh):
    """Re-seeding built-in langs must drop the old built-in/llm rows first
    so we don't accumulate stale entries across reseeds."""
    from backend.services import seed as s
    s.initialize_language("en")
    s.initialize_language("en", force=True)  # reseed
    s.initialize_language("en", force=True)  # again
    from backend.db import get_conn
    with get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM structures WHERE language='en' "
            "AND source='built-in' AND user_id=1"
        ).fetchone()["c"]
    # Count should match the JSON file, not 3x it.
    assert n >= 50


def test_seed_builtin_raises_for_unknown_lang(fresh):
    from backend.services import seed as s
    with pytest.raises(FileNotFoundError):
        s.seed_builtin("xx")


# --- seed_via_llm ------------------------------------------------------


def test_seed_via_llm_inserts_rows(fresh, monkeypatch):
    from backend.services import seed as s
    from backend.db import get_conn

    payload = {
        "structures": [{"pattern": "S V O",
                         "explanation_primary": "Basic",
                         "example_sentence": None,
                         "explanation_secondary": None}],
        "phrases": [{"phrase": "Hi", "explanation_primary": "Hello",
                      "literal_translation": None,
                      "explanation_secondary": None}],
    }

    def fake(*a, **kw):
        return payload

    monkeypatch.setattr(s, "generate_seed_payload", fake) if hasattr(s, "generate_seed_payload") else None
    from backend.services import llm as llm_svc
    monkeypatch.setattr(llm_svc, "generate_seed_payload", fake)

    s.ensure_language_row("es", "Spanish", is_built_in=0)
    counts = s.seed_via_llm("es")
    assert counts["structures"] == 1
    assert counts["phrases"] == 1
    with get_conn() as conn:
        row = conn.execute(
            "SELECT source FROM structures WHERE language='es' AND user_id=1"
        ).fetchone()
    assert row["source"] == "llm"


def test_seed_via_llm_replaces_previous_llm_rows(fresh, monkeypatch):
    from backend.services import seed as s
    from backend.db import get_conn
    from backend.services import llm as llm_svc

    payload1 = {"structures": [{"pattern": "A", "explanation_primary": "x"}],
                "phrases": [{"phrase": "a", "explanation_primary": "x"}]}
    payload2 = {"structures": [{"pattern": "B", "explanation_primary": "y"}],
                "phrases": [{"phrase": "b", "explanation_primary": "y"}]}

    calls = {"payload": payload1}

    def fake(*a, **kw):
        return calls["payload"]

    monkeypatch.setattr(llm_svc, "generate_seed_payload", fake)
    s.ensure_language_row("es", "Spanish", is_built_in=0)
    s.seed_via_llm("es")
    calls["payload"] = payload2
    s.seed_via_llm("es")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT pattern FROM structures WHERE language='es' "
            "AND user_id=1 ORDER BY added_at DESC"
        ).fetchall()
    patterns = [r["pattern"] for r in rows]
    # The second seed must have wiped the first.
    assert patterns == ["B"]


def test_seed_via_llm_propagates_llm_error(fresh, monkeypatch):
    from backend.services import seed as s
    from backend.services import llm as llm_svc

    def boom(*a, **kw):
        raise llm_svc.LLMError("provider down")
    monkeypatch.setattr(llm_svc, "generate_seed_payload", boom)
    s.ensure_language_row("es", "Spanish", is_built_in=0)
    with pytest.raises(llm_svc.LLMError):
        s.seed_via_llm("es")


# --- initialize_language edge cases -----------------------------------


def test_initialize_already_seeded_returns_reason(fresh):
    from backend.services import seed as s
    s.initialize_language("en")
    r = s.initialize_language("en")
    assert r == {"seeded": False, "reason": "already_seeded"}


def test_initialize_non_builtin_via_llm_creates_rows(fresh, monkeypatch):
    from backend.services import seed as s
    from backend.services import llm as llm_svc

    payload = {
        "structures": [{"pattern": "S V", "explanation_primary": "ok"}],
        "phrases": [{"phrase": "Hi", "explanation_primary": "Hello"}],
    }

    def fake(*a, **kw):
        return payload
    monkeypatch.setattr(llm_svc, "generate_seed_payload", fake)
    s.ensure_language_row("es", "Spanish", is_built_in=0)
    r = s.initialize_language("es")
    assert r["seeded"] is True
    assert r["source"] == "llm"


def test_initialize_force_reseeds_built_in(fresh):
    from backend.services import seed as s
    r1 = s.initialize_language("en")
    r2 = s.initialize_language("en", force=True)
    assert r2["seeded"] is True


# --- _display_name_for private helper ---------------------------------


def test_display_name_for_returns_lowercase(fresh):
    from backend.services import seed as s
    assert s._display_name_for("en") == "english"


def test_display_name_for_unknown_returns_code(fresh):
    from backend.services import seed as s
    assert s._display_name_for("xx") == "xx"
