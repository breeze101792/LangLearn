"""Configuration for LangLearn.

Reads environment variables; exposes paths, defaults, and LLM settings.
Nothing in this module reads from disk at import time beyond resolving the
project root.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _data_dir() -> Path:
    raw = os.environ.get("LANGLEARN_DATA_DIR")
    if raw:
        p = Path(raw).expanduser().resolve()
    else:
        p = (PROJECT_ROOT / "data").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _port() -> int:
    raw = os.environ.get("PORT")
    try:
        return int(raw) if raw else 5056
    except ValueError:
        return 5056


def _host() -> str:
    return os.environ.get("HOST", "0.0.0.0")


def _debug() -> bool:
    return os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")


HOST: str = _host()
PORT: int = _port()
DEBUG: bool = _debug()


def data_dir() -> Path:
    """Resolve the runtime data dir on every call so tests can override via env."""
    raw = os.environ.get("LANGLEARN_DATA_DIR")
    if raw:
        p = Path(raw).expanduser().resolve()
    else:
        p = (PROJECT_ROOT / "data").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "langlearn.sqlite"


DATA_DIR: Path = data_dir()
DB_PATH: Path = db_path()

BUILTIN_SEED_DIR: Path = PROJECT_ROOT / "backend" / "data" / "built-in"
FRONTEND_DIR: Path = PROJECT_ROOT / "frontend"
TEMPLATES_DIR: Path = FRONTEND_DIR / "templates"
STATIC_DIR: Path = FRONTEND_DIR / "static"

MIGRATIONS_DIR: Path = Path(__file__).resolve().parent / "migrations"

DEFAULT_USER_ID = 1
DEFAULT_LANGUAGE = "en"

LANGUAGE_CATALOG: list[dict] = [
    {"code": "en", "display_name": "English", "is_built_in": 1},
    {"code": "es", "display_name": "Spanish", "is_built_in": 0},
    {"code": "ja", "display_name": "Japanese", "is_built_in": 0},
    {"code": "pt", "display_name": "Portuguese", "is_built_in": 0},
    {"code": "zh", "display_name": "Traditional Chinese", "is_built_in": 0},
    {"code": "fr", "display_name": "French", "is_built_in": 0},
    {"code": "de", "display_name": "German", "is_built_in": 0},
]

OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL: str = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

LLM_TIMEOUT_SECONDS: int = int(os.environ.get("LLM_TIMEOUT_SECONDS", "120"))


# ---- TTS (pronunciation) -----------------------------------------------------
#
# `TTS_LANG_MAP` translates the app's `lang` codes into the TTS provider's
# language codes. The only non-trivial entry is `zh` (Traditional Chinese)
# which needs `zh-TW` for the Google endpoint so we get the right script.
#
# `TTS_TIMEOUT_SECONDS` caps the upstream network call. The disk cache lives
# under `data/tts_cache/<lang>/<sha1>.<ext>` to keep repeat lookups offline.
TTS_LANG_MAP: dict[str, str] = {
    "zh": "zh-TW",
}

TTS_TIMEOUT_SECONDS: int = int(os.environ.get("TTS_TIMEOUT_SECONDS", "15"))


def tts_cache_dir() -> Path:
    p = data_dir() / "tts_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p