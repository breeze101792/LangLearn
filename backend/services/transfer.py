"""Transfer service: export/import user content as JSON or CSV.

Two phases:

- ``build_export`` produces a payload that round-trips through the parse
  path (``scope="all"`` produces JSON; per-table CSV is also supported).
- ``parse_import`` accepts JSON or CSV; for CSV with no header, callers
  pass an explicit ``mapping`` describing which column position means
  which target field.
- ``compute_merge`` looks each parsed row up in the DB and tags it
  ``new`` / ``existing`` / ``invalid``. No writes happen.
- ``apply_import`` writes the chosen rows in one transaction using the
  existing service upserts (``vocab_svc.add_vocab`` for vocab; direct
  inserts for structures/phrases since they have no service wrapper).

Imported rows are stamped ``source="user"`` so they remain editable; the
merge path treats ``built-in`` rows as effectively read-only — an
overwrite against a built-in row is silently downgraded to skip.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
from typing import Any

from ..db import get_conn, transaction
from ..util import is_known_lang, is_word, normalize_word
from . import vocab as vocab_svc


FORMAT_VERSION = 1

# Per-table field sets. Order is significant: it's the default CSV
# column order for exports and the order used when auto-rendering the
# mapping UI.

VOCAB_FIELDS: list[str] = [
    "language", "word", "source", "pos", "glossary", "example",
    "explanation_primary", "explanation_secondary",
    "leitner_box", "next_due", "added_at",
]

STRUCTURE_FIELDS: list[str] = [
    "language", "pattern", "example_sentence", "explanation",
    "explanation_primary", "explanation_secondary",
    "source", "familiar", "added_at",
]

PHRASE_FIELDS: list[str] = [
    "language", "phrase", "example_sentence", "explanation",
    "explanation_primary", "explanation_secondary",
    "source", "familiar", "added_at",
]

# Auto-guess table for CSV column headers. Lower-cased / trimmed header
# → canonical field name. Only one match wins; ambiguous columns stay
# unmapped so the UI can ask the user.
HEADER_GUESS: dict[str, str] = {
    # shared
    "language": "language", "lang": "language", "code": "language",
    "source": "source",
    "added_at": "added_at", "created_at": "added_at",
    "explanation_primary": "explanation_primary",
    "explanation_secondary": "explanation_secondary",
    "familiar": "familiar",
    # vocab
    "word": "word", "term": "word", "vocab": "word", "lemma": "word", "front": "word",
    "pos": "pos", "part_of_speech": "pos",
    "glossary": "glossary", "translation": "glossary", "meaning": "glossary",
    "definition": "glossary", "def": "glossary", "back": "glossary",
    "example": "example", "example_sentence": "example", "sentence": "example",
    "usage": "example",
    "leitner_box": "leitner_box", "box": "leitner_box", "level": "leitner_box",
    "memory_level": "leitner_box",
    "next_due": "next_due", "next_review": "next_due", "review_date": "next_due",
    # structures
    "pattern": "pattern",
    # phrases
    "phrase": "phrase",
}


# ---------- export ----------


def build_export(*, user_id: int, scope: str, lang: str | None = None) -> dict:
    """Return a versioned export payload.

    ``scope`` is one of ``"vocab" | "structures" | "phrases" | "all"``.
    ``lang`` (optional) restricts the row pull to a single language.
    """
    payload: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "exported_at": _now_iso(),
        "user_id": user_id,
        "scope": scope,
    }
    include_vocab = scope in ("vocab", "all")
    include_structures = scope in ("structures", "all")
    include_phrases = scope in ("phrases", "all")
    include_settings = scope == "all"

    if include_vocab:
        payload["vocab"] = _export_rows(
            user_id, "vocab_items",
            "id, language, word, source, sense_idx, pos, glossary, example,"
            " explanation_primary, explanation_secondary, leitner_box,"
            " next_due, added_at",
            lang=lang,
        )
    if include_structures:
        payload["structures"] = _export_rows(
            user_id, "structures",
            "id, language, pattern, example_sentence, explanation,"
            " explanation_primary, explanation_secondary, source,"
            " familiar, added_at",
            lang=lang,
        )
    if include_phrases:
        payload["phrases"] = _export_rows(
            user_id, "phrases",
            "id, language, phrase, example_sentence, explanation,"
            " explanation_primary, explanation_secondary, source,"
            " familiar, added_at",
            lang=lang,
        )
    if include_settings:
        from . import settings as settings_svc
        payload["settings"] = settings_svc.get_settings(user_id)
    return payload


def to_csv(payload: dict, *, table: str) -> str:
    """Serialise one ``table`` key of a payload to CSV."""
    fields = _fields_for(table)
    rows = payload.get(table, []) or []
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(fields)
    for row in rows:
        writer.writerow([_csv_value(row.get(f)) for f in fields])
    return buf.getvalue()


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _fields_for(table: str) -> list[str]:
    if table == "vocab":
        return VOCAB_FIELDS
    if table == "structures":
        return STRUCTURE_FIELDS
    if table == "phrases":
        return PHRASE_FIELDS
    raise ValueError(f"unknown table: {table}")


def _export_rows(user_id: int, table: str, columns: str, *,
                 lang: str | None) -> list[dict]:
    sql = f"SELECT {columns} FROM {table} WHERE user_id=?"
    params: list[Any] = [user_id]
    if lang:
        sql += " AND language=?"
        params.append(lang)
    sql += " ORDER BY added_at ASC, id ASC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ---------- parse ----------


def parse_import(*, text: str, format: str, table: str,
                 default_lang: str | None = None,
                 mapping: list[dict] | None = None,
                 has_header: bool = True) -> list[dict]:
    """Parse a file body into canonical row dicts.

    ``format`` is ``"json"`` or ``"csv"``. ``table`` selects the canonical
    field set (``vocab`` / ``structures`` / ``phrases``).

    For CSV with a header row (the default), the header names are
    auto-guessed and ``mapping`` may be supplied to override the guess.
    For CSV without a header, ``mapping`` is required and describes each
    target field's 0-based column index. Indices may appear in any order
    but every target field must be either mapped or absent; an unmapped
    canonical field stays empty.

    ``default_lang`` fills any rows missing a language value (e.g. an
    external project's CSV that assumes a single language).
    """
    if format == "json":
        return _parse_json(text, table=table, default_lang=default_lang)
    if format == "csv":
        return _parse_csv(text, table=table, default_lang=default_lang,
                          mapping=mapping or [], has_header=has_header)
    raise ValueError(f"unknown format: {format}")


def _parse_json(text: str, *, table: str,
                default_lang: str | None) -> list[dict]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise ValueError("JSON payload must be an object")
    rows = payload.get(table, [])
    if not isinstance(rows, list):
        raise ValueError(f"JSON payload.{table} must be a list")
    canonical_fields = _fields_for(table)
    out: list[dict] = []
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"{table}[{i}] must be an object")
        row = {k: raw.get(k) for k in canonical_fields}
        if not row.get("language") and default_lang:
            row["language"] = default_lang
        out.append(row)
    return out


def _parse_csv(text: str, *, table: str, default_lang: str | None,
               mapping: list[dict], has_header: bool) -> list[dict]:
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if r]
    if not rows:
        return []
    canonical_fields = _fields_for(table)
    idx_to_field = _resolve_mapping(
        mapping=mapping,
        header_row=rows[0] if has_header else None,
        canonical_fields=canonical_fields,
    )
    body = rows[1:] if has_header else rows
    parsed: list[dict] = []
    for r in body:
        if not any(c.strip() for c in r):
            continue
        row: dict[str, Any] = {f: None for f in canonical_fields}
        for field, idx in idx_to_field.items():
            if idx < len(r):
                row[field] = r[idx]
        if not row.get("language") and default_lang:
            row["language"] = default_lang
        parsed.append(row)
    return parsed


def _resolve_mapping(*, mapping: list[dict], header_row: list[str] | None,
                     canonical_fields: list[str]) -> dict[str, int]:
    """Return ``{canonical_field: 0-based column index}``.

    - If ``header_row`` is given, auto-guess names using ``HEADER_GUESS``
      and let the explicit ``mapping`` override. Column index is derived
      from the header position.
    - If no header, ``mapping`` must provide every required field's index
      by name or integer ``index``.
    """
    header_to_field: dict[int, str] = {}
    if header_row is not None:
        for i, raw in enumerate(header_row):
            key = (raw or "").strip().lower()
            if key in HEADER_GUESS:
                header_to_field[i] = HEADER_GUESS[key]
    idx_to_field: dict[str, int] = {}
    for entry in mapping or []:
        field = entry.get("field")
        if not isinstance(field, str) or field not in canonical_fields:
            continue
        idx_raw = entry.get("index")
        idx = _coerce_index(idx_raw, header_row)
        if idx is None or idx < 0:
            continue
        idx_to_field[field] = idx
    for i, field in header_to_field.items():
        idx_to_field.setdefault(field, i)
    return idx_to_field


def _coerce_index(raw: Any, header_row: list[str] | None) -> int | None:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.isdigit():
            return int(s)
        if header_row is not None:
            for i, h in enumerate(header_row):
                if (h or "").strip().lower() == s.lower():
                    return i
    return None


# ---------- merge ----------


def compute_merge(rows: list[dict], *, user_id: int, table: str) -> list[dict]:
    """Tag each parsed row with its merge status.

    Statuses: ``"new"`` (no matching DB row), ``"existing"`` (content
    match — overwrite is allowed), ``"invalid"`` (missing/invalid
    fields — will be skipped on apply). ``existing`` rows additionally
    carry ``existing_id`` and ``existing_source`` so the UI can show
    them and the apply path can guard against overwriting built-ins.
    """
    indexed = _index_existing(user_id, table)
    out: list[dict] = []
    for i, raw in enumerate(rows):
        reason = _validate_row(raw, table=table)
        if reason:
            out.append({
                "index": i, "status": "invalid",
                "fields": raw, "reason": reason,
            })
            continue
        key = _key_for_row(raw, table=table)
        if key is None:
            out.append({
                "index": i, "status": "invalid",
                "fields": raw, "reason": "missing key fields",
            })
            continue
        hit = indexed.get(key)
        if hit:
            out.append({
                "index": i, "status": "existing",
                "fields": raw,
                "existing_id": hit["id"],
                "existing_source": hit["source"],
            })
        else:
            out.append({
                "index": i, "status": "new",
                "fields": raw,
            })
    return out


def _index_existing(user_id: int, table: str) -> dict[tuple, dict]:
    if table == "vocab":
        sql = ("SELECT id, language, word, source FROM vocab_items "
               "WHERE user_id=?")
        cols = ("id", "language", "word", "source")
    elif table == "structures":
        sql = ("SELECT id, language, pattern, source FROM structures "
               "WHERE user_id=?")
        cols = ("id", "language", "pattern", "source")
    elif table == "phrases":
        sql = ("SELECT id, language, phrase, source FROM phrases "
               "WHERE user_id=?")
        cols = ("id", "language", "phrase", "source")
    else:
        raise ValueError(f"unknown table: {table}")
    out: dict[tuple, dict] = {}
    with get_conn() as conn:
        for row in conn.execute(sql, (user_id,)).fetchall():
            d = dict(row)
            key = _key_for_row(d, table=table)
            if key is not None:
                out[key] = {c: d[c] for c in cols}
    return out


def _key_for_row(row: dict, *, table: str) -> tuple | None:
    lang = row.get("language")
    if not lang:
        return None
    if table == "vocab":
        word = normalize_word(row.get("word") or "")
        if not word:
            return None
        return ("vocab", lang, word)
    if table == "structures":
        pat = (row.get("pattern") or "").strip()
        return ("structures", lang, pat) if pat else None
    if table == "phrases":
        phr = (row.get("phrase") or "").strip()
        return ("phrases", lang, phr) if phr else None
    return None


def _validate_row(row: dict, *, table: str) -> str | None:
    lang = row.get("language")
    if not is_known_lang(lang):
        return f"unknown language: {lang!r}"
    if table == "vocab":
        word = row.get("word")
        if not is_word(word or ""):
            return "word missing or invalid"
        glossary = row.get("glossary")
        if not isinstance(glossary, str) or not glossary.strip():
            return "glossary required"
    else:
        text_field = "pattern" if table == "structures" else "phrase"
        if not isinstance(row.get(text_field), str) or not (row.get(text_field) or "").strip():
            return f"{text_field} required"
        if not isinstance(row.get("example_sentence"), str) or not (row.get("example_sentence") or "").strip():
            return "example_sentence required"
        if not isinstance(row.get("explanation"), str) or not (row.get("explanation") or "").strip():
            return "explanation required"
    return None


# ---------- apply ----------


def apply_import(*, rows: list[dict], decisions: list[dict],
                 user_id: int, table: str) -> dict:
    """Apply the user's per-row decisions.

    ``decisions`` is a parallel list of ``{"index": int, "action":
    "add"|"overwrite"|"skip"}`` referring to positions in ``rows``.
    Invalid rows are always skipped regardless of decision. Built-in
    rows are never overwritten — an ``"overwrite"`` against a built-in
    silently downgrades to ``"skip"`` and is reported under
    ``builtin_protected``.

    Returns a ``{"added": N, "overwritten": N, "skipped": N,
    "builtin_protected": N}`` count plus the affected ids.
    """
    counts = {"added": 0, "overwritten": 0, "skipped": 0,
              "builtin_protected": 0, "errors": 0}
    affected_ids: list[int] = []
    errors: list[str] = []
    decision_by_index = {d.get("index"): d.get("action") for d in decisions}
    invalid_status_by_index: dict[int, bool] = {}
    # Pre-compute invalid rows so we can force-skip them regardless of
    # the user's decision. ``rows`` here are the parsed field dicts the
    # preview returned; we re-validate to stay consistent with what
    # ``compute_merge`` would have produced.
    for i, row in enumerate(rows):
        invalid_status_by_index[i] = _validate_row(row, table=table) is not None
    for i, row in enumerate(rows):
        if invalid_status_by_index.get(i):
            counts["skipped"] += 1
            continue
        action = decision_by_index.get(i) or ""
        try:
            res = _apply_one(row=row, action=action, user_id=user_id,
                             table=table)
        except Exception as e:  # noqa: BLE001 — surface to user
            counts["errors"] += 1
            errors.append(f"row {i}: {e}")
            continue
        counts[res["bucket"]] += 1
        if res.get("id") is not None:
            affected_ids.append(res["id"])
    return {**counts, "affected_ids": affected_ids, "error_messages": errors}


def _apply_one(*, row: dict, action: str, user_id: int,
               table: str) -> dict:
    if action not in ("add", "overwrite"):
        # Unknown / missing / explicit-skip all land here.
        return {"bucket": "skipped"}
    if table == "vocab":
        return _apply_vocab(row=row, action=action, user_id=user_id)
    if table == "structures":
        return _apply_other_row(row=row, action=action, user_id=user_id,
                                table="structures")
    if table == "phrases":
        return _apply_other_row(row=row, action=action, user_id=user_id,
                                table="phrases")
    raise ValueError(f"unknown table: {table}")


def _apply_vocab(*, row: dict, action: str, user_id: int) -> dict:
    existing = _lookup_vocab(user_id, row["language"], row["word"])
    if action == "overwrite":
        if existing is None:
            action = "add"
        elif existing["source"] == "built-in":
            return {"bucket": "builtin_protected"}
    res = vocab_svc.add_vocab(
        user_id=user_id,
        language=row["language"],
        word=row["word"],
        source="user",
        pos=row.get("pos"),
        glossary=row.get("glossary") or "",
        example=row.get("example"),
        explanation_primary=row.get("explanation_primary"),
        explanation_secondary=row.get("explanation_secondary"),
        leitner_box=_as_int(row.get("leitner_box"), default=1),
        next_due=row.get("next_due"),
        added_at=row.get("added_at"),
        auto_add=False,
    )
    bucket = "added" if res["created"] else "overwritten"
    return {"bucket": bucket, "id": res["id"]}


def _apply_other_row(*, row: dict, action: str, user_id: int,
                     table: str) -> dict:
    text_field = "pattern" if table == "structures" else "phrase"
    existing = _lookup_other(user_id, table=table,
                             lang=row["language"],
                             content=row[text_field])
    if action == "overwrite":
        if existing is None:
            action = "add"
        elif existing["source"] == "built-in":
            return {"bucket": "builtin_protected"}
        else:
            with transaction() as conn:
                conn.execute(
                    f"UPDATE {table} SET example_sentence=?, explanation=?,"
                    " explanation_primary=?, explanation_secondary=?,"
                    " familiar=?"
                    " WHERE id=? AND user_id=?",
                    (
                        (row.get("example_sentence") or "").strip(),
                        (row.get("explanation") or "").strip(),
                        (row.get("explanation_primary") or "").strip() or None,
                        (row.get("explanation_secondary") or "").strip() or None,
                        1 if _truthy(row.get("familiar")) else 0,
                        existing["id"], user_id,
                    ),
                )
            return {"bucket": "overwritten", "id": existing["id"]}
    if action == "add":
        with transaction() as conn:
            cur = conn.execute(
                f"INSERT INTO {table} (user_id, language, {text_field},"
                " example_sentence, explanation, explanation_primary,"
                " explanation_secondary, source, familiar)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'user', ?)",
                (
                    user_id,
                    row["language"],
                    (row.get(text_field) or "").strip(),
                    (row.get("example_sentence") or "").strip(),
                    (row.get("explanation") or "").strip(),
                    (row.get("explanation_primary") or "").strip() or None,
                    (row.get("explanation_secondary") or "").strip() or None,
                    1 if _truthy(row.get("familiar")) else 0,
                ),
            )
            new_id = cur.lastrowid
        return {"bucket": "added", "id": new_id}
    return {"bucket": "skipped"}


def _lookup_vocab(user_id: int, lang: str, word: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, source FROM vocab_items "
            "WHERE user_id=? AND language=? AND word=?",
            (user_id, lang, normalize_word(word)),
        ).fetchone()
    return dict(row) if row else None


def _lookup_other(user_id: int, *, table: str, lang: str,
                  content: str) -> dict | None:
    text_field = "pattern" if table == "structures" else "phrase"
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT id, source FROM {table} "
            "WHERE user_id=? AND language=? AND "
            f"{text_field}=?",
            (user_id, lang, (content or "").strip()),
        ).fetchone()
    return dict(row) if row else None


# ---------- helpers ----------


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _as_int(value: Any, *, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False