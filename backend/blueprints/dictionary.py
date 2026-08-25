"""Dictionary lookup blueprint."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

from .. import config
from ..services import settings as settings_svc
from ..services import vocab as vocab_svc
from ..services.dictionaries import installer as dict_installer
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
    client_chain = body.get("chain")
    if not isinstance(lang, str) or not is_valid_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    if not isinstance(word, str) or not is_word(word):
        return jsonify(err("word must be 1-200 chars of letters", code="invalid_word")), 400
    word = normalize_word(word)
    if not word:
        return jsonify(err("word must be 1-200 chars of letters", code="invalid_word")), 400
    settings = settings_svc.get_settings(config.DEFAULT_USER_ID)
    # The chain the client asks the server to run. When the client
    # walks the chain itself (Wiktionary is browser-side, so the
    # client tries it first and only falls through here on miss), it
    # sends a client-pruned chain containing only server-side
    # providers. Otherwise we use the user's stored chain.
    if isinstance(client_chain, list):
        chain = client_chain
    else:
        chain = settings["dict_chain_json"].get(lang, []) if isinstance(settings["dict_chain_json"], dict) else []
    level = settings_svc.get_language_level(lang, config.DEFAULT_USER_ID)

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

    chain_errors: list[dict] = []
    if provider_override:
        result = registry.lookup_with_provider(
            word=word, lang=lang, provider_name=provider_override,
            explanation_primary=settings.get("explanation_primary"),
            explanation_secondary=settings.get("explanation_secondary"),
            level=level,
        )
        used_provider = provider_override
    else:
        result = registry.lookup_via_chain(
            word=word,
            lang=lang,
            chain=chain,
            explanation_primary=settings.get("explanation_primary"),
            explanation_secondary=settings.get("explanation_secondary"),
            level=level,
        )
    entry = result.entry
    chain_errors = result.errors
    auto_added = False
    if entry.is_empty and not chain and not provider_override:
        return ok({"entry": entry.to_dict(), "source": "", "auto_added": False,
                   "providers_in_chain": 0, "provider_errors": []})
    if not entry.is_empty and settings.get("auto_add_vocab"):
        try:
            auto_added = vocab_svc.auto_add_from_lookup(
                user_id=config.DEFAULT_USER_ID,
                entry=entry,
                auto_add_enabled=True,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("auto-add failed for %s: %s", word, e)
    # Always include the current vocab state so the card can render either
    # the box badge (in vocab) or the "Add to box 1" button (not in vocab)
    # without a follow-up request.
    vocab_state = vocab_svc.find_vocab_box(
        user_id=config.DEFAULT_USER_ID, language=lang, word=word,
    )
    payload = {
        "entry": entry.to_dict(),
        "source": entry.source,
        "auto_added": auto_added,
        "providers_in_chain": len(chain),
        "provider_errors": chain_errors,
        "in_vocab": vocab_state is not None,
        "leitner_box": vocab_state["leitner_box"] if vocab_state else None,
        "vocab_id": vocab_state["id"] if vocab_state else None,
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
    result = registry.lookup_with_provider(
        word=word, lang=lang, provider_name=provider,
        explanation_primary=settings.get("explanation_primary"),
        explanation_secondary=settings.get("explanation_secondary"),
        level=settings_svc.get_language_level(lang, config.DEFAULT_USER_ID),
    )
    entry = result.entry
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
        "provider_errors": result.errors,
    })


@bp.get("/providers")
def providers():
    """List providers with display metadata and, when `lang` is passed
    as a query parameter, which ones support that language and are
    currently installed for it.

    Server-side providers come from the chain registry (WordNet, LLM).
    Client-side providers (Wiktionary) come from the catalog: they're
    not registered in the chain executor but the dictionary page needs
    to know they exist and are installed so the chain walker can
    dispatch them in the browser.
    """
    lang = request.args.get("lang")
    if lang is not None and (not isinstance(lang, str) or not is_valid_lang(lang)):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    items = registry.available_providers_detailed()
    # Augment with client-side catalog entries that the registry
    # doesn't know about, so the UI sees a unified list.
    catalog_items = dict_installer.catalog_view(lang)
    catalog_by_name: dict[str, dict] = {e["provider"]: e for e in catalog_items}
    for entry in catalog_items:
        if entry.get("client_side") is not True:
            continue
        if any(i["name"] == entry["provider"] for i in items):
            continue
        items.append({
            "name": entry["provider"],
            "display_name": entry["display_name"],
            "description": entry["description"],
            "kind": "online",
            "client_side": True,
        })
    if lang is not None:
        installed = registry.installed_providers_for(lang)
        for item in items:
            name = item.get("name")
            if not isinstance(name, str):
                continue
            catalog_entry = catalog_by_name.get(name, {})
            if name == "llm":
                # LLM is always considered installed — it has no
                # install row, the user just needs the env vars.
                item["installed"] = True
                item["supports"] = True
                continue
            if item.get("client_side"):
                item["supports"] = lang in catalog_entry.get("languages", [])
            else:
                item["supports"] = registry.supports_provider(name, lang)
            item["installed"] = name in installed
    llm_ready, llm_kind = _llm_status()
    for item in items:
        if item.get("name") == "llm":
            item["configured"] = llm_ready
            item["provider_kind"] = llm_kind
    return ok({"providers": items, "llm_configured": llm_ready,
               "llm_provider_kind": llm_kind})


@bp.get("/catalog")
def catalog():
    """Available offline dictionaries with per-language install status.

    Shape: ``{"entries": [...], "installed": {lang: [provider, ...]}}``.
    The Settings UI consumes this to render the install / uninstall
    controls. LLM is intentionally absent from the catalog — it has no
    install state, it's always available as a fallback.
    """
    lang = request.args.get("lang")
    if lang is not None and (not isinstance(lang, str) or not is_valid_lang(lang)):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    entries = dict_installer.catalog_view(lang)
    installed_raw = dict_installer.installed_providers()
    installed_dict = installed_raw if isinstance(installed_raw, dict) else {}
    installed = {k: sorted(v) for k, v in installed_dict.items()}
    return ok({"entries": entries, "installed": installed})


@bp.post("/install")
def install_dictionary():
    """Mark ``{provider, language}`` as installed. Idempotent.

    For v1 this is just a marker row — WordNet's data was already
    fetched by NLTK at first lookup. Future offline dictionaries with
    large data files will do their download here.
    """
    body = request.get_json(silent=True) or {}
    provider = body.get("provider")
    language = body.get("language")
    if not isinstance(provider, str) or not provider:
        return jsonify(err("provider is required", code="invalid_provider")), 400
    if not isinstance(language, str) or not is_valid_lang(language):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    try:
        result = dict_installer.install(provider, language)
    except ValueError as e:
        return jsonify(err(str(e), code="unknown_dictionary")), 404
    return ok({
        "provider": result.provider,
        "language": result.language,
        "installed": result.installed,
        "already": result.already,
        "client_side": result.client_side,
    })


@bp.post("/uninstall")
def uninstall_dictionary():
    """Drop the install row for ``{provider, language}``. The UI prevents
    uninstalling auto-installed dictionaries; this endpoint enforces the
    same invariant on the server side."""
    body = request.get_json(silent=True) or {}
    provider = body.get("provider")
    language = body.get("language")
    if not isinstance(provider, str) or not provider:
        return jsonify(err("provider is required", code="invalid_provider")), 400
    if not isinstance(language, str) or not is_valid_lang(language):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    from ..services.dictionaries import catalog as catalog_mod
    if catalog_mod.find(provider, language) is None:
        return jsonify(err("unknown dictionary", code="unknown_dictionary")), 404
    if dict_installer.is_protected(provider, language):
        return jsonify(err(
            f"{provider} is a default dictionary for {language} and cannot be uninstalled",
            code="protected_dictionary",
        )), 409
    removed = dict_installer.uninstall(provider, language)
    return ok({"provider": provider, "language": language, "removed": removed})


def _llm_status() -> tuple[bool, str]:
    """True when at least one OpenAI-compatible provider has the credentials
    it needs. The primary counts if it has any URL + (if it's OpenAI's
    hosted API) a key; otherwise it's good to go without a key. The
    secondary counts whenever its base URL is set — non-OpenAI endpoints
    like a local Ollama proxy work without auth.

    Mirrors the env-var checks inside the LLM clients so the UI can warn
    the user before they wait on a lookup that will fail. Reads env vars
    per call so test fixtures that mutate env after import time still see
    the latest values.
    """
    import os
    kind = "openai-compat"
    # Primary: any URL is enough; key only required for api.openai.com.
    base = os.environ.get("OPENAI_BASE_URL") or config.OPENAI_BASE_URL
    if base and "api.openai.com" not in base:
        return True, kind
    key = os.environ.get("OPENAI_API_KEY") or config.OPENAI_API_KEY
    if key:
        return True, kind
    # Secondary: configured (base URL set) is enough on its own — the
    # fallback handles the rest. Mirrors ``config.secondary_llm_configured()``.
    if config.secondary_llm_configured():
        return True, kind
    return False, kind


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