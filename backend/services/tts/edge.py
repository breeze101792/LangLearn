"""Microsoft Edge TTS provider.

Uses the ``edge-tts`` package, which speaks to the public
``wss://speech.platform.bing.com`` read-aloud WebSocket endpoint. No API
key required, no auth header to manage — the same protocol the Edge
browser uses for its "Read Aloud" feature. Returns MP3 bytes.

The active voice is auto-picked from ``_VOICES`` for the app's
``lang`` code (zh maps to zh-TW-HsiaoChenNeural etc.). When the
language isn't in the table the call fails fast with a
``TTSAudioError`` so the registry can fall back to the next
registered provider.

Latency note: ``edge-tts`` is async under the hood (``Communicate`` +
``save``). We call ``save`` from a sync method, which blocks on a
fresh event loop per call. That's fine for this app: a TTS request
is one user click → one round-trip → one MP3, and the disk cache
absorbs repeat lookups. If we ever need concurrent TTS we'll move
this to a dedicated worker process.
"""

from __future__ import annotations

import logging
import os
import tempfile

from ... import config
from .base import TTSAudioError

log = logging.getLogger(__name__)


# Default neural voice per app language code. Picked for broad
# availability on the read-aloud endpoint and a neutral accent.
# Unknown languages are rejected by ``supports`` so the registry can
# fall through to a provider that does cover them.
_VOICES: dict[str, str] = {
    "en": "en-US-AriaNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "ja": "ja-JP-NanamiNeural",
    "zh": "zh-TW-HsiaoChenNeural",
}


class EdgeTTS:
    name = "edge"
    content_type = "audio/mpeg"

    def supports(self, lang: str) -> bool:
        if not isinstance(lang, str):
            return False
        if lang in _VOICES:
            return True
        # Allow the user to override via env (e.g. a new language the
        # table doesn't know about yet). Must still be a known app
        # language — keeps the door shut on typos.
        return bool(config.LANGUAGE_CATALOG and any(
            l["code"] == lang for l in config.LANGUAGE_CATALOG
        ))

    def synth(self, text: str, lang: str) -> bytes:
        if not text or not text.strip():
            raise TTSAudioError("text is empty")
        if not isinstance(lang, str) or not self.supports(lang):
            raise TTSAudioError(
                f"provider '{self.name}' does not support language '{lang}'"
            )
        voice = self._voice_for(lang)
        try:
            import edge_tts  # local: keep top-level import lazy for tests
        except ImportError as e:
            raise TTSAudioError(
                "edge-tts package is not installed; run "
                "pip install -r backend/requirements.txt"
            ) from e

        # ``Communicate.save`` writes MP3 to a path. Use a NamedTemporaryFile
        # so the file is cleaned up on every exit branch, including exceptions.
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".mp3", delete=False
            ) as tmp:
                tmp_path = tmp.name
            try:
                import asyncio
                asyncio.run(self._save(text, voice, tmp_path))
                with open(tmp_path, "rb") as f:
                    body = f.read()
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except TTSAudioError:
            raise
        except Exception as e:
            raise TTSAudioError(f"edge-tts synth failed: {e}") from e

        if not body:
            raise TTSAudioError("edge-tts returned empty body")
        # Edge delivers MP3 frames. Reject anything that doesn't look
        # like an MP3 payload (matches the Google provider's safety net).
        if not (body[:3] == b"ID3" or body[0] == 0xFF):
            raise TTSAudioError("edge-tts returned non-audio payload")
        return body

    @staticmethod
    async def _save(text: str, voice: str, out_path: str) -> None:
        """Async helper so ``asyncio.run`` has a coroutine to drive."""
        import edge_tts
        comm = edge_tts.Communicate(text, voice)
        await comm.save(out_path)

    @staticmethod
    def _voice_for(lang: str) -> str:
        # ``supports`` already gated on this, but check again defensively
        # in case a caller bypassed it.
        if lang in _VOICES:
            return _VOICES[lang]
        # Last-resort fallback for any catalog language the table
        # doesn't list yet. Use the language code itself; the read-aloud
        # endpoint accepts arbitrary BCP-47 tags and picks a sensible voice.
        return lang
