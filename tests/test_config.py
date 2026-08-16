"""Tests for config helper functions.

config.py reads env vars at import time for the module-level constants, but
the ``_port`` / ``_debug`` / ``_data_dir`` helpers are re-evaluated per call
and can be tested directly.
"""

from __future__ import annotations

import pytest


def test_port_fallback_on_invalid(monkeypatch):
    from backend import config
    monkeypatch.setenv("PORT", "abc")
    assert config._port() == 5056


def test_port_valid(monkeypatch):
    from backend import config
    monkeypatch.setenv("PORT", "8080")
    assert config._port() == 8080


def test_port_default_when_unset(monkeypatch):
    from backend import config
    monkeypatch.delenv("PORT", raising=False)
    assert config._port() == 5056


def test_debug_true(monkeypatch):
    from backend import config
    monkeypatch.setenv("FLASK_DEBUG", "true")
    assert config._debug() is True


def test_debug_false(monkeypatch):
    from backend import config
    monkeypatch.setenv("FLASK_DEBUG", "no")
    assert config._debug() is False


def test_data_dir_uses_env(monkeypatch, tmp_path):
    from backend import config
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path / "custom"))
    d = config.data_dir()
    assert d == (tmp_path / "custom").resolve()
    assert d.exists()


def test_tts_cache_dir_creates_subdir(monkeypatch, tmp_path):
    from backend import config
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    cache = config.tts_cache_dir()
    assert cache == (tmp_path / "tts_cache").resolve()
    assert cache.exists()
