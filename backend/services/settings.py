"""User settings (single-user v1; future-proof for multi-user).

The settings row for user_id=1 holds all configurable behaviour.
"""

from __future__ import annotations

import json
from typing import Any

from .. import config
from ..db import get_conn
from ..util import is_valid_lang

DEFAULTS: dict[str, Any] = {
    "active_language": config.DEFAULT_LANGUAGE,
    "auto_add_vocab": 1,
    "page_size": 20,
    "review_session_size": 30,
    "explanation_primary": "en",
    "explanation_secondary": None,
    "dict_chain_json": {},
    "theme": "auto",
    "show_readings": 1,
    "tts_provider": "google",
}

ALLOWED_KEYS = set(DEFAULTS.keys())


def default_dict_chain() -> dict[str, list[dict]]:
    """Initial chain: WordNet (where supported) then LLM, per language."""
    chain: dict[str, list[dict]] = {}
    for lang in config.LANGUAGE_CATALOG:
        code = lang["code"]
        providers = []
        if code == "en":
            providers.append({"name": "wordnet", "enabled": True})
        providers.append({"name": "llm", "enabled": True})
        chain[code] = providers
    return chain


def get_settings(user_id: int = config.DEFAULT_USER_ID) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM settings WHERE user_id=?", (user_id,)
        ).fetchone()
    if row is None:
        create_default_settings(user_id)
        return get_settings(user_id)
    return _row_to_dict(row)


def create_default_settings(user_id: int = config.DEFAULT_USER_ID) -> None:
    chain_json = json.dumps(default_dict_chain(), ensure_ascii=False)
    from . import seed as seed_svc
    seed_svc.ensure_language_row(DEFAULTS["active_language"], "English", is_built_in=1)
    for lang in config.LANGUAGE_CATALOG:
        if lang["code"] != DEFAULTS["active_language"]:
            seed_svc.ensure_language_row(lang["code"], lang["display_name"], is_built_in=lang["is_built_in"])
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO settings ("
            "  user_id, active_language, auto_add_vocab, page_size,"
            "  review_session_size, explanation_primary, explanation_secondary,"
            "  dict_chain_json, theme, show_readings, tts_provider"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                DEFAULTS["active_language"],
                DEFAULTS["auto_add_vocab"],
                DEFAULTS["page_size"],
                DEFAULTS["review_session_size"],
                DEFAULTS["explanation_primary"],
                DEFAULTS["explanation_secondary"],
                chain_json,
                DEFAULTS["theme"],
                DEFAULTS["show_readings"],
                DEFAULTS["tts_provider"],
            ),
        )


def update_settings(updates: dict, user_id: int = config.DEFAULT_USER_ID) -> dict:
    if not isinstance(updates, dict):
        raise ValueError("updates must be an object")

    cleaned: dict[str, Any] = {}
    for key, value in updates.items():
        if key not in ALLOWED_KEYS:
            raise ValueError(f"unknown setting key: {key}")
        cleaned[key] = _coerce(key, value)

    fields = list(cleaned.keys())
    if not fields:
        return get_settings(user_id)

    sets_parts = []
    params: list[Any] = []
    for k in fields:
        v = cleaned[k]
        if k == "dict_chain_json":
            v = json.dumps(v, ensure_ascii=False)
        sets_parts.append(f"{k}=?")
        params.append(v)
    sets = ", ".join(sets_parts)
    create_default_settings(user_id)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE settings SET {sets}, updated_at=datetime('now') WHERE user_id=?",
            (*params, user_id),
        )
    return get_settings(user_id)


def get_dict_chain(lang: str, user_id: int = config.DEFAULT_USER_ID) -> list[dict]:
    settings = get_settings(user_id)
    chain = settings["dict_chain_json"]
    if not isinstance(chain, dict):
        return []
    entries = chain.get(lang, [])
    return [e for e in entries if isinstance(e, dict) and "name" in e]


def set_dict_chain(lang: str, entries: list[dict], user_id: int = config.DEFAULT_USER_ID) -> dict:
    if not is_valid_lang(lang):
        raise ValueError(f"unknown language: {lang}")
    cleaned_chain = _clean_dict_chain({lang: entries})
    cleaned = cleaned_chain[lang]
    settings = get_settings(user_id)
    chain = dict(settings["dict_chain_json"]) if isinstance(settings["dict_chain_json"], dict) else {}
    chain[lang] = cleaned
    update_settings({"dict_chain_json": chain}, user_id)
    return get_settings(user_id)


def _coerce(key: str, value: Any) -> Any:
    if key in ("active_language", "explanation_primary", "explanation_secondary"):
        if value is None or value == "":
            return None
        if not is_valid_lang(value):
            raise ValueError(f"invalid language code for {key}: {value}")
        return value
    if key == "auto_add_vocab" or key == "show_readings":
        return 1 if _truthy(value) else 0
    if key == "page_size" or key == "review_session_size":
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be int")
        if not 5 <= n <= 50:
            raise ValueError(f"{key} must be between 5 and 50")
        return n
    if key == "theme":
        if value not in ("auto", "light", "dark"):
            raise ValueError("theme must be auto|light|dark")
        return value
    if key == "dict_chain_json":
        if not isinstance(value, dict):
            raise ValueError("dict_chain_json must be an object")
        return _clean_dict_chain(value)
    if key == "tts_provider":
        if not isinstance(value, str):
            raise ValueError("tts_provider must be a string")
        # Validate against the live TTS registry. We import lazily to avoid
        # a circular import: the tts module imports config, which loads
        # before services.
        from .tts import registry as tts_registry
        if value not in tts_registry.available():
            raise ValueError(f"unknown tts provider: {value!r}")
        return value
    raise ValueError(f"unhandled key {key}")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes", "on")
    return False


def _clean_dict_chain(value: Any) -> dict[str, list[dict]]:
    """Validate and normalize a dict_chain_json payload.

    Shape: { lang_code: [{"name": "...", "enabled": bool}, ...] }.
    Unknown provider names and malformed entries are rejected so we never
    persist garbage that the chain executor would silently skip.

    Invariant: every language's chain contains `llm` as an enabled provider.
    If the user submits a chain without `llm`, it is appended at the end
    so the AI fallback is always available.
    """
    from .dictionaries import registry
    known = set(registry.available_providers())
    if not isinstance(value, dict):
        raise ValueError("dict_chain_json must be an object")
    out: dict[str, list[dict]] = {}
    for lang, entries in value.items():
        if not is_valid_lang(lang):
            raise ValueError(f"dict_chain_json: unknown language '{lang}'")
        if not isinstance(entries, list):
            raise ValueError(f"dict_chain_json[{lang}] must be a list")
        cleaned: list[dict] = []
        seen: set[str] = set()
        for e in entries:
            if not isinstance(e, dict) or "name" not in e:
                raise ValueError(f"dict_chain_json[{lang}]: each entry needs a 'name'")
            name = e["name"]
            if not isinstance(name, str):
                raise ValueError(f"dict_chain_json[{lang}]: 'name' must be a string")
            if name not in known:
                raise ValueError(f"dict_chain_json[{lang}]: unknown provider '{name}'")
            if name in seen:
                raise ValueError(f"dict_chain_json[{lang}]: provider '{name}' listed twice")
            seen.add(name)
            cleaned.append({"name": name, "enabled": bool(e.get("enabled", True))})
        # Invariant: LLM must always be present and enabled, so the user has
        # an AI fallback even if they disable every other provider.
        if "llm" not in seen:
            cleaned.append({"name": "llm", "enabled": True})
        else:
            # Re-enable llm if the user submitted it disabled. The Settings
            # UI also prevents disabling it; this is defense in depth.
            for entry in cleaned:
                if entry["name"] == "llm":
                    entry["enabled"] = True
                    break
        out[lang] = cleaned
    return out


def _row_to_dict(row) -> dict:
    chain_raw = row["dict_chain_json"]
    chain = json.loads(chain_raw) if chain_raw else {}
    out = {
        "user_id": row["user_id"],
        "active_language": row["active_language"],
        "auto_add_vocab": bool(row["auto_add_vocab"]),
        "page_size": row["page_size"],
        "explanation_primary": row["explanation_primary"],
        "explanation_secondary": row["explanation_secondary"],
        "dict_chain_json": chain,
        "theme": row["theme"],
        "show_readings": bool(row["show_readings"]),
    }
    # `tts_provider` is added in migration 007; older DB rows may not have
    # the column. Tolerate that with a fallback to the default.
    try:
        out["tts_provider"] = row["tts_provider"]
    except (IndexError, KeyError):
        out["tts_provider"] = DEFAULTS["tts_provider"]
    # `review_session_size` is added in migration 009; older DB rows may
    # not have the column. Tolerate that with a fallback to the default.
    try:
        out["review_session_size"] = row["review_session_size"]
    except (IndexError, KeyError):
        out["review_session_size"] = DEFAULTS["review_session_size"]
    return out