"""HTTP tests for the settings blueprint."""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    from backend import db
    db.init_schema()
    return tmp_path


def test_get_settings_creates_defaults(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    data = body["data"]
    assert data["active_language"] == "en"
    assert data["auto_add_vocab"] is True
    assert data["page_size"] == 20


def test_update_settings_persists(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={
        "active_language": "es",
        "page_size": 30,
        "theme": "dark",
    })
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["active_language"] == "es"
    assert data["page_size"] == 30
    assert data["theme"] == "dark"
    # And it's persisted: a second GET sees the new values.
    r2 = client.get("/api/settings")
    assert r2.get_json()["data"]["page_size"] == 30


def test_update_settings_rejects_unknown_key(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"unknown_key": "x"})
    assert r.status_code == 400


def test_update_settings_empty_body_is_noop(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={})
    assert r.status_code == 200


def test_update_settings_non_dict_body_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json=["not", "an", "object"])
    assert r.status_code == 400


def test_update_settings_invalid_page_size_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"page_size": 999})
    assert r.status_code == 400


def test_update_settings_invalid_theme_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"theme": "neon"})
    assert r.status_code == 400


def test_update_settings_invalid_active_language_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"active_language": "ENG123"})
    assert r.status_code == 400


def test_update_settings_truthy_string_for_bool(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"auto_add_vocab": "true",
                                            "show_readings": "yes"})
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["auto_add_vocab"] is True
    assert data["show_readings"] is True


def test_update_settings_falsy_value_disables_bool(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"auto_add_vocab": False})
    assert r.status_code == 200
    assert r.get_json()["data"]["auto_add_vocab"] is False


def test_update_settings_null_explanation_secondary_accepted(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"explanation_secondary": None})
    assert r.status_code == 200
    assert r.get_json()["data"]["explanation_secondary"] is None


def test_update_settings_empty_string_explanation_treated_as_null(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"explanation_secondary": ""})
    assert r.status_code == 200
    assert r.get_json()["data"]["explanation_secondary"] is None


def test_update_settings_invalid_explanation_language_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"explanation_primary": "ENG123"})
    assert r.status_code == 400


def test_update_settings_invalid_dict_chain_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"dict_chain_json": {"en": "string"}})
    assert r.status_code == 400


def test_update_settings_dict_chain_unknown_provider_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={
        "dict_chain_json": {"en": [{"name": "made_up", "enabled": True}]}
    })
    assert r.status_code == 400


def test_update_settings_dict_chain_unknown_lang_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={
        "dict_chain_json": {"ZZ": [{"name": "llm"}]}
    })
    assert r.status_code == 400


def test_update_settings_page_size_non_int_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"page_size": "abc"})
    assert r.status_code == 400


def test_update_settings_page_size_below_lower_bound_400(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.put("/api/settings", json={"page_size": 4})
    assert r.status_code == 400


def test_get_dict_chain_endpoint(fresh):
    """There is no separate GET /dict-chain endpoint, but update_settings
    round-trips the chain and get_settings returns it."""
    from backend.app import create_app
    from backend.services import settings as s
    s.set_dict_chain("es", [{"name": "llm", "enabled": True}])
    app = create_app()
    client = app.test_client()
    r = client.get("/api/settings")
    chain = r.get_json()["data"]["dict_chain_json"]["es"]
    assert chain == [{"name": "llm", "enabled": True}]


def test_settings_response_includes_all_keys(fresh):
    from backend.app import create_app
    app = create_app()
    client = app.test_client()
    r = client.get("/api/settings")
    data = r.get_json()["data"]
    for key in ("active_language", "auto_add_vocab", "page_size",
                 "explanation_primary", "explanation_secondary",
                 "dict_chain_json", "theme", "show_readings"):
        assert key in data
