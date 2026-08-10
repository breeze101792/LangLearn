"""Phrases blueprint: list, add, update, delete, fill-via-LLM."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import config
from ..services import llm as llm_svc
from ..services import settings as settings_svc
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
    familiar_raw = request.args.get("familiar")
    if familiar_raw is None or familiar_raw == "":
        familiar: bool | None = None
    elif familiar_raw in ("0", "false", "False"):
        familiar = False
    elif familiar_raw in ("1", "true", "True"):
        familiar = True
    else:
        return jsonify(err("familiar must be 0/1 or true/false", code="invalid_input")), 400
    sql = (
        "SELECT id, language, phrase, example_sentence, explanation,"
        "       explanation_primary, explanation_secondary, source,"
        "       familiar, added_at "
        "FROM phrases WHERE user_id=? AND language=?"
    )
    params: list = [config.DEFAULT_USER_ID, lang]
    if familiar is not None:
        sql += " AND familiar=?"
        params.append(1 if familiar else 0)
    sql += " ORDER BY source DESC, added_at DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
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
        example = _coerce_str(body.get("example_sentence"), max_len=1000)
        if not example:
            return jsonify(err("example_sentence required", code="invalid_input")), 400
        explanation = _coerce_str(body.get("explanation"), max_len=1500)
        if not explanation:
            return jsonify(err("explanation required", code="invalid_input")), 400
        explanation_primary = _coerce_str(body.get("explanation_primary"), max_len=1000)
        explanation_secondary = _coerce_str(body.get("explanation_secondary"), max_len=1000)
        source = body.get("source", "user")
        if source not in ("user", "llm"):
            source = "user"
    except ValueError as e:
        return jsonify(err(str(e), code="invalid_input")), 400
    # Apply the same explanation-language rules the LLM path uses.
    user_settings = settings_svc.get_settings(config.DEFAULT_USER_ID)
    primary = user_settings.get("explanation_primary")
    secondary = user_settings.get("explanation_secondary")
    if not llm_svc._should_generate_primary(lang, primary):
        explanation_primary = None
    if not llm_svc._should_generate_secondary(primary, secondary):
        explanation_secondary = None
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO phrases (user_id, language, phrase, example_sentence,"
            "  explanation, explanation_primary, explanation_secondary, source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (config.DEFAULT_USER_ID, lang, phrase, example, explanation,
             explanation_primary, explanation_secondary, source),
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
        for field, col, maxlen, required in [
            ("phrase", "phrase", 500, True),
            ("example_sentence", "example_sentence", 1000, True),
            ("explanation", "explanation", 1500, True),
            ("explanation_primary", "explanation_primary", 1000, False),
            ("explanation_secondary", "explanation_secondary", 1000, False),
        ]:
            if field in body:
                try:
                    v = _coerce_str(body.get(field), max_len=maxlen)
                except ValueError as e:
                    return jsonify(err(str(e), code="invalid_input")), 400
                if field == "phrase" and v is None:
                    return jsonify(err("phrase cannot be null", code="invalid_input")), 400
                # NOT NULL columns store empty string when the user
                # passes null/blank. NULL would violate the constraint.
                if required and v is None:
                    v = ""
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


@bp.patch("/<int:item_id>")
def patch_phrase(item_id: int):
    """Toggle the `familiar` flag on a row. Built-in rows are markable too —
    matches the structures endpoint and the user's mental model."""
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or "familiar" not in body:
        return jsonify(err("familiar required (bool)", code="invalid_input")), 400
    raw = body["familiar"]
    if isinstance(raw, bool):
        familiar = raw
    elif raw in (0, 1):
        familiar = bool(raw)
    else:
        return jsonify(err("familiar must be a boolean", code="invalid_input")), 400
    with transaction() as conn:
        row = conn.execute(
            "UPDATE phrases SET familiar=? WHERE id=? AND user_id=?",
            (1 if familiar else 0, item_id, config.DEFAULT_USER_ID),
        )
        if row.rowcount == 0:
            existing = conn.execute(
                "SELECT 1 FROM phrases WHERE id=? AND user_id=?",
                (item_id, config.DEFAULT_USER_ID),
            ).fetchone()
            if existing is None:
                return jsonify(err("not found", code="not_found")), 404
    return ok({"id": item_id, "familiar": familiar})


@bp.post("/fill")
def fill_phrase():
    body = request.get_json(silent=True) or {}
    lang = body.get("language")
    if not isinstance(lang, str) or not is_known_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    partial = {
        "example_sentence": body.get("example_sentence"),
        "explanation": body.get("explanation"),
        "explanation_primary": body.get("explanation_primary"),
        "explanation_secondary": body.get("explanation_secondary"),
    }
    user_settings = settings_svc.get_settings(config.DEFAULT_USER_ID)
    primary = user_settings.get("explanation_primary")
    secondary = user_settings.get("explanation_secondary")
    try:
        filled = llm_svc.fill_phrase_via_llm(
            lang=lang,
            partial=partial,
            primary=primary,
            secondary=secondary,
        )
    except llm_svc.LLMError as e:
        return jsonify(err(str(e), code="llm_error")), 502
    # Enforce the explanation-language rules at the persistence boundary
    # so the rules hold even if the LLM service is mocked.
    llm_svc.apply_explanation_rules(
        filled, lang=lang, primary=primary, secondary=secondary,
    )
    return ok(filled)