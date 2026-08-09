"""Tests for the vocab service."""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    from backend import db
    db.init_schema()
    return tmp_path


def _seed_vocab(fresh, *, word="casa", lang="es", glossary="house"):
    from backend.services import vocab as v
    return v.add_vocab(
        user_id=1, language=lang, word=word, source="wordnet",
        glossary=glossary, pos="noun",
    )


def test_add_vocab_creates_row(fresh):
    res = _seed_vocab(fresh)
    assert res["created"] is True
    assert res["word"] == "casa"


def test_add_vocab_dedupes(fresh):
    _seed_vocab(fresh)
    res = _seed_vocab(fresh)
    assert res["created"] is False


def test_list_vocab_paginated(fresh):
    for w in ["a", "b", "c"]:
        _seed_vocab(fresh, word=w)
    from backend.services import vocab as v
    items = v.list_vocab(user_id=1, language="es", limit=2)
    assert len(items) == 2


def test_delete_and_restore(fresh):
    res = _seed_vocab(fresh)
    vocab_id = res["id"]
    from backend.services import vocab as v
    del_res = v.delete_vocab(user_id=1, vocab_id=vocab_id)
    assert del_res["deleted_id"] == vocab_id
    assert "undo_token" in del_res
    items = v.list_vocab(user_id=1, language="es")
    assert items == []
    restored = v.restore_vocab(user_id=1, undo_token=del_res["undo_token"])
    assert restored["restored_id"] == vocab_id
    items = v.list_vocab(user_id=1, language="es")
    assert len(items) == 1


def test_review_next_returns_due(fresh):
    res = _seed_vocab(fresh, word="alpha")
    _seed_vocab(fresh, word="beta")
    from backend.services import vocab as v
    items = v.review_next(user_id=1, language="es", n=10)
    assert len(items) == 2
    assert items[0]["word"] in ("alpha", "beta")


def test_apply_grade_promotes(fresh):
    res = _seed_vocab(fresh)
    from backend.services import vocab as v
    out = v.apply_review_grade(user_id=1, vocab_id=res["id"], grade_value="easy")
    assert out["leitner_box"] == 2
    out = v.apply_review_grade(user_id=1, vocab_id=res["id"], grade_value="hard")
    assert out["leitner_box"] == 1


def test_apply_grade_invalid(fresh):
    res = _seed_vocab(fresh)
    from backend.services import vocab as v
    with pytest.raises(ValueError):
        v.apply_review_grade(user_id=1, vocab_id=res["id"], grade_value="ok")


def test_auto_add_from_lookup(fresh):
    from backend.services.dictionaries.base import Definition, Sense, WordEntry
    from backend.services import vocab as v

    entry = WordEntry(
        word="gato", language="es", source="wordnet",
        senses=[Sense(pos="noun",
                      definitions=[Definition(glossary="cat", example="Mi gato.")],
                      explanations={"primary": "cat", "secondary": "猫"})],
    )
    added = v.auto_add_from_lookup(user_id=1, entry=entry, auto_add_enabled=True)
    assert added is True
    items = v.list_vocab(user_id=1, language="es")
    assert any(i["word"] == "gato" for i in items)


def test_auto_add_disabled(fresh):
    from backend.services.dictionaries.base import Definition, Sense, WordEntry
    from backend.services import vocab as v

    entry = WordEntry(
        word="gato", language="es", source="wordnet",
        senses=[Sense(pos="noun",
                      definitions=[Definition(glossary="cat")])],
    )
    added = v.auto_add_from_lookup(user_id=1, entry=entry, auto_add_enabled=False)
    assert added is False
    items = v.list_vocab(user_id=1, language="es")
    assert items == []


def test_auto_add_multi_sense_creates_one_row(fresh):
    """Regression: a multi-sense lookup must produce one vocab row, not one per sense.

    Otherwise the review session shows the same word N times in a row, which
    defeats spaced repetition.
    """
    from backend.services.dictionaries.base import Definition, Sense, WordEntry
    from backend.services import vocab as v

    entry = WordEntry(
        word="test", language="en", source="wordnet",
        senses=[
            Sense(pos="noun", definitions=[Definition(glossary="a procedure",
                                                     example="run a test")]),
            Sense(pos="verb", definitions=[Definition(glossary="to evaluate",
                                                     example="test the water")]),
            Sense(pos="noun", definitions=[Definition(glossary="an exam",
                                                     example="take a test")]),
        ],
    )
    added = v.auto_add_from_lookup(user_id=1, entry=entry, auto_add_enabled=True)
    assert added is True
    items = v.list_vocab(user_id=1, language="en")
    assert len(items) == 1, f"expected 1 vocab row, got {len(items)}"
    assert items[0]["word"] == "test"

    next_items = v.review_next(user_id=1, language="en", n=10)
    assert len(next_items) == 1


def test_auto_add_re_lookup_updates_in_place(fresh):
    """Looking up the same word again must update the row, not create a duplicate."""
    from backend.services.dictionaries.base import Definition, Sense, WordEntry
    from backend.services import vocab as v

    first = WordEntry(
        word="test", language="en", source="wordnet",
        senses=[Sense(pos="noun", definitions=[Definition(glossary="old gloss")])],
    )
    v.auto_add_from_lookup(user_id=1, entry=first, auto_add_enabled=True)

    second = WordEntry(
        word="test", language="en", source="llm",
        senses=[Sense(pos="noun", definitions=[Definition(glossary="new gloss",
                                                         example="a fresh example")])],
    )
    res = v.auto_add_from_lookup(user_id=1, entry=second, auto_add_enabled=True)
    assert res is False  # updated, not created

    items = v.list_vocab(user_id=1, language="en")
    assert len(items) == 1
    assert items[0]["glossary"] == "new gloss"
    assert items[0]["source"] == "llm"


def test_review_status(fresh):
    _seed_vocab(fresh, word="a")
    _seed_vocab(fresh, word="b")
    from backend.services import vocab as v
    st = v.review_status(user_id=1, language="es")
    assert st["due"] == 2
    assert st["by_box"][1] == 2