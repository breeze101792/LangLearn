"""Vocab service: CRUD over vocab_items + auto-add on lookup."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from typing import Any

from .. import config
from ..db import get_conn, transaction
from ..util import is_valid_lang
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
              auto_add: bool = True) -> dict:
    if not is_valid_lang(language):
        raise ValueError("invalid language")
    if not isinstance(word, str) or not word.strip():
        raise ValueError("word required")
    if not isinstance(glossary, str) or not glossary.strip():
        raise ValueError("glossary required")
    if source not in ("wordnet", "llm", "user"):
        raise ValueError("invalid source")
    word = word.strip()[:200]
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
        cur = conn.execute(
            "INSERT INTO vocab_items ("
            "  user_id, language, word, source, sense_idx, pos, glossary, example,"
            "  explanation_primary, explanation_secondary, leitner_box, next_due"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, datetime('now'))",
            (
                user_id, language, word, source, sense_idx, pos, glossary, example,
                explanation_primary, explanation_secondary,
            ),
        )
        vocab_id = cur.lastrowid
    return {"id": vocab_id, "created": True, "language": language, "word": word}


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


def list_vocab(*, user_id: int, language: str, limit: int = 100, offset: int = 0) -> list[dict]:
    if not is_valid_lang(language):
        raise ValueError("invalid language")
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM vocab_items WHERE user_id=? AND language=? "
            "ORDER BY added_at DESC LIMIT ? OFFSET ?",
            (user_id, language, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def review_next(*, user_id: int, language: str, n: int = 20) -> list[dict]:
    if not is_valid_lang(language):
        raise ValueError("invalid language")
    n = max(1, min(50, int(n)))
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, language, word, pos, glossary, example,"
            "       explanation_primary, explanation_secondary,"
            "       leitner_box, next_due "
            "FROM vocab_items WHERE user_id=? AND language=? AND next_due <= datetime('now') "
            "ORDER BY next_due ASC LIMIT ?",
            (user_id, language, n),
        ).fetchall()
    return [dict(r) for r in rows]


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
    counts = leitner.count_by_box(language, user_id)
    due = leitner.count_due(language, user_id)
    return {
        "due": due,
        "by_box": {b: counts.get(b, 0) for b in range(1, 6)},
    }