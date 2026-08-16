"""Vocab service: CRUD over vocab_items + auto-add on lookup."""

from __future__ import annotations

import logging
import random
import sqlite3
import threading
import time
from collections import OrderedDict
from typing import Any

from .. import config
from ..db import get_conn, transaction
from ..util import is_valid_lang, normalize_word
from . import leitner

log = logging.getLogger(__name__)


# In-memory undo tokens. Kept in memory only — a server restart clears them.
_undo_tokens: "OrderedDict[str, dict]" = OrderedDict()
_undo_lock = threading.Lock()
UNDO_TTL_SECONDS = 5


def add_vocab(*, user_id: int, language: str, word: str, source: str,
              sense_idx: int = 0, pos: str | None = None,
              glossary: str = "", example: str | None = None,
              explanation_primary: str | None = None,
              explanation_secondary: str | None = None,
              leitner_box: int | None = None,
              next_due: str | None = None,
              added_at: str | None = None,
              auto_add: bool = True) -> dict:
    if not is_valid_lang(language):
        raise ValueError("invalid language")
    if not isinstance(word, str) or not word.strip():
        raise ValueError("word required")
    if not isinstance(glossary, str) or not glossary.strip():
        raise ValueError("glossary required")
    if source not in ("wordnet", "llm", "user"):
        raise ValueError("invalid source")
    # Normalize multi-word input: "snap at" -> "snap_at". The dictionary
    # provider indexes multi-word lemmas with underscores (WordNet's
    # convention), and the vocab lookup normalizes the same way, so storing
    # the normalized form keeps lookups round-trippable in either direction.
    word = normalize_word(word)[:200]
    if not word:
        raise ValueError("word required")
    glossary = glossary.strip()[:1000]
    if example is not None:
        example = example.strip()[:1000] or None
    if pos is not None:
        pos = pos.strip()[:32] or None
    if explanation_primary is not None:
        explanation_primary = explanation_primary.strip()[:1000] or None
    if explanation_secondary is not None:
        explanation_secondary = explanation_secondary.strip()[:1000] or None
    sense_idx = max(0, int(sense_idx))
    # leitner_box/next_due/added_at are only used on insert (imports carry
    # history). On update they are intentionally preserved — re-looking-up a
    # word must not reset the user's spaced-repetition schedule.
    # The default for insert_due preserves the original ``datetime('now')``
    # behaviour so freshly added rows are reviewable immediately. Imports
    # that supply a custom box without next_due get a box-based cadence.
    insert_box = 1 if leitner_box is None else _clamp_leitner(leitner_box)
    explicit_schedule = leitner_box is not None or next_due is not None
    if next_due is not None:
        insert_due = str(next_due).strip()[:32] or None
    elif explicit_schedule:
        insert_due = leitner.due_iso(insert_box)
    else:
        insert_due = None  # signals "use SQLite datetime('now')" below
    insert_added = str(added_at).strip()[:32] if added_at is not None else None
    with transaction() as conn:
        existing = conn.execute(
            "SELECT id FROM vocab_items WHERE user_id=? AND language=? AND word=?",
            (user_id, language, word),
        ).fetchone()
        if existing is not None:
            conn.execute(
                "UPDATE vocab_items SET source=?, sense_idx=?, pos=?, glossary=?,"
                "  example=?, explanation_primary=?, explanation_secondary=?"
                " WHERE id=? AND user_id=?",
                (source, sense_idx, pos, glossary, example, explanation_primary,
                 explanation_secondary, existing["id"], user_id),
            )
            return {"id": existing["id"], "created": False, "language": language, "word": word}
        if insert_due is None:
            cur = conn.execute(
                "INSERT INTO vocab_items ("
                "  user_id, language, word, source, sense_idx, pos, glossary, example,"
                "  explanation_primary, explanation_secondary, leitner_box, next_due"
                + (", added_at" if insert_added is not None else "")
                + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')"
                + (", ?" if insert_added is not None else "")
                + ")",
                (
                    user_id, language, word, source, sense_idx, pos, glossary, example,
                    explanation_primary, explanation_secondary, insert_box,
                ) + ((insert_added,) if insert_added is not None else ()),
            )
        else:
            cur = conn.execute(
                "INSERT INTO vocab_items ("
                "  user_id, language, word, source, sense_idx, pos, glossary, example,"
                "  explanation_primary, explanation_secondary, leitner_box, next_due"
                + (", added_at" if insert_added is not None else "")
                + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"
                + (", ?" if insert_added is not None else "")
                + ")",
                (
                    user_id, language, word, source, sense_idx, pos, glossary, example,
                    explanation_primary, explanation_secondary, insert_box, insert_due,
                ) + ((insert_added,) if insert_added is not None else ()),
            )
        vocab_id = cur.lastrowid
    return {"id": vocab_id, "created": True, "language": language, "word": word}


