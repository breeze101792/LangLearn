"""Edge-case tests for the wordnet provider and suggest service.

test_dictionary.py / test_suggest.py cover the main flows. This file pins
the remaining branches:

- wordnet: ``_wn()`` returns None when nltk is missing / download fails
- wordnet: ``lookup`` returns empty when WordNet is unavailable
- suggest: ``_wordnet_lemma_set`` returns empty when WordNet is unavailable
- suggest: ``warmup`` returns empty when the lemma set fails
- suggest: ``_vocab_candidates`` swallows DB errors
- suggest: ``fuzzy`` with no window / no scored matches returns []
- suggest: ``_levenshtein`` boundary cases
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(clean_state):
    from backend.services.dictionaries import registry
    registry.bootstrap()
    return clean_state


# ---------- wordnet ----------


def test_wordnet_wn_returns_none_when_nltk_missing(fresh, monkeypatch):
    """If nltk can't be imported, _wn() returns None (fail-soft)."""
    from backend.services.dictionaries import wordnet
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "nltk.corpus":
            raise ImportError("no nltk")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    wordnet._wn.cache_clear()
    assert wordnet._wn() is None
    wordnet._wn.cache_clear()


def test_wordnet_lookup_empty_when_unavailable(fresh, monkeypatch):
    """When WordNet is unavailable, lookup returns an empty entry."""
    from backend.services.dictionaries import wordnet
    monkeypatch.setattr(wordnet, "_wn", lambda: None)
    entry = wordnet.lookup("dog", "en")
    assert entry.is_empty


def test_wordnet_wn_download_failure_returns_none(fresh, monkeypatch):
    """If ensure_loaded raises LookupError and the download fails, _wn()
    returns None."""
    from backend.services.dictionaries import wordnet
    import builtins
    import nltk

    class _FakeWN:
        def ensure_loaded(self):
            raise LookupError("corpus not found")

    real_import = builtins.__import__
    fake_wn = _FakeWN()

    def fake_import(name, *a, **kw):
        if name == "nltk.corpus":
            mod = real_import(name, *a, **kw)
            mod.wordnet = fake_wn
            return mod
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    def fake_download(*a, **kw):
        raise RuntimeError("download failed")
    monkeypatch.setattr(nltk, "download", fake_download)
    wordnet._wn.cache_clear()
    assert wordnet._wn() is None
    wordnet._wn.cache_clear()


# ---------- suggest ----------


def test_suggest_lemma_set_empty_when_wordnet_unavailable(fresh, monkeypatch):
    from backend.services.dictionaries import suggest
    monkeypatch.setattr(suggest.wordnet_provider, "_wn", lambda: None)
    suggest._wordnet_lemma_set.cache_clear()
    assert suggest._wordnet_lemma_set() == frozenset()
    suggest._wordnet_lemma_set.cache_clear()


def test_suggest_warmup_returns_empty_on_failure(fresh, monkeypatch):
    from backend.services.dictionaries import suggest

    def boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(suggest, "_wordnet_lemma_set", boom)
    assert suggest.warmup() == frozenset()


def test_suggest_vocab_candidates_swallows_db_error(fresh, monkeypatch):
    from backend.services.dictionaries import suggest
    from backend import db

    def boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "get_conn", boom)
    assert suggest._vocab_candidates(1, "es") == []


def test_suggest_fuzzy_no_window_returns_empty(fresh):
    """A query whose first-letter/length window has no candidates returns []."""
    from backend.services.dictionaries import suggest
    # 'zzzzz' has no candidates starting with 'z' in the pool.
    assert suggest.fuzzy("es", 1, "zzzzz", limit=5) == []


def test_suggest_fuzzy_no_scored_returns_empty(fresh):
    """When no candidate is within the max distance, fuzzy returns []."""
    from backend.services.dictionaries import suggest
    # 'zzzzzzzz' (8 chars) has max_d=3; no 'z' words exist, so no matches.
    assert suggest.fuzzy("es", 1, "zzzzzzzz", limit=5) == []


def test_levenshtein_boundary_cases(fresh):
    from backend.services.dictionaries import suggest
    assert suggest._levenshtein("a", "a", max_distance=1) == 0
    assert suggest._levenshtein("", "abc", max_distance=3) == 3
    assert suggest._levenshtein("abc", "", max_distance=3) == 3
    # Length difference exceeds max_distance -> None.
    assert suggest._levenshtein("a", "abcdef", max_distance=2) is None
    # Distance exceeds max_distance -> None.
    assert suggest._levenshtein("abc", "xyz", max_distance=1) is None
