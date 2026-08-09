"""Prefix and fuzzy word suggestions for the dictionary search box.

Used by:
  * `GET /api/dictionary/suggest`  — live dropdown while typing.
  * `POST /api/dictionary/lookup`   — "did you mean …" when the lookup returns
                                      no entry.

Candidate sources
-----------------
For English (`lang == "en"`), the primary source is the WordNet lemma list
(loaded lazily on first call and cached). For any language, the user's own
vocab words are unioned in so previously looked-up words can be suggested
even when no dictionary provider has a word list (e.g. Spanish).

Fuzzy ranking uses a small bounded Levenshtein distance so we never scan
the full lemma list. The candidate set is restricted by first-letter match
plus a length window of ±2 chars, which keeps latency in single-digit ms.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Iterable

from ...util import normalize_word
from . import wordnet as wordnet_provider

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 8
MAX_LIMIT = 25
MIN_QUERY_LEN_FOR_FUZZY = 3
FUZZY_CANDIDATE_LENGTH_WINDOW = 2


@lru_cache(maxsize=1)
def _wordnet_lemma_set() -> frozenset[str]:
    """All WordNet lemma names, lowercased and de-duped.

    Cached for process lifetime; WordNet does not change at runtime.
    Returns an empty set if NLTK / WordNet is unavailable.
    """
    wn = wordnet_provider._wn()
    if wn is None:
        return frozenset()
    try:
        return frozenset(n.lower() for n in wn.all_lemma_names())
    except Exception as e:  # noqa: BLE001
        log.warning("failed to enumerate WordNet lemmas: %s", e)
        return frozenset()


def _vocab_candidates(user_id: int, lang: str) -> list[str]:
    """Distinct, lowercased words in the user's vocab for `lang`."""
    from ...db import get_conn
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT word FROM vocab_items WHERE user_id=? AND language=?",
                (user_id, lang),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        log.warning("failed to load vocab candidates for %s/%s: %s", lang, user_id, e)
        return []
    return [r["word"].lower() for r in rows if r["word"]]


def _candidates(lang: str, user_id: int) -> list[str]:
    """Union of WordNet lemmas (en only) and the user's vocab words."""
    out: set[str] = set()
    if lang == "en":
        out.update(_wordnet_lemma_set())
    out.update(_vocab_candidates(user_id, lang))
    if not out:
        return []
    return sorted(out)


def prefix(lang: str, user_id: int, query: str, limit: int = DEFAULT_LIMIT) -> list[str]:
    """Return up to `limit` candidates that start with `query` (case-insensitive).

    The query is normalized via `util.normalize_word` so spaces become
    underscores, matching the convention used by the dictionary lookup.
    """
    q = normalize_word(query).lower()
    if not q:
        return []
    pool = _candidates(lang, user_id)
    if not pool:
        return []
    matches = [w for w in pool if w.startswith(q)]
    if not matches:
        return []
    return _shortest_first(matches)[: max(1, min(limit, MAX_LIMIT))]


def _shortest_first(words: list[str]) -> list[str]:
    """Shorter words first, then alphabetical. Helps 'app' surface 'app' over 'appalachicola'."""
    return sorted(words, key=lambda w: (len(w), w))


def _levenshtein(a: str, b: str, *, max_distance: int) -> int | None:
    """Bounded Levenshtein. Returns None if distance > max_distance."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > max_distance:
        return None
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        curr = [i] + [0] * lb
        row_min = curr[0]
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            curr[j] = min(
                curr[j - 1] + 1,        # insertion
                prev[j] + 1,            # deletion
                prev[j - 1] + cost,     # substitution
            )
            if curr[j] < row_min:
                row_min = curr[j]
        if row_min > max_distance:
            return None
        prev = curr
    return prev[lb] if prev[lb] <= max_distance else None


def _fuzzy_window(query: str, pool: Iterable[str]) -> list[str]:
    """Pre-filter pool by first letter + length window before distance calc."""
    q_first = query[0]
    q_len = len(query)
    lo = q_len - FUZZY_CANDIDATE_LENGTH_WINDOW
    hi = q_len + FUZZY_CANDIDATE_LENGTH_WINDOW
    return [
        w for w in pool
        if w[0] == q_first and lo <= len(w) <= hi
    ]


def fuzzy(lang: str, user_id: int, query: str, limit: int = DEFAULT_LIMIT) -> list[str]:
    """Return up to `limit` close matches for `query`, best (lowest distance) first.

    Returns [] for queries shorter than `MIN_QUERY_LEN_FOR_FUZZY` to avoid noisy
    suggestions on very short input.
    """
    q = normalize_word(query).lower()
    if len(q) < MIN_QUERY_LEN_FOR_FUZZY:
        return []
    pool = _candidates(lang, user_id)
    if not pool:
        return []
    # Max distance: 1 for <=4 chars, 2 for 5-7 chars, 3 for 8+.
    if len(q) <= 4:
        max_d = 1
    elif len(q) <= 7:
        max_d = 2
    else:
        max_d = 3
    window = _fuzzy_window(q, pool)
    if not window:
        return []
    scored: list[tuple[int, int, str]] = []
    for w in window:
        d = _levenshtein(q, w, max_distance=max_d)
        if d is None:
            continue
        scored.append((d, len(w), w))
    if not scored:
        return []
    scored.sort()
    out: list[str] = []
    for d, _length, w in scored:
        if w == q:
            continue
        out.append(w)
        if len(out) >= max(1, min(limit, MAX_LIMIT)):
            break
    return out