def _clamp_leitner(value: Any) -> int:
    from . import leitner
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1
    return max(leitner.MIN_BOX, min(leitner.MAX_BOX, n))


def delete_vocab(*, user_id: int, vocab_id: int) -> dict:
    with transaction() as conn:
        row = conn.execute(
            "SELECT * FROM vocab_items WHERE id=? AND user_id=?",
            (vocab_id, user_id),
        ).fetchone()
        if row is None:
            raise LookupError("vocab item not found")
        conn.execute("DELETE FROM vocab_items WHERE id=? AND user_id=?",
                     (vocab_id, user_id))
    token = f"{vocab_id}-{int(time.time()*1000)}"
    with _undo_lock:
        _undo_tokens[token] = dict(row)
        _evict_undo_locked()
    return {"deleted_id": vocab_id, "undo_token": token, "ttl_seconds": UNDO_TTL_SECONDS}


def restore_vocab(*, user_id: int, undo_token: str) -> dict:
    with _undo_lock:
        record = _undo_tokens.pop(undo_token, None)
    if record is None:
        raise LookupError("undo token expired or unknown")
    with transaction() as conn:
        conn.execute(
            "INSERT INTO vocab_items (id, user_id, language, word, source, sense_idx,"
            "  pos, glossary, example, explanation_primary, explanation_secondary,"
            "  leitner_box, next_due, added_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO NOTHING",
            (
                record["id"], user_id,
                record["language"], record["word"], record["source"],
                int(record["sense_idx"] or 0),
                record["pos"], record["glossary"], record["example"],
                record["explanation_primary"], record["explanation_secondary"],
                int(record["leitner_box"] or 1),
                record["next_due"], record["added_at"],
            ),
        )
    return {"restored_id": record["id"]}


def _evict_undo_locked() -> None:
    cutoff = time.time() - UNDO_TTL_SECONDS
    while _undo_tokens:
        token, record = next(iter(_undo_tokens.items()))
        token_ts = int(token.rsplit("-", 1)[-1]) / 1000
        if token_ts < cutoff:
            _undo_tokens.pop(token, None)
        else:
            break


def find_vocab_box(*, user_id: int, language: str, word: str) -> dict | None:
    """Return the Leitner box for `(language, word)` or None if not in vocab.

    The dictionary card uses this to decide whether to show an "Add to box 1"
    button (no row) or the current box badge (row exists).

    Multi-word input is normalized the same way the dictionary lookup does:
    internal whitespace becomes an underscore, so a user typing
    ``"snap at"`` finds a row stored as ``"snap_at"`` (the convention
    WordNet uses for multi-word lemmas) and vice versa.
    """
    from ..util import normalize_word
    if not is_valid_lang(language):
        raise ValueError("invalid language")
    if not isinstance(word, str):
        return None
    word = normalize_word(word)
    if not word:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, leitner_box FROM vocab_items "
            "WHERE user_id=? AND language=? AND word=?",
            (user_id, language, word[:200]),
        ).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "leitner_box": int(row["leitner_box"])}


