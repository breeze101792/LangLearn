"""Edge-case tests for the TTS google provider and registry.

test_tts.py covers the main provider + registry flows. This file pins the
remaining error branches:

- google: ``_to_tts_lang`` rejects non-string
- google: ``_is_catalog_lang`` rejects non-string
- google: ``synth`` rejects empty text
- google: ``synth`` rejects non-200 status
- registry: ``get`` with empty/None name
- registry: ``active`` with no providers returns None
- registry: ``synth`` with no providers raises TTSAudioError
- registry: ``synth`` with unsupported lang raises TTSAudioError
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def fresh(clean_state):
    """Re-export of the autouse clean_state fixture."""
    return clean_state


def _patch_google(monkeypatch, *, status_code=200, content=None):
    google = importlib.import_module("backend.services.tts.google")
    body = content if content is not None else b"ID3" + b"\x00" * 32

    class _FakeResp:
        def __init__(self):
            self.status_code = status_code
            self.content = body

    monkeypatch.setattr(google.requests, "get", lambda *a, **kw: _FakeResp())
    return google


# ---------- google provider ----------


def test_to_tts_lang_rejects_non_string(fresh):
    from backend.services.tts import google
    with pytest.raises(Exception):
        google._to_tts_lang(123)


def test_is_catalog_lang_rejects_non_string(fresh):
    from backend.services.tts import google
    assert google._is_catalog_lang(123) is False


def test_synth_rejects_empty_text(fresh):
    from backend.services.tts import google
    from backend.services.tts.base import TTSAudioError
    with pytest.raises(TTSAudioError):
        google.GoogleTTS().synth("", "en")


def test_synth_rejects_non_200(fresh, monkeypatch):
    from backend.services.tts import google
    from backend.services.tts.base import TTSAudioError
    _patch_google(monkeypatch, status_code=500)
    with pytest.raises(TTSAudioError):
        google.GoogleTTS().synth("hola", "es")


# ---------- registry ----------


def test_registry_get_empty_and_none(fresh):
    from backend.services.tts import registry
    assert registry.get("") is None
    assert registry.get(None) is None


def test_registry_active_no_providers_returns_none(fresh):
    from backend.services.tts import registry
    registry.PROVIDERS.clear()
    registry.PROVIDER_META.clear()
    assert registry.active("google") is None


def test_registry_synth_no_providers_raises(fresh):
    from backend.services.tts import registry
    from backend.services.tts.base import TTSAudioError
    registry.PROVIDERS.clear()
    registry.PROVIDER_META.clear()
    with pytest.raises(TTSAudioError, match="no TTS provider"):
        registry.synth("hi", "en", "google")


def test_registry_synth_unsupported_lang_raises(fresh):
    from backend.services.tts import registry
    from backend.services.tts.base import TTSAudioError
    registry.PROVIDERS.clear()
    registry.PROVIDER_META.clear()
    registry.bootstrap()
    with pytest.raises(TTSAudioError, match="does not support"):
        registry.synth("hi", "zz", "google")
