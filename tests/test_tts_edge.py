"""Edge-case tests for the TTS blueprint's cache and phrase validation.

test_tts.py covers the provider, registry, and happy-path HTTP flows. This
file pins the blueprint's internal helpers that were not exercised there:

- ``_is_speakable_phrase`` boundary cases (non-string, empty, too long,
  invalid characters)
- ``_read_cache`` OSError path (path is a directory)
- ``_write_cache`` failure paths (parent is a file; os.replace failure
  cleans up the temp file)
- ``_cache_path`` includes the provider name in the key
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def fresh(clean_state):
    """Re-export of the autouse clean_state fixture."""
    return clean_state


# ---------- _is_speakable_phrase ----------


def test_speakable_phrase_rejects_non_string(fresh):
    from backend.blueprints import tts as tts_bp
    assert tts_bp._is_speakable_phrase(123) is False
    assert tts_bp._is_speakable_phrase(None) is False


def test_speakable_phrase_rejects_empty(fresh):
    from backend.blueprints import tts as tts_bp
    assert tts_bp._is_speakable_phrase("") is False
    assert tts_bp._is_speakable_phrase("   ") is False


def test_speakable_phrase_rejects_too_long(fresh):
    from backend.blueprints import tts as tts_bp
    assert tts_bp._is_speakable_phrase("a" * 201) is False


def test_speakable_phrase_accepts_sentence_punctuation(fresh):
    from backend.blueprints import tts as tts_bp
    assert tts_bp._is_speakable_phrase("Hello, world! How are you?") is True


def test_speakable_phrase_rejects_invalid_chars(fresh):
    from backend.blueprints import tts as tts_bp
    assert tts_bp._is_speakable_phrase("hello@world") is False
    assert tts_bp._is_speakable_phrase("hello#world") is False


# ---------- _cache_path ----------


def test_cache_path_includes_provider_in_key(fresh):
    """The provider name is part of the cache key so switching providers
    doesn't replay the old provider's audio."""
    from backend.blueprints import tts as tts_bp
    p1 = tts_bp._cache_path("en", "hello", "google")
    p2 = tts_bp._cache_path("en", "hello", "other")
    assert p1 != p2
    assert p1.endswith(".mp3")
    assert "en" in str(p1)


# ---------- _read_cache ----------


def test_read_cache_missing_returns_none(fresh, tmp_path):
    from backend.blueprints import tts as tts_bp
    assert tts_bp._read_cache(str(tmp_path / "nope" / "x.mp3")) is None


def test_read_cache_oserror_returns_none(fresh, tmp_path):
    """Reading a directory raises OSError; the helper must swallow it and
    return None (treated as a cache miss)."""
    from backend.blueprints import tts as tts_bp
    assert tts_bp._read_cache(str(tmp_path)) is None


def test_read_cache_returns_bytes_and_mime(fresh, tmp_path):
    from backend.blueprints import tts as tts_bp
    target = tmp_path / "x.mp3"
    target.write_bytes(b"ID3data")
    body, ctype = tts_bp._read_cache(str(target))
    assert body == b"ID3data"
    assert ctype == "audio/mpeg"


# ---------- _write_cache ----------


def test_write_cache_creates_file(fresh, tmp_path):
    from backend.blueprints import tts as tts_bp
    target = tmp_path / "sub" / "x.mp3"
    tts_bp._write_cache(str(target), b"data")
    assert target.read_bytes() == b"data"


def test_write_cache_oserror_swallowed(fresh, tmp_path):
    """If the parent path is a file, makedirs fails; the helper logs and
    returns without raising."""
    from backend.blueprints import tts as tts_bp
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    tts_bp._write_cache(str(blocker / "x.mp3"), b"data")  # must not raise


def test_write_cache_cleans_temp_on_replace_failure(fresh, tmp_path, monkeypatch):
    """If os.replace fails, the temp file must be cleaned up. The outer
    OSError handler swallows the exception (it only logs), so we assert the
    temp file is gone and the target was not created."""
    from backend.blueprints import tts as tts_bp

    def bad_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(tts_bp.os, "replace", bad_replace)
    target = tmp_path / "x.mp3"
    tts_bp._write_cache(str(target), b"data")  # must not raise
    # No leftover temp files, and the target was never created.
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".tmp.")]
    assert leftovers == []
    assert not target.exists()
