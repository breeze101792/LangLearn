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
                          "source": "user", "example_sentence": "...", "explanation": "..."})
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
                          "source": "user", "explanation": "...", "example_sentence": "..."})
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


# --- familiar flag ---------------------------------------------------------


def test_structures_default_familiar_is_false():
    """Brand-new rows default to unfamiliar so they show up in the default tab."""
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures",
                    json={"language": "en", "pattern": "S V O",
                          "explanation_primary": "Basic", "source": "user", "example_sentence": "...", "explanation": "..."})
    sid = r.get_json()["data"]["id"]
    items = client.get("/api/structures?lang=en").get_json()["data"]["items"]
    row = next(i for i in items if i["id"] == sid)
    assert row["familiar"] is False or row["familiar"] == 0


def test_structures_patch_familiar_marks_and_filters():
    """PATCH flips the flag; list filter ?familiar=1 only returns familiar rows."""
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()

    a = client.post("/api/structures",
                    json={"language": "en", "pattern": "A B C",
                          "explanation_primary": "a", "source": "user", "example_sentence": "...", "explanation": "..."}).get_json()["data"]["id"]
    b = client.post("/api/structures",
                    json={"language": "en", "pattern": "D E F",
                          "explanation_primary": "b", "source": "user", "example_sentence": "...", "explanation": "..."}).get_json()["data"]["id"]

    r = client.patch(f"/api/structures/{a}", json={"familiar": True, "example_sentence": "...", "explanation": "...", "pattern": "..."})
    assert r.status_code == 200
    assert r.get_json()["data"]["familiar"] is True

    items = client.get("/api/structures?lang=en&familiar=1").get_json()["data"]["items"]
    assert {i["id"] for i in items} == {a}

    items_unfamiliar = client.get("/api/structures?lang=en&familiar=0").get_json()["data"]["items"]
    assert {i["id"] for i in items_unfamiliar} == {b}


def test_structures_patch_familiar_works_on_builtin():
    """Built-in rows are otherwise read-only, but the familiar toggle must work."""
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import seed as seed_svc
    init_schema()
    seed_svc.initialize_language("en")
    app = create_app()
    client = app.test_client()

    items = client.get("/api/structures?lang=en").get_json()["data"]["items"]
    builtin = next(i for i in items if i["source"] == "built-in")
    sid = builtin["id"]

    r = client.patch(f"/api/structures/{sid}", json={"familiar": True})
    assert r.status_code == 200

    items_familiar = client.get("/api/structures?lang=en&familiar=1").get_json()["data"]["items"]
    assert any(i["id"] == sid for i in items_familiar)


def test_structures_patch_familiar_validates_body():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    sid = client.post("/api/structures",
                      json={"language": "en", "pattern": "S V O",
                            "explanation_primary": "x", "example_sentence": "...", "explanation": "..."}).get_json()["data"]["id"]

    r = client.patch(f"/api/structures/{sid}", json={})
    assert r.status_code == 400
    r = client.patch(f"/api/structures/{sid}", json={"familiar": "yes"})
    assert r.status_code == 400


def test_structures_patch_familiar_404():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.patch("/api/structures/99999", json={"familiar": True})
    assert r.status_code == 404


def test_phrases_default_familiar_is_false():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    pid = client.post("/api/phrases",
                      json={"language": "en", "phrase": "Hi",
                            "explanation_primary": "x", "source": "user", "explanation": "...", "example_sentence": "..."}).get_json()["data"]["id"]
    row = next(i for i in client.get("/api/phrases?lang=en").get_json()["data"]["items"]
               if i["id"] == pid)
    assert row["familiar"] is False or row["familiar"] == 0


def test_phrases_patch_familiar_marks_and_filters():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    pid = client.post("/api/phrases",
                      json={"language": "en", "phrase": "Hi",
                            "explanation_primary": "x", "source": "user", "explanation": "...", "example_sentence": "..."}).get_json()["data"]["id"]
    r = client.patch(f"/api/phrases/{pid}", json={"familiar": True, "explanation": "...", "example_sentence": "...", "phrase": "..."})
    assert r.status_code == 200
    items = client.get("/api/phrases?lang=en&familiar=1").get_json()["data"]["items"]
    assert any(i["id"] == pid for i in items)
    items_unfamiliar = client.get("/api/phrases?lang=en&familiar=0").get_json()["data"]["items"]
    assert not any(i["id"] == pid for i in items_unfamiliar)


def test_phrases_patch_familiar_works_on_builtin():
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import seed as seed_svc
    init_schema()
    seed_svc.initialize_language("en")
    app = create_app()
    client = app.test_client()
    items = client.get("/api/phrases?lang=en").get_json()["data"]["items"]
    builtin = next(i for i in items if i["source"] == "built-in")
    pid = builtin["id"]
    r = client.patch(f"/api/phrases/{pid}", json={"familiar": True})
    assert r.status_code == 200


def test_phrases_patch_familiar_validates_body():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    pid = client.post("/api/phrases",
                      json={"language": "en", "phrase": "Hi",
                            "explanation_primary": "x", "source": "user", "explanation": "...", "example_sentence": "..."}).get_json()["data"]["id"]
    assert client.patch(f"/api/phrases/{pid}", json={}).status_code == 400
    assert client.patch(f"/api/phrases/{pid}", json={"familiar": "yes"}).status_code == 400


def test_phrases_patch_familiar_404():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.patch("/api/phrases/99999", json={"familiar": True})
    assert r.status_code == 404


def test_structures_filter_invalid_value_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.get("/api/structures?lang=en&familiar=maybe")
    assert r.status_code == 400


def test_phrases_filter_invalid_value_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.get("/api/phrases?lang=en&familiar=maybe")
    assert r.status_code == 400


def test_structures_patch_unfamiliar_round_trip():
    """Toggling familiar then back to unfamiliar restores the row to the default tab."""
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    sid = client.post("/api/structures",
                      json={"language": "en", "pattern": "S V O",
                            "explanation_primary": "x", "source": "user", "example_sentence": "...", "explanation": "..."}).get_json()["data"]["id"]

    client.patch(f"/api/structures/{sid}", json={"familiar": True, "example_sentence": "...", "explanation": "...", "pattern": "..."})
    client.patch(f"/api/structures/{sid}", json={"familiar": False})

    items = client.get("/api/structures?lang=en&familiar=0").get_json()["data"]["items"]
    assert any(i["id"] == sid for i in items)
    items_familiar = client.get("/api/structures?lang=en&familiar=1").get_json()["data"]["items"]
    assert not any(i["id"] == sid for i in items_familiar)