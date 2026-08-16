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
    box_min = None
    raw_min = request.args.get("box_min")
    if raw_min is not None and raw_min != "":
        try:
            box_min = int(raw_min)
        except (TypeError, ValueError):
            return jsonify(err("box_min must be an integer 1-5", code="invalid_box")), 400
    box_max = None
    raw_max = request.args.get("box_max")
    if raw_max is not None and raw_max != "":
        try:
            box_max = int(raw_max)
        except (TypeError, ValueError):
            return jsonify(err("box_max must be an integer 1-5", code="invalid_box")), 400
    added_after = request.args.get("added_after")
    added_before = request.args.get("added_before")
    reviewed_after = request.args.get("reviewed_after")
    reviewed_before = request.args.get("reviewed_before")
    try:
        items = vocab_svc.list_vocab(user_id=config.DEFAULT_USER_ID, language=lang,
                                     limit=limit, offset=offset, box=box,
                                     box_min=box_min, box_max=box_max,
                                     added_after=added_after, added_before=added_before,
                                     reviewed_after=reviewed_after, reviewed_before=reviewed_before)
        total = vocab_svc.count_vocab(user_id=config.DEFAULT_USER_ID, language=lang,
                                      box=box, box_min=box_min, box_max=box_max,
                                      added_after=added_after, added_before=added_before,
                                      reviewed_after=reviewed_after, reviewed_before=reviewed_before)
    except ValueError as e:
        return jsonify(err(str(e), code="invalid_input")), 400
    return ok({"items": items, "limit": limit, "offset": offset,
               "box": box, "box_min": box_min, "box_max": box_max,
               "added_after": added_after, "added_before": added_before,
               "reviewed_after": reviewed_after, "reviewed_before": reviewed_before,
               "total": total,
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


@bp.get("/lookup")
def lookup_vocab():
    """Return the Leitner box for a (lang, word), or {in_vocab:false}.

    The Dictionary page calls this after rendering a result so it can show
    either "Add to box 1" or the current box number next to the Source row.
    """
    lang = request.args.get("lang")
    word = request.args.get("word")
    if not is_valid_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    if not isinstance(word, str) or not word.strip():
        return jsonify(err("word required", code="invalid_word")), 400
    found = vocab_svc.find_vocab_box(
        user_id=config.DEFAULT_USER_ID, language=lang, word=word,
    )
    return ok({"in_vocab": found is not None,
               "lang": lang, "word": word.strip()[:200],
               "leitner_box": found["leitner_box"] if found else None,
               "vocab_id": found["id"] if found else None})


@bp.post("/add-from-entry")
def add_from_entry():
    """Insert (or refresh) a vocab row from a flattened dictionary payload.

    The Dictionary card's "Add to box 1" button calls this when auto-add
    is off. Always lands at box 1. Re-adding an existing word refreshes
    the row's data and leaves the box unchanged.
    """
    body = request.get_json(silent=True) or {}
    lang = body.get("lang")
    if not is_valid_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    word = body.get("word")
    source = body.get("source")
    pos = body.get("pos")
    glossary = body.get("glossary")
    example = body.get("example")
    explanation_primary = body.get("explanation_primary")
    explanation_secondary = body.get("explanation_secondary")
    if not isinstance(word, str) or not word.strip():
        return jsonify(err("word required", code="invalid_word")), 400
    if not isinstance(source, str) or source not in ("wordnet", "llm", "user"):
        return jsonify(err("source must be wordnet, llm, or user", code="invalid_source")), 400
    if not isinstance(glossary, str) or not glossary.strip():
        return jsonify(err("glossary required", code="invalid_input")), 400
    try:
        res = vocab_svc.add_vocab(
            user_id=config.DEFAULT_USER_ID,
            language=lang,
            word=word,
            source=source,
            sense_idx=0,
            pos=pos,
            glossary=glossary,
            example=example,
            explanation_primary=explanation_primary,
            explanation_secondary=explanation_secondary,
        )
    except (ValueError, TypeError) as e:
        return jsonify(err(str(e), code="invalid_input")), 400
    # Surface the resulting box so the UI can swap the button for a badge
    # without a second round-trip.
    box = vocab_svc.find_vocab_box(
        user_id=config.DEFAULT_USER_ID, language=lang, word=word,
    )
    return ok({**res, "leitner_box": box["leitner_box"] if box else 1})


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
    box = None
    raw_box = request.args.get("box")
    if raw_box is not None and raw_box != "":
        try:
            box = int(raw_box)
        except (TypeError, ValueError):
            return jsonify(err("box must be an integer 0-5", code="invalid_box")), 400
    shuffle = request.args.get("shuffle", "0").lower() in ("1", "true", "yes", "on")
    try:
        items = vocab_svc.review_next(user_id=config.DEFAULT_USER_ID, language=lang,
                                      n=n, box=box, shuffle=shuffle)
    except ValueError as e:
        return jsonify(err(str(e), code="invalid_input")), 400
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