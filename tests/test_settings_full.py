"""More settings-service tests beyond the happy paths in test_settings.py."""

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


# --- _coerce branches -------------------------------------------------


def test_coerce_truthy_variants(fresh):
    from backend.services import settings as s
    assert s._truthy(True) is True
    assert s._truthy(1) is True
    assert s._truthy("yes") is True
    assert s._truthy("ON") is True
    assert s._truthy("1") is True
    assert s._truthy(False) is False
    assert s._truthy(0) is False
    assert s._truthy("no") is False
    assert s._truthy(None) is False
    assert s._truthy([]) is False


def test_coerce_explanation_secondary_empty_string_to_none(fresh):
    """Empty string for explanation_secondary must be coerced to None."""
    from backend.services import settings as s
    assert s._coerce("explanation_secondary", "") is None


def test_coerce_explanation_primary_rejects_invalid_lang(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s._coerce("explanation_primary", "ENG123")


def test_coerce_active_language_rejects_invalid(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s._coerce("active_language", "BAD")


def test_coerce_page_size_non_int_raises(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError, match="int"):
        s._coerce("page_size", "abc")


def test_coerce_page_size_float_rejected(fresh):
    from backend.services import settings as s
    # int("3.5") raises, so this becomes "page_size must be int".
    with pytest.raises(ValueError):
        s._coerce("page_size", "3.5")


def test_coerce_theme_unknown_value_raises(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError, match="theme"):
        s._coerce("theme", "neon")


def test_coerce_dict_chain_non_dict_raises(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s._coerce("dict_chain_json", "not an object")


def test_coerce_unknown_key_raises(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError, match="unhandled"):
        s._coerce("brand_new_key", "x")


# --- _clean_dict_chain edge cases ----------------------------------


def test_clean_dict_chain_rejects_non_string_name(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError, match="string"):
        s._clean_dict_chain({"en": [{"name": 123, "enabled": True}]})


def test_clean_dict_chain_coerces_enabled_to_bool(fresh):
    from backend.services import settings as s
    out = s._clean_dict_chain({"en": [{"name": "llm", "enabled": "yes"}]})
    assert out["en"][0]["enabled"] is True


def test_clean_dict_chain_default_enabled_true(fresh):
    """Missing `enabled` defaults to True."""
    from backend.services import settings as s
    out = s._clean_dict_chain({"en": [{"name": "llm"}]})
    assert out["en"][0]["enabled"] is True


def test_clean_dict_chain_rejects_non_dict_entries(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s._clean_dict_chain({"en": ["not-a-dict"]})


def test_clean_dict_chain_rejects_entries_missing_name(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError, match="name"):
        s._clean_dict_chain({"en": [{"enabled": True}]})


def test_clean_dict_chain_rejects_non_list_entries(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError, match="list"):
        s._clean_dict_chain({"en": "string"})


# --- default chain --------------------------------------------------


def test_default_dict_chain_includes_wordnet_only_for_english(fresh):
    from backend.services import settings as s
    chain = s.default_dict_chain()
    # 'en' has wordnet+llm; other langs have llm only.
    en_names = [p["name"] for p in chain["en"]]
    assert "wordnet" in en_names
    assert "llm" in en_names
    for code in ("es", "ja", "fr", "de", "zh", "pt"):
        names = [p["name"] for p in chain[code]]
        assert "llm" in names
        assert "wordnet" not in names


# --- get_settings / update_settings / set_dict_chain ----------------


def test_get_settings_creates_row_when_missing(fresh):
    """First call must create the default settings row."""
    from backend.services import settings as s
    from backend.db import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM settings WHERE user_id=1").fetchone()
    assert row is None
    s.get_settings(1)
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM settings WHERE user_id=1").fetchone()
    assert row is not None


def test_update_settings_empty_dict_is_noop(fresh):
    """Passing {} returns the current settings without writing."""
    from backend.services import settings as s
    s.get_settings(1)
    out = s.update_settings({}, 1)
    assert "active_language" in out


def test_update_settings_non_dict_raises(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s.update_settings("not a dict", 1)


def test_get_settings_propagates_corrupted_chain_json(fresh):
    """If dict_chain_json is malformed, get_settings surfaces the error
    (defense-in-depth; the migration layer guarantees a valid value)."""
    import json
    from backend.services import settings as s
    from backend.db import get_conn

    s.get_settings(1)
    with get_conn() as conn:
        conn.execute("UPDATE settings SET dict_chain_json='not-json' "
                     "WHERE user_id=1")
    with pytest.raises(json.JSONDecodeError):
        s.get_settings(1)


def test_get_dict_chain_filters_out_invalid_entries(fresh):
    """If a stored chain has non-dict entries, only valid dicts with a
    'name' survive the filter (the dict-level filter)."""
    from backend.services import settings as s
    s.set_dict_chain("es", [{"name": "llm", "enabled": True}])
    from backend.db import get_conn
    with get_conn() as conn:
        # Manually overwrite with a malformed chain to test the filter.
        conn.execute(
            "UPDATE settings SET dict_chain_json=? WHERE user_id=1",
            ('{"es": [{"name": "llm", "enabled": true}, "garbage", {"no_name": true}]}',),
        )
    out = s.get_dict_chain("es", 1)
    # Only the well-formed entry survives.
    assert out == [{"name": "llm", "enabled": True}]


def test_set_dict_chain_rejects_invalid_lang(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError):
        s.set_dict_chain("ENG123", [{"name": "llm"}])


def test_create_default_settings_seeds_languages(fresh):
    """First-time settings creation must also seed every catalog language."""
    from backend.services import settings as s
    from backend.db import get_conn
    s.create_default_settings(1)
    with get_conn() as conn:
        rows = conn.execute("SELECT code FROM languages").fetchall()
    codes = [r["code"] for r in rows]
    for code in ("en", "es", "ja", "pt", "zh", "fr", "de"):
        assert code in codes
