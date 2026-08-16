"""Leitner 5-box scheduler.

Box 1 -> 1 day, Box 2 -> 3 days, Box 3 -> 7 days, Box 4 -> 14 days, Box 5 -> 30 days.
'easy' promotes (clamped at 5); 'hard' demotes (floored at 1).
"""

from __future__ import annotations

import datetime as _dt
import sqlite3

from ..db import get_conn

BOX_INTERVALS_DAYS: list[int] = [1, 3, 7, 14, 30]
MAX_BOX = len(BOX_INTERVALS_DAYS)  # 5
MIN_BOX = 1


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def due_iso(box: int, now: _dt.datetime | None = None) -> str:
    days = BOX_INTERVALS_DAYS[max(0, min(MAX_BOX - 1, box - 1))]
    base = now or _now()
    return (base + _dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def grade(box: int, grade_value: str) -> tuple[int, str]:
    """Return (new_box, new_next_due_iso)."""
    if grade_value not in ("easy", "hard"):
        raise ValueError("grade must be 'easy' or 'hard'")
    if grade_value == "easy":
        new_box = min(MAX_BOX, box + 1)
    else:
        new_box = MIN_BOX
    return new_box, due_iso(new_box)


def is_due(next_due: str, now: _dt.datetime | None = None) -> bool:
    base = now or _now()
    try:
        nd = _dt.datetime.strptime(next_due, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return nd <= base


def apply_grade(conn: sqlite3.Connection, vocab_id: int, grade_value: str,
                user_id: int) -> tuple[int, str]:
    row = conn.execute(
        "SELECT leitner_box FROM vocab_items WHERE id=? AND user_id=?",
        (vocab_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError(f"vocab item not found: {vocab_id}")
    new_box, new_due = grade(row["leitner_box"], grade_value)
    conn.execute(
        "UPDATE vocab_items SET leitner_box=?, next_due=?, reviewed_at=datetime('now') "
        "WHERE id=? AND user_id=?",
        (new_box, new_due, vocab_id, user_id),
    )
    return new_box, new_due


def count_by_box(lang: str, user_id: int) -> dict[int, int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT leitner_box, COUNT(*) AS c FROM vocab_items "
            "WHERE user_id=? AND language=? GROUP BY leitner_box",
            (user_id, lang),
        ).fetchall()
    return {r["leitner_box"]: r["c"] for r in rows}


def count_due(lang: str, user_id: int, now: _dt.datetime | None = None) -> int:
    base = now or _now()
    base_iso = base.strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM vocab_items "
            "WHERE user_id=? AND language=? AND next_due <= ?",
            (user_id, lang, base_iso),
        ).fetchone()
    return row["c"]