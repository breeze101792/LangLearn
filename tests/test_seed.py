"""Tests for built-in seed loading + idempotent initialization."""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(clean_state):
    """Re-export of the autouse clean_state fixture for tests that
    read `fresh` for documentation purposes. The autouse fixture in
    conftest.py already sets up the data dir + db schema and clears
    module-level state — see `tests/conftest.py`."""
    return clean_state


def test_english_seed_loads(fresh):
    from backend.services import seed as s
    data = s.load_builtin_seed("en")
    assert data is not None
    assert len(data["structures"]) >= 50
    assert len(data["phrases"]) >= 100


def test_unknown_lang_returns_none(fresh):
    from backend.services import seed as s
    assert s.load_builtin_seed("klingon") is None


def test_initialize_english_idempotent(fresh):
    from backend.services import seed as s
    s.ensure_language_row("en", "English", is_built_in=1)
    r1 = s.initialize_language("en")
    assert r1["seeded"] is True
    assert r1["source"] == "built-in"
    assert r1["structures"] >= 50
    assert r1["phrases"] >= 100
    r2 = s.initialize_language("en")
    assert r2["seeded"] is False
    assert r2["reason"] == "already_seeded"


def test_initialize_with_force_reseeds(fresh):
    from backend.services import seed as s
    s.ensure_language_row("en", "English", is_built_in=1)
    s.initialize_language("en")
    r = s.initialize_language("en", force=True)
    assert r["seeded"] is True


def test_initialize_non_builtin_fails_without_llm(monkeypatch, fresh):
    """Without a real LLM, non-built-in language init should raise LLMError."""
    from backend.services import seed as s
    from backend.services import llm
    monkeypatch.setattr(llm, "LLMError", llm.LLMError)

    def _boom(*a, **kw):
        raise llm.LLMError("provider unavailable")
    monkeypatch.setattr(s, "seed_via_llm", _boom)

    s.ensure_language_row("es", "Spanish", is_built_in=0)
    with pytest.raises(llm.LLMError):
        s.initialize_language("es")