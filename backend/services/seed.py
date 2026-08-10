"""Seed loader and LLM-based seeder.

Built-in JSON lives in backend/data/built-in/<lang>.json.
LLM seeding is implemented in services.llm.seed (Phase C) and called here.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from .. import config
from ..db import get_conn, transaction
from ..util import is_valid_lang

log = logging.getLogger(__name__)

STRUCTURES_PER_LANG = 50
PHRASES_PER_LANG = 100


def get_seed_path(lang: str) -> Path | None:
    if not is_valid_lang(lang):
        return None
    candidates = [config.BUILTIN_SEED_DIR / f"{lang}.json",
                  config.BUILTIN_SEED_DIR / f"{_display_name_for(lang)}.json"]
    for p in candidates:
        if p.exists():
            return p
    return None


def _display_name_for(lang: str) -> str:
    for entry in config.LANGUAGE_CATALOG:
        if entry["code"] == lang:
            return entry["display_name"].lower()
    return lang


def load_builtin_seed(lang: str) -> dict | None:
    p = get_seed_path(lang)
    if p is None:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.error("failed to load seed %s: %s", p, e)
        return None


def is_seeded(lang: str, user_id: int = config.DEFAULT_USER_ID) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT seeded_at FROM languages WHERE code=? AND seeded_at IS NOT NULL",
            (lang,),
        ).fetchone()
    return row is not None


def ensure_language_row(lang: str, display_name: str | None = None, is_built_in: int = 0) -> None:
    if not is_valid_lang(lang):
        raise ValueError(f"invalid language code: {lang}")
    if display_name is None:
        display_name = lang.upper()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO languages (code, display_name, is_built_in) VALUES (?, ?, ?)"
            " ON CONFLICT(code) DO UPDATE SET display_name=excluded.display_name",
            (lang, display_name, is_built_in),
        )


def seed_builtin(lang: str, user_id: int = config.DEFAULT_USER_ID) -> dict:
    """Seed from built-in JSON. Returns counts inserted."""
    data = load_builtin_seed(lang)
    if data is None:
        raise FileNotFoundError(f"no built-in seed for {lang}")
    structures = data.get("structures", [])
    phrases = data.get("phrases", [])
    with transaction() as conn:
        _replace_built_in(conn, user_id, lang)
        for s in structures:
            conn.execute(
                "INSERT INTO structures ("
                "  user_id, language, pattern, example_sentence,"
                "  explanation, explanation_primary, explanation_secondary, source"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 'built-in')",
                (
                    user_id, lang,
                    s.get("pattern", ""),
                    s.get("example_sentence"),
                    s.get("explanation") or "",
                    s.get("explanation_primary"),
                    s.get("explanation_secondary"),
                ),
            )
        for p in phrases:
            conn.execute(
                "INSERT INTO phrases ("
                "  user_id, language, phrase, example_sentence,"
                "  explanation, explanation_primary, explanation_secondary, source"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 'built-in')",
                (
                    user_id, lang,
                    p.get("phrase", ""),
                    p.get("example_sentence"),
                    p.get("explanation") or "",
                    p.get("explanation_primary"),
                    p.get("explanation_secondary"),
                ),
            )
        conn.execute(
            "UPDATE languages SET seeded_at=datetime('now') WHERE code=?",
            (lang,),
        )
    return {"structures": len(structures), "phrases": len(phrases)}


def seed_via_llm(lang: str, user_id: int = config.DEFAULT_USER_ID,
                 n_structures: int = STRUCTURES_PER_LANG,
                 n_phrases: int = PHRASES_PER_LANG) -> dict:
    """Seed by calling the LLM service. Caller wraps in a seed_jobs row."""
    from . import settings as settings_svc
    from .llm import LLMError, apply_explanation_rules, generate_seed_payload

    user_settings = settings_svc.get_settings(user_id)
    primary = user_settings.get("explanation_primary")
    secondary = user_settings.get("explanation_secondary")
    try:
        payload = generate_seed_payload(
            lang, n_structures, n_phrases,
            primary=primary, secondary=secondary,
        )
    except LLMError:
        raise
    # Enforce the explanation-language rules at the persistence boundary
    # so the rules hold even if the LLM service is mocked.
    apply_explanation_rules(
        payload, lang=lang, primary=primary, secondary=secondary,
    )

    structures = payload.get("structures", [])
    phrases = payload.get("phrases", [])
    with transaction() as conn:
        _replace_built_in(conn, user_id, lang)
        for s in structures:
            conn.execute(
                "INSERT INTO structures ("
                "  user_id, language, pattern, example_sentence,"
                "  explanation, explanation_primary, explanation_secondary, source"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 'llm')",
                (
                    user_id, lang,
                    s.get("pattern", ""),
                    s.get("example_sentence"),
                    s.get("explanation") or "",
                    s.get("explanation_primary"),
                    s.get("explanation_secondary"),
                ),
            )
        for p in phrases:
            conn.execute(
                "INSERT INTO phrases ("
                "  user_id, language, phrase, example_sentence,"
                "  explanation, explanation_primary, explanation_secondary, source"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 'llm')",
                (
                    user_id, lang,
                    p.get("phrase", ""),
                    p.get("example_sentence"),
                    p.get("explanation") or "",
                    p.get("explanation_primary"),
                    p.get("explanation_secondary"),
                ),
            )
        conn.execute(
            "UPDATE languages SET seeded_at=datetime('now') WHERE code=?",
            (lang,),
        )
    return {"structures": len(structures), "phrases": len(phrases)}


def _replace_built_in(conn: sqlite3.Connection, user_id: int, lang: str) -> None:
    conn.execute(
        "DELETE FROM structures WHERE user_id=? AND language=? AND source IN ('built-in','llm')",
        (user_id, lang),
    )
    conn.execute(
        "DELETE FROM phrases WHERE user_id=? AND language=? AND source IN ('built-in','llm')",
        (user_id, lang),
    )


def apply_explanations(lang: str, user_id: int = config.DEFAULT_USER_ID) -> dict:
    """Translate the user's existing target-language structures and
    phrases into the user's current ``explanation_primary`` and
    ``explanation_secondary`` natives, per the
    [explanation-language rules].

    Does not touch the row's target-language content (pattern /
    example_sentence / phrase / literal_translation). Only the
    explanation columns are overwritten. Safe to call multiple times;
    the latest settings always win.

    Returns a counts dict ``{"structures": int, "phrases": int}``
    indicating how many rows were updated.

    Raises ``LLMError`` on provider / schema failure.
    """
    from . import settings as settings_svc
    from . import llm as llm_svc

    user_settings = settings_svc.get_settings(user_id)
    primary = user_settings.get("explanation_primary")
    secondary = user_settings.get("explanation_secondary")

    with get_conn() as conn:
        struct_rows = conn.execute(
            "SELECT id, pattern, example_sentence, explanation FROM structures"
            " WHERE user_id=? AND language=?",
            (user_id, lang),
        ).fetchall()
        phrase_rows = conn.execute(
            "SELECT id, phrase, example_sentence, explanation FROM phrases"
            " WHERE user_id=? AND language=?",
            (user_id, lang),
        ).fetchall()

    structures = [dict(r) for r in struct_rows]
    phrases = [dict(r) for r in phrase_rows]
    if not structures and not phrases:
        return {"structures": 0, "phrases": 0}

    payload = llm_svc.apply_explanations_via_llm(
        lang=lang, structures=structures, phrases=phrases,
        primary=primary, secondary=secondary,
    )

    updated_s = 0
    updated_p = 0
    with transaction() as conn:
        for item in (payload.get("structures") or []):
            if not isinstance(item, dict):
                continue
            row_id = item.get("id")
            if not isinstance(row_id, int):
                continue
            conn.execute(
                "UPDATE structures SET"
                "  explanation=?, explanation_primary=?, explanation_secondary=?"
                " WHERE id=? AND user_id=? AND language=?",
                (item.get("explanation"),
                 item.get("explanation_primary"),
                 item.get("explanation_secondary"),
                 row_id, user_id, lang),
            )
            updated_s += 1
        for item in (payload.get("phrases") or []):
            if not isinstance(item, dict):
                continue
            row_id = item.get("id")
            if not isinstance(row_id, int):
                continue
            conn.execute(
                "UPDATE phrases SET"
                "  explanation=?, explanation_primary=?, explanation_secondary=?"
                " WHERE id=? AND user_id=? AND language=?",
                (item.get("explanation"),
                 item.get("explanation_primary"),
                 item.get("explanation_secondary"),
                 row_id, user_id, lang),
            )
            updated_p += 1
    return {"structures": updated_s, "phrases": updated_p}


def initialize_language(lang: str, user_id: int = config.DEFAULT_USER_ID,
                        force: bool = False) -> dict:
    """Idempotent initializer. Built-in langs use JSON; others use LLM (when available)."""
    ensure_language_row(lang)
    if is_seeded(lang, user_id) and not force:
        return {"seeded": False, "reason": "already_seeded"}
    if get_seed_path(lang) is not None:
        counts = seed_builtin(lang, user_id)
        return {"seeded": True, "source": "built-in", **counts}
    counts = seed_via_llm(lang, user_id)
    return {"seeded": True, "source": "llm", **counts}