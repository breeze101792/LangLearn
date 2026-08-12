"""Tests for the transfer blueprint + service.

Covers:

- Export: JSON shape, CSV download, CSV ``scope=all`` rejection, lang
  filter, ``default_lang`` fallback on import, JSON round-trip.
- Preview: marks new/existing/invalid correctly; CSV with header is
  auto-mapped; CSV without header requires a mapping.
- Apply: per-row actions honored, built-in rows are not overwritten,
  invalid rows skipped regardless of decision.
"""

from __future__ import annotations

import json


# ---------- helpers ----------

def _client():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    return app.test_client()


def _seed_vocab(client, *, word="casa", lang="es", glossary="house", box=1):
    """Add a vocab row via the canonical service to bypass HTTP layer."""
    from backend.services import vocab as v
    return v.add_vocab(
        user_id=1, language=lang, word=word, source="user",
        glossary=glossary, pos="noun", leitner_box=box,
    )


# ---------- export ----------

def test_export_json_full_backup():
    _seed_vocab(client=_client(), word="casa", lang="es", glossary="house")
    r = _client().get("/api/transfer/export?scope=all&format=json")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    data = body["data"]
    assert data["scope"] == "all"
    assert data["format_version"] == 1
    assert any(row["word"] == "casa" for row in data["vocab"])
    assert "settings" in data  # full backup carries settings


def test_export_json_vocab_only():
    _seed_vocab(client=_client(), word="perro")
    r = _client().get("/api/transfer/export?scope=vocab&format=json")
    body = r.get_json()["data"]
    assert "vocab" in body
    assert "structures" not in body
    assert "settings" not in body


def test_export_json_lang_filter():
    _seed_vocab(client=_client(), word="casa", lang="es", glossary="house")
    _seed_vocab(client=_client(), word="dog", lang="en", glossary="animal")
    r = _client().get("/api/transfer/export?scope=vocab&format=json&lang=es")
    rows = r.get_json()["data"]["vocab"]
    assert {row["word"] for row in rows} == {"casa"}


def test_export_csv_returns_file():
    _seed_vocab(client=_client(), word="perro", lang="es", glossary="dog")
    r = _client().get("/api/transfer/export?scope=vocab&format=csv&lang=es")
    assert r.status_code == 200
    assert "text/csv" in r.headers["Content-Type"]
    assert "attachment" in r.headers["Content-Disposition"]
    body = r.get_data(as_text=True)
    assert body.startswith("language,word,source")
    assert "perro" in body


def test_export_csv_scope_all_rejected():
    r = _client().get("/api/transfer/export?scope=all&format=csv")
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_export_invalid_scope():
    r = _client().get("/api/transfer/export?scope=banana&format=json")
    assert r.status_code == 400


# ---------- preview: JSON ----------

def test_preview_json_round_trip_marks_existing_and_new():
    """Export → import preview shows existing rows as 'existing' and new rows as 'new'."""
    _seed_vocab(client=_client(), word="casa", lang="es", glossary="house")
    payload = _client().get("/api/transfer/export?scope=vocab&format=json").get_json()["data"]
    payload["vocab"].append({
        "language": "es", "word": "perro", "glossary": "dog",
    })
    body = json.dumps(payload)
    r = _client().post("/api/transfer/import/preview?table=vocab&format=json",
                       data=body, content_type="application/json")
    assert r.status_code == 200
    data = r.get_json()["data"]
    rows = data["rows"]
    statuses = {row["fields"]["word"]: row["status"] for row in rows}
    assert statuses["casa"] == "existing"
    assert statuses["perro"] == "new"
    assert data["stats"] == {"new": 1, "existing": 1, "invalid": 0}


def test_preview_marks_invalid_rows():
    body = json.dumps({"vocab": [
        {"language": "es", "word": "ok", "glossary": "fine"},
        {"language": "es", "word": "no_glossary"},  # missing glossary
        {"language": "zz", "word": "bad_lang", "glossary": "x"},
        {"language": "es", "word": ""},  # missing word
    ]})
    r = _client().post("/api/transfer/import/preview?table=vocab&format=json",
                       data=body, content_type="application/json")
    rows = r.get_json()["data"]["rows"]
    statuses = {(row["fields"].get("word") or ""): row["status"] for row in rows}
    assert statuses["ok"] == "new"
    invalid = [row for row in rows if row["status"] == "invalid"]
    assert len(invalid) == 3
    reasons = {(row["fields"].get("word") or ""): row["reason"] for row in invalid}
    assert "glossary" in reasons["no_glossary"].lower()
    assert "language" in reasons["bad_lang"].lower()
    assert "word" in reasons[""].lower()


# ---------- preview: CSV ----------

def test_preview_csv_with_header_auto_maps():
    csv_text = "language,word,glossary\nen,dog,animal\n"
    r = _client().post(
        "/api/transfer/import/preview?table=vocab&format=csv&has_header=1",
        data=csv_text, content_type="text/csv",
    )
    assert r.status_code == 200
    rows = r.get_json()["data"]["rows"]
    assert rows[0]["status"] == "new"
    assert rows[0]["fields"]["word"] == "dog"