def list_vocab(*, user_id: int, language: str, limit: int = 100, offset: int = 0,
               box: int | None = None, box_min: int | None = None,
               box_max: int | None = None,
               added_after: str | None = None, added_before: str | None = None,
               reviewed_after: str | None = None, reviewed_before: str | None = None,
               ) -> list[dict]:
    """List vocab rows for `language`, newest first.

    `box` (1-5) optionally restricts the result to items at that Leitner box;
    this powers the Vocabulary page's per-level view. `box_min`/`box_max`
    restrict to a range of boxes (e.g. 2-5 for "reviewed" words).

    `added_after`/`added_before` and `reviewed_after`/`reviewed_before`
    filter rows by their ``added_at`` / ``reviewed_at`` timestamps (inclusive
    ISO datetimes). The Review page uses these to bound "today" in the
    browser's local timezone.
    """
    if not is_valid_lang(language):
        raise ValueError("invalid language")
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    where, params = _vocab_where(
        user_id, language, box, box_min, box_max,
        added_after, added_before, reviewed_after, reviewed_before,
    )
    params += [limit, offset]
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM vocab_items WHERE {where} "
            "ORDER BY added_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def count_vocab(*, user_id: int, language: str, box: int | None = None,
                box_min: int | None = None, box_max: int | None = None,
                added_after: str | None = None, added_before: str | None = None,
                reviewed_after: str | None = None, reviewed_before: str | None = None,
                ) -> int:
    """Count vocab rows matching the same filter as ``list_vocab``.

    Used by the Vocabulary page to compute total pages for pagination.
    """
    if not is_valid_lang(language):
        raise ValueError("invalid language")
    where, params = _vocab_where(
        user_id, language, box, box_min, box_max,
        added_after, added_before, reviewed_after, reviewed_before,
    )
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM vocab_items WHERE {where}", params,
        ).fetchone()
    return int(row["c"])


def _vocab_where(user_id: int, language: str, box: int | None = None,
                 box_min: int | None = None, box_max: int | None = None,
                 added_after: str | None = None, added_before: str | None = None,
                 reviewed_after: str | None = None, reviewed_before: str | None = None,
                 ) -> tuple[str, list[Any]]:
    where = "user_id=? AND language=?"
    params: list[Any] = [user_id, language]
    if box is not None:
        if not isinstance(box, int) or not (leitner.MIN_BOX <= box <= leitner.MAX_BOX):
            raise ValueError(f"box must be between {leitner.MIN_BOX} and {leitner.MAX_BOX}")
        where += " AND leitner_box=?"
        params.append(box)
    else:
        if box_min is not None:
            if not isinstance(box_min, int) or not (leitner.MIN_BOX <= box_min <= leitner.MAX_BOX):
                raise ValueError(f"box_min must be between {leitner.MIN_BOX} and {leitner.MAX_BOX}")
            where += " AND leitner_box>=?"
            params.append(box_min)
        if box_max is not None:
            if not isinstance(box_max, int) or not (leitner.MIN_BOX <= box_max <= leitner.MAX_BOX):
                raise ValueError(f"box_max must be between {leitner.MIN_BOX} and {leitner.MAX_BOX}")
            where += " AND leitner_box<=?"
            params.append(box_max)
    for col, lo, hi in (("added_at", added_after, added_before),
                        ("reviewed_at", reviewed_after, reviewed_before)):
        if lo is not None:
            where += f" AND {col}>=?"
            params.append(str(lo))
        if hi is not None:
            where += f" AND {col}<=?"
            params.append(str(hi))
    return where, params


def set_box(*, user_id: int, vocab_id: int, box: int) -> dict:
    """Manually set a vocab item's Leitner box (and reschedule next_due).

    Lets the user self-rate "I remember this at level N" without going through
    the review flow. Resets ``next_due`` using the Leitner interval table so
    future review picks it up at the right cadence.
    """
    if not isinstance(box, int) or not (leitner.MIN_BOX <= box <= leitner.MAX_BOX):
        raise ValueError(f"box must be between {leitner.MIN_BOX} and {leitner.MAX_BOX}")
    with transaction() as conn:
        row = conn.execute(
            "SELECT id FROM vocab_items WHERE id=? AND user_id=?",
            (vocab_id, user_id),
        ).fetchone()
        if row is None:
            raise LookupError("vocab item not found")
        new_due = leitner.due_iso(box)
        conn.execute(
            "UPDATE vocab_items SET leitner_box=?, next_due=? "
            "WHERE id=? AND user_id=?",
            (box, new_due, vocab_id, user_id),
        )
    return {"vocab_id": vocab_id, "leitner_box": box, "next_due": new_due}


