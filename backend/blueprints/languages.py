"""Languages blueprint: list, add, initialize (seed)."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import config
from ..services import seed as seed_svc
from ..util import err, is_valid_lang, ok

bp = Blueprint("languages", __name__, url_prefix="/api/languages")


@bp.get("")
def list_languages():
    with_seed_status = []
    for lang in config.LANGUAGE_CATALOG:
        seeded = seed_svc.is_seeded(lang["code"])
        with_seed_status.append({**lang, "seeded": seeded})
    return ok(with_seed_status)


@bp.post("")
def add_language():
    body = request.get_json(silent=True) or {}
    code = body.get("code")
    if not isinstance(code, str) or not is_valid_lang(code):
        return jsonify(err("invalid language code", code="invalid_code")), 400
    display_name = body.get("display_name")
    if not isinstance(display_name, str) or not display_name:
        display_name = code.upper()
    seed_svc.ensure_language_row(code, display_name, is_built_in=0)
    return ok({"code": code, "display_name": display_name})


@bp.post("/<code>/initialize")
def initialize_language(code: str):
    if not is_valid_lang(code):
        return jsonify(err("invalid language code", code="invalid_code")), 400
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force", False))
    try:
        result = seed_svc.initialize_language(code, force=force)
    except FileNotFoundError as e:
        return jsonify(err(str(e), code="no_built_in_seed")), 404
    except Exception as e:
        from ..services.llm import LLMError
        if isinstance(e, LLMError):
            return jsonify(err(str(e), code="llm_error")), 502
        raise
    return ok(result)


@bp.post("/<code>/apply-explanations")
def apply_explanations(code: str):
    """Translate existing target-language structures and phrases into
    the user's current ``explanation_primary`` /
    ``explanation_secondary`` native languages. Does not touch the
    target-language content; only the explanation columns are
    overwritten. See the
    [explanation-language rules](../../../../../docs/design/architecture.md#explanation-language-rules)
    for what gets filled vs skipped."""
    if not is_valid_lang(code):
        return jsonify(err("invalid language code", code="invalid_code")), 400
    try:
        result = seed_svc.apply_explanations(code)
    except Exception as e:
        from ..services.llm import LLMError
        if isinstance(e, LLMError):
            return jsonify(err(str(e), code="llm_error")), 502
        raise
    return ok(result)


@bp.get("/<code>/seed-status")
def seed_status(code: str):
    if not is_valid_lang(code):
        return jsonify(err("invalid language code", code="invalid_code")), 400
    with _conn() as conn:
        row = conn.execute(
            "SELECT seeded_at FROM languages WHERE code=?", (code,)
        ).fetchone()
    seeded = bool(row and row["seeded_at"])
    return ok({"code": code, "seeded": seeded})


def _conn():
    from ..db import get_conn
    return get_conn()