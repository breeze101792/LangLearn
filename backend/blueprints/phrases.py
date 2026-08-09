"""Phrases blueprint: list, add, update, delete, fill-via-LLM."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import config
from ..services import llm as llm_svc
from ..db import get_conn, transaction
from ..util import err, is_known_lang, ok

bp = Blueprint("phrases", __name__, url_prefix="/api/phrases")

EDITABLE_SOURCES = ("user", "llm")


def _coerce_str(value, *, max_len: int):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{value!r} not a string")
    v = value.strip()
    if not v:
        return None
    if len(v) > max_len:
        raise ValueError(f"value too long (>{max_len})")
    return v


@bp.get("")
def list_phrases():
    lang = request.args.get("lang")
    if not is_known_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, language, phrase, literal_translation, explanation_primary,"
            "       explanation_secondary, source, added_at "
            "FROM phrases WHERE user_id=? AND language=? ORDER BY source DESC, added_at DESC",
            (config.DEFAULT_USER_ID, lang),
        ).fetchall()
    return ok({"items": [dict(r) for r in rows]})


@bp.post("")
def add_phrase():
    body = request.get_json(silent=True) or {}
    lang = body.get("language")
    if not isinstance(lang, str) or not is_known_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    try:
        phrase = _coerce_str(body.get("phrase"), max_len=500)
        if not phrase:
            return jsonify(err("phrase required", code="invalid_input")), 400
        literal = _coerce_str(body.get("literal_translation"), max_len=500)
        explanation_primary = _coerce_str(body.get("explanation_primary"), max_len=1000)
        explanation_secondary = _coerce_str(body.get("explanation_secondary"), max_len=1000)
        source = body.get("source", "user")
        if source not in ("user", "llm"):
            source = "user"
    except ValueError as e:
        return jsonify(err(str(e), code="invalid_input")), 400
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO phrases (user_id, language, phrase, literal_translation,"
            "  explanation_primary, explanation_secondary, source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (config.DEFAULT_USER_ID, lang, phrase, literal, explanation_primary,
             explanation_secondary, source),
        )
        new_id = cur.lastrowid
    return ok({"id": new_id, "source": source})


@bp.put("/<int:item_id>")
def update_phrase(item_id: int):
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(err("body must be object", code="invalid_input")), 400
    with transaction() as conn:
        row = conn.execute(
            "SELECT source FROM phrases WHERE id=? AND user_id=?",
            (item_id, config.DEFAULT_USER_ID),
        ).fetchone()
        if row is None:
            return jsonify(err("not found", code="not_found")), 404
        if row["source"] not in EDITABLE_SOURCES:
            return jsonify(err("built-in items are read-only", code="forbidden")), 403
        updates = []
        params: list = []
        for field, col, maxlen in [
            ("phrase", "phrase", 500),
            ("literal_translation", "literal_translation", 500),
            ("explanation_primary", "explanation_primary", 1000),
            ("explanation_secondary", "explanation_secondary", 1000),
        ]:
            if field in body:
                try:
                    v = _coerce_str(body.get(field), max_len=maxlen)
                except ValueError as e:
                    return jsonify(err(str(e), code="invalid_input")), 400
                if field == "phrase" and not v:
                    return jsonify(err("phrase cannot be empty", code="invalid_input")), 400
                updates.append(f"{col}=?")
                params.append(v)
        if updates:
            params.append(item_id)
            params.append(config.DEFAULT_USER_ID)
            conn.execute(
                f"UPDATE phrases SET {', '.join(updates)} WHERE id=? AND user_id=?",
                params,
            )
    return ok({"id": item_id})


@bp.delete("/<int:item_id>")
def delete_phrase(item_id: int):
    with transaction() as conn:
        row = conn.execute(
            "SELECT source FROM phrases WHERE id=? AND user_id=?",
            (item_id, config.DEFAULT_USER_ID),
        ).fetchone()
        if row is None:
            return jsonify(err("not found", code="not_found")), 404
        if row["source"] not in EDITABLE_SOURCES:
            return jsonify(err("built-in items are read-only", code="forbidden")), 403
        conn.execute("DELETE FROM phrases WHERE id=? AND user_id=?",
                     (item_id, config.DEFAULT_USER_ID))
    return ok({"deleted_id": item_id})


@bp.post("/fill")
def fill_phrase():
    body = request.get_json(silent=True) or {}
    lang = body.get("language")
    if not isinstance(lang, str) or not is_known_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    partial = {
        "literal_translation": body.get("literal_translation"),
        "explanation_primary": body.get("explanation_primary"),
        "explanation_secondary": body.get("explanation_secondary"),
    }
    try:
        filled = llm_svc.fill_phrase_via_llm(lang=lang, partial=partial)
    except llm_svc.LLMError as e:
        return jsonify(err(str(e), code="llm_error")), 502
    return ok(filled)