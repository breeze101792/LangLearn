"""Translate blueprint: take a sentence or short paragraph in any
language and return a translation into the target (active) language,
plus a teaching breakdown — alternative phrasings, a word-by-word
gloss, and a short grammar note. The source language is auto-detected
by the model.

The endpoint is fire-and-forget: nothing about the request or response
is persisted. The page is a study aid, not a knowledge store.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import config
from ..services import llm as llm_svc
from ..services import settings as settings_svc
from ..util import err, is_known_lang, ok

bp = Blueprint("translate", __name__, url_prefix="/api/translate")


MAX_TEXT_LEN = 4000


@bp.post("")
def translate_text():
    """Run :func:`llm.translate_text_via_llm` on the request body.

    Body: ``{"text": "..."}`` or ``{"target_language": "en", "text": "..."}``.
    ``target_language`` defaults to the user's active language when
    omitted. The source language is auto-detected by the model. The
    user's ``explanation_primary`` / ``explanation_secondary`` settings
    are pulled server-side so the client can't override them.

    Returns the parsed ``{sentences, notes, notes_primary?,
    notes_secondary?}`` object where each sentence carries its own
    ``translation``, ``alternatives`` (with nuance), ``breakdown``, and
    ``notes``.
    """
    body = request.get_json(silent=True) or {}
    user_settings = settings_svc.get_settings(config.DEFAULT_USER_ID)
    target_lang = body.get("target_language") or user_settings.get("active_language")
    text = body.get("text")
    if not isinstance(target_lang, str) or not is_known_lang(target_lang):
        return jsonify(err("invalid target language", code="invalid_lang")), 400
    if not isinstance(text, str) or not text.strip():
        return jsonify(err("text required", code="invalid_input")), 400
    text = text.strip()
    if len(text) > MAX_TEXT_LEN:
        return jsonify(err(f"text too long (>{MAX_TEXT_LEN})",
                           code="invalid_input")), 400

    primary = user_settings.get("explanation_primary")
    secondary = user_settings.get("explanation_secondary")
    level = settings_svc.get_language_level(target_lang, config.DEFAULT_USER_ID)
    try:
        result = llm_svc.translate_text_via_llm(
            target_lang=target_lang, text=text,
            primary=primary, secondary=secondary, level=level,
        )
    except llm_svc.LLMError as e:
        return jsonify(err(str(e), code="llm_error")), 502
    return ok(result)