"""Tests for the suggest module's private helpers and edge cases.

test_suggest.py covers the public prefix/fuzzy/warmup API. This file
pins the internal `_levenshtein`, `_fuzzy_window`, `_display_words`,
`_shortest_first` helpers, plus the distance-threshold behavior of
fuzzy() at different query lengths.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(clean_state):
    from backend.services.dictionaries import registry
    registry.bootstrap()
    return clean_state


# --- _display_words -----------------------------------------------------


def test_display_words_replaces_underscore(fresh):
    from backend.services.dictionaries import suggest
    assert suggest._display_words(["snap_at"]) == ["snap at"]


def test_display_words_handles_multiple(fresh):
    from backend.services.dictionaries import suggest
    assert suggest._display_words(["a_b", "c_d", "e"]) == ["a b", "c d", "e"]


def test_display_words_empty(fresh):
    from backend.services.dictionaries import suggest
    assert suggest._display_words([]) == []


# --- _shortest_first ---------------------------------------------------


def test_shortest_first_puts_shorter_first(fresh):
    from backend.services.dictionaries import suggest
    # 'ape' (3 chars) sorts before 'app' (3 chars) alphabetically because 'e' < 'p'.
    out = suggest._shortest_first(["banana", "app", "ape", "a"])
    assert out == ["a", "ape", "app", "banana"]


def test_shortest_first_alphabetical_tiebreak(fresh):
    from backend.services.dictionaries import suggest
    out = suggest._shortest_first(["c", "a", "b"])
    assert out == ["a", "b", "c"]


def test_shortest_first_empty(fresh):
    from backend.services.dictionaries import suggest
    assert suggest._shortest_first([]) == []


# --- _levenshtein -------------------------------------------------------


def test_levenshtein_identical_strings(fresh):
    from backend.services.dictionaries import suggest
    assert suggest._levenshtein("dog", "dog", max_distance=2) == 0


def test_levenshtein_one_substitution(fresh):
    from backend.services.dictionaries import suggest
    assert suggest._levenshtein("dog", "dig", max_distance=2) == 1


def test_levenshtein_one_insertion(fresh):
    from backend.services.dictionaries import suggest
    assert suggest._levenshtein("dog", "dogs", max_distance=2) == 1


def test_levenshtein_one_deletion(fresh):
    from backend.services.dictionaries import suggest
    assert suggest._levenshtein("dogs", "dog", max_distance=2) == 1


def test_levenshtein_length_diff_exceeds_max(fresh):
    from backend.services.dictionaries import suggest
    # |len(a)-len(b)| = 5 > max_distance=2
    assert suggest._levenshtein("a", "xxxxx", max_distance=2) is None


def test_levenshtein_above_max_returns_none(fresh):
    """Even if lengths match, distance > max must return None."""
    from backend.services.dictionaries import suggest
    # "abc" vs "xyz" is distance 3
    assert suggest._levenshtein("abc", "xyz", max_distance=2) is None


def test_levenshtein_empty_a(fresh):
    from backend.services.dictionaries import suggest
    assert suggest._levenshtein("", "abc", max_distance=5) == 3


def test_levenshtein_empty_b(fresh):
    from backend.services.dictionaries import suggest
    assert suggest._levenshtein("abc", "", max_distance=5) == 3


# --- _fuzzy_window ------------------------------------------------------


def test_fuzzy_window_first_letter_filter(fresh):
    from backend.services.dictionaries import suggest
    pool = ["dog", "cat", "dig", "duck"]
    out = suggest._fuzzy_window("dog", pool)
    assert all(w.startswith("d") for w in out)


def test_fuzzy_window_length_window(fresh):
    from backend.services.dictionaries import suggest
    # Query "dog" (3 chars), window ±2 -> len 1..5
    pool = ["d", "do", "dog", "dogs", "doggy", "dogsssss"]
    out = suggest._fuzzy_window("dog", pool)
    assert "d" in out
    assert "dog" in out
    assert "dogs" in out
    assert "doggy" in out
    assert "dogsssss" not in out


# --- fuzzy() distance-threshold branches ------------------------------


def test_fuzzy_short_query_returns_empty(fresh):
    from backend.services.dictionaries import suggest
    # min_query_len = 3 -> 1- and 2-char queries return [].
    assert suggest.fuzzy("en", 1, "") == []
    assert suggest.fuzzy("en", 1, "a") == []
    assert suggest.fuzzy("en", 1, "do") == []


def test_fuzzy_four_char_query_distance_threshold_one(fresh):
    """For len(query) <= 4, max_d=1. 'doog' (1 substitution away from
    'dog') must appear; 'dgoose' (1 away from 'goose'? no, 2) must not."""
    from backend.services.dictionaries import suggest
    out = suggest.fuzzy("en", 1, "doog", limit=20)
    assert "dog" in out
    # distance(dog, doggy) = 3 -> not within max_d=1
    assert "doggy" not in out


def test_fuzzy_seven_char_query_distance_threshold_two(fresh):
    """For 5-7 char queries, max_d=2."""
    from backend.services.dictionaries import suggest
    # 'sitting' is 1 from 'sittin' and 3 from 'sitting'... let's verify
    # with something WordNet definitely has.
    out = suggest.fuzzy("en", 1, "sittin", limit=20)
    # 'sitting' differs at position 5 only -> distance 1
    assert "sitting" in out


def test_fuzzy_excludes_self(fresh):
    """The query itself (if it's a candidate) must not appear in the
    results — they're meant to suggest alternatives."""
    from backend.services.dictionaries import suggest
    # 'apple' is in WordNet
    out = suggest.fuzzy("en", 1, "apple", limit=20)
    assert "apple" not in out


def test_fuzzy_limits_result_size(fresh):
    from backend.services.dictionaries import suggest
    out = suggest.fuzzy("en", 1, "do", limit=0)  # 0 clamps to >=1
    # Should still return at most MAX_LIMIT (25) but the limit=0 clamps to 1.
    assert len(out) <= 25


def test_fuzzy_returns_display_words_not_underscores(fresh):
    from backend.services.dictionaries import suggest
    out = suggest.fuzzy("en", 1, "snap_at", limit=20)
    assert all("_" not in w for w in out)


# --- prefix() edge cases -----------------------------------------------


def test_prefix_returns_display_words(fresh):
    from backend.services.dictionaries import suggest
    out = suggest.prefix("en", 1, "app", limit=10)
    assert "apple" in out
    assert all("_" not in w for w in out)


def test_prefix_normalizes_query_spaces(fresh):
    from backend.services.dictionaries import suggest
    # WordNet indexes multi-word lemmas with underscores; the prefix
    # normalizer must also convert so 'snap at' matches 'snap_at'.
    out = suggest.prefix("en", 1, "snap at", limit=10)
    assert "snap at" in out


def test_prefix_clamps_limit_to_max(fresh):
    """Passing a huge limit must clamp to MAX_LIMIT."""
    from backend.services.dictionaries import suggest
    out = suggest.prefix("en", 1, "a", limit=99999)
    assert len(out) <= suggest.MAX_LIMIT


def test_prefix_clamps_negative_limit_to_one(fresh):
    from backend.services.dictionaries import suggest
    out = suggest.prefix("en", 1, "a", limit=-5)
    # Just verify it doesn't crash and returns at most one entry.
    assert len(out) <= 1


def test_prefix_no_matches_returns_empty(fresh):
    from backend.services.dictionaries import suggest
    assert suggest.prefix("en", 1, "zzzzzzz", limit=8) == []


# --- warmup edge cases ----------------------------------------------


def test_warmup_returns_frozenset(fresh):
    from backend.services.dictionaries import suggest
    out = suggest.warmup()
    assert isinstance(out, frozenset)


def test_warmup_caches_result(fresh):
    """Calling warmup() repeatedly returns the same cached object."""
    from backend.services.dictionaries import suggest
    out1 = suggest.warmup()
    out2 = suggest.warmup()
    assert out1 is out2


# --- _vocab_candidates / _candidates --------------------------------


def test_candidates_for_non_en_uses_only_vocab(fresh):
    """For non-English, only the user's vocab contributes (no WordNet)."""
    from backend.db import transaction
    from backend.services.dictionaries import suggest

    with transaction() as conn:
        conn.execute(
            "INSERT INTO vocab_items (user_id, language, word, source, glossary) "
            "VALUES (1, 'fr', 'maison', 'llm', 'g')"
        )
    pool = suggest._candidates("fr", 1)
    assert "maison" in pool


def test_candidates_dedupe_case_insensitive(fresh):
    """Vocab words that differ only in case must not appear twice."""
    from backend.db import transaction
    from backend.services.dictionaries import suggest

    with transaction() as conn:
        conn.execute(
            "INSERT INTO vocab_items (user_id, language, word, source, glossary) "
            "VALUES (1, 'fr', 'Maison', 'llm', 'g')"
        )
        conn.execute(
            "INSERT INTO vocab_items (user_id, language, word, source, glossary) "
            "VALUES (1, 'fr', 'MAISON', 'llm', 'g')"
        )
    pool = suggest._candidates("fr", 1)
    assert pool.count("maison") == 1


def test_candidates_returns_sorted(fresh):
    from backend.services.dictionaries import suggest
    pool = suggest._candidates("en", 1)
    assert pool == sorted(pool)


def test_vocab_candidates_empty_db(fresh):
    from backend.services.dictionaries import suggest
    assert suggest._vocab_candidates(1, "en") == []
