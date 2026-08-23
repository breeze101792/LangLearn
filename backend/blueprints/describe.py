"""Describe blueprint: take an uploaded image and the user's target
(active) language and return a target-language description of the
picture plus a list of concrete vocabulary items visible in it. Each
word can be one-click saved to the vocab table via the existing
``/api/vocab/add-from-entry`` endpoint.

The endpoint is fire-and-forget: the image bytes and the LLM response
are not persisted. Like Analyze, the page is a study aid.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import config
from ..services import llm as llm_svc
from ..services import settings as settings_svc
from ..util import err, is_known_lang, ok

bp = Blueprint("describe", __name__, url_prefix="/api/describe")


MAX_IMAGE_BYTES = 30 * 1024 * 1024  # 30 MiB. The frontend resizes before
# upload so most requests land far below this; the cap is a safety net
# for direct API callers and for browsers where the canvas path fails.
_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@bp.post("")
def describe_image():
    """Run :func:`llm.describe_image_via_llm` on the uploaded image.

    Accepts ``multipart/form-data`` with a ``file`` part (preferred) or a
    raw body of bytes (``Content-Type`` must be a recognized image type).

    Form fields (multipart only):

    - ``language`` (optional): the target language. Defaults to the
      user's active language from settings. The user's
      ``explanation_primary`` / ``explanation_secondary`` settings are
      pulled server-side so the client can't override them.

    Returns the parsed ``{description, description_primary?,
    description_secondary?, words}`` object. Each word item carries the
    same explanation fields the Analyze page produces so the frontend
    can "Add to Vocab" with a single click.
    """
    if request.content_length and request.content_length > MAX_IMAGE_BYTES:
        return jsonify(err("image too large", code="invalid_input")), 400

    lang = None
    image_bytes: bytes | None = None
    mime_type = "image/jpeg"

    content_type = (request.content_type or "").lower()
    if "multipart/form-data" in content_type:
        f = request.files.get("file")
        if f is None:
            return jsonify(err("file part required", code="invalid_input")), 400
        raw = f.read(MAX_IMAGE_BYTES + 1)
        if len(raw) > MAX_IMAGE_BYTES:
            return jsonify(err("image too large", code="invalid_input")), 400
        image_bytes = raw
        mime_type = (f.mimetype or "image/jpeg").lower()
        lang = request.form.get("language")
    else:
        # Raw body upload: trust the request Content-Type for the mime.
        raw = request.get_data(cache=False)
        if not raw:
            return jsonify(err("image body required", code="invalid_input")), 400
        if len(raw) > MAX_IMAGE_BYTES:
            return jsonify(err("image too large", code="invalid_input")), 400
        image_bytes = raw
        mime_type = content_type or "image/jpeg"

    if mime_type not in _ALLOWED_MIME:
        return jsonify(err(
            f"unsupported image type: {mime_type}. Allowed: "
            f"{', '.join(sorted(_ALLOWED_MIME))}",
            code="invalid_input",
        )), 400

    user_settings = settings_svc.get_settings(config.DEFAULT_USER_ID)
    if not lang:
        lang = user_settings.get("active_language")
    if not isinstance(lang, str) or not is_known_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400

    primary = user_settings.get("explanation_primary")
    secondary = user_settings.get("explanation_secondary")
    level = settings_svc.get_language_level(lang, config.DEFAULT_USER_ID)
    assert isinstance(image_bytes, bytes)
    try:
        result = llm_svc.describe_image_via_llm(
            target_lang=lang, image_bytes=image_bytes, mime_type=mime_type,
            primary=primary, secondary=secondary, level=level,
        )
    except ValueError as e:
        return jsonify(err(str(e), code="invalid_input")), 400
    except llm_svc.LLMError as e:
        return jsonify(err(str(e), code="llm_error")), 502
    return ok(result)