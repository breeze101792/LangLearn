"""Edge-case tests for the settings service.

test_settings.py / test_settings_api.py cover the main flows. This file
pins the remaining branches:

- ``get_dict_chain`` returns [] when the stored chain isn't a dict
- ``_coerce`` tts_provider validation (non-string, unknown provider)
- ``_clean_dict_chain`` rejects a non-dict value
- ``_row_to_dict`` tolerates a missing tts_provider column (pre-007 DB)
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh(clean_state):
    """Re-export of the autouse clean_state fixture."""
    return clean_state


def test_get_dict_chain_returns_empty_when_not_dict(fresh):
    """If the stored dict_chain_json is not a dict, get_dict_chain returns []."""
    from backend.services import settings as s
    from backend.db import get_conn
    s.create_default_settings()
    # Corrupt the stored chain to a JSON value that parses to a non-dict.
    with get_conn() as conn:
        conn.execute(
            "UPDATE settings SET dict_chain_json='[1,2,3]' WHERE user_id=1"
        )
    assert s.get_dict_chain("en") == []


def test_coerce_tts_provider_non_string_raises(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError, match="tts_provider"):
        s._coerce("tts_provider", 42)


def test_coerce_tts_provider_unknown_raises(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError, match="unknown tts provider"):
        s._coerce("tts_provider", "made-up")


def test_clean_dict_chain_rejects_non_dict(fresh):
    from backend.services import settings as s
    with pytest.raises(ValueError, match="dict_chain_json"):
        s._clean_dict_chain("not-a-dict")


def test_row_to_dict_tolerates_missing_tts_provider(fresh):
    """A pre-007 DB row without the tts_provider column falls back to the
    default rather than raising."""
    from backend.services import settings as s
    from backend.db import get_conn
    # Simulate a row without tts_provider by using a dict-like object.
    class FakeRow:
        def __getitem__(self, key):
            if key == "tts_provider":
                raise KeyError("tts_provider")
            return {
                "user_id": 1, "active_language": "en", "auto_add_vocab": 1,
                "page_size": 20, "explanation_primary": "en",
                "explanation_secondary": None, "dict_chain_json": "{}",
                "theme": "auto", "show_readings": 1,
            }[key]
    out = s._row_to_dict(FakeRow())
    assert out["tts_provider"] == s.DEFAULTS["tts_provider"]


def test_row_to_dict_tolerates_missing_review_session_size(fresh):
    """A pre-009 DB row without the review_session_size column falls back
    to the default rather than raising."""
    from backend.services import settings as s
    from backend.db import get_conn
    class FakeRow:
        def __getitem__(self, key):
            if key == "review_session_size":
                raise KeyError("review_session_size")
            return {
                "user_id": 1, "active_language": "en", "auto_add_vocab": 1,
                "page_size": 20, "explanation_primary": "en",
                "explanation_secondary": None, "dict_chain_json": "{}",
                "theme": "auto", "show_readings": 1, "tts_provider": "google",
            }[key]
    out = s._row_to_dict(FakeRow())
    assert out["review_session_size"] == s.DEFAULTS["review_session_size"]
