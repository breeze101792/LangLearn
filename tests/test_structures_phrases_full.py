"""More HTTP tests for structures + phrases blueprints.

test_structures_api.py covers the happy paths and built-in read-only
behavior. This file targets validation, LLM-fill, missing fields,
non-built-in sources, edit/delete not-found, and method-not-allowed paths.
"""

from __future__ import annotations

import json as _json
from unittest import mock

import pytest


def _stub_llm_payload():
    return {
        "pattern": "S V O",
        "example_sentence": "She reads books.",
        "explanation_primary": "Subject verb object.",
        "explanation_secondary": "主谓宾。",
    }


def _stub_phrase_payload():
    return {
        "literal_translation": "good night",
        "explanation_primary": "Farewell used in the evening.",
        "explanation_secondary": "晚安。",
    }


# --- structures: list ----------------------------------------------------


def test_structures_list_missing_lang_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.get("/api/structures")
    assert r.status_code == 400


def test_structures_list_unknown_lang_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.get("/api/structures?lang=ENG")
    assert r.status_code == 400


def test_structures_list_returns_only_user_language():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    client.post("/api/structures",
                json={"language": "en", "pattern": "S V O",
                      "explanation_primary": "Basic", "source": "user"})
    client.post("/api/structures",
                json={"language": "es", "pattern": "S V O",
                      "explanation_primary": "Basico", "source": "user"})
    en = client.get("/api/structures?lang=en").get_json()["data"]["items"]
    es = client.get("/api/structures?lang=es").get_json()["data"]["items"]
    assert all(i["language"] == "en" for i in en)
    assert all(i["language"] == "es" for i in es)


# --- structures: add -----------------------------------------------------


def test_structures_add_invalid_language_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures",
                    json={"language": "ENG", "pattern": "S V O",
                          "explanation_primary": "Basic"})
    assert r.status_code == 400


def test_structures_add_missing_pattern_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures",
                    json={"language": "en", "explanation_primary": "Basic"})
    assert r.status_code == 400


def test_structures_add_blank_pattern_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures",
                    json={"language": "en", "pattern": "   ",
                          "explanation_primary": "Basic"})
    assert r.status_code == 400


def test_structures_add_pattern_too_long_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures",
                    json={"language": "en", "pattern": "X" * 501,
                          "explanation_primary": "Basic"})
    assert r.status_code == 400


def test_structures_add_non_string_pattern_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures",
                    json={"language": "en", "pattern": 123,
                          "explanation_primary": "Basic"})
    assert r.status_code == 400


def test_structures_add_default_source_is_user():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures",
                    json={"language": "en", "pattern": "S V O",
                          "explanation_primary": "Basic"})
    assert r.status_code == 200
    assert r.get_json()["data"]["source"] == "user"


def test_structures_add_unknown_source_coerced_to_user():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures",
                    json={"language": "en", "pattern": "S V O",
                          "explanation_primary": "Basic",
                          "source": "made_up"})
    assert r.status_code == 200
    assert r.get_json()["data"]["source"] == "user"


def test_structures_add_llm_source_accepted():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures",
                    json={"language": "en", "pattern": "S V O",
                          "explanation_primary": "Basic", "source": "llm"})
    assert r.status_code == 200
    assert r.get_json()["data"]["source"] == "llm"


# --- structures: update --------------------------------------------------


def test_structures_update_not_found_404():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.put("/api/structures/9999", json={"pattern": "X"})
    assert r.status_code == 404


def test_structures_update_non_dict_body_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures",
                    json={"language": "en", "pattern": "S V O",
                          "explanation_primary": "Basic", "source": "user"})
    sid = r.get_json()["data"]["id"]
    r = client.put(f"/api/structures/{sid}", json=["not", "a", "dict"])
    assert r.status_code == 400


def test_structures_update_clears_explanation_with_null():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures",
                    json={"language": "en", "pattern": "S V O",
                          "explanation_primary": "Basic",
                          "explanation_secondary": "second", "source": "user"})
    sid = r.get_json()["data"]["id"]
    r = client.put(f"/api/structures/{sid}",
                   json={"explanation_secondary": None})
    assert r.status_code == 200
    items = client.get("/api/structures?lang=en").get_json()["data"]["items"]
    target = next(i for i in items if i["id"] == sid)
    assert target["explanation_secondary"] is None