def review_next(*, user_id: int, language: str, n: int = 20,
                box: int | None = None, shuffle: bool = False) -> list[dict]:
    """Return the next `n` due items for `language`.

    By default only items whose ``next_due`` has passed are returned. Pass
    ``box`` (1-5) to restrict to a single Leitner box, or ``box=0`` to pull
    from *every* box regardless of due date (used by the Review page's
    "review all boxes" mode).

    When ``shuffle`` is True the eligible pool is returned in random order
    (used by the Review page so a session isn't the same every time). A
    deterministic ORDER BY is still used to seed the row selection, then the
    result set is shuffled before truncating to ``n``.
    """
    if not is_valid_lang(language):
        raise ValueError("invalid language")
    n = max(1, min(50, int(n)))
    where = "user_id=? AND language=?"
    params: list[Any] = [user_id, language]
    if box is not None:
        if not isinstance(box, int) or not (0 <= box <= leitner.MAX_BOX):
            raise ValueError(f"box must be between 0 and {leitner.MAX_BOX}")
        if box == 0:
            # Review across every box, ignoring due dates.
            pass
        else:
            where += " AND leitner_box=?"
            params.append(box)
    else:
        where += " AND next_due <= datetime('now')"
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, language, word, pos, glossary, example,"
            "       explanation_primary, explanation_secondary,"
            "       leitner_box, next_due "
            f"FROM vocab_items WHERE {where} "
            "ORDER BY next_due ASC",
            params,
        ).fetchall()
    items = [dict(r) for r in rows]
    if shuffle:
        random.shuffle(items)
    return items[:n]


def apply_review_grade(*, user_id: int, vocab_id: int, grade_value: str) -> dict:
    if grade_value not in ("easy", "hard"):
        raise ValueError("grade must be easy or hard")
    with transaction() as conn:
        new_box, new_due = leitner.apply_grade(conn, vocab_id, grade_value, user_id)
    return {"vocab_id": vocab_id, "leitner_box": new_box, "next_due": new_due}


def auto_add_from_lookup(*, user_id: int, entry: Any, auto_add_enabled: bool) -> bool:
    """Insert or update one vocab row per unique word.

    One row per (user_id, language, word). The first sense's data is stored;
    re-looking-up the same word updates the row in place (glossary, example,
    explanations refreshed; leitner_box and next_due preserved).

    Returns True if a new row was created (False on update or skip).
    """
    if not auto_add_enabled:
        return False
    senses = getattr(entry, "senses", []) or []
    if not senses:
        return False
    source = entry.source or "llm"
    sense = senses[0]
    defs = sense.definitions or []
    if not defs:
        return False
    d = defs[0]
    try:
        res = add_vocab(
            user_id=user_id,
            language=entry.language,
            word=entry.word,
            source=source,
            sense_idx=0,
            pos=sense.pos,
            glossary=d.glossary,
            example=d.example,
            explanation_primary=(sense.explanations or {}).get("primary"),
            explanation_secondary=(sense.explanations or {}).get("secondary"),
            auto_add=True,
        )
    except ValueError as e:
        log.warning("auto-add skipped for %s: %s", entry.word, e)
        return False
    return bool(res.get("created"))


def review_status(*, user_id: int, language: str) -> dict:
    if not is_valid_lang(language):
        raise ValueError("invalid language")
    counts = leitner.count_by_box(language, user_id)
    due = leitner.count_due(language, user_id)
    return {
        "due": due,
        "by_box": {b: counts.get(b, 0) for b in range(1, 6)},
    }