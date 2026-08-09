"""Vocab blueprint: list, add, delete, restore, review next/grade."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import config
from ..services import vocab as vocab_svc
from ..util import err, is_valid_lang, ok

bp = Blueprint("vocab", __name__, url_prefix="/api/vocab")


@bp.get("")
def list_vocab():
    lang = request.args.get("lang")
    if not is_valid_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    limit = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))
    box = None
    raw_box = request.args.get("box")
    if raw_box is not None and raw_box != "":
        try:
            box = int(raw_box)
        except (TypeError, ValueError):
            return jsonify(err("box must be an integer 1-5", code="invalid_box")), 400
    try:
        items = vocab_svc.list_vocab(user_id=config.DEFAULT_USER_ID, language=lang,
                                     limit=limit, offset=offset, box=box)
        total = vocab_svc.count_vocab(user_id=config.DEFAULT_USER_ID, language=lang, box=box)
    except ValueError as e:
        return jsonify(err(str(e), code="invalid_input")), 400
    return ok({"items": items, "limit": limit, "offset": offset,
               "box": box, "total": total,
               "by_box": vocab_svc.review_status(
                   user_id=config.DEFAULT_USER_ID, language=lang)["by_box"]})


@bp.post("")
def add_vocab():
    body = request.get_json(silent=True) or {}
    try:
        res = vocab_svc.add_vocab(user_id=config.DEFAULT_USER_ID, **body)
    except (ValueError, TypeError) as e:
        return jsonify(err(str(e), code="invalid_input")), 400
    return ok(res)


@bp.delete("/<int:vocab_id>")
def delete_vocab(vocab_id: int):
    try:
        res = vocab_svc.delete_vocab(user_id=config.DEFAULT_USER_ID, vocab_id=vocab_id)
    except LookupError as e:
        return jsonify(err(str(e), code="not_found")), 404
    return ok(res)


@bp.post("/<int:vocab_id>/restore")
def restore_vocab(vocab_id: int):
    body = request.get_json(silent=True) or {}
    token = body.get("undo_token") if isinstance(body, dict) else None
    if not isinstance(token, str):
        return jsonify(err("undo_token required", code="invalid_input")), 400
    try:
        res = vocab_svc.restore_vocab(user_id=config.DEFAULT_USER_ID, undo_token=token)
    except LookupError as e:
        return jsonify(err(str(e), code="not_found")), 404
    return ok(res)


@bp.patch("/<int:vocab_id>")
def update_vocab(vocab_id: int):
    """Update mutable fields on a vocab item. Today only ``leitner_box``
    is supported (used by the Vocabulary page to let the user self-rate
    "I remember this at level N")."""
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(err("body must be an object", code="invalid_input")), 400
    box = body.get("leitner_box")
    if box is None:
        return jsonify(err("leitner_box required", code="invalid_input")), 400
    if not isinstance(box, int) or isinstance(box, bool):
        return jsonify(err("leitner_box must be an integer 1-5", code="invalid_input")), 400
    try:
        res = vocab_svc.set_box(user_id=config.DEFAULT_USER_ID, vocab_id=vocab_id, box=box)
    except LookupError as e:
        return jsonify(err(str(e), code="not_found")), 404
    except ValueError as e:
        return jsonify(err(str(e), code="invalid_input")), 400
    return ok(res)


@bp.get("/review/status")
def review_status():
    lang = request.args.get("lang")
    if not is_valid_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    res = vocab_svc.review_status(user_id=config.DEFAULT_USER_ID, language=lang)
    return ok(res)


@bp.get("/review/next")
def review_next():
    lang = request.args.get("lang")
    if not is_valid_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    n = int(request.args.get("n", 20))
    items = vocab_svc.review_next(user_id=config.DEFAULT_USER_ID, language=lang, n=n)
    return ok({"items": items, "count": len(items)})


@bp.post("/review/grade")
def review_grade():
    body = request.get_json(silent=True) or {}
    vocab_id = body.get("vocab_id") if isinstance(body, dict) else None
    grade = body.get("grade") if isinstance(body, dict) else None
    if not isinstance(vocab_id, int):
        return jsonify(err("vocab_id (int) required", code="invalid_input")), 400
    if grade not in ("easy", "hard"):
        return jsonify(err("grade must be easy or hard", code="invalid_input")), 400
    try:
        res = vocab_svc.apply_review_grade(user_id=config.DEFAULT_USER_ID,
                                           vocab_id=vocab_id, grade_value=grade)
    except LookupError as e:
        return jsonify(err(str(e), code="not_found")), 404
    return ok(res)