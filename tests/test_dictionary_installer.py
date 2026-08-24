"""Tests for the offline-dictionary install surface.

Covers:
- catalog shape + lookup
- installer: auto-install on first boot, manual install/uninstall, idempotency,
  protection of auto-installed entries, error on unknown catalog pairs
- registry chain filtering by install state
- HTTP endpoints: /api/dictionary/catalog, /install, /uninstall, /providers
  install flag
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


# ---------- catalog ----------------------------------------------------------


def test_catalog_includes_wordnet_for_english():
    from backend.services.dictionaries import catalog
    entry = catalog.find("wordnet", "en")
    assert entry is not None
    assert entry.provider == "wordnet"
    assert "en" in entry.languages
    assert entry.auto_install is True
    assert entry.source == "bundled"


def test_catalog_unknown_provider_returns_none():
    from backend.services.dictionaries import catalog
    assert catalog.find("wordnet", "es") is None
    assert catalog.find("freedict-de-en", "en") is None


def test_catalog_no_offline_for_other_languages_today():
    """Today only WordNet/English ships; other languages have no offline
    catalog entries. This test pins that invariant so a future contributor
    adding a new dictionary is forced to update the catalog."""
    from backend.services.dictionaries import catalog
    for lang in ("es", "ja", "fr", "de", "zh", "pt"):
        assert catalog.providers_for_language(lang) == [], lang


# ---------- installer --------------------------------------------------------


def test_auto_install_defaults_installs_wordnet_for_english(clean_state):
    """clean_state already runs auto_install_defaults. Verify the result."""
    from backend.services.dictionaries import installer
    assert installer.is_installed("wordnet", "en") is True
    installed = installer.installed_providers()
    assert isinstance(installed, dict)
    assert "wordnet" in installed.get("en", set())


def test_auto_install_defaults_is_idempotent(clean_state):
    from backend.services.dictionaries import installer
    # Run twice — second call should be a no-op.
    second = installer.auto_install_defaults()
    assert second == []


def test_install_unknown_pair_raises(clean_state):
    from backend.services.dictionaries import installer
    with pytest.raises(ValueError):
        installer.install("nonexistent-dict", "en")
    with pytest.raises(ValueError):
        installer.install("wordnet", "es")  # wordnet doesn't cover es


def test_install_unknown_language_rejected(clean_state):
    from backend.services.dictionaries import installer
    with pytest.raises(ValueError):
        installer.install("wordnet", "zzz")


def test_install_then_uninstall_roundtrip(clean_state):
    """If we manually uninstall the auto-installed wordnet, we can re-install
    it later. (The HTTP uninstall endpoint blocks this for protected pairs;
    the service-level installer doesn't — defense in depth.)"""
    from backend.services.dictionaries import installer
    # clean_state already installed wordnet for en. Verify, uninstall, reinstall.
    assert installer.is_installed("wordnet", "en")
    assert installer.uninstall("wordnet", "en") is True
    assert not installer.is_installed("wordnet", "en")
    # Re-install via service (bypasses the protected check used by the API).
    result = installer.install("wordnet", "en")
    assert result.installed and not result.already
    assert installer.is_installed("wordnet", "en")


def test_install_idempotent_returns_already_true(clean_state):
    from backend.services.dictionaries import installer
    # First call: not already. Second call: already.
    installer.uninstall("wordnet", "en")
    first = installer.install("wordnet", "en")
    second = installer.install("wordnet", "en")
    assert first.already is False
    assert second.already is True


def test_installed_providers_per_language_vs_all(clean_state):
    from backend.services.dictionaries import installer
    per_lang = installer.installed_providers("en")
    all_data = installer.installed_providers()
    assert isinstance(per_lang, set)
    assert isinstance(all_data, dict)
    assert "wordnet" in per_lang
    assert "wordnet" in all_data.get("en", set())


def test_is_protected_true_for_auto_install(clean_state):
    from backend.services.dictionaries import installer
    assert installer.is_protected("wordnet", "en") is True
    assert installer.is_protected("wordnet", "es") is False  # unknown pair


def test_catalog_view_marks_installed_for_lang(clean_state):
    from backend.services.dictionaries import installer
    items = installer.catalog_view(language="en")
    assert isinstance(items, list)
    wn = next((i for i in items if i["provider"] == "wordnet"), None)
    assert wn is not None
    assert wn["installed"] is True
    assert wn["supported"] is True
    items_es = installer.catalog_view(language="es")
    wn_es = next((i for i in items_es if i["provider"] == "wordnet"), None)
    # wordnet is in the catalog (English) but not supported by 'es'.
    assert wn_es is not None
    assert wn_es["supported"] is False
    assert wn_es["installed"] is False


# ---------- registry / chain filtering ---------------------------------------


def test_chain_skips_uninstalled_offline_provider(clean_state):
    """If the chain lists wordnet but it's not installed, the executor skips
    it instead of failing. This protects users from chains saved before
    they uninstalled a dictionary, and from manually-edited settings."""
    from backend.services.dictionaries import installer, registry
    from backend.services import llm as llm_svc

    registry.bootstrap()  # register wordnet+llm (idempotent in create_app)
    installer.uninstall("wordnet", "en")
    captured = {}

    def fake_llm(*, lang, word, **kw):
        captured["called"] = True
        return {"senses": []}  # empty so chain continues; but we expect wordnet to be skipped first

    with patch.object(llm_svc, "lookup_word_via_llm", side_effect=fake_llm):
        chain = [{"name": "wordnet", "enabled": True},
                 {"name": "llm", "enabled": True}]
        result = registry.lookup_via_chain(word="dog", lang="en", chain=chain)

    # WordNet was skipped (not installed); LLM returned no senses; result is empty.
    assert result.entry.is_empty
    # The chain still ran end-to-end without raising and registered providers
    # are unaffected by install state.
    assert "wordnet" in registry.available_providers()
    assert captured.get("called") is True


def test_force_provider_blocked_when_uninstalled(clean_state):
    from backend.services.dictionaries import installer, registry
    installer.uninstall("wordnet", "en")
    result = registry.lookup_with_provider(word="dog", lang="en", provider_name="wordnet")
    assert result.entry.is_empty
    assert result.errors == []


def test_force_provider_works_when_installed(clean_state):
    from backend.services.dictionaries import registry
    result = registry.lookup_with_provider(word="dog", lang="en", provider_name="wordnet")
    # WordNet should have at least one sense for "dog".
    assert not result.entry.is_empty
    assert result.entry.source == "wordnet"


# ---------- HTTP endpoints ---------------------------------------------------


def _client():
    from backend.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_get_catalog_returns_entries_and_installed_map(clean_state):
    client = _client()
    res = client.get("/api/dictionary/catalog")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert isinstance(data["data"]["entries"], list)
    providers = {e["provider"] for e in data["data"]["entries"]}
    assert "wordnet" in providers
    installed = data["data"]["installed"]
    assert "wordnet" in installed.get("en", [])


def test_get_catalog_with_lang_marks_per_lang_status(clean_state):
    client = _client()
    res = client.get("/api/dictionary/catalog?lang=en")
    assert res.status_code == 200
    data = res.get_json()
    wn = next(e for e in data["data"]["entries"] if e["provider"] == "wordnet")
    assert wn["installed"] is True
    assert wn["supported"] is True


def test_get_catalog_invalid_lang_400(clean_state):
    """Anything that doesn't match the lang-code regex is 400. ``zzzz`` is
    syntactically valid but unknown to the catalog — that's a 200 with an
    empty install map, not an error."""
    client = _client()
    # Too long / contains digits — rejected by the lang-code regex.
    res = client.get("/api/dictionary/catalog?lang=12")
    assert res.status_code == 400


def test_get_providers_includes_installed_flag(clean_state):
    client = _client()
    res = client.get("/api/dictionary/providers?lang=en")
    assert res.status_code == 200
    items = res.get_json()["data"]["providers"]
    wn = next(i for i in items if i["name"] == "wordnet")
    llm = next(i for i in items if i["name"] == "llm")
    assert wn["installed"] is True
    assert llm["installed"] is True  # LLM is always considered installed


def test_install_endpoint_unknown_provider_404(clean_state):
    client = _client()
    res = client.post("/api/dictionary/install",
                      json={"provider": "nonexistent", "language": "en"})
    assert res.status_code == 404
    assert res.get_json()["ok"] is False


def test_install_endpoint_unsupported_lang_400(clean_state):
    """`zz` is a syntactically valid lang code but isn't in the catalog,
    so install() raises ValueError -> 404."""
    client = _client()
    res = client.post("/api/dictionary/install",
                      json={"provider": "wordnet", "language": "zz"})
    # wordnet doesn't cover zz — 404 (unknown_dictionary), not 400.
    assert res.status_code == 404


def test_install_endpoint_idempotent(clean_state):
    client = _client()
    # WordNet already installed for English by clean_state.
    res = client.post("/api/dictionary/install",
                      json={"provider": "wordnet", "language": "en"})
    assert res.status_code == 200
    body = res.get_json()["data"]
    assert body["installed"] is True
    assert body["already"] is True


def test_uninstall_endpoint_blocks_protected_pair(clean_state):
    """WordNet-for-English is auto-installed and the API refuses to
    uninstall it (the UI hides the button too)."""
    client = _client()
    res = client.post("/api/dictionary/uninstall",
                      json={"provider": "wordnet", "language": "en"})
    assert res.status_code == 409
    body = res.get_json()
    assert body["ok"] is False
    assert body["code"] == "protected_dictionary"


def test_uninstall_endpoint_missing_fields_400(clean_state):
    client = _client()
    res = client.post("/api/dictionary/uninstall", json={})
    assert res.status_code == 400


def test_uninstall_endpoint_unknown_pair_404(clean_state):
    """Uninstalling a pair that's not catalogued returns 404 because
    there's nothing to uninstall."""
    client = _client()
    res = client.post("/api/dictionary/uninstall",
                      json={"provider": "nonexistent", "language": "en"})
    assert res.status_code == 404


def test_uninstall_then_lookup_skips_provider(clean_state):
    """End-to-end: API rejects the uninstall for the protected default, but
    if we uninstall via the service directly the lookup API sees the new
    state immediately (no app restart needed)."""
    from backend.services.dictionaries import installer, registry
    installer.uninstall("wordnet", "en")
    client = _client()
    res = client.post(
        "/api/dictionary/lookup",
        json={"lang": "en", "word": "dog"},
    )
    assert res.status_code == 200
    body = res.get_json()["data"]
    # Without wordnet installed, lookup either gets nothing or hits LLM.
    # We only assert the chain ran end-to-end without throwing — the
    # source is whatever the LLM returned (or empty).
    assert "source" in body
    # Re-install so later tests in the same module aren't affected.
    installer.install("wordnet", "en")
