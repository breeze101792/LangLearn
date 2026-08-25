"""Tests for the Microsoft Edge TTS provider.

The provider delegates to the ``edge-tts`` package's
``Communicate.save`` method, which is async and writes MP3 to a
file. We patch ``edge_tts.Communicate`` so the tests run offline
and deterministically.
"""
from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture
def fresh(clean_state):
    return clean_state


def _fake_mp3_bytes() -> bytes:
    # Real MP3 frames start with 0xFF. 256 bytes is enough to look
    # like audio to the magic-byte check the provider runs.
    return b"\xff" * 256


class _FakeCommunicateRecorder:
    """Records every (text, voice) pair handed to ``Communicate`` so
    tests can assert voice selection without poking at asyncio internals."""

    instances: list = []

    def __init__(self, text, voice, **_kw):
        self.text = text
        self.voice = voice
        _FakeCommunicateRecorder.instances.append((text, voice))

    async def save(self, audio_fname, metadata_fname=None):
        with open(audio_fname, "wb") as f:
            f.write(_fake_mp3_bytes())


class _FakeCommunicateEmpty:
    def __init__(self, *a, **kw):
        pass

    async def save(self, audio_fname, metadata_fname=None):
        with open(audio_fname, "wb") as f:
            f.write(b"")


class _FakeCommunicateHtml:
    def __init__(self, *a, **kw):
        pass

    async def save(self, audio_fname, metadata_fname=None):
        with open(audio_fname, "wb") as f:
            f.write(b"<html>error</html>")


class _FakeCommunicateBoom:
    def __init__(self, *a, **kw):
        pass

    async def save(self, *a, **kw):
        raise RuntimeError("websocket disconnected")


def _install_fake_edge_tts(monkeypatch, cls):
    """Install a stub ``edge_tts`` module so the provider's lazy
    import picks up our fake ``Communicate`` class."""
    fake = types.ModuleType("edge_tts")
    fake.Communicate = cls  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edge_tts", fake)


def _install_recorder(monkeypatch):
    _FakeCommunicateRecorder.instances = []
    _install_fake_edge_tts(monkeypatch, _FakeCommunicateRecorder)
    return _FakeCommunicateRecorder


# ---------- voice selection ----------


def test_supports_known_languages(fresh):
    from backend.services.tts import edge
    p = edge.EdgeTTS()
    for lang in ("en", "es", "fr", "de", "pt", "ja", "zh"):
        assert p.supports(lang) is True, lang


def test_supports_unknown_language_rejects(fresh):
    from backend.services.tts import edge
    p = edge.EdgeTTS()
    assert p.supports("zz") is False
    assert p.supports(None) is False  # type: ignore[arg-type]
    assert p.supports(123) is False  # type: ignore[arg-type]


# ---------- synth: happy path ----------


def test_synth_returns_mp3_bytes(fresh, monkeypatch):
    from backend.services.tts import edge
    rec = _install_recorder(monkeypatch)

    body = edge.EdgeTTS().synth("hello", "en")

    assert body == _fake_mp3_bytes()
    assert rec.instances == [("hello", "en-US-AriaNeural")]


def test_synth_picks_correct_voice_per_language(fresh, monkeypatch):
    from backend.services.tts import edge
    rec = _install_recorder(monkeypatch)

    cases = [
        ("en", "en-US-AriaNeural"),
        ("es", "es-ES-ElviraNeural"),
        ("fr", "fr-FR-DeniseNeural"),
        ("de", "de-DE-KatjaNeural"),
        ("pt", "pt-BR-FranciscaNeural"),
        ("ja", "ja-JP-NanamiNeural"),
        ("zh", "zh-TW-HsiaoChenNeural"),
    ]
    p = edge.EdgeTTS()
    for lang, expected_voice in cases:
        p.synth("hi", lang)

    assert [v for _, v in rec.instances] == [v for _, v in cases]


# ---------- synth: error branches ----------


def test_synth_rejects_empty_text(fresh):
    from backend.services.tts import edge
    from backend.services.tts.base import TTSAudioError
    p = edge.EdgeTTS()
    with pytest.raises(TTSAudioError, match="empty"):
        p.synth("", "en")
    with pytest.raises(TTSAudioError, match="empty"):
        p.synth("   ", "en")


def test_synth_rejects_unsupported_language(fresh):
    from backend.services.tts import edge
    from backend.services.tts.base import TTSAudioError
    with pytest.raises(TTSAudioError, match="does not support"):
        edge.EdgeTTS().synth("hi", "zz")


def test_synth_rejects_empty_response(fresh, monkeypatch):
    from backend.services.tts import edge
    from backend.services.tts.base import TTSAudioError
    _install_fake_edge_tts(monkeypatch, _FakeCommunicateEmpty)
    with pytest.raises(TTSAudioError, match="empty body"):
        edge.EdgeTTS().synth("hi", "en")


def test_synth_rejects_non_audio_payload(fresh, monkeypatch):
    from backend.services.tts import edge
    from backend.services.tts.base import TTSAudioError
    _install_fake_edge_tts(monkeypatch, _FakeCommunicateHtml)
    with pytest.raises(TTSAudioError, match="non-audio payload"):
        edge.EdgeTTS().synth("hi", "en")


def test_synth_translates_underlying_exception(fresh, monkeypatch):
    """A bug inside ``edge_tts.Communicate.save`` surfaces as a
    ``TTSAudioError`` so the blueprint can return 502."""
    from backend.services.tts import edge
    from backend.services.tts.base import TTSAudioError
    _install_fake_edge_tts(monkeypatch, _FakeCommunicateBoom)

    with pytest.raises(TTSAudioError, match="edge-tts synth failed"):
        edge.EdgeTTS().synth("hi", "en")


# ---------- registry integration ----------


def test_registry_bootstraps_edge_provider(fresh):
    from backend.services.tts import registry
    registry.bootstrap()
    assert "edge" in registry.PROVIDERS
    assert "google" in registry.PROVIDERS
    by_name = {m["name"]: m for m in registry.available_detailed()}
    assert by_name["edge"]["display_name"] == "Microsoft Edge"


def test_registry_synth_dispatches_edge(fresh, monkeypatch):
    """Calling ``registry.synth(..., 'edge')`` runs the edge provider
    and returns MP3 with the provider's content type."""
    from backend.services.tts import registry
    _install_recorder(monkeypatch)

    registry.bootstrap()
    body, content_type = registry.synth("hi", "en", "edge")
    assert body == _fake_mp3_bytes()
    assert content_type == "audio/mpeg"