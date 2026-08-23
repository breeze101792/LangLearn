"""Analyze blueprint: extract structures, phrases, and hard words from a
free-form sentence or paragraph in the target language.

The page is fire-and-forget: the input text and the LLM response are not
persisted. Each item the AI extracts can be one-click saved into the
existing structures / phrases / vocab tables by hitting the same
endpoints those pages already use (``/api/structures``, ``/api/phrases``,
``/api/vocab/add-from-entry``).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import config
from ..services import llm as llm_svc
from ..services import settings as settings_svc
from ..util import err, is_known_lang, ok

bp = Blueprint("analyze", __name__, url_prefix="/api/analyze")


MAX_TEXT_LEN = 4000


@bp.post("")
def analyze_text():
    """Run :func:`llm.analyze_text_via_llm` on the request body.

    Body: ``{"language": "en", "text": "..."}``. The user's
    ``explanation_primary`` / ``explanation_secondary`` settings are
    pulled server-side so the client can't override them.

    Returns the parsed ``{structures, phrases, words}`` object (each item
    may include ``explanation_primary`` / ``explanation_secondary`` in
    the user's native languages, or null per the explanation-language
    rules).
    """
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
    level = settings_svc.get_language_level(lang, config.DEFAULT_USER_ID)
    try:
        result = llm_svc.analyze_text_via_llm(
            lang=lang, text=text,
            primary=primary, secondary=secondary, level=level,
        )
    except llm_svc.LLMError as e:
        return jsonify(err(str(e), code="llm_error")), 502
    return ok(result)