def test_structures_update_no_fields_is_noop():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures",
                    json={"language": "en", "pattern": "S V O",
                          "explanation_primary": "Basic", "source": "user"})
    sid = r.get_json()["data"]["id"]
    r = client.put(f"/api/structures/{sid}", json={})
    assert r.status_code == 200


def test_structures_update_rejects_empty_pattern():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures",
                    json={"language": "en", "pattern": "S V O",
                          "explanation_primary": "Basic", "source": "user"})
    sid = r.get_json()["data"]["id"]
    r = client.put(f"/api/structures/{sid}", json={"pattern": "   "})
    assert r.status_code == 400


def test_structures_update_pattern_too_long_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures",
                    json={"language": "en", "pattern": "S V O",
                          "explanation_primary": "Basic", "source": "user"})
    sid = r.get_json()["data"]["id"]
    r = client.put(f"/api/structures/{sid}", json={"pattern": "X" * 501})
    assert r.status_code == 400


# --- structures: delete --------------------------------------------------


def test_structures_delete_not_found_404():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.delete("/api/structures/9999")
    assert r.status_code == 404


def test_structures_delete_then_404():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures",
                    json={"language": "en", "pattern": "S V O",
                          "explanation_primary": "Basic", "source": "user"})
    sid = r.get_json()["data"]["id"]
    client.delete(f"/api/structures/{sid}")
    r2 = client.delete(f"/api/structures/{sid}")
    assert r2.status_code == 404


# --- structures: fill via LLM -------------------------------------------


def test_structures_fill_returns_llm_payload(monkeypatch):
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import llm as llm_svc
    init_schema()

    monkeypatch.setattr(llm_svc, "fill_structure_via_llm",
                        lambda *, lang, partial: _stub_llm_payload())
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures/fill",
                    json={"language": "en", "pattern": "S V O"})
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["pattern"] == "S V O"
    assert data["example_sentence"] == "She reads books."


def test_structures_fill_invalid_language_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures/fill",
                    json={"language": "ENG", "pattern": "S V O"})
    assert r.status_code == 400


def test_structures_fill_llm_error_returns_502(monkeypatch):
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import llm as llm_svc
    init_schema()

    def boom(*, lang, partial):
        raise llm_svc.LLMError("provider unavailable")
    monkeypatch.setattr(llm_svc, "fill_structure_via_llm", boom)
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures/fill",
                    json={"language": "en", "pattern": "S V O"})
    assert r.status_code == 502


def test_structures_fill_llm_schema_error_returns_502(monkeypatch):
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import llm as llm_svc
    init_schema()

    def boom(*, lang, partial):
        raise llm_svc.LLMSchemaError("bad shape")
    monkeypatch.setattr(llm_svc, "fill_structure_via_llm", boom)
    app = create_app()
    client = app.test_client()
    r = client.post("/api/structures/fill",
                    json={"language": "en", "pattern": "S V O"})
    assert r.status_code == 502


# --- phrases: list -------------------------------------------------------


def test_phrases_list_missing_lang_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.get("/api/phrases")
    assert r.status_code == 400


def test_phrases_list_invalid_lang_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.get("/api/phrases?lang=ENG")
    assert r.status_code == 400


def test_phrases_list_isolates_by_language():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    client.post("/api/phrases",
                json={"language": "en", "phrase": "Hi",
                      "explanation_primary": "Hello"})
    client.post("/api/phrases",
                json={"language": "es", "phrase": "Hola",
                      "explanation_primary": "Hello"})
    en = client.get("/api/phrases?lang=en").get_json()["data"]["items"]
    es = client.get("/api/phrases?lang=es").get_json()["data"]["items"]
    assert all(i["language"] == "en" for i in en)
    assert all(i["language"] == "es" for i in es)


# --- phrases: add --------------------------------------------------------


def test_phrases_add_missing_phrase_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/phrases",
                    json={"language": "en", "explanation_primary": "Hello"})
    assert r.status_code == 400


def test_phrases_add_blank_phrase_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/phrases",
                    json={"language": "en", "phrase": "   ",
                          "explanation_primary": "Hello"})
    assert r.status_code == 400


def test_phrases_add_phrase_too_long_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/phrases",
                    json={"language": "en", "phrase": "X" * 501,
                          "explanation_primary": "Hello"})
    assert r.status_code == 400


def test_phrases_add_default_source_is_user():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/phrases",
                    json={"language": "en", "phrase": "Hi",
                          "explanation_primary": "Hello"})
    assert r.status_code == 200
    assert r.get_json()["data"]["source"] == "user"


def test_phrases_add_unknown_source_coerced_to_user():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/phrases",
                    json={"language": "en", "phrase": "Hi",
                          "explanation_primary": "Hello",
                          "source": "made_up"})
    assert r.status_code == 200
    assert r.get_json()["data"]["source"] == "user"


def test_phrases_add_invalid_language_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/phrases",
                    json={"language": "ENG", "phrase": "Hi",
                          "explanation_primary": "Hello"})
    assert r.status_code == 400


# --- phrases: update / delete -------------------------------------------


def test_phrases_update_not_found_404():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.put("/api/phrases/9999", json={"phrase": "Hi"})
    assert r.status_code == 404


def test_phrases_update_clears_lit_translation_with_null():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/phrases",
                    json={"language": "en", "phrase": "Hi",
                          "literal_translation": "hello",
                          "explanation_primary": "Hello"})
    pid = r.get_json()["data"]["id"]
    r = client.put(f"/api/phrases/{pid}", json={"literal_translation": None})
    assert r.status_code == 200
    items = client.get("/api/phrases?lang=en").get_json()["data"]["items"]
    target = next(i for i in items if i["id"] == pid)
    assert target["literal_translation"] is None


def test_phrases_update_rejects_empty_phrase():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/phrases",
                    json={"language": "en", "phrase": "Hi",
                          "explanation_primary": "Hello"})
    pid = r.get_json()["data"]["id"]
    r = client.put(f"/api/phrases/{pid}", json={"phrase": "   "})
    assert r.status_code == 400


def test_phrases_update_phrase_too_long_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/phrases",
                    json={"language": "en", "phrase": "Hi",
                          "explanation_primary": "Hello"})
    pid = r.get_json()["data"]["id"]
    r = client.put(f"/api/phrases/{pid}", json={"phrase": "X" * 501})
    assert r.status_code == 400


def test_phrases_delete_not_found_404():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.delete("/api/phrases/9999")
    assert r.status_code == 404


def test_phrases_built_in_readonly():
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import seed as seed_svc
    init_schema()
    seed_svc.initialize_language("en")
    app = create_app()
    client = app.test_client()
    items = client.get("/api/phrases?lang=en").get_json()["data"]["items"]
    builtin = [i for i in items if i["source"] == "built-in"]
    if builtin:
        pid = builtin[0]["id"]
        r = client.put(f"/api/phrases/{pid}", json={"phrase": "X"})
        assert r.status_code == 403
        r = client.delete(f"/api/phrases/{pid}")
        assert r.status_code == 403


# --- phrases: fill via LLM ---------------------------------------------


def test_phrases_fill_returns_llm_payload(monkeypatch):
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import llm as llm_svc
    init_schema()

    monkeypatch.setattr(llm_svc, "fill_phrase_via_llm",
                        lambda *, lang, partial: _stub_phrase_payload())
    app = create_app()
    client = app.test_client()
    r = client.post("/api/phrases/fill",
                    json={"language": "en"})
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["literal_translation"] == "good night"


def test_phrases_fill_invalid_language_400():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/phrases/fill", json={"language": "ENG"})
    assert r.status_code == 400


def test_phrases_fill_llm_error_returns_502(monkeypatch):
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import llm as llm_svc
    init_schema()

    def boom(*, lang, partial):
        raise llm_svc.LLMError("provider unavailable")
    monkeypatch.setattr(llm_svc, "fill_phrase_via_llm", boom)
    app = create_app()
    client = app.test_client()
    r = client.post("/api/phrases/fill", json={"language": "en"})
    assert r.status_code == 502


# --- 405 on wrong methods ----------------------------------------------


def test_structures_wrong_method_405():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.patch("/api/structures")
    assert r.status_code == 405


def test_phrases_wrong_method_405():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.patch("/api/phrases")
    assert r.status_code == 405
