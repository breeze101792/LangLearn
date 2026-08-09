"""Tests for backend/util.py helpers."""

from __future__ import annotations

import pytest

from backend.util import (
    LANG_CODE_RE,
    WORD_RE,
    clamp_int,
    err,
    is_known_lang,
    is_nonempty_str,
    is_valid_lang,
    is_word,
    normalize_word,
    ok,
    safe_path,
)


# --- ok / err -------------------------------------------------------------


def test_ok_envelope():
    assert ok({"a": 1}) == {"ok": True, "data": {"a": 1}}


def test_ok_with_list():
    assert ok([1, 2]) == {"ok": True, "data": [1, 2]}


def test_ok_with_none():
    assert ok(None) == {"ok": True, "data": None}


def test_err_envelope_with_code():
    assert err("bad", code="bad_code") == {"ok": False, "error": "bad", "code": "bad_code"}


def test_err_envelope_without_code():
    assert err("bad") == {"ok": False, "error": "bad"}


# --- is_valid_lang --------------------------------------------------------


@pytest.mark.parametrize("code,expected", [
    ("en", True),
    ("es", True),
    ("zh", True),
    ("a" * 8, True),
    ("e", False),          # too short
    ("eng123", False),     # digits not allowed
    ("en-US", False),      # hyphen not allowed
    ("EN", False),         # uppercase
    ("", False),
    (None, False),
    (123, False),
    (["en"], False),
])
def test_is_valid_lang(code, expected):
    assert is_valid_lang(code) is expected


# --- is_known_lang --------------------------------------------------------


def test_is_known_lang_accepts_catalog_codes():
    assert is_known_lang("en") is True
    assert is_known_lang("es") is True
    assert is_known_lang("ja") is True


def test_is_known_lang_rejects_unknown_code():
    assert is_known_lang("klingon") is False


def test_is_known_lang_rejects_invalid_shape():
    assert is_known_lang("ENG") is False
    assert is_known_lang(None) is False


# --- is_word --------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("dog", True),
    ("don't", True),
    ("snap_at", True),
    ("hello-world", True),
    ("a.b.c", True),
    ("a", True),
    ("", False),
    ("   ", False),
    ("   dog   ", True),  # strip()'d internally
    ("a" * 200, True),
    ("a" * 201, False),
    ("!@#", False),          # punctuation not allowed
    (None, False),
    (123, False),
    (["dog"], False),
])
def test_is_word(value, expected):
    assert is_word(value) is expected


# --- normalize_word -------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("hello", "hello"),
    ("  hello  ", "hello"),
    ("snap at", "snap_at"),
    ("  snap   at  ", "snap_at"),
    ("don't snap", "don't_snap"),
    ("snap-at", "snap-at"),
    ("", ""),
])
def test_normalize_word(raw, expected):
    assert normalize_word(raw) == expected


def test_normalize_word_truncates_to_200():
    long_word = "a" * 300
    assert len(normalize_word(long_word)) == 200


def test_normalize_word_non_string_returns_empty():
    assert normalize_word(None) == ""
    assert normalize_word(123) == ""
    assert normalize_word([]) == ""


# --- is_nonempty_str ------------------------------------------------------


def test_is_nonempty_str_basic():
    assert is_nonempty_str("hi") is True
    assert is_nonempty_str("") is False
    assert is_nonempty_str(None) is False
    assert is_nonempty_str(123) is False


def test_is_nonempty_str_respects_max_len():
    assert is_nonempty_str("a" * 1000, max_len=1000) is True
    assert is_nonempty_str("a" * 1001, max_len=1000) is False


# --- clamp_int ------------------------------------------------------------


def test_clamp_int_within_range():
    assert clamp_int(5, 1, 10) == 5


def test_clamp_int_below_low():
    assert clamp_int(-5, 0, 10) == 0


def test_clamp_int_above_high():
    assert clamp_int(99, 1, 10) == 10


def test_clamp_int_with_string_input():
    assert clamp_int("7", 1, 10) == 7


def test_clamp_int_with_invalid_string():
    assert clamp_int("abc", 1, 10) is None


def test_clamp_int_with_none():
    assert clamp_int(None, 1, 10) is None


def test_clamp_int_with_float_string_returns_none():
    # int() rejects non-integer strings; clamp_int surfaces None instead of
    # silently truncating so the caller knows the value was malformed.
    assert clamp_int("3.5", 0, 10) is None


def test_clamp_int_with_int_string_truncates_via_int():
    # Pure integer strings are accepted and clamped to the bounds.
    assert clamp_int("7", 1, 10) == 7
    assert clamp_int("0", 1, 10) == 1
    assert clamp_int("99", 1, 10) == 10


# --- safe_path ------------------------------------------------------------


def test_safe_path_returns_resolved_path(tmp_path):
    p = safe_path(tmp_path, "sub", "file.txt")
    assert p.is_absolute()
    assert p.parent.name == "sub"
    assert p.name == "file.txt"


def test_safe_path_blocks_traversal_outside_base(tmp_path):
    with pytest.raises(ValueError, match="traversal"):
        safe_path(tmp_path, "..", "outside.txt")


def test_safe_path_blocks_nested_traversal(tmp_path):
    with pytest.raises(ValueError, match="traversal"):
        safe_path(tmp_path, "a", "..", "..", "evil")


# --- regex sanity ---------------------------------------------------------


def test_lang_code_regex_rejects_uppercase():
    assert LANG_CODE_RE.match("EN") is None


def test_word_regex_matches_ascii():
    assert WORD_RE.match("hello world") is not None
    assert WORD_RE.match("don't") is not None
