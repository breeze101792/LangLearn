"""Shared pytest fixtures.

`clean_state` resets module-level in-memory state (undo tokens, etc.) and
sets up a fresh data dir + DB before each test.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    # Isolate LLM config from any developer .env: tests that care about the
    # LLM set their own env. Point at OpenAI's endpoint with no key so lookups
    # fail fast and deterministically instead of hitting a real server.
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    from backend import db
    db.init_schema()
    # Reset module-level in-memory state.
    from backend.services import vocab as vocab_svc
    vocab_svc._undo_tokens.clear()
    yield tmp_path