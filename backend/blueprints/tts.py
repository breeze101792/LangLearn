"""TTS (pronunciation) blueprint.

Endpoints:
  GET /api/tts/audio?lang=&word=  — raw audio bytes (Content-Type per provider)
  GET /api/tts/providers           — list registered providers (Settings UI)

The audio endpoint is the only non-JSON endpoint in the API. It returns
``Content-Type: audio/mpeg`` (or whatever the active provider supplies)
directly because there is no useful ``{ok,data}`` framing for binary blobs.
Errors are still returned as JSON with the standard envelope so the
frontend can show a useful message instead of decoding raw bytes.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request

from .. import config
from ..services import settings as settings_svc
from ..services.tts import registry as tts_registry
from ..services.tts.base import TTSAudioError
from ..util import err, is_known_lang, normalize_word, ok

log = logging.getLogger(__name__)

bp = Blueprint("tts", __name__, url_prefix="/api/tts")

# TTS accepts full sentences (with punctuation) up to this length.
# The dictionary validator (is_word) stays strict for WordNet
# lookups; this is the looser equivalent for the right-click
# "speak this selection" flow. 200 chars matches Google TTS's
# practical limit before audio quality degrades.
import re as _re
_TTS_PHRASE_RE = _re.compile(
    r"^[\w\s'\"\-\.,;:!\?—–\(\)…\u00BB\u00AB]+$",
    _re.UNICODE,
)
_MAX_TTS_CHARS = 200


def _is_speakable_phrase(value: object) -> bool:
    """A TTS phrase: letters, numbers, whitespace, common sentence
    punctuation. Up to 200 chars."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v or len(v) > _MAX_TTS_CHARS:
        return False
    return bool(_TTS_PHRASE_RE.match(v))


@bp.get("/providers")
def providers():
    """List registered TTS providers with display metadata. The Settings
    page uses this to render the provider dropdown."""
    return ok({"providers": tts_registry.available_detailed()})


@bp.get("/audio")
def audio():
    lang = request.args.get("lang", "")
    word = request.args.get("word", "")
    if not isinstance(lang, str) or not is_known_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    # TTS accepts full sentences with punctuation; the dictionary
    # validator (is_word) is stricter because WordNet only has lemma
    # entries. _is_speakable_phrase is the looser TTS equivalent.
    if not isinstance(word, str) or not _is_speakable_phrase(word):
        return jsonify(err(
            "phrase must be 1-200 chars of letters, numbers, or sentence punctuation",
            code="invalid_phrase",
        )), 400
    word = normalize_word(word)
    if not word:
        return jsonify(err(
            "phrase must be 1-200 chars of letters, numbers, or sentence punctuation",
            code="invalid_phrase",
        )), 400

    # The TTS provider choice is a per-user setting; resolve here so a
    # mid-session change takes effect on the next click.
    settings = settings_svc.get_settings(config.DEFAULT_USER_ID)
    provider_name = settings.get("tts_provider")

    # The provider works with human-readable text; multi-word entries are
    # stored with underscores after `normalize_word`. Split those back out.
    text = word.replace("_", " ").strip()
    if not text:
        return jsonify(err(
            "phrase must be 1-200 chars of letters, numbers, or sentence punctuation",
            code="invalid_phrase",
        )), 400

    cache_path = _cache_path(lang, word, provider_name)
    cached = _read_cache(cache_path)
    if cached is not None:
        body, content_type = cached
        return _audio_response(body, content_type, cache_hit=True)

    try:
        body, content_type = tts_registry.synth(text, lang, provider_name)
    except TTSAudioError as e:
        log.info("tts synth failed (%s/%s, %s): %s", lang, word, provider_name, e)
        return jsonify(err(str(e), code="tts_failed")), 502

    _write_cache(cache_path, body)
    return _audio_response(body, content_type, cache_hit=False)


def _audio_response(body: bytes, content_type: str, *, cache_hit: bool) -> Response:
    resp = Response(body, mimetype=content_type)
    # Browser-side: revisit the same URL within 24h without re-asking the
    # server. Audio rarely changes; this is safe.
    resp.headers["Cache-Control"] = "public, max-age=86400"
    resp.headers["X-TTS-Cache"] = "hit" if cache_hit else "miss"
    return resp


def _cache_path(lang: str, word: str, provider_name: str | None) -> str:
    """Path under the TTS cache dir for ``(lang, word, provider)``.

    The provider name is part of the key so a switch to a different
    provider doesn't replay the old provider's audio.
    """
    key = f"{provider_name or 'default'}|{lang}|{word.lower()}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    base = config.tts_cache_dir()
    return str(base / lang / f"{digest}.mp3")


def _read_cache(path: str) -> tuple[bytes, str] | None:
    try:
        with open(path, "rb") as f:
            return f.read(), "audio/mpeg"
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning("tts cache read failed for %s: %s", path, e)
        return None


def _write_cache(path: str, body: bytes) -> None:
    parent = os.path.dirname(path)
    try:
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp.", dir=parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as e:
        log.warning("tts cache write failed for %s: %s", path, e)
