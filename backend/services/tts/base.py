"""TTS provider interface and shared error type.

Concrete providers (e.g. `google.GoogleTTS`) implement the two methods
below. The registry in `registry.py` dispatches calls to the active
provider selected by the user's settings.
"""

from __future__ import annotations

from typing import Protocol


class TTSAudioError(Exception):
    """Raised by a provider when synthesis fails (network, empty response,
    unsupported language, etc.). The blueprint translates this to HTTP 502.
    """


class TTSProvider(Protocol):
    """Minimum surface a TTS provider must expose.

    `name` is the registry key (used in `settings.tts_provider`).
    `synth` returns raw audio bytes (caller interprets Content-Type via the
    `content_type` attribute). `supports` is a quick predicate so the UI
    can show the provider in a dropdown only for languages it handles.
    """

    name: str
    content_type: str

    def synth(self, text: str, lang: str) -> bytes: ...

    def supports(self, lang: str) -> bool: ...
