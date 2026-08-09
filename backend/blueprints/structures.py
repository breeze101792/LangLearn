"""Structures blueprint: list, add, update, delete, fill-via-LLM."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import config
from ..services import llm as llm_svc
from ..db import get_conn, transaction
from ..util import err, is_known_lang, is_nonempty_str, ok

bp = Blueprint("structures", __name__, url_prefix="/api/structures")

EDITABLE_SOURCES = ("user", "llm")
READONLY_SOURCES = ("built-in",)


def _list(lang: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, language, pattern, example_sentence, explanation_primary,"
            "       explanation_secondary, source, added_at "
            "FROM structures WHERE user_id=? AND language=? ORDER BY source DESC, added_at DESC",
            (config.DEFAULT_USER_ID, lang),
        ).fetchall()
    return [dict(r) for r in rows]


def _ensure_lang(lang: str) -> bool:
    return is_known_lang(lang)


def _coerce_str(value, *, max_len: int, allow_none: bool = True):
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
def list_structures():
    lang = request.args.get("lang")
    if not _ensure_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    return ok({"items": _list(lang)})


@bp.post("")
def add_structure():
    body = request.get_json(silent=True) or {}
    lang = body.get("language")
    if not isinstance(lang, str) or not _ensure_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    try:
        pattern = _coerce_str(body.get("pattern"), max_len=500, allow_none=False)
        if not pattern:
            return jsonify(err("pattern required", code="invalid_input")), 400
        example = _coerce_str(body.get("example_sentence"), max_len=1000)
        explanation_primary = _coerce_str(body.get("explanation_primary"), max_len=1000)
        explanation_secondary = _coerce_str(body.get("explanation_secondary"), max_len=1000)
        source = body.get("source", "user")
        if source not in ("user", "llm"):
            source = "user"
    except ValueError as e:
        return jsonify(err(str(e), code="invalid_input")), 400
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO structures (user_id, language, pattern, example_sentence,"
            "  explanation_primary, explanation_secondary, source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (config.DEFAULT_USER_ID, lang, pattern, example, explanation_primary,
             explanation_secondary, source),
        )
        new_id = cur.lastrowid
    return ok({"id": new_id, "source": source})


@bp.put("/<int:item_id>")
def update_structure(item_id: int):
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(err("body must be object", code="invalid_input")), 400
    with transaction() as conn:
        row = conn.execute(
            "SELECT source FROM structures WHERE id=? AND user_id=?",
            (item_id, config.DEFAULT_USER_ID),
        ).fetchone()
        if row is None:
            return jsonify(err("not found", code="not_found")), 404
        if row["source"] not in EDITABLE_SOURCES:
            return jsonify(err("built-in items are read-only", code="forbidden")), 403
        updates = []
        params: list = []
        for field, col, maxlen in [
            ("pattern", "pattern", 500),
            ("example_sentence", "example_sentence", 1000),
            ("explanation_primary", "explanation_primary", 1000),
            ("explanation_secondary", "explanation_secondary", 1000),
        ]:
            if field in body:
                try:
                    v = _coerce_str(body.get(field), max_len=maxlen)
                except ValueError as e:
                    return jsonify(err(str(e), code="invalid_input")), 400
                if field == "pattern" and not v:
                    return jsonify(err("pattern cannot be empty", code="invalid_input")), 400
                updates.append(f"{col}=?")
                params.append(v)
        if updates:
            params.append(item_id)
            params.append(config.DEFAULT_USER_ID)
            conn.execute(
                f"UPDATE structures SET {', '.join(updates)} WHERE id=? AND user_id=?",
                params,
            )
    return ok({"id": item_id})


@bp.delete("/<int:item_id>")
def delete_structure(item_id: int):
    with transaction() as conn:
        row = conn.execute(
            "SELECT source FROM structures WHERE id=? AND user_id=?",
            (item_id, config.DEFAULT_USER_ID),
        ).fetchone()
        if row is None:
            return jsonify(err("not found", code="not_found")), 404
        if row["source"] not in EDITABLE_SOURCES:
            return jsonify(err("built-in items are read-only", code="forbidden")), 403
        conn.execute("DELETE FROM structures WHERE id=? AND user_id=?",
                     (item_id, config.DEFAULT_USER_ID))
    return ok({"deleted_id": item_id})


@bp.post("/fill")
def fill_structure():
    body = request.get_json(silent=True) or {}
    lang = body.get("language")
    if not isinstance(lang, str) or not _ensure_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    partial = {
        "pattern": body.get("pattern"),
        "example_sentence": body.get("example_sentence"),
        "explanation_primary": body.get("explanation_primary"),
        "explanation_secondary": body.get("explanation_secondary"),
    }
    try:
        filled = llm_svc.fill_structure_via_llm(lang=lang, partial=partial)
    except llm_svc.LLMError as e:
        return jsonify(err(str(e), code="llm_error")), 502
    return ok(filled)