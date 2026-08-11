"""Google Translate TTS provider.

Uses the public `translate.google.com/translate_tts` endpoint. No API key
required. Returns MP3 bytes. The endpoint is unofficial and may rate-limit
or change without notice; the registry / module boundary makes it cheap to
swap in an alternative later (Edge TTS, Azure, local model, ...).

`tl` codes: pass through the app's `lang` (which is already a TTS code for
most languages). The app's `zh` is Traditional Chinese, so we map it to
`zh-TW`. The mapping is sourced from `config.TTS_LANG_MAP` so users can
override it without editing code.
"""

from __future__ import annotations

import logging
import urllib.parse

import requests

from ... import config
from .base import TTSAudioError

log = logging.getLogger(__name__)


# Browser User-Agent — Google serves a small subset of clients without one
# (the response body is empty or an HTML error page).
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_ENDPOINT = "https://translate.google.com/translate_tts"


class GoogleTTS:
    name = "google"
    content_type = "audio/mpeg"

    def supports(self, lang: str) -> bool:
        return _is_catalog_lang(lang)

    def synth(self, text: str, lang: str) -> bytes:
        if not text or not text.strip():
            raise TTSAudioError("text is empty")
        tl = _to_tts_lang(lang)
        params = {
            "ie": "UTF-8",
            "q": text,
            "tl": tl,
            "client": "tw-ob",
            "total": "1",
            "idx": "0",
            "textlen": str(len(text)),
        }
        url = f"{_ENDPOINT}?{urllib.parse.urlencode(params)}"
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": _USER_AGENT, "Referer": "https://translate.google.com/"},
                timeout=config.TTS_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise TTSAudioError(f"network error: {e}") from e
        if resp.status_code != 200:
            raise TTSAudioError(f"upstream HTTP {resp.status_code}")
        body = resp.content
        if not body:
            raise TTSAudioError("upstream returned empty body")
        # Google occasionally returns an HTML error page with 200; reject
        # any payload that doesn't start with the MP3 magic bytes.
        if not (body[:3] == b"ID3" or body[0] == 0xFF):
            raise TTSAudioError("upstream returned non-audio payload")
        return body


def _to_tts_lang(lang: str) -> str:
    """Map an app language code to a TTS language code (e.g. zh -> zh-TW)."""
    if not isinstance(lang, str):
        raise TTSAudioError("lang must be a string")
    return config.TTS_LANG_MAP.get(lang, lang)


def _is_catalog_lang(lang: str) -> bool:
    if not isinstance(lang, str):
        return False
    return any(l["code"] == lang for l in config.LANGUAGE_CATALOG)