def test_preview_csv_headerless_requires_mapping():
    csv_text = "en,dog,animal\n"
    r = _client().post(
        "/api/transfer/import/preview?table=vocab&format=csv&has_header=0",
        data=csv_text, content_type="text/csv",
    )
    # Without a mapping, every column index is unmapped; the row's
    # required fields are missing → marked invalid.
    rows = r.get_json()["data"]["rows"]
    assert rows[0]["status"] == "invalid"


def test_preview_csv_headerless_with_explicit_mapping():
    """Headerless CSV from another project: user supplies a mapping by index."""
    csv_text = "en,dog,animal,noun\nfr,chat,cat,n\n"
    mapping = json.dumps([
        {"field": "language", "index": 0},
        {"field": "word",     "index": 1},
        {"field": "glossary", "index": 2},
        {"field": "pos",      "index": 3},
    ])
    r = _client().post(
        "/api/transfer/import/preview?table=vocab&format=csv&has_header=0"
        f"&mapping={mapping}",
        data=csv_text, content_type="text/csv",
    )
    rows = r.get_json()["data"]["rows"]
    assert all(r["status"] == "new" for r in rows)
    assert rows[0]["fields"]["word"] == "dog"
    assert rows[0]["fields"]["pos"] == "noun"


def test_preview_csv_default_lang_fills_missing():
    csv_text = "dog,animal\ncat,feline\n"
    mapping = json.dumps([
        {"field": "word", "index": 0},
        {"field": "glossary", "index": 1},
    ])
    r = _client().post(
        "/api/transfer/import/preview?table=vocab&format=csv&has_header=0"
        "&default_lang=en&" + f"mapping={mapping}",
        data=csv_text, content_type="text/csv",
    )
    rows = r.get_json()["data"]["rows"]
    assert all(r["fields"]["language"] == "en" for r in rows)


# ---------- apply ----------

def test_apply_adds_new_and_overwrites_existing():
    c = _client()
    _seed_vocab(client=c, word="casa", lang="es", glossary="house")
    rows = [
        {"language": "es", "word": "casa", "glossary": "home (overwritten)"},
        {"language": "es", "word": "perro", "glossary": "dog"},
    ]
    decisions = [
        {"index": 0, "action": "overwrite"},
        {"index": 1, "action": "add"},
    ]
    r = c.post("/api/transfer/import/apply?table=vocab",
               json={"table": "vocab", "rows": rows, "decisions": decisions})
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["added"] == 1
    assert data["overwritten"] == 1
    assert data["skipped"] == 0
    assert data["errors"] == 0


def test_apply_invalid_rows_always_skipped():
    c = _client()
    rows = [
        {"language": "es", "word": "ok", "glossary": "fine"},
        {"language": "es", "word": "no_glossary"},
    ]
    decisions = [
        {"index": 0, "action": "add"},
        {"index": 1, "action": "add"},  # would normally add, but row is invalid
    ]
    r = c.post("/api/transfer/import/apply?table=vocab",
               json={"table": "vocab", "rows": rows, "decisions": decisions})
    data = r.get_json()["data"]
    assert data["added"] == 1
    assert data["skipped"] == 1


def test_apply_skips_overwrite_when_target_is_builtin():
    """Built-in rows are protected: an overwrite decision downgrades to skip."""
    from backend.db import transaction, get_conn
    # Seed a built-in structure row directly.
    with transaction() as conn:
        conn.execute(
            "INSERT INTO structures (user_id, language, pattern, example_sentence,"
            " explanation, explanation_primary, explanation_secondary, source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'built-in')",
            (1, "en", "S V O", "x", "x", None, None),
        )
    rows = [{"language": "en", "pattern": "S V O", "example_sentence": "y",
             "explanation": "y"}]
    decisions = [{"index": 0, "action": "overwrite"}]
    r = _client().post("/api/transfer/import/apply?table=structures",
                       json={"table": "structures", "rows": rows,
                             "decisions": decisions})
    data = r.get_json()["data"]
    assert data["builtin_protected"] == 1
    assert data["overwritten"] == 0
    # The built-in row is untouched.
    with get_conn() as conn:
        row = conn.execute(
            "SELECT example_sentence FROM structures WHERE pattern='S V O'"
        ).fetchone()
    assert row["example_sentence"] == "x"


def test_apply_imports_preserved_history():
    """Imported rows keep their leitner_box / next_due verbatim."""
    c = _client()
    rows = [{"language": "es", "word": "perro", "glossary": "dog",
             "leitner_box": 4, "next_due": "2099-01-01 00:00:00"}]
    decisions = [{"index": 0, "action": "add"}]
    r = c.post("/api/transfer/import/apply?table=vocab",
               json={"table": "vocab", "rows": rows, "decisions": decisions})
    assert r.status_code == 200
    # Verify by exporting back.
    export = c.get("/api/transfer/export?scope=vocab&format=json").get_json()["data"]
    [row] = [r for r in export["vocab"] if r["word"] == "perro"]
    assert row["leitner_box"] == 4
    assert row["next_due"].startswith("2099-01-01")


def test_apply_unknown_action_treated_as_skip():
    c = _client()
    rows = [{"language": "es", "word": "perro", "glossary": "dog"}]
    decisions = [{"index": 0, "action": "bogus"}]
    r = c.post("/api/transfer/import/apply?table=vocab",
               json={"table": "vocab", "rows": rows, "decisions": decisions})
    data = r.get_json()["data"]
    assert data["skipped"] == 1
    assert data["added"] == 0