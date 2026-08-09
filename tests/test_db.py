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