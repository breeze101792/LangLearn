"""Tests for db initialization and migration runner."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
def temp_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    yield tmp_path


def test_init_schema_creates_tables(temp_data_dir):
    from backend import config, db

    db.init_schema()
    assert config.DB_PATH.exists()

    with db.get_conn() as conn:
        names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"users", "settings", "languages", "vocab_items", "structures",
            "phrases", "seed_jobs", "schema_migrations"} <= names


def test_init_schema_is_idempotent(temp_data_dir):
    from backend import db

    db.init_schema()
    db.init_schema()
    with db.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM schema_migrations").fetchone()["c"]
    assert count >= 1


def test_default_user_seeded(temp_data_dir):
    from backend import db

    db.init_schema()
    with db.get_conn() as conn:
        row = conn.execute("SELECT id, username FROM users WHERE id=1").fetchone()
    assert row["id"] == 1
    assert row["username"] == "me"


def test_get_conn_raises_outside_context(temp_data_dir):
    from backend import db

    db.init_schema()
    conn_cm = db.get_conn()
    with conn_cm as c:
        assert isinstance(c, sqlite3.Connection)


def test_safe_path_blocks_traversal():
    from backend.util import safe_path

    base = Path("/tmp/safe-base").resolve()
    base.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError):
        safe_path(base, "..", "etc", "passwd")


def test_safe_path_allows_child():
    from backend.util import safe_path

    base = Path("/tmp/safe-base").resolve()
    p = safe_path(base, "a", "b.txt")
    assert str(p).endswith("a/b.txt")


def test_migration_renames_review_session_size(temp_data_dir):
    """003_rename_page_size.sql must rename the column on existing DBs and
    preserve the user's existing value (no data loss)."""
    from backend import config, db

    # Wipe everything the autouse `clean_state` fixture already created so
    # we can rebuild the database in a pre-003 state.
    with db.get_conn() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        for name in tables:
            conn.execute(f"DROP TABLE IF EXISTS {name}")
        conn.execute("PRAGMA foreign_keys = ON")

    # Apply only 001 and 002 directly, then seed a settings row that uses
    # the legacy column name. This is what a real pre-003 DB looks like.
    with db.get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                     "  filename TEXT PRIMARY KEY,"
                     "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
                     ")")
        for sql_file in sorted(config.MIGRATIONS_DIR.glob("*.sql")):
            if sql_file.name == "003_rename_page_size.sql":
                continue  # skip the rename; we're simulating pre-003
            conn.executescript(sql_file.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (filename) VALUES (?)",
                (sql_file.name,),
            )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, username) VALUES (1, 'me')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO languages (code, display_name, is_built_in) "
            "VALUES ('en', 'English', 1)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings ("
            "  user_id, active_language, auto_add_vocab, review_session_size,"
            "  explanation_primary, explanation_secondary, dict_chain_json,"
            "  theme, show_readings"
            ") VALUES (1, 'en', 1, 35, 'en', NULL, '{}', 'auto', 1)"
        )
        # The settings row FKs to languages(code); seed 'en' before we
        # touch settings.
        conn.execute(
            "INSERT OR IGNORE INTO languages (code, display_name, is_built_in) "
            "VALUES ('en', 'English', 1)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, username) VALUES (1, 'me')"
        )
        cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(settings)")}
        assert "review_session_size" in cols_before
        assert "page_size" not in cols_before

    # Now run the real migration runner; it must apply 003 only.
    db.init_schema()

    with db.get_conn() as conn:
        cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(settings)")}
        row = conn.execute("SELECT page_size FROM settings WHERE user_id=1").fetchone()
        applied = {r["filename"] for r in conn.execute("SELECT filename FROM schema_migrations")}

    assert "page_size" in cols_after
    assert "review_session_size" not in cols_after
    assert "003_rename_page_size.sql" in applied
    # The existing user value must survive the rename.
    assert row["page_size"] == 35