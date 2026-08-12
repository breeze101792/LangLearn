"""Tests for the Leitner scheduler beyond the happy-path in test_leitner.py."""

from __future__ import annotations

import datetime as _dt

import pytest


@pytest.fixture
def fresh(clean_state):
    """Re-export of the autouse clean_state fixture for tests that
    read `fresh` for documentation purposes. The autouse fixture in
    conftest.py already sets up the data dir + db schema and clears
    module-level state — see `tests/conftest.py`."""
    return clean_state


# --- due_iso -----------------------------------------------------------


def test_due_iso_returns_iso_string():
    from backend.services import leitner
    base = _dt.datetime(2025, 1, 1, 12, 0, 0)
    assert leitner.due_iso(1, now=base) == "2025-01-02 12:00:00"
    assert leitner.due_iso(2, now=base) == "2025-01-04 12:00:00"
    assert leitner.due_iso(3, now=base) == "2025-01-08 12:00:00"
    assert leitner.due_iso(4, now=base) == "2025-01-15 12:00:00"
    assert leitner.due_iso(5, now=base) == "2025-01-31 12:00:00"


def test_due_iso_clamps_box_to_known_intervals():
    """Out-of-range boxes must not crash; they clamp into BOX_INTERVALS_DAYS."""
    from backend.services import leitner
    base = _dt.datetime(2025, 1, 1, 12, 0, 0)
    # box=0 -> index = max(0, min(4, -1)) = 0 -> 1 day
    assert leitner.due_iso(0, now=base) == "2025-01-02 12:00:00"
    # box=99 -> index = max(0, min(4, 98)) = 4 -> 30 days
    assert leitner.due_iso(99, now=base) == "2025-01-31 12:00:00"


# --- grade --------------------------------------------------------------


def test_grade_returns_tuple():
    from backend.services import leitner
    out = leitner.grade(1, "easy")
    assert isinstance(out, tuple)
    assert len(out) == 2


def test_grade_hard_always_returns_box_1():
    from backend.services import leitner
    assert leitner.grade(1, "hard")[0] == 1
    assert leitner.grade(3, "hard")[0] == 1
    assert leitner.grade(5, "hard")[0] == 1


def test_grade_easy_clamps_at_max_box():
    from backend.services import leitner
    assert leitner.grade(5, "easy")[0] == 5


def test_grade_non_string_grade_raises():
    from backend.services import leitner
    with pytest.raises(ValueError):
        leitner.grade(1, None)
    with pytest.raises(ValueError):
        leitner.grade(1, 1)


# --- is_due ------------------------------------------------------------


def test_is_due_invalid_format_treats_as_due():
    """Malformed next_due should not crash; it should surface as 'due'."""
    from backend.services import leitner
    assert leitner.is_due("not-a-date") is True


def test_is_due_empty_string_treated_as_due():
    from backend.services import leitner
    assert leitner.is_due("") is True


def test_is_due_at_exact_now_treated_as_due():
    """next_due <= now means due."""
    from backend.services import leitner
    now = _dt.datetime(2025, 6, 1, 12, 0, 0)
    assert leitner.is_due("2025-06-01 12:00:00", now=now) is True


def test_is_due_one_second_in_future_not_due():
    from backend.services import leitner
    now = _dt.datetime(2025, 6, 1, 12, 0, 0)
    assert leitner.is_due("2025-06-01 12:00:01", now=now) is False


# --- apply_grade -------------------------------------------------------


def test_apply_grade_unknown_vocab_raises(fresh):
    """apply_grade must surface LookupError when the vocab_id doesn't exist."""
    from backend.services import leitner
    from backend.db import get_conn
    with get_conn() as conn:
        with pytest.raises(LookupError):
            leitner.apply_grade(conn, vocab_id=9999, grade_value="easy",
                                user_id=1)


def test_apply_grade_promotes_box_in_db(fresh):
    from backend.services import leitner
    from backend.db import transaction, get_conn
    from backend.services import vocab as vocab_svc

    res = vocab_svc.add_vocab(user_id=1, language="en", word="x",
                              source="user", glossary="g")
    with get_conn() as conn:
        new_box, new_due = leitner.apply_grade(conn, vocab_id=res["id"],
                                                grade_value="easy",
                                                user_id=1)
    assert new_box == 2
    with transaction() as conn:
        row = conn.execute(
            "SELECT leitner_box FROM vocab_items WHERE id=?",
            (res["id"],),
        ).fetchone()
    assert row["leitner_box"] == 2


def test_apply_grade_respects_user_id(fresh):
    """apply_grade must not let user A grade user B's vocab row."""
    from backend.services import leitner
    from backend.db import get_conn
    from backend.services import vocab as vocab_svc

    vocab_svc.add_vocab(user_id=1, language="en", word="x", source="user",
                        glossary="g")
    with get_conn() as conn:
        with pytest.raises(LookupError):
            leitner.apply_grade(conn, vocab_id=1, grade_value="easy",
                                user_id=2)


def test_apply_grade_invalid_grade_raises(fresh):
    from backend.services import leitner
    from backend.db import get_conn
    from backend.services import vocab as vocab_svc

    vocab_svc.add_vocab(user_id=1, language="en", word="x", source="user",
                        glossary="g")
    with get_conn() as conn:
        with pytest.raises(ValueError):
            leitner.apply_grade(conn, vocab_id=1, grade_value="invalid",
                                user_id=1)


# --- count_by_box / count_due -----------------------------------------


def test_count_by_box_groups_correctly(fresh):
    from backend.services import vocab as vocab_svc
    from backend.services import leitner
    a = vocab_svc.add_vocab(user_id=1, language="en", word="a", source="user",
                            glossary="g")
    b = vocab_svc.add_vocab(user_id=1, language="en", word="b", source="user",
                            glossary="g")
    vocab_svc.add_vocab(user_id=1, language="en", word="c", source="user",
                        glossary="g")
    vocab_svc.set_box(user_id=1, vocab_id=a["id"], box=3)
    vocab_svc.set_box(user_id=1, vocab_id=b["id"], box=3)
    counts = leitner.count_by_box("en", 1)
    assert counts.get(1) == 1
    assert counts.get(3) == 2


def test_count_by_box_excludes_other_languages(fresh):
    from backend.services import vocab as vocab_svc
    from backend.services import leitner
    vocab_svc.add_vocab(user_id=1, language="en", word="a", source="user",
                        glossary="g")
    vocab_svc.add_vocab(user_id=1, language="fr", word="a", source="user",
                        glossary="g")
    counts = leitner.count_by_box("en", 1)
    assert sum(counts.values()) == 1


def test_count_due_excludes_future_items(fresh):
    from backend.services import vocab as vocab_svc
    from backend.services import leitner
    vocab_svc.add_vocab(user_id=1, language="en", word="a", source="user",
                        glossary="g")
    # Force the item to box 5 -> 30 days out
    vocab_svc.set_box(user_id=1, vocab_id=1, box=5)
    due = leitner.count_due("en", 1)
    assert due == 0


def test_count_due_includes_overdue(fresh):
    from backend.services import vocab as vocab_svc
    from backend.services import leitner
    from backend.db import transaction
    vocab_svc.add_vocab(user_id=1, language="en", word="a", source="user",
                        glossary="g")
    with transaction() as conn:
        conn.execute(
            "UPDATE vocab_items SET next_due='1999-01-01 00:00:00' WHERE id=1"
        )
    assert leitner.count_due("en", 1) == 1


def test_count_due_empty_language(fresh):
    from backend.services import leitner
    assert leitner.count_due("en", 1) == 0


# --- constants --------------------------------------------------------


def test_constants_invariant():
    from backend.services import leitner
    assert leitner.MIN_BOX == 1
    assert leitner.MAX_BOX == 5
    assert len(leitner.BOX_INTERVALS_DAYS) == leitner.MAX_BOX
