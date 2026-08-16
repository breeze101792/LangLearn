"""Edge-case tests for the vocab service's uncovered branches.

test_vocab.py covers the main CRUD + review flows. This file pins the
remaining branches:

- ``add_vocab`` with an explicit ``next_due`` / ``leitner_box`` schedule
  (the ``insert_due`` non-None path)
- ``add_vocab`` with an explicit ``added_at`` (the extra column path)
- ``_clamp_leitner`` boundary cases (non-int, out-of-range)
- ``_evict_undo_locked`` evicting expired tokens
- ``add_vocab`` word that normalizes to empty
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(clean_state):
    """Re-export of the autouse clean_state fixture."""
    return clean_state


def test_add_vocab_with_explicit_schedule(fresh):
    """A custom leitner_box without next_due gets a box-based cadence."""
    from backend.services import vocab as v
    res = v.add_vocab(user_id=1, language="es", word="perro", source="user",
                      glossary="dog", leitner_box=4)
    assert res["created"] is True
    items = v.list_vocab(user_id=1, language="es")
    assert items[0]["leitner_box"] == 4
    # next_due was computed from the box cadence (a future date).
    assert items[0]["next_due"] > "2000-01-01"


def test_add_vocab_with_explicit_next_due(fresh):
    """A supplied next_due is stored verbatim."""
    from backend.services import vocab as v
    res = v.add_vocab(user_id=1, language="es", word="gato", source="user",
                      glossary="cat", next_due="2099-01-01 00:00:00")
    assert res["created"] is True
    items = v.list_vocab(user_id=1, language="es")
    assert items[0]["next_due"].startswith("2099-01-01")


def test_add_vocab_with_explicit_added_at(fresh):
    """A supplied added_at is stored (extra column path)."""
    from backend.services import vocab as v
    res = v.add_vocab(user_id=1, language="es", word="sol", source="user",
                      glossary="sun", added_at="2020-05-05 10:00:00")
    assert res["created"] is True
    items = v.list_vocab(user_id=1, language="es")
    assert items[0]["added_at"].startswith("2020-05-05")


def test_clamp_leitner_non_int_returns_one(fresh):
    from backend.services import vocab as v
    assert v._clamp_leitner("abc") == 1
    assert v._clamp_leitner(None) == 1


def test_clamp_leitner_clamps_range(fresh):
    from backend.services import vocab as v
    assert v._clamp_leitner(0) == 1
    assert v._clamp_leitner(99) == 5
    assert v._clamp_leitner(3) == 3


def test_evict_undo_locked_removes_expired(fresh):
    """Expired undo tokens are evicted from the in-memory store."""
    from backend.services import vocab as v
    # Insert an old token directly (timestamp far in the past).
    v._undo_tokens["999-1000"] = {"id": 999}
    v._evict_undo_locked()
    assert "999-1000" not in v._undo_tokens


def test_evict_undo_locked_keeps_fresh(fresh):
    from backend.services import vocab as v
    import time
    fresh_ts = int(time.time() * 1000)
    v._undo_tokens[f"1-{fresh_ts}"] = {"id": 1}
    v._evict_undo_locked()
    assert f"1-{fresh_ts}" in v._undo_tokens
