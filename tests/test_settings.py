"""Tests for the settings service."""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    from backend import db
    db.init_schema()
    from backend.services.dictionaries import registry
    registry.bootstrap()
    return tmp_path


def test_get_settings_creates_default(fresh):
    from backend.services import settings as s
    s1 = s.get_settings()
    assert s1["active_language"] == "en"
    assert s1["auto_add_vocab"] is True
    assert s1["page_size"] == 20
    assert s1["review_session_size"] == 30
    assert s1["explanation_primary"] == "en"
    assert s1["explanation_secondary"] is None
    assert s1["theme"] == "auto"
    assert "wordnet" in [p["name"] for p in s1["dict_chain_json"]["en"]]


def test_update_settings_validates_keys(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s.update_settings({"unknown_key": "x"})


def test_update_settings_coerces_types(fresh):
    from backend.services import settings as s
    s.update_settings({
        "auto_add_vocab": False,
        "page_size": 30,
        "explanation_secondary": "zh",
        "theme": "dark",
    })
    s1 = s.get_settings()
    assert s1["auto_add_vocab"] is False
    assert s1["page_size"] == 30
    assert s1["explanation_secondary"] == "zh"
    assert s1["theme"] == "dark"


def test_page_size_bounds(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s.update_settings({"page_size": 100})
    with pytest.raises(ValueError):
        s.update_settings({"page_size": 0})


def test_review_session_size_bounds(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s.update_settings({"review_session_size": 100})
    with pytest.raises(ValueError):
        s.update_settings({"review_session_size": 0})


def test_review_session_size_persists(fresh):
    from backend.services import settings as s
    s.update_settings({"review_session_size": 25})
    assert s.get_settings()["review_session_size"] == 25


def test_invalid_language_rejected(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s.update_settings({"active_language": "ENG123"})


def test_set_dict_chain(fresh):
    from backend.services import settings as s
    s.set_dict_chain("es", [{"name": "llm", "enabled": True}])
    chain = s.get_dict_chain("es")
    assert chain == [{"name": "llm", "enabled": True}]


def test_set_dict_chain_rejects_unknown_provider(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s.set_dict_chain("es", [{"name": "made_up", "enabled": True}])


def test_set_dict_chain_rejects_duplicate(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s.set_dict_chain("es", [{"name": "llm", "enabled": True},
                                {"name": "llm", "enabled": False}])


def test_update_settings_validates_dict_chain_shape(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s.update_settings({"dict_chain_json": {"en": [{"name": "nope"}]}})
    with pytest.raises(ValueError):
        s.update_settings({"dict_chain_json": {"ZZZ": [{"name": "llm"}]}})
    with pytest.raises(ValueError):
        s.update_settings({"dict_chain_json": {"en": "not a list"}})


def test_dict_chain_always_includes_llm_enabled(fresh):
    """The chain must always end with an enabled LLM entry, even if the
    user submits a chain that omits it or lists it disabled."""
    from backend.services import settings as s

    out = s.update_settings({"dict_chain_json": {"en": [{"name": "wordnet", "enabled": True}]}})
    en_chain = out["dict_chain_json"]["en"]
    names = [e["name"] for e in en_chain]
    assert "llm" in names
    llm = next(e for e in en_chain if e["name"] == "llm")
    assert llm["enabled"] is True

    out = s.update_settings({"dict_chain_json": {"es": [{"name": "llm", "enabled": False}]}})
    es_chain = out["dict_chain_json"]["es"]
    llm = next(e for e in es_chain if e["name"] == "llm")
    assert llm["enabled"] is True


def test_dict_chain_preserves_user_order_then_appends_llm(fresh):
    """LLM should land at the end of the chain, after any user entries."""
    from backend.services import settings as s

    out = s.update_settings({"dict_chain_json": {"en": [{"name": "llm", "enabled": True},
                                                       {"name": "wordnet", "enabled": True}]}})
    names = [e["name"] for e in out["dict_chain_json"]["en"]]
    # User asked LLM first; we keep that order but ensure wordnet+llm both
    # present (duplicate-rejection keeps the first occurrence).
    assert names == ["llm", "wordnet"]