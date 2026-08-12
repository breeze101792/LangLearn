"""Refine blueprint: take a sentence or short paragraph in the target
language, return a corrected version, a more idiomatic native-speaker
version, a list of small in-place edits with reasons, and a short
explanation in the target language.

The endpoint is fire-and-forget: nothing about the request or response
is persisted. The page is a quick utility, not a knowledge store.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import config
from ..services import llm as llm_svc
from ..services import settings as settings_svc
from ..util import err, is_known_lang, ok

bp = Blueprint("refine", __name__, url_prefix="/api/refine")


MAX_TEXT_LEN = 4000


@bp.post("")
def refine_text():
    body = request.get_json(silent=True) or {}
    lang = body.get("language")
    text = body.get("text")
    if not isinstance(lang, str) or not is_known_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    if not isinstance(text, str) or not text.strip():
        return jsonify(err("text required", code="invalid_input")), 400
    text = text.strip()
    if len(text) > MAX_TEXT_LEN:
        return jsonify(err(f"text too long (>{MAX_TEXT_LEN})",
                           code="invalid_input")), 400

    user_settings = settings_svc.get_settings(config.DEFAULT_USER_ID)
    primary = user_settings.get("explanation_primary")
    secondary = user_settings.get("explanation_secondary")
    try:
        result = llm_svc.refine_text_via_llm(
            lang=lang, text=text,
            primary=primary, secondary=secondary,
        )
    except llm_svc.LLMError as e:
        return jsonify(err(str(e), code="llm_error")), 502
    return ok(result)
