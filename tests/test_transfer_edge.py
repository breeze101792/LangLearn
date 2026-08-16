"""Edge-case tests for the transfer blueprint's HTTP layer.

test_transfer.py covers the happy paths and the service internals. This
file pins the blueprint's validation and error paths that were not
exercised there:

- multipart uploads (with and without a ``file`` part)
- file-too-large rejection
- format detection from filename / content-type
- form-encoded args (has_header, default_lang, mapping, table)
- invalid mapping JSON / non-list mapping
- apply: non-dict body, invalid table, non-list rows/decisions
- export: unknown lang, invalid format
- structures / phrases apply paths (add + overwrite)
"""

from __future__ import annotations

import io
import json

import pytest


@pytest.fixture
def fresh(clean_state):
    """Re-export of the autouse clean_state fixture. The conftest autouse
    fixture already sets up the data dir + db schema and clears module-level
    state — see tests/conftest.py."""
    return clean_state


def _client():
    from backend.app import create_app
    app = create_app()
    return app.test_client()


# ---------- multipart uploads ----------


def test_preview_multipart_csv_file(fresh):
    """A multipart upload with a ``file`` part named ``file`` is parsed."""
    data = {"file": (io.BytesIO(b"language,word,glossary\nen,dog,animal\n"),
                     "test.csv")}
    r = _client().post("/api/transfer/import/preview?table=vocab",
                       data=data, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["format"] == "csv"
    assert body["row_count"] == 1
    assert body["rows"][0]["fields"]["word"] == "dog"


def test_preview_multipart_json_file(fresh):
    """A multipart upload carrying a JSON body is parsed as JSON."""
    payload = json.dumps({"vocab": [
        {"language": "es", "word": "casa", "glossary": "house"},
    ]})
    data = {"file": (io.BytesIO(payload.encode("utf-8")), "backup.json")}
    r = _client().post("/api/transfer/import/preview?table=vocab",
                       data=data, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["format"] == "json"
    assert body["rows"][0]["fields"]["word"] == "casa"


def test_preview_multipart_missing_file_part_500(fresh):
    """A multipart request without a ``file`` part raises ValueError inside
    ``_read_upload``; the blueprint does not catch it, so it surfaces as a
    500. This pins the current behaviour (the error is not a 400)."""
    r = _client().post("/api/transfer/import/preview?table=vocab",
                       data={}, content_type="multipart/form-data")
    assert r.status_code == 500
    assert r.get_json()["ok"] is False


def test_preview_multipart_file_too_large(fresh):
    """A multipart file part larger than the 10 MiB cap is rejected."""
    big = b"x" * (10 * 1024 * 1024 + 1)
    data = {"file": (io.BytesIO(big), "big.csv")}
    r = _client().post("/api/transfer/import/preview?table=vocab",
                       data=data, content_type="multipart/form-data")
    assert r.status_code == 500  # ValueError escapes the route (see above)


# ---------- format detection ----------


def test_preview_format_detected_from_filename(fresh):
    """When no explicit format is given, a ``.csv`` filename selects CSV."""
    data = {"file": (io.BytesIO(b"language,word,glossary\nen,dog,animal\n"),
                     "data.csv")}
    r = _client().post("/api/transfer/import/preview?table=vocab",
                       data=data, content_type="multipart/form-data")
    assert r.get_json()["data"]["format"] == "csv"


def test_preview_format_detected_from_content_type(fresh):
    """A raw body with ``text/csv`` content-type selects CSV."""
    r = _client().post("/api/transfer/import/preview?table=vocab",
                       data="en,dog,animal\n", content_type="text/csv")
    assert r.status_code == 200
    assert r.get_json()["data"]["format"] == "csv"


# ---------- form-encoded args ----------


def test_preview_form_encoded_args(fresh):
    """has_header / default_lang / mapping / table can come from the form
    body (multipart) rather than the query string."""
    mapping = json.dumps([
        {"field": "language", "index": 0},
        {"field": "word", "index": 1},
        {"field": "glossary", "index": 2},
    ])
    data = {
        "file": (io.BytesIO(b"en,dog,animal\n"), "data.csv"),
        "has_header": "0",
        "default_lang": "en",
        "mapping": mapping,
        "table": "vocab",
    }
    r = _client().post(
        "/api/transfer/import/preview",
        data=data,
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["row_count"] == 1
    assert body["rows"][0]["fields"]["language"] == "en"
    assert body["rows"][0]["fields"]["word"] == "dog"


# ---------- invalid mapping ----------


def test_preview_invalid_mapping_json_400(fresh):
    r = _client().post(
        "/api/transfer/import/preview?table=vocab&format=csv&mapping={bad",
        data="en,dog\n", content_type="text/csv",
    )
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


def test_preview_mapping_not_a_list_400(fresh):
    r = _client().post(
        "/api/transfer/import/preview?table=vocab&format=csv"
        "&mapping=%7B%22a%22%3A1%7D",
        data="en,dog\n", content_type="text/csv",
    )
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


# ---------- apply validation ----------


def test_apply_non_dict_body_400(fresh):
    r = _client().post("/api/transfer/import/apply?table=vocab",
                       json=[1, 2, 3])
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


def test_apply_invalid_table_400(fresh):
    r = _client().post("/api/transfer/import/apply?table=banana",
                       json={"table": "banana", "rows": [], "decisions": []})
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


def test_apply_non_list_rows_400(fresh):
    r = _client().post("/api/transfer/import/apply?table=vocab",
                       json={"table": "vocab", "rows": "x", "decisions": []})
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


# ---------- export validation ----------


def test_export_unknown_lang_400(fresh):
    r = _client().get("/api/transfer/export?scope=vocab&format=json&lang=zz")
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_lang"


def test_export_invalid_format_400(fresh):
    r = _client().get("/api/transfer/export?scope=vocab&format=xml")
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


# ---------- structures / phrases apply ----------


def test_apply_structures_add(fresh):
    c = _client()
    r = c.post("/api/transfer/import/apply?table=structures", json={
        "table": "structures",
        "rows": [{"language": "en", "pattern": "S V O",
                  "example_sentence": "x", "explanation": "y"}],
        "decisions": [{"index": 0, "action": "add"}],
    })
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["added"] == 1
    assert data["overwritten"] == 0


def test_apply_structures_overwrite_user_row(fresh):
    c = _client()
    c.post("/api/transfer/import/apply?table=structures", json={
        "table": "structures",
        "rows": [{"language": "en", "pattern": "S V O",
                  "example_sentence": "x", "explanation": "y"}],
        "decisions": [{"index": 0, "action": "add"}],
    })
    r = c.post("/api/transfer/import/apply?table=structures", json={
        "table": "structures",
        "rows": [{"language": "en", "pattern": "S V O",
                  "example_sentence": "new", "explanation": "new",
                  "familiar": True}],
        "decisions": [{"index": 0, "action": "overwrite"}],
    })
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["overwritten"] == 1
    # The row's content was updated.
    items = c.get("/api/structures?lang=en").get_json()["data"]["items"]
    row = next(i for i in items if i["pattern"] == "S V O")
    assert row["example_sentence"] == "new"
    assert row["familiar"] in (1, True)


def test_apply_phrases_add(fresh):
    c = _client()
    r = c.post("/api/transfer/import/apply?table=phrases", json={
        "table": "phrases",
        "rows": [{"language": "en", "phrase": "Hi",
                  "example_sentence": "x", "explanation": "y"}],
        "decisions": [{"index": 0, "action": "add"}],
    })
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["added"] == 1


def test_apply_phrases_overwrite_user_row(fresh):
    c = _client()
    c.post("/api/transfer/import/apply?table=phrases", json={
        "table": "phrases",
        "rows": [{"language": "en", "phrase": "Hi",
                  "example_sentence": "x", "explanation": "y"}],
        "decisions": [{"index": 0, "action": "add"}],
    })
    r = c.post("/api/transfer/import/apply?table=phrases", json={
        "table": "phrases",
        "rows": [{"language": "en", "phrase": "Hi",
                  "example_sentence": "new", "explanation": "new"}],
        "decisions": [{"index": 0, "action": "overwrite"}],
    })
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["overwritten"] == 1
    items = c.get("/api/phrases?lang=en").get_json()["data"]["items"]
    row = next(i for i in items if i["phrase"] == "Hi")
    assert row["example_sentence"] == "new"


def test_apply_phrases_builtin_protected(fresh):
    """An overwrite against a built-in phrase is downgraded to skip."""
    from backend.db import transaction
    with transaction() as conn:
        conn.execute(
            "INSERT INTO phrases (user_id, language, phrase, example_sentence,"
            " explanation, explanation_primary, explanation_secondary, source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'built-in')",
            (1, "en", "BuiltIn", "x", "x", None, None),
        )
    c = _client()
    r = c.post("/api/transfer/import/apply?table=phrases", json={
        "table": "phrases",
        "rows": [{"language": "en", "phrase": "BuiltIn",
                  "example_sentence": "y", "explanation": "y"}],
        "decisions": [{"index": 0, "action": "overwrite"}],
    })
    data = r.get_json()["data"]
    assert data["builtin_protected"] == 1
    assert data["overwritten"] == 0
