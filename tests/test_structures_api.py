"""Tests for structures + phrases blueprints."""

from __future__ import annotations

import json

import pytest


def test_structures_list_empty():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.get("/api/structures?lang=en")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"]["items"] == []


def test_structures_add_and_edit_user():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()

    r = client.post("/api/structures",
                    json={"language": "en", "pattern": "S V O",
                          "explanation_primary": "Basic SVO",
                          "source": "user"})
    assert r.status_code == 200
    sid = r.get_json()["data"]["id"]

    r = client.get("/api/structures?lang=en")
    items = r.get_json()["data"]["items"]
    assert any(i["id"] == sid and i["source"] == "user" for i in items)

    r = client.put(f"/api/structures/{sid}",
                   json={"pattern": "S V O (revised)"})
    assert r.status_code == 200

    r = client.delete(f"/api/structures/{sid}")
    assert r.status_code == 200


def test_built_in_structures_are_readonly():
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import seed as seed_svc
    init_schema()
    seed_svc.initialize_language("en")
    app = create_app()
    client = app.test_client()

    r = client.get("/api/structures?lang=en")
    items = r.get_json()["data"]["items"]
    builtin = [i for i in items if i["source"] == "built-in"]
    assert len(builtin) > 0

    sid = builtin[0]["id"]
    r = client.put(f"/api/structures/{sid}", json={"pattern": "X"})
    assert r.status_code == 403
    r = client.delete(f"/api/structures/{sid}")
    assert r.status_code == 403


def test_phrases_add_and_edit():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()

    r = client.post("/api/phrases",
                    json={"language": "en", "phrase": "Hi",
                          "explanation_primary": "Casual hello",
                          "source": "user"})
    assert r.status_code == 200
    pid = r.get_json()["data"]["id"]

    r = client.put(f"/api/phrases/{pid}", json={"phrase": "Hello"})
    assert r.status_code == 200
    r = client.delete(f"/api/phrases/{pid}")
    assert r.status_code == 200


def test_invalid_language_rejected():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()

    r = client.get("/api/structures?lang=invalid")
    assert r.status_code == 400
    body = r.get_json()
    assert body["ok"] is False