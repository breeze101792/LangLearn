"""Shared pytest fixtures.

`clean_state` resets module-level in-memory state (undo tokens, etc.) and
sets up a fresh data dir + DB before each test.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    from backend import db
    db.init_schema()
    # Reset module-level in-memory state.
    from backend.services import vocab as vocab_svc
    vocab_svc._undo_tokens.clear()
    yield tmp_path