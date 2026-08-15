"""Structures blueprint: list, add, update, delete, fill-via-LLM."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import config
from ..services import llm as llm_svc
from ..services import settings as settings_svc
from ..db import get_conn, transaction
from ..util import err, is_known_lang, is_nonempty_str, ok

bp = Blueprint("structures", __name__, url_prefix="/api/structures")

EDITABLE_SOURCES = ("user", "llm")
READONLY_SOURCES = ("built-in",)


def _list(lang: str, *, familiar: bool | None = None,
          limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
    sql = (
        "SELECT id, language, pattern, example_sentence, explanation,"
        "       explanation_primary, explanation_secondary, source,"
        "       familiar, added_at "
        "FROM structures WHERE user_id=? AND language=?"
    )
    params: list = [config.DEFAULT_USER_ID, lang]
    if familiar is not None:
        sql += " AND familiar=?"
        params.append(1 if familiar else 0)
    sql += " ORDER BY source DESC, added_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    where = "user_id=? AND language=?"
    count_params: list = [config.DEFAULT_USER_ID, lang]
    if familiar is not None:
        where += " AND familiar=?"
        count_params.append(1 if familiar else 0)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM structures WHERE {where}", count_params,
        ).fetchone()["c"]
    return [dict(r) for r in rows], int(total)


def _parse_familiar_arg(raw: str | None) -> bool | None:
    """Parse the `?familiar=` query string. None means "no filter"."""
    if raw is None or raw == "":
        return None
    if raw in ("0", "false", "False"):
        return False
    if raw in ("1", "true", "True"):
        return True
    raise ValueError("familiar must be 0/1 or true/false")


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
    try:
        familiar = _parse_familiar_arg(request.args.get("familiar"))
    except ValueError as e:
        return jsonify(err(str(e), code="invalid_input")), 400
    try:
        limit = max(1, min(500, int(request.args.get("limit", 100))))
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        return jsonify(err("limit/offset must be integers", code="invalid_input")), 400
    items, total = _list(lang, familiar=familiar, limit=limit, offset=offset)
    return ok({"items": items, "limit": limit, "offset": offset, "total": total})


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
        example = _coerce_str(body.get("example_sentence"), max_len=1000,
                              allow_none=False)
        if not example:
            return jsonify(err("example_sentence required", code="invalid_input")), 400
        explanation = _coerce_str(body.get("explanation"), max_len=1500,
                                  allow_none=False)
        if not explanation:
            return jsonify(err("explanation required", code="invalid_input")), 400
        explanation_primary = _coerce_str(body.get("explanation_primary"), max_len=1000)
        explanation_secondary = _coerce_str(body.get("explanation_secondary"), max_len=1000)
        source = body.get("source", "user")
        if source not in ("user", "llm"):
            source = "user"
    except ValueError as e:
        return jsonify(err(str(e), code="invalid_input")), 400
    # Apply the same explanation-language rules the LLM path uses, so a
    # user who types a redundant `explanation_primary` (same language as
    # the target) doesn't end up with it in the DB.
    user_settings = settings_svc.get_settings(config.DEFAULT_USER_ID)
    primary = user_settings.get("explanation_primary")
    secondary = user_settings.get("explanation_secondary")
    if not llm_svc._should_generate_primary(lang, primary):
        explanation_primary = None
    if not llm_svc._should_generate_secondary(primary, secondary):
        explanation_secondary = None
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO structures (user_id, language, pattern, example_sentence,"
            "  explanation, explanation_primary, explanation_secondary, source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (config.DEFAULT_USER_ID, lang, pattern, example, explanation,
             explanation_primary, explanation_secondary, source),
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
        for field, col, maxlen, required in [
            ("pattern", "pattern", 500, True),
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
                if field == "pattern" and v is None:
                    return jsonify(err("pattern cannot be null", code="invalid_input")), 400
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


@bp.patch("/<int:item_id>")
def patch_structure(item_id: int):
    """Toggle the `familiar` flag on a row. Built-in rows are markable too,
    which is intentional — the Structures page lets users retire starter rows
    they've outgrown. `familiar` is the only field accepted on this route;
    content edits still go through PUT."""
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
            "UPDATE structures SET familiar=? WHERE id=? AND user_id=?",
            (1 if familiar else 0, item_id, config.DEFAULT_USER_ID),
        )
        if row.rowcount == 0:
            existing = conn.execute(
                "SELECT 1 FROM structures WHERE id=? AND user_id=?",
                (item_id, config.DEFAULT_USER_ID),
            ).fetchone()
            if existing is None:
                return jsonify(err("not found", code="not_found")), 404
    return ok({"id": item_id, "familiar": familiar})


@bp.post("/fill")
def fill_structure():
    body = request.get_json(silent=True) or {}
    lang = body.get("language")
    if not isinstance(lang, str) or not _ensure_lang(lang):
        return jsonify(err("invalid language", code="invalid_lang")), 400
    partial = {
        "pattern": body.get("pattern"),
        "example_sentence": body.get("example_sentence"),
        "explanation": body.get("explanation"),
        "explanation_primary": body.get("explanation_primary"),
        "explanation_secondary": body.get("explanation_secondary"),
    }
    user_settings = settings_svc.get_settings(config.DEFAULT_USER_ID)
    primary = user_settings.get("explanation_primary")
    secondary = user_settings.get("explanation_secondary")
    try:
        filled = llm_svc.fill_structure_via_llm(
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