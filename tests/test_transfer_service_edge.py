"""Edge-case tests for the transfer service internals.

test_transfer.py covers the main service flows. This file pins the remaining
branches:

- ``_csv_value`` bool handling
- ``_fields_for`` structures / phrases / unknown table
- ``parse_import`` unknown format raises
- ``_parse_json`` default_lang fill
- ``_parse_csv`` empty body returns []
- ``_resolve_mapping`` header guess + coerce
- ``compute_merge`` missing key fields
- ``_index_existing`` structures / phrases
- ``_key_for_row`` structures / phrases
- ``apply_import`` error handling
- ``_apply_one`` unknown table raises
- ``_apply_vocab`` overwrite existing / builtin
- ``_apply_other_row`` overwrite existing user / add
- ``_as_int`` non-int
- ``_truthy`` int/float and string
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(clean_state):
    """Re-export of the autouse clean_state fixture."""
    return clean_state


# ---------- _csv_value ----------


def test_csv_value_bool(fresh):
    from backend.services import transfer as tr
    assert tr._csv_value(True) == "1"
    assert tr._csv_value(False) == "0"
    assert tr._csv_value(None) == ""
    assert tr._csv_value("x") == "x"


# ---------- _fields_for ----------


def test_fields_for_structures_and_phrases(fresh):
    from backend.services import transfer as tr
    assert tr._fields_for("structures") == tr.STRUCTURE_FIELDS
    assert tr._fields_for("phrases") == tr.PHRASE_FIELDS
    with pytest.raises(ValueError, match="unknown table"):
        tr._fields_for("banana")


# ---------- parse_import ----------


def test_parse_import_unknown_format(fresh):
    from backend.services import transfer as tr
    with pytest.raises(ValueError, match="unknown format"):
        tr.parse_import(text="x", format="xml", table="vocab")


def test_parse_json_default_lang_fills(fresh):
    from backend.services import transfer as tr
    rows = tr.parse_import(
        text='{"vocab": [{"word": "dog", "glossary": "g"}]}',
        format="json", table="vocab", default_lang="en",
    )
    assert rows[0]["language"] == "en"


def test_parse_csv_empty_body_returns_empty(fresh):
    from backend.services import transfer as tr
    assert tr.parse_import(text="", format="csv", table="vocab") == []


# ---------- _resolve_mapping ----------


def test_resolve_mapping_header_guess_and_coerce(fresh):
    from backend.services import transfer as tr
    fields = tr.VOCAB_FIELDS
    # Header guess maps 'word' -> word, 'glossary' -> glossary.
    mapping = tr._resolve_mapping(
        mapping=[], header_row=["word", "glossary"], canonical_fields=fields,
    )
    assert mapping["word"] == 0
    assert mapping["glossary"] == 1
    # Explicit mapping overrides the header guess.
    mapping2 = tr._resolve_mapping(
        mapping=[{"field": "word", "index": 1}],
        header_row=["glossary", "word"], canonical_fields=fields,
    )
    assert mapping2["word"] == 1


# ---------- compute_merge ----------


def test_compute_merge_missing_key_fields(fresh):
    """A row with a valid language but no word is invalid (caught by
    _validate_row before the key check)."""
    from backend.services import transfer as tr
    rows = [{"language": "en", "glossary": "g"}]  # no word
    out = tr.compute_merge(rows, user_id=1, table="vocab")
    assert out[0]["status"] == "invalid"
    assert out[0]["reason"] == "word missing or invalid"


# ---------- _index_existing / _key_for_row ----------


def test_index_existing_structures_and_phrases(fresh):
    from backend.services import transfer as tr
    from backend.db import transaction
    with transaction() as conn:
        conn.execute(
            "INSERT INTO structures (user_id, language, pattern, example_sentence,"
            " explanation, explanation_primary, explanation_secondary, source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'user')",
            (1, "en", "S V O", "x", "x", None, None),
        )
        conn.execute(
            "INSERT INTO phrases (user_id, language, phrase, example_sentence,"
            " explanation, explanation_primary, explanation_secondary, source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'user')",
            (1, "en", "Hi", "x", "x", None, None),
        )
    idx_s = tr._index_existing(1, "structures")
    assert ("structures", "en", "S V O") in idx_s
    idx_p = tr._index_existing(1, "phrases")
    assert ("phrases", "en", "Hi") in idx_p


def test_key_for_row_structures_and_phrases(fresh):
    from backend.services import transfer as tr
    assert tr._key_for_row({"language": "en", "pattern": "S V O"},
                           table="structures") == ("structures", "en", "S V O")
    assert tr._key_for_row({"language": "en", "phrase": "Hi"},
                           table="phrases") == ("phrases", "en", "Hi")
    # Empty pattern/phrase -> None.
    assert tr._key_for_row({"language": "en", "pattern": "  "},
                           table="structures") is None
    assert tr._key_for_row({"language": "en", "phrase": ""},
                           table="phrases") is None


# ---------- apply_import ----------


def test_apply_import_records_errors(fresh, monkeypatch):
    """If _apply_one raises, the error is recorded and counted."""
    from backend.services import transfer as tr

    def boom(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(tr, "_apply_one", boom)
    rows = [{"language": "en", "word": "dog", "glossary": "g"}]
    out = tr.apply_import(rows=rows, decisions=[{"index": 0, "action": "add"}],
                          user_id=1, table="vocab")
    assert out["errors"] == 1
    assert out["error_messages"] == ["row 0: boom"]


def test_apply_one_unknown_table_raises(fresh):
    from backend.services import transfer as tr
    with pytest.raises(ValueError, match="unknown table"):
        tr._apply_one(row={}, action="add", user_id=1, table="banana")


def test_apply_vocab_overwrite_existing_user(fresh):
    from backend.services import transfer as tr
    from backend.services import vocab as v
    v.add_vocab(user_id=1, language="es", word="casa", source="user",
                glossary="house")
    res = tr._apply_vocab(
        row={"language": "es", "word": "casa", "glossary": "home"},
        action="overwrite", user_id=1,
    )
    assert res["bucket"] == "overwritten"


def test_apply_vocab_overwrite_builtin_protected(fresh):
    from backend.services import transfer as tr
    from backend.db import transaction
    with transaction() as conn:
        conn.execute(
            "INSERT INTO vocab_items (user_id, language, word, source, glossary)"
            " VALUES (?, ?, ?, 'built-in', ?)",
            (1, "es", "casa", "house"),
        )
    res = tr._apply_vocab(
        row={"language": "es", "word": "casa", "glossary": "home"},
        action="overwrite", user_id=1,
    )
    assert res["bucket"] == "builtin_protected"


def test_apply_other_row_overwrite_existing_user(fresh):
    from backend.services import transfer as tr
    from backend.db import transaction
    with transaction() as conn:
        conn.execute(
            "INSERT INTO structures (user_id, language, pattern, example_sentence,"
            " explanation, explanation_primary, explanation_secondary, source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'user')",
            (1, "en", "S V O", "old", "old", None, None),
        )
    res = tr._apply_other_row(
        row={"language": "en", "pattern": "S V O", "example_sentence": "new",
             "explanation": "new", "familiar": True},
        action="overwrite", user_id=1, table="structures",
    )
    assert res["bucket"] == "overwritten"
    from backend.db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT example_sentence, familiar FROM structures WHERE pattern='S V O'"
        ).fetchone()
    assert row["example_sentence"] == "new"
    assert row["familiar"] == 1


def test_apply_other_row_add(fresh):
    from backend.services import transfer as tr
    res = tr._apply_other_row(
        row={"language": "en", "phrase": "Hi", "example_sentence": "x",
             "explanation": "y"},
        action="add", user_id=1, table="phrases",
    )
    assert res["bucket"] == "added"
    assert res["id"] is not None


# ---------- _as_int / _truthy ----------


def test_as_int_non_int_returns_default(fresh):
    from backend.services import transfer as tr
    assert tr._as_int("abc", default=1) == 1
    assert tr._as_int(None, default=1) == 1
    assert tr._as_int("", default=1) == 1
    assert tr._as_int("5", default=1) == 5


def test_truthy_variants(fresh):
    from backend.services import transfer as tr
    assert tr._truthy(1) is True
    assert tr._truthy(0) is False
    assert tr._truthy(1.5) is True
    assert tr._truthy("yes") is True
    assert tr._truthy("off") is False
    assert tr._truthy(True) is True
    assert tr._truthy(None) is False


# ---------- more _resolve_mapping / _key_for_row / _index_existing ----------


def test_resolve_mapping_skips_invalid_entries(fresh):
    """Mapping entries with a non-canonical field or a bad index are skipped."""
    from backend.services import transfer as tr
    fields = tr.VOCAB_FIELDS
    mapping = tr._resolve_mapping(
        mapping=[
            {"field": "not_a_field", "index": 0},  # invalid field
            {"field": "word", "index": -1},        # negative index
            {"field": "word", "index": "nope"},    # unresolvable index
        ],
        header_row=None, canonical_fields=fields,
    )
    assert "word" not in mapping


def test_index_existing_unknown_table_raises(fresh):
    from backend.services import transfer as tr
    with pytest.raises(ValueError, match="unknown table"):
        tr._index_existing(1, "banana")


def test_key_for_row_unknown_table_returns_none(fresh):
    from backend.services import transfer as tr
    assert tr._key_for_row({"language": "en"}, table="banana") is None


def test_apply_vocab_overwrite_missing_becomes_add(fresh):
    """An overwrite decision against a non-existent row becomes an add."""
    from backend.services import transfer as tr
    res = tr._apply_vocab(
        row={"language": "es", "word": "perro", "glossary": "dog"},
        action="overwrite", user_id=1,
    )
    assert res["bucket"] == "added"


def test_apply_other_row_overwrite_missing_becomes_add(fresh):
    from backend.services import transfer as tr
    res = tr._apply_other_row(
        row={"language": "en", "phrase": "Hi", "example_sentence": "x",
             "explanation": "y"},
        action="overwrite", user_id=1, table="phrases",
    )
    assert res["bucket"] == "added"


def test_apply_other_row_unknown_action_skipped(fresh):
    from backend.services import transfer as tr
    res = tr._apply_other_row(
        row={"language": "en", "phrase": "Hi", "example_sentence": "x",
             "explanation": "y"},
        action="bogus", user_id=1, table="phrases",
    )
    assert res["bucket"] == "skipped"
