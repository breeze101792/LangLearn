"""Edge-case tests for the phrases and structures blueprints.

test_structures_api.py covers the main CRUD + familiar flows. This file
pins the remaining validation branches:

- phrases: non-string field values raise 400 (via _coerce_str)
- phrases: PUT with a non-dict body returns 400
- phrases: PATCH familiar accepts integer 0/1
- structures: non-string field values raise 400
- structures: PUT with a required field set to null stores empty string
- structures: PATCH familiar accepts integer 0/1
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(clean_state):
    """Re-export of the autouse clean_state fixture."""
    return clean_state


def _client():
    from backend.app import create_app
    app = create_app()
    return app.test_client()


# ---------- phrases ----------


def test_phrases_add_rejects_non_string_field(fresh):
    """A non-string example_sentence raises ValueError -> 400."""
    c = _client()
    r = c.post("/api/phrases", json={
        "language": "en", "phrase": "Hi",
        "example_sentence": 123, "explanation": "x",
    })
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


def test_phrases_add_rejects_missing_example(fresh):
    c = _client()
    r = c.post("/api/phrases", json={
        "language": "en", "phrase": "Hi", "explanation": "x",
    })
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


def test_phrases_add_rejects_missing_explanation(fresh):
    c = _client()
    r = c.post("/api/phrases", json={
        "language": "en", "phrase": "Hi", "example_sentence": "x",
    })
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


def test_phrases_put_non_dict_body_400(fresh):
    c = _client()
    pid = c.post("/api/phrases", json={
        "language": "en", "phrase": "Hi", "example_sentence": "x",
        "explanation": "y",
    }).get_json()["data"]["id"]
    r = c.put(f"/api/phrases/{pid}", json=[1, 2, 3])
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


def test_phrases_patch_familiar_accepts_int_0_1(fresh):
    c = _client()
    pid = c.post("/api/phrases", json={
        "language": "en", "phrase": "Hi", "example_sentence": "x",
        "explanation": "y",
    }).get_json()["data"]["id"]
    r = c.patch(f"/api/phrases/{pid}", json={"familiar": 1})
    assert r.status_code == 200
    assert r.get_json()["data"]["familiar"] is True
    r = c.patch(f"/api/phrases/{pid}", json={"familiar": 0})
    assert r.status_code == 200
    assert r.get_json()["data"]["familiar"] is False


# ---------- structures ----------


def test_structures_add_rejects_non_string_field(fresh):
    c = _client()
    r = c.post("/api/structures", json={
        "language": "en", "pattern": "S V O",
        "example_sentence": 123, "explanation": "x",
    })
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


def test_structures_add_rejects_missing_example(fresh):
    c = _client()
    r = c.post("/api/structures", json={
        "language": "en", "pattern": "S V O", "explanation": "x",
    })
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


def test_structures_add_rejects_missing_explanation(fresh):
    c = _client()
    r = c.post("/api/structures", json={
        "language": "en", "pattern": "S V O", "example_sentence": "x",
    })
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


def test_structures_put_required_null_stores_empty(fresh):
    """A required field set to null is stored as an empty string (NOT NULL
    constraint), not rejected."""
    c = _client()
    sid = c.post("/api/structures", json={
        "language": "en", "pattern": "S V O", "example_sentence": "x",
        "explanation": "y",
    }).get_json()["data"]["id"]
    r = c.put(f"/api/structures/{sid}", json={"example_sentence": None})
    assert r.status_code == 200
    items = c.get("/api/structures?lang=en").get_json()["data"]["items"]
    row = next(i for i in items if i["id"] == sid)
    assert row["example_sentence"] == ""


def test_structures_patch_familiar_accepts_int_0_1(fresh):
    c = _client()
    sid = c.post("/api/structures", json={
        "language": "en", "pattern": "S V O", "example_sentence": "x",
        "explanation": "y",
    }).get_json()["data"]["id"]
    r = c.patch(f"/api/structures/{sid}", json={"familiar": 1})
    assert r.status_code == 200
    assert r.get_json()["data"]["familiar"] is True
    r = c.patch(f"/api/structures/{sid}", json={"familiar": 0})
    assert r.status_code == 200
    assert r.get_json()["data"]["familiar"] is False
