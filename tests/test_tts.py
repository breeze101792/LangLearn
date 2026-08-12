"""Tests for the TTS (pronunciation) blueprint + provider module.

Network calls to Google are stubbed via `monkeypatch.setattr` on
`backend.services.tts.google.requests.get`. We never hit the real
endpoint.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def fresh(clean_state):
    """Re-export of the autouse clean_state fixture for tests that
    read `fresh` for documentation purposes. The autouse fixture in
    conftest.py already sets up the data dir + db schema and clears
    module-level state — see `tests/conftest.py`."""
    return clean_state


def _mp3_bytes() -> bytes:
    # An ID3 header followed by a few zero bytes. Long enough to look like
    # a real payload without actually being one.
    return b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 32


class _FakeResp:
    def __init__(self, content=None, status_code=200):
        self.content = content if content is not None else _mp3_bytes()
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def _patch_google(monkeypatch, *, content=None, status_code=200, side_effect=None):
    """Patch `requests.get` inside the google provider module."""
    google = importlib.import_module("backend.services.tts.google")
    if side_effect is not None:
        monkeypatch.setattr(google.requests, "get", side_effect)
        return google
    resp = _FakeResp(content=content, status_code=status_code)
    monkeypatch.setattr(google.requests, "get", lambda *a, **kw: resp)
    return google


# ---- Provider unit tests ---------------------------------------------------


def test_google_provider_zh_maps_to_zh_tw(monkeypatch, fresh):
    google = _patch_google(monkeypatch)
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return _FakeResp()

    monkeypatch.setattr(google.requests, "get", fake_get)
    out = google.GoogleTTS().synth("你好", "zh")
    assert out  # bytes
    assert "tl=zh-TW" in captured["url"]
    assert "client=tw-ob" in captured["url"]


def test_google_provider_passes_text_passthrough(monkeypatch, fresh):
    google = _patch_google(monkeypatch)
    captured = {}
    monkeypatch.setattr(google.requests, "get",
                        lambda url, **kw: (captured.update(url=url) or _FakeResp()))
    google.GoogleTTS().synth("hello world", "en")
    from urllib.parse import unquote
    assert "q=hello+world" in captured["url"] or "q=hello%20world" in captured["url"]
    # Whatever URL form, the unquoted query contains the original text.
    assert "tl=en" in unquote(captured["url"])


def test_google_provider_rejects_empty_payload(monkeypatch, fresh):
    from backend.services.tts.base import TTSAudioError
    google = _patch_google(monkeypatch, content=b"")
    with pytest.raises(TTSAudioError):
        google.GoogleTTS().synth("hola", "es")


def test_google_provider_rejects_non_audio_payload(monkeypatch, fresh):
    from backend.services.tts.base import TTSAudioError
    google = _patch_google(monkeypatch, content=b"<html>oops</html>")
    with pytest.raises(TTSAudioError):
        google.GoogleTTS().synth("hola", "es")


def test_google_provider_propagates_request_exception(monkeypatch, fresh):
    from backend.services.tts.base import TTSAudioError
    import requests as real_requests
    google = _patch_google(monkeypatch)
    def boom(*a, **kw):
        raise real_requests.ConnectionError("nope")
    monkeypatch.setattr(google.requests, "get", boom)
    with pytest.raises(TTSAudioError):
        google.GoogleTTS().synth("hola", "es")


def test_google_provider_supports_known_catalog_langs():
    from backend.services.tts import google
    p = google.GoogleTTS()
    for code in ("en", "es", "ja", "pt", "zh", "fr", "de"):
        assert p.supports(code) is True
    assert p.supports("zz") is False
    assert p.supports("") is False


# ---- Registry tests --------------------------------------------------------


def test_registry_bootstrap_registers_google():
    from backend.services.tts import registry
    registry.PROVIDERS.clear()
    registry.PROVIDER_META.clear()
    registry.bootstrap()
    assert "google" in registry.available()
    assert registry.get("google") is not None
    assert registry.active(None) is registry.get("google")


def test_registry_active_falls_back_to_first_when_unknown():
    from backend.services.tts import registry
    registry.PROVIDERS.clear()
    registry.PROVIDER_META.clear()
    registry.bootstrap()
    p = registry.active("nonexistent")
    assert p is registry.get("google")


def test_registry_synth_returns_bytes_and_content_type(monkeypatch, fresh):
    from backend.services.tts import registry
    registry.PROVIDERS.clear()
    registry.PROVIDER_META.clear()
    registry.bootstrap()
    _patch_google(monkeypatch)
    body, ctype = registry.synth("hola", "es", "google")
    assert body
    assert ctype == "audio/mpeg"


def test_registry_synth_raises_when_lang_unsupported():
    from backend.services.tts import registry
    from backend.services.tts.base import TTSAudioError
    registry.PROVIDERS.clear()
    registry.PROVIDER_META.clear()
    registry.bootstrap()
    with pytest.raises(TTSAudioError):
        registry.synth("nope", "zz", "google")


# ---- HTTP / blueprint tests ------------------------------------------------


def test_audio_endpoint_returns_audio_mpeg(monkeypatch, fresh):
    from backend.app import create_app
    _patch_google(monkeypatch)
    app = create_app()
    client = app.test_client()
    r = client.get("/api/tts/audio?lang=es&word=hola")
    assert r.status_code == 200
    assert r.mimetype == "audio/mpeg"
    assert r.data[:3] == b"ID3"
    assert r.headers.get("X-TTS-Cache") == "miss"


def test_audio_endpoint_validates_lang(monkeypatch, fresh):
    from backend.app import create_app
    _patch_google(monkeypatch)
    app = create_app()
    client = app.test_client()
    r = client.get("/api/tts/audio?lang=ZZ&word=hi")
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_audio_endpoint_validates_word(monkeypatch, fresh):
    from backend.app import create_app
    _patch_google(monkeypatch)
    app = create_app()
    client = app.test_client()
    r = client.get("/api/tts/audio?lang=en&word=")
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_audio_endpoint_underscore_to_space(monkeypatch, fresh):
    from backend.app import create_app
    from urllib.parse import unquote
    google = _patch_google(monkeypatch)
    captured = {}
    monkeypatch.setattr(google.requests, "get",
                        lambda url, **kw: (captured.update(url=url) or _FakeResp()))
    app = create_app()
    client = app.test_client()
    r = client.get("/api/tts/audio?lang=en&word=snap_at")
    assert r.status_code == 200
    assert "q=snap+at" in captured["url"] or "q=snap%20at" in unquote(captured["url"])


def test_audio_endpoint_502_on_provider_failure(monkeypatch, fresh):
    from backend.app import create_app
    google = _patch_google(monkeypatch, content=b"")
    app = create_app()
    client = app.test_client()
    r = client.get("/api/tts/audio?lang=en&word=hello")
    assert r.status_code == 502
    assert r.get_json()["ok"] is False


def test_audio_endpoint_uses_cache(monkeypatch, fresh):
    from backend.app import create_app
    call_count = {"n": 0}
    google = _patch_google(monkeypatch)
    def counting_get(url, **kw):
        call_count["n"] += 1
        return _FakeResp()
    monkeypatch.setattr(google.requests, "get", counting_get)
    app = create_app()
    client = app.test_client()
    r1 = client.get("/api/tts/audio?lang=en&word=hello")
    assert r1.status_code == 200
    assert r1.headers.get("X-TTS-Cache") == "miss"
    r2 = client.get("/api/tts/audio?lang=en&word=hello")
    assert r2.status_code == 200
    assert r2.headers.get("X-TTS-Cache") == "hit"
    # Google endpoint was called only once.
    assert call_count["n"] == 1


def test_providers_endpoint_lists_registered(monkeypatch, fresh):
    from backend.app import create_app
    from backend.services.tts import registry
    registry.PROVIDERS.clear()
    registry.PROVIDER_META.clear()
    registry.bootstrap()
    app = create_app()
    client = app.test_client()
    r = client.get("/api/tts/providers")
    assert r.status_code == 200
    data = r.get_json()["data"]
    names = [p["name"] for p in data["providers"]]
    assert "google" in names
    google = next(p for p in data["providers"] if p["name"] == "google")
    assert google["display_name"]
    assert google["description"]


# ---- Settings integration --------------------------------------------------


def test_tts_provider_default_in_settings(monkeypatch, fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/settings")
    assert r.get_json()["data"]["tts_provider"] == "google"


def test_tts_provider_persists_on_update(monkeypatch, fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"tts_provider": "google"})
    assert r.status_code == 200
    assert r.get_json()["data"]["tts_provider"] == "google"
    r2 = client.get("/api/settings")
    assert r2.get_json()["data"]["tts_provider"] == "google"


def test_tts_provider_unknown_400(monkeypatch, fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"tts_provider": "made-up"})
    assert r.status_code == 400


def test_audio_endpoint_uses_settings_provider(monkeypatch, fresh):
    from backend.app import create_app
    google = _patch_google(monkeypatch)
    captured = {}
    monkeypatch.setattr(google.requests, "get",
                        lambda url, **kw: (captured.update(url=url) or _FakeResp()))
    app = create_app()
    client = app.test_client()
    # Sanity: provider = google, so we expect a network call.
    client.get("/api/tts/audio?lang=en&word=hi")
    assert "google" in captured.get("url", "").lower() or "translate" in captured.get("url", "")
