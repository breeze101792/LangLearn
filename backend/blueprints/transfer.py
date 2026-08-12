"""Transfer blueprint: export and import user content.

Routes:

- ``GET  /api/transfer/export`` — produce a JSON or CSV file for download.
- ``POST /api/transfer/import/preview`` — parse an uploaded file, tag
  each row with its merge status. No DB writes.
- ``POST /api/transfer/import/apply`` — write the chosen rows back via
  the canonical service upserts. Per-row decisions are honored.

The JSON export mirrors the JSON import format. CSV is single-table —
``scope=all`` is rejected for CSV because there is no clean way to
multiplex vocab + structures + phrases into one flat sheet.
"""

from __future__ import annotations

import io
import json

from flask import Blueprint, Response, jsonify, request

from .. import config
from ..services import transfer as transfer_svc
from ..util import err, is_known_lang, ok

bp = Blueprint("transfer", __name__, url_prefix="/api/transfer")


_VALID_SCOPES = ("vocab", "structures", "phrases", "all")
_VALID_FORMATS = ("json", "csv")
_VALID_TABLES = ("vocab", "structures", "phrases")
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MiB


@bp.get("/export")
def export_data():
    scope = (request.args.get("scope") or "all").lower()
    if scope not in _VALID_SCOPES:
        return jsonify(err(f"scope must be one of {_VALID_SCOPES}",
                            code="invalid_input")), 400
    fmt = (request.args.get("format") or "json").lower()
    if fmt not in _VALID_FORMATS:
        return jsonify(err(f"format must be one of {_VALID_FORMATS}",
                            code="invalid_input")), 400
    if fmt == "csv" and scope == "all":
        return jsonify(err("CSV export supports a single table; pick a"
                            " scope of vocab|structures|phrases",
                            code="invalid_input")), 400
    lang = request.args.get("lang")
    if lang and not is_known_lang(lang):
        return jsonify(err("unknown language", code="invalid_lang")), 400
    if fmt == "csv":
        # CSV path: figure out which table and return a download.
        table = scope
        payload = transfer_svc.build_export(
            user_id=config.DEFAULT_USER_ID, scope=scope, lang=lang,
        )
        body = transfer_svc.to_csv(payload, table=table)
        return _download(body, f"langlearn-{scope}.csv", "text/csv")
    # JSON path: wrap the payload in the standard {ok, data} envelope.
    payload = transfer_svc.build_export(
        user_id=config.DEFAULT_USER_ID, scope=scope, lang=lang,
    )
    return ok(payload)


@bp.post("/import/preview")
def import_preview():
    """Parse an uploaded file (multipart or raw body) into a merge preview."""
    raw_text, filename, content_type = _read_upload(request)
    table = _read_table_arg(request)
    fmt = _read_format_arg(request, content_type=content_type, filename=filename)
    default_lang = request.args.get("default_lang") or (
        request.form.get("default_lang") if request.form else None
    )
    has_header_raw = request.args.get("has_header")
    if has_header_raw is None and request.form:
        has_header_raw = request.form.get("has_header")
    has_header = True if has_header_raw is None else _truthy(has_header_raw)
    try:
        mapping = _read_mapping_arg(request)
    except ValueError as e:
        return jsonify(err(str(e), code="invalid_input")), 400

    try:
        rows = transfer_svc.parse_import(
            text=raw_text, format=fmt, table=table,
            default_lang=default_lang, mapping=mapping, has_header=has_header,
        )
    except ValueError as e:
        return jsonify(err(str(e), code="invalid_input")), 400

    merged = transfer_svc.compute_merge(
        rows, user_id=config.DEFAULT_USER_ID, table=table,
    )
    stats = _stats(merged)
    return ok({
        "table": table,
        "format": fmt,
        "rows": merged,
        "stats": stats,
        "row_count": len(merged),
    })


@bp.post("/import/apply")
def import_apply():
    """Apply the user's per-row decisions. Body is JSON."""
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(err("body must be an object", code="invalid_input")), 400
    rows = body.get("rows")
    decisions = body.get("decisions")
    table = body.get("table")
    if table not in _VALID_TABLES:
        return jsonify(err(f"table must be one of {_VALID_TABLES}",
                            code="invalid_input")), 400
    if not isinstance(rows, list) or not isinstance(decisions, list):
        return jsonify(err("rows and decisions must be lists",
                            code="invalid_input")), 400
    counts = transfer_svc.apply_import(
        rows=rows, decisions=decisions,
        user_id=config.DEFAULT_USER_ID, table=table,
    )
    return ok({"table": table, **counts})


# ---------- helpers ----------


def _download(body: str, filename: str, mime: str) -> Response:
    resp = Response(body, mimetype=mime)
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    # We bypass the standard {ok,data} envelope for file downloads. The
    # frontend switches to a blob fetch when format=csv.
    return resp


def _read_upload(req) -> tuple[str, str | None, str | None]:
    """Return ``(body_text, filename, content_type)``.

    Accepts a multipart file part named ``file`` or, as a fallback, the
    raw request body. We deliberately do NOT cap at a small size for
    JSON since 10 MiB is already large enough for a personal export.
    """
    if req.content_length and req.content_length > _MAX_UPLOAD_BYTES:
        raise ValueError("file too large")
    if "multipart/form-data" in (req.content_type or ""):
        f = req.files.get("file")
        if f is None:
            raise ValueError("multipart upload requires a 'file' part")
        raw = f.read(_MAX_UPLOAD_BYTES + 1)
        if len(raw) > _MAX_UPLOAD_BYTES:
            raise ValueError("file too large")
        return raw.decode("utf-8", errors="replace"), f.filename, f.mimetype
    raw = req.get_data(cache=False, as_text=True)
    if len(raw.encode("utf-8")) > _MAX_UPLOAD_BYTES:
        raise ValueError("file too large")
    return raw, None, req.content_type


def _read_table_arg(req) -> str:
    table = req.args.get("table")
    if not table and req.form:
        table = req.form.get("table")
    if table not in _VALID_TABLES:
        raise ValueError(f"table must be one of {_VALID_TABLES}")
    return table


def _read_format_arg(req, *, content_type: str | None,
                     filename: str | None) -> str:
    fmt = req.args.get("format")
    if not fmt and req.form:
        fmt = req.form.get("format")
    if fmt:
        fmt = fmt.lower()
        if fmt not in _VALID_FORMATS:
            raise ValueError(f"format must be one of {_VALID_FORMATS}")
        return fmt
    if filename and filename.lower().endswith(".csv"):
        return "csv"
    if content_type and "csv" in content_type.lower():
        return "csv"
    return "json"


def _read_mapping_arg(req) -> list[dict]:
    """Read the column mapping from the ``mapping`` query/form/json field.

    Accepts either a JSON-encoded string (forms) or a JSON body on the
    same request. Returns ``[]`` when absent.
    """
    raw = req.args.get("mapping")
    if raw is None and req.form:
        raw = req.form.get("mapping")
    if raw is None:
        body = req.get_json(silent=True)
        if isinstance(body, dict):
            candidate = body.get("mapping")
            if isinstance(candidate, list):
                return candidate
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid mapping JSON: {e}") from e
    if not isinstance(parsed, list):
        raise ValueError("mapping must be a list")
    return parsed


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def _stats(rows: list[dict]) -> dict[str, int]:
    out = {"new": 0, "existing": 0, "invalid": 0}
    for r in rows:
        status = r.get("status")
        if status in out:
            out[status] += 1
    return out