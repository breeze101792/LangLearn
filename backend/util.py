"""Helpers: ok/err response shapes, safe path, validators."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

LANG_CODE_RE = re.compile(r"^[a-z]{2,8}$")
WORD_RE = re.compile(r"^[\w\s'\-\.]+$", re.UNICODE)


def ok(data: Any) -> dict:
    return {"ok": True, "data": data}


def err(message: str, code: str | None = None) -> dict:
    payload: dict = {"ok": False, "error": message}
    if code:
        payload["code"] = code
    return payload


def safe_path(base: Path, *parts: str) -> Path:
    """Resolve a path under base; raise if traversal escapes."""
    candidate = base.joinpath(*parts).resolve()
    base_resolved = base.resolve()
    if os.path.commonpath([str(candidate), str(base_resolved)]) != str(base_resolved):
        raise ValueError("path traversal detected")
    return candidate


def is_valid_lang(code: Any) -> bool:
    return isinstance(code, str) and bool(LANG_CODE_RE.match(code))


def is_known_lang(code: Any) -> bool:
    """True if `code` is in the catalog AND syntactically valid."""
    if not is_valid_lang(code):
        return False
    from . import config
    return any(l["code"] == code for l in config.LANGUAGE_CATALOG)


def is_nonempty_str(value: Any, *, max_len: int = 1000) -> bool:
    return isinstance(value, str) and 0 < len(value) <= max_len


def is_word(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v or len(v) > 200:
        return False
    return bool(WORD_RE.match(v))


def normalize_word(value: str) -> str:
    """Normalize a user-typed word for dictionary lookup.

    Replaces internal whitespace with a single underscore so that phrases
    like ``"snap at"`` resolve the same way as ``"snap_at"`` (which is how
    WordNet indexes multi-word expressions). Hyphens, apostrophes, and dots
    are left intact because those are valid characters within a single
    lemma token. The result is also stripped and truncated to 200 chars.
    """
    if not isinstance(value, str):
        return ""
    v = value.strip()
    if not v:
        return ""
    parts = v.split()
    return "_".join(parts)[:200]


def clamp_int(value: Any, lo: int, hi: int) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, n))