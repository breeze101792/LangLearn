"""Dictionary lookup blueprint."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

from .. import config
from ..services import settings as settings_svc
from ..services import vocab as vocab_svc
from ..services.dictionaries import registry
from ..services.dictionaries import suggest as suggest_svc
from ..util import err, is_valid_lang, is_word, normalize_word, ok

bp = Blueprint("dictionary", __name__, url_prefix="/api/dictionary")


@bp.post("/lookup")
def lookup():
    body = request.get_json(silent=True) or {}
    lang = body.get("lang")
    word = body.get("word")
    provider_override = body.get("provider")
    if not isinstance(lang, str) or not is_valid_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    if not isinstance(word, str) or not is_word(word):
        return jsonify(err("word must be 1-200 chars of letters", code="invalid_word")), 400
    word = normalize_word(word)
    if not word:
        return jsonify(err("word must be 1-200 chars of letters", code="invalid_word")), 400
    settings = settings_svc.get_settings(config.DEFAULT_USER_ID)
    chain = settings["dict_chain_json"].get(lang, []) if isinstance(settings["dict_chain_json"], dict) else []

    used_provider: str | None = None
    if provider_override is not None:
        if provider_override == "" or provider_override is None:
            provider_override = None
        elif not isinstance(provider_override, str) or provider_override not in registry.available_providers():
            return jsonify(err("unknown provider", code="unknown_provider")), 400
        elif not registry.supports_provider(provider_override, lang):
            return jsonify(err(
                f"provider '{provider_override}' does not support language '{lang}'",
                code="provider_unsupported_lang",
            )), 400

    if provider_override:
        entry = registry.lookup_with_provider(
            word=word, lang=lang, provider_name=provider_override,
            explanation_primary=settings.get("explanation_primary"),
            explanation_secondary=settings.get("explanation_secondary"),
        )
        used_provider = provider_override
    else:
        entry = registry.lookup_via_chain(
            word=word,
            lang=lang,
            chain=chain,
            explanation_primary=settings.get("explanation_primary"),
            explanation_secondary=settings.get("explanation_secondary"),
        )
    auto_added = False
    if entry.is_empty and not chain and not provider_override:
        return ok({"entry": entry.to_dict(), "source": "", "auto_added": False,
                   "providers_in_chain": 0})
    if not entry.is_empty and settings.get("auto_add_vocab"):
        try:
            auto_added = vocab_svc.auto_add_from_lookup(
                user_id=config.DEFAULT_USER_ID,
                entry=entry,
                auto_add_enabled=True,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("auto-add failed for %s: %s", word, e)
    payload = {
        "entry": entry.to_dict(),
        "source": entry.source,
        "auto_added": auto_added,
        "providers_in_chain": len(chain),
    }
    if used_provider:
        payload["provider"] = used_provider
    if entry.is_empty:
        payload["suggestions"] = suggest_svc.fuzzy(
            lang=lang, user_id=config.DEFAULT_USER_ID, query=word,
        )
    return ok(payload)


@bp.post("/<provider>")
def force_provider(provider: str):
    """Manual 'Look up with AI' button — forces a specific provider."""
    if provider not in registry.available_providers():
        return jsonify(err("unknown provider", code="unknown_provider")), 404
    body = request.get_json(silent=True) or {}
    lang = body.get("lang")
    word = body.get("word")
    if not isinstance(lang, str) or not is_valid_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    if not isinstance(word, str) or not is_word(word):
        return jsonify(err("word must be 1-200 chars of letters", code="invalid_word")), 400
    word = normalize_word(word)
    if not word:
        return jsonify(err("word must be 1-200 chars of letters", code="invalid_word")), 400
    settings = settings_svc.get_settings(config.DEFAULT_USER_ID)
    entry = registry.lookup_with_provider(
        word=word, lang=lang, provider_name=provider,
        explanation_primary=settings.get("explanation_primary"),
        explanation_secondary=settings.get("explanation_secondary"),
    )
    auto_added = False
    if not entry.is_empty and settings.get("auto_add_vocab"):
        try:
            auto_added = vocab_svc.auto_add_from_lookup(
                user_id=config.DEFAULT_USER_ID,
                entry=entry,
                auto_add_enabled=True,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("auto-add failed: %s", e)
    return ok({
        "entry": entry.to_dict(),
        "source": entry.source,
        "provider": provider,
        "auto_added": auto_added,
    })


@bp.get("/providers")
def providers():
    """List registered providers with display metadata and, when `lang` is
    passed as a query parameter, which ones support that language."""
    lang = request.args.get("lang")
    if lang is not None and (not isinstance(lang, str) or not is_valid_lang(lang)):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    items = registry.available_providers_detailed()
    if lang is not None:
        for item in items:
            item["supports"] = registry.supports_provider(item["name"], lang)
    llm_ready, llm_kind = _llm_status()
    for item in items:
        if item.get("name") == "llm":
            item["configured"] = llm_ready
            item["provider_kind"] = llm_kind
    return ok({"providers": items, "llm_configured": llm_ready,
               "llm_provider_kind": llm_kind})


def _llm_status() -> tuple[bool, str]:
    """True when the configured LLM provider has the credentials it needs.

    Mirrors the env-var checks inside `OpenAICompatClient` / `OllamaCompatClient`
    so the UI can warn the user before they wait on a lookup that will fail.
    Reads env vars per call so test fixtures that mutate env after import
    time still see the latest values.
    """
    import os
    kind = (os.environ.get("LLM_PROVIDER") or config.LLM_PROVIDER or "openai").lower()
    if kind == "ollama":
        base = os.environ.get("OLLAMA_BASE_URL") or config.OLLAMA_BASE_URL
        return bool(base), kind
    # OpenAI-compatible: any URL + a non-empty API key is enough for most
    # providers. Allow missing key when the base URL is non-OpenAI (some
    # local proxies don't require auth).
    base = os.environ.get("OPENAI_BASE_URL") or config.OPENAI_BASE_URL
    if base and "api.openai.com" not in base:
        return True, kind
    key = os.environ.get("OPENAI_API_KEY") or config.OPENAI_API_KEY
    return bool(key), kind


@bp.get("/suggest")
def suggest():
    """Prefix suggestions for the live search dropdown."""
    q = request.args.get("q", "")
    lang = request.args.get("lang", "")
    if not isinstance(lang, str) or not is_valid_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    if not isinstance(q, str):
        return jsonify(err("invalid query", code="invalid_query")), 400
    limit = suggest_svc.DEFAULT_LIMIT
    raw_limit = request.args.get("limit")
    if raw_limit is not None:
        try:
            n = int(raw_limit)
        except (TypeError, ValueError):
            return jsonify(err("limit must be an integer", code="invalid_limit")), 400
        limit = max(1, min(n, suggest_svc.MAX_LIMIT))
    suggestions = suggest_svc.prefix(
        lang=lang, user_id=config.DEFAULT_USER_ID, query=q, limit=limit,
    )
    return ok({"query": q.strip().lower(), "lang": lang, "suggestions": suggestions})