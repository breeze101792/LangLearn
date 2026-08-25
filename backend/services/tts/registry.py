"""TTS provider registry.

Mirrors the dictionary chain registry but with a single active provider.
The active provider is selected by the user's `settings.tts_provider` value
(e.g. ``"google"``). Lookups fall back to the first registered provider if
the configured one is unknown — never fail open with nothing.
"""

from __future__ import annotations

import logging
from typing import Callable

from .base import TTSProvider

log = logging.getLogger(__name__)

PROVIDERS: dict[str, TTSProvider] = {}
# Optional display metadata for the Settings UI.
PROVIDER_META: dict[str, dict] = {}

# Provider used when the user hasn't picked one. Keep Google as the
# default for backward compatibility with existing single-user
# installs — adding Edge shouldn't silently retarget new users.
DEFAULT_PROVIDER_NAME: str = "google"


def register(name: str, provider: TTSProvider, *,
             display_name: str, description: str = "") -> None:
    PROVIDERS[name] = provider
    PROVIDER_META[name] = {
        "name": name,
        "display_name": display_name,
        "description": description,
    }


def available() -> list[str]:
    return sorted(PROVIDERS.keys())


def available_detailed() -> list[dict]:
    out: list[dict] = []
    for n in available():
        meta = dict(PROVIDER_META.get(n, {"name": n}))
        out.append(meta)
    return out


def get(name: str | None) -> TTSProvider | None:
    if not name:
        return None
    return PROVIDERS.get(name)


def active(name: str | None) -> TTSProvider | None:
    """Resolve the active provider: explicit name, else the configured
    default, else the first registered alphabetically, else None.
    Callers should treat None as 'no TTS available'."""
    if name and name in PROVIDERS:
        return PROVIDERS[name]
    if not PROVIDERS:
        return None
    # Explicit default beats alphabetical, so adding a new provider
    # (e.g. 'edge') doesn't silently change the default for users who
    # never picked one in Settings.
    if DEFAULT_PROVIDER_NAME in PROVIDERS:
        chosen = DEFAULT_PROVIDER_NAME
    else:
        chosen = sorted(PROVIDERS.keys())[0]
    if name and name not in PROVIDERS:
        log.info("tts provider %r not registered; falling back to %r", name, chosen)
    return PROVIDERS[chosen]


def synth(text: str, lang: str, provider_name: str | None) -> tuple[bytes, str]:
    """Synthesize audio. Returns ``(bytes, content_type)`` or raises
    ``TTSAudioError`` when the provider is missing/unsupported. The caller
    chooses the HTTP status code."""
    from .base import TTSAudioError  # local import to keep registry import-light
    provider = active(provider_name)
    if provider is None:
        raise TTSAudioError("no TTS provider registered")
    if not provider.supports(lang):
        raise TTSAudioError(
            f"provider '{provider.name}' does not support language '{lang}'"
        )
    audio = provider.synth(text, lang)
    return audio, provider.content_type


def bootstrap() -> None:
    """Register built-in providers. Idempotent."""
    if PROVIDERS:
        return
    from . import edge as edge_provider
    from . import google as google_provider
    register(
        "edge",
        edge_provider.EdgeTTS(),
        display_name="Microsoft Edge",
        description=(
            "Microsoft Edge read-aloud voices via the edge-tts package. "
            "No API key. Higher-quality neural voices than Google."
        ),
    )
    register(
        "google",
        google_provider.GoogleTTS(),
        display_name="Google Translate",
        description="Google's public translate_tts endpoint. No API key, unofficial.",
    )
