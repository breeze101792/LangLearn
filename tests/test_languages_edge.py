"""Edge-case tests for the languages blueprint.

test_auth_languages.py covers the main flows. This file pins the two
remaining error branches:

- ``initialize_language`` returns 404 when there's no built-in seed and the
  LLM path raises FileNotFoundError
- ``apply_explanations`` re-raises non-LLMError exceptions as 500
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


def test_initialize_language_no_built_in_seed_404(fresh, monkeypatch):
    """When seed_via_llm raises FileNotFoundError, the route returns 404
    with code no_built_in_seed."""
    from backend.services import seed as seed_svc

    def boom(*a, **kw):
        raise FileNotFoundError("no built-in seed for es")
    monkeypatch.setattr(seed_svc, "seed_via_llm", boom)
    seed_svc.ensure_language_row("es", "Spanish", is_built_in=0)

    c = _client()
    r = c.post("/api/languages/es/initialize", json={})
    assert r.status_code == 404
    body = r.get_json()
    assert body["ok"] is False
    assert body["code"] == "no_built_in_seed"


def test_apply_explanations_non_llm_error_500(fresh, monkeypatch):
    """A non-LLMError exception in apply_explanations propagates as 500."""
    from backend.services import seed as seed_svc

    def boom(*a, **kw):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(seed_svc, "apply_explanations", boom)

    c = _client()
    r = c.post("/api/languages/en/apply-explanations", json={})
    assert r.status_code == 500
    assert r.get_json()["ok"] is False
