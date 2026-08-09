"""Tests for backend/db.py utilities (atomic writes, transaction rollback,
get_conn contextmanager)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    from backend import db
    db.init_schema()
    return tmp_path


# --- get_conn / transaction ------------------------------------------


def test_get_conn_returns_sqlite_connection(fresh):
    from backend import db
    with db.get_conn() as conn:
        assert isinstance(conn, sqlite3.Connection)


def test_get_conn_closes_after_context(fresh):
    from backend import db
    conn_ref = {}
    with db.get_conn() as conn:
        conn_ref["c"] = conn
    # Connection is closed; subsequent operations raise.
    with pytest.raises(sqlite3.ProgrammingError):
        conn_ref["c"].execute("SELECT 1")


def test_transaction_commits_on_success(fresh):
    from backend import db
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO languages (code, display_name, is_built_in) "
            "VALUES ('es', 'Spanish', 0)"
        )
    with db.get_conn() as conn:
        row = conn.execute("SELECT code FROM languages WHERE code='es'").fetchone()
    assert row is not None


def test_transaction_rolls_back_on_exception(fresh):
    from backend import db
    with pytest.raises(RuntimeError, match="boom"):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO languages (code, display_name, is_built_in) "
                "VALUES ('es', 'Spanish', 0)"
            )
            raise RuntimeError("boom")
    with db.get_conn() as conn:
        row = conn.execute("SELECT code FROM languages WHERE code='es'").fetchone()
    assert row is None


def test_transaction_raises_when_inner_block_raises(fresh):
    """If the block raises, the transaction context re-raises after rollback."""
    from backend import db
    with pytest.raises(ValueError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO languages (code, display_name, is_built_in) "
                "VALUES ('es', 'Spanish', 0)"
            )
            raise ValueError("explicit fail")


# --- atomic_write_json ------------------------------------------------


def test_atomic_write_json_creates_file(fresh, tmp_path):
    from backend import db
    target = tmp_path / "out.json"
    db.atomic_write_json(target, {"a": 1, "b": "hello"})
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "b": "hello"}


def test_atomic_write_json_creates_parent_dirs(fresh, tmp_path):
    from backend import db
    target = tmp_path / "deeply" / "nested" / "out.json"
    db.atomic_write_json(target, {"x": 1})
    assert target.exists()


def test_atomic_write_json_overwrites_existing(fresh, tmp_path):
    from backend import db
    target = tmp_path / "out.json"
    db.atomic_write_json(target, {"v": 1})
    db.atomic_write_json(target, {"v": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 2}


def test_atomic_write_json_preserves_unicode(fresh, tmp_path):
    from backend import db
    target = tmp_path / "out.json"
    db.atomic_write_json(target, {"word": "你好"})
    text = target.read_text(encoding="utf-8")
    assert "你好" in text


def test_atomic_write_json_no_partial_file_left_on_success(fresh, tmp_path):
    """The temp file must be replaced (not left alongside) on success."""
    from backend import db
    target = tmp_path / "out.json"
    db.atomic_write_json(target, {"v": 1})
    siblings = [p for p in tmp_path.iterdir() if p.name.startswith("out.json.")]
    assert siblings == []


def test_atomic_write_json_cleans_temp_on_failure(fresh, tmp_path, monkeypatch):
    """If serialization fails, the temp file must not be left behind."""
    from backend import db

    def boom(*a, **kw):
        raise RuntimeError("disk full")
    monkeypatch.setattr(db.json, "dump", boom)

    target = tmp_path / "out.json"
    with pytest.raises(RuntimeError):
        db.atomic_write_json(target, {"v": 1})
    siblings = [p for p in tmp_path.iterdir()
                 if p.name.startswith("out.json.") and p != target]
    assert siblings == []


# --- atomic_write_text ------------------------------------------------


def test_atomic_write_text_creates_file(fresh, tmp_path):
    from backend import db
    target = tmp_path / "out.txt"
    db.atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_text_overwrites(fresh, tmp_path):
    from backend import db
    target = tmp_path / "out.txt"
    db.atomic_write_text(target, "v1")
    db.atomic_write_text(target, "v2")
    assert target.read_text(encoding="utf-8") == "v2"


def test_atomic_write_text_creates_parent_dirs(fresh, tmp_path):
    from backend import db
    target = tmp_path / "a" / "b" / "out.txt"
    db.atomic_write_text(target, "x")
    assert target.read_text(encoding="utf-8") == "x"


def test_atomic_write_text_cleans_temp_on_failure(fresh, tmp_path, monkeypatch):
    from backend import db

    def boom(*a, **kw):
        raise RuntimeError("nope")
    monkeypatch.setattr(db.os, "fdopen", boom)

    target = tmp_path / "out.txt"
    with pytest.raises(RuntimeError):
        db.atomic_write_text(target, "x")
    siblings = [p for p in tmp_path.iterdir()
                 if p.name.startswith("out.txt.") and p != target]
    assert siblings == []


# --- init_schema edge cases ------------------------------------------


def test_init_schema_handles_missing_migrations_dir(tmp_path, monkeypatch):
    """If the migrations dir is missing, init_schema must not crash."""
    from backend import config, db

    monkeypatch.setattr(config, "MIGRATIONS_DIR", Path("/nonexistent"))
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    db.init_schema()  # Must not raise.


def test_data_dir_is_created_on_call(tmp_path, monkeypatch):
    """data_dir() must create the directory if missing."""
    from backend import config
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path / "fresh"))
    d = config.data_dir()
    assert d.exists()
    assert d.is_dir()
