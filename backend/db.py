"""SQLite layer.

- Single shared connection per request.
- Migration runner applies SQL files in `backend/migrations/*.sql` in order,
  recording applied filenames in a `schema_migrations` table.
- Atomic write helpers for JSON side-files (settings cache, undo tokens).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import config
from .util import safe_path

log = logging.getLogger(__name__)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect(config.db_path())
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    with get_conn() as conn:
        try:
            conn.execute("BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def init_schema() -> None:
    """Apply migrations idempotently."""
    config.data_dir().mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  filename TEXT PRIMARY KEY,"
            "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        applied = {r["filename"] for r in conn.execute("SELECT filename FROM schema_migrations")}
        if not config.MIGRATIONS_DIR.exists():
            log.warning("migrations dir missing: %s", config.MIGRATIONS_DIR)
            return
        for sql_file in sorted(config.MIGRATIONS_DIR.glob("*.sql")):
            if sql_file.name in applied:
                continue
            log.info("applying migration %s", sql_file.name)
            conn.executescript(sql_file.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (filename) VALUES (?)",
                (sql_file.name,),
            )


def atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON atomically via tempfile + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


__all__ = [
    "get_conn",
    "transaction",
    "init_schema",
    "atomic_write_json",
    "atomic_write_text",
    "safe_path",
]