"""Tests for the LLM service.

We monkeypatch the HTTP call so the tests don't need real network access.
"""

from __future__ import annotations

import json as _json
from unittest import mock

import pytest


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    from backend import db
    db.init_schema()
    return tmp_path


def _mock_openai_response(content: str):
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return resp


def test_lookup_word_happy_path(fresh, monkeypatch):
    from backend.services import llm

    payload = {
        "senses": [
            {
                "pos": "noun",
                "definitions": [{"glossary": "A house.", "example": "Mi casa."}],
                "explanations": {"primary": "House.", "secondary": "房子。"},
            }
        ]
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.lookup_word_via_llm(
        lang="es", word="casa",
        explanation_primary="en", explanation_secondary="zh",
    )
    assert out["senses"][0]["definitions"][0]["glossary"] == "A house."


def test_lookup_word_prompt_specifies_target_lang_for_glossary_and_example(
    fresh, monkeypatch,
):
    """The user prompt must tell the model that `glossary` and `example`
    are written in the TARGET language (the word's language), not in the
    explanation languages — otherwise we get English glosses/example
    sentences for Chinese words, which is what prompted this fix."""
    from backend.services import llm

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["payload"] = json or {}
        return _mock_openai_response(_json.dumps({
            "senses": [{
                "pos": "noun",
                "definitions": [{"glossary": "ok"}],
            }],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.lookup_word_via_llm(
        lang="zh", word="從",
        explanation_primary="en", explanation_secondary="fr",
    )
    user_msg = next(
        (m for m in captured["payload"]["messages"] if m["role"] == "user"),
        None,
    )
    assert user_msg is not None
    text = user_msg["content"]
    # Glossary definition must be tied to the target language.
    assert "Traditional Chinese" in text
    # Example sentence must also be tied to the target language.
    # Appears at least twice (once for glossary context, once for example).
    assert text.count("Traditional Chinese") >= 2
    # The prompt must explicitly contrast glossary (target lang) with
    # explanations (other languages), so the model doesn't conflate them.
    assert "explanation" in text.lower()


def test_lookup_word_prompt_for_english_word(fresh, monkeypatch):
    """Same contract for English: glossary/example in English, explanations
    in the user's other languages."""
    from backend.services import llm

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["payload"] = json or {}
        return _mock_openai_response(_json.dumps({
            "senses": [{
                "pos": "noun",
                "definitions": [{"glossary": "ok"}],
            }],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.lookup_word_via_llm(
        lang="en", word="dog",
        explanation_primary="zh", explanation_secondary=None,
    )
    user_msg = next(
        (m for m in captured["payload"]["messages"] if m["role"] == "user"),
        None,
    )
    assert user_msg is not None
    assert "English" in user_msg["content"]


def test_lookup_word_prompt_steers_word_to_target_language(fresh, monkeypatch):
    """The prompt must tell the model the word belongs to the target
    language, so an English dictionary doesn't translate a Spanish word
    (or vice versa)."""
    from backend.services import llm

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["payload"] = json or {}
        return _mock_openai_response(_json.dumps({
            "senses": [{"pos": "noun", "definitions": [{"glossary": "ok"}]}],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.lookup_word_via_llm(
        lang="en", word="casa",
        explanation_primary="zh", explanation_secondary=None,
    )
    user_msg = next(
        (m for m in captured["payload"]["messages"] if m["role"] == "user"),
        None,
    )
    assert user_msg is not None
    text = user_msg["content"]
    # The word is explicitly declared to be an English word.
    assert "is a word in English" in text
    # The model is told not to translate it into another language.
    assert "Do NOT translate the word" in text


def test_lookup_word_retries_on_invalid_json(fresh, monkeypatch):
    from backend.services import llm

    good = {"senses": [{"pos": "noun", "definitions": [{"glossary": "ok"}]}]}
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _mock_openai_response("not json {")
        return _mock_openai_response(_json.dumps(good))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.lookup_word_via_llm(
        lang="es", word="casa",
        explanation_primary="en", explanation_secondary=None,
    )
    assert calls["n"] == 2
    assert out["senses"][0]["definitions"][0]["glossary"] == "ok"


def test_lookup_word_retries_on_schema_violation(fresh, monkeypatch):
    from backend.services import llm

    bad = {"wrong": "shape"}
    good = {"senses": [{"pos": "noun", "definitions": [{"glossary": "ok"}]}]}
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _mock_openai_response(_json.dumps(bad))
        return _mock_openai_response(_json.dumps(good))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.lookup_word_via_llm(
        lang="es", word="casa",
        explanation_primary="en", explanation_secondary=None,
    )
    assert calls["n"] == 2


def test_lookup_word_raises_after_two_failures(fresh, monkeypatch):
    from backend.services import llm

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response("not json")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMSchemaError):
        llm.lookup_word_via_llm(
            lang="es", word="casa",
            explanation_primary="en", explanation_secondary=None,
        )


def test_missing_api_key_raises(fresh, monkeypatch):
    from backend.services import llm

    monkeypatch.setattr(llm.config, "OPENAI_API_KEY", "")
    with pytest.raises(llm.LLMError):
        llm.lookup_word_via_llm(
            lang="es", word="casa",
            explanation_primary="en", explanation_secondary=None,
        )


def test_network_error_maps_to_llm_error(fresh, monkeypatch):
    from backend.services import llm
    import requests

    def fake_post(url, json=None, headers=None, timeout=None):
        raise requests.RequestException("boom")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMError):
        llm.lookup_word_via_llm(
            lang="es", word="casa",
            explanation_primary="en", explanation_secondary=None,
        )


# ---- primary/secondary fallback ---------------------------------------
#
# The LLM service accepts a SECONDARY_OPENAI_* OpenAI-compatible endpoint
# as an automatic fallback when the primary fails. These tests pin the
# fallback contract: primary is tried first with its full retry budget,
# secondary is tried only after that fails, and a working secondary lets
# the call succeed even when the primary is broken in various ways.


def _enable_secondary(monkeypatch, *, base="https://secondary.invalid/v1",
                      model="gpt-secondary"):
    """Enable the secondary provider for a single test."""
    from backend import config
    monkeypatch.setenv("SECONDARY_OPENAI_API_KEY", "secondary-key")
    monkeypatch.setenv("SECONDARY_OPENAI_BASE_URL", base)
    monkeypatch.setenv("SECONDARY_OPENAI_MODEL", model)
    monkeypatch.setattr(config, "SECONDARY_OPENAI_API_KEY", "secondary-key")
    monkeypatch.setattr(config, "SECONDARY_OPENAI_BASE_URL", base)
    monkeypatch.setattr(config, "SECONDARY_OPENAI_MODEL", model)


def _primary_url_marker(base_url: str) -> str:
    """Substring that uniquely identifies a call as primary (vs secondary)
    based on the URL the request hit. The primary uses the developer /
    test-set OPENAI_BASE_URL, the secondary uses a different one."""
    return base_url


def test_falls_back_to_secondary_when_primary_network_fails(fresh, monkeypatch):
    from backend.services import llm
    import requests

    _enable_secondary(monkeypatch)

    good = {"senses": [{"pos": "noun", "definitions": [{"glossary": "ok"}]}]}
    urls_hit: list[str] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        urls_hit.append(url)
        if "example.invalid" in url:
            raise requests.RequestException("primary down")
        return _mock_openai_response(_json.dumps(good))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.lookup_word_via_llm(
        lang="es", word="casa",
        explanation_primary="en", explanation_secondary=None,
    )
    assert out["senses"][0]["definitions"][0]["glossary"] == "ok"
    # Primary tried exactly once, then secondary succeeded on its first attempt.
    assert len(urls_hit) == 2
    assert "example.invalid" in urls_hit[0]
    assert "secondary.invalid" in urls_hit[1]


def test_falls_back_to_secondary_when_primary_times_out(fresh, monkeypatch):
    from backend.services import llm
    import requests

    _enable_secondary(monkeypatch)
    good = {"senses": [{"pos": "noun", "definitions": [{"glossary": "ok"}]}]}
    urls_hit: list[str] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        urls_hit.append(url)
        if "example.invalid" in url:
            raise requests.Timeout("primary slow")
        return _mock_openai_response(_json.dumps(good))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.lookup_word_via_llm(
        lang="es", word="casa",
        explanation_primary="en", explanation_secondary=None,
    )
    assert out["senses"][0]["definitions"][0]["glossary"] == "ok"
    assert len(urls_hit) == 2
    assert "example.invalid" in urls_hit[0]
    assert "secondary.invalid" in urls_hit[1]


def test_falls_back_to_secondary_when_primary_returns_http_error(fresh, monkeypatch):
    from backend.services import llm

    _enable_secondary(monkeypatch)
    good = {"senses": [{"pos": "noun", "definitions": [{"glossary": "ok"}]}]}
    urls_hit: list[str] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        urls_hit.append(url)
        if "example.invalid" in url:
            err = mock.Mock()
            err.status_code = 503
            err.text = "service unavailable"
            return err
        return _mock_openai_response(_json.dumps(good))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.lookup_word_via_llm(
        lang="es", word="casa",
        explanation_primary="en", explanation_secondary=None,
    )
    assert out["senses"][0]["definitions"][0]["glossary"] == "ok"
    assert len(urls_hit) == 2


def test_falls_back_to_secondary_when_primary_schema_retries_exhausted(
    fresh, monkeypatch,
):
    from backend.services import llm

    _enable_secondary(monkeypatch)
    good = {"senses": [{"pos": "noun", "definitions": [{"glossary": "ok"}]}]}
    urls_hit: list[str] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        urls_hit.append(url)
        if "example.invalid" in url:
            # Always invalid schema — primary exhausts its retry budget.
            return _mock_openai_response(_json.dumps({"wrong": "shape"}))
        return _mock_openai_response(_json.dumps(good))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.lookup_word_via_llm(
        lang="es", word="casa",
        explanation_primary="en", explanation_secondary=None,
    )
    assert out["senses"][0]["definitions"][0]["glossary"] == "ok"
    # Primary used its full retry budget (default 1 retry = 2 attempts)
    # before the secondary got a single, successful attempt.
    primary_calls = sum(1 for u in urls_hit if "example.invalid" in u)
    secondary_calls = sum(1 for u in urls_hit if "secondary.invalid" in u)
    assert primary_calls == 2
    assert secondary_calls == 1


def test_secondary_also_uses_full_retry_budget(fresh, monkeypatch):
    from backend.services import llm

    _enable_secondary(monkeypatch)
    good = {"senses": [{"pos": "noun", "definitions": [{"glossary": "ok"}]}]}
    urls_hit: list[str] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        urls_hit.append(url)
        if "example.invalid" in url:
            return _mock_openai_response(_json.dumps({"wrong": "shape"}))
        if "secondary.invalid" in url:
            # Bad once, then good.
            if sum(1 for u in urls_hit if u == url) == 1:
                return _mock_openai_response(_json.dumps({"wrong": "shape"}))
            return _mock_openai_response(_json.dumps(good))
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.lookup_word_via_llm(
        lang="es", word="casa",
        explanation_primary="en", explanation_secondary=None,
    )
    assert out["senses"][0]["definitions"][0]["glossary"] == "ok"
    # Primary: 2 attempts (its full budget). Secondary: 2 attempts (its full
    # budget — first bad, second good). The primary's `last_error` does NOT
    # carry into the secondary's prompt.
    primary_calls = sum(1 for u in urls_hit if "example.invalid" in u)
    secondary_calls = sum(1 for u in urls_hit if "secondary.invalid" in u)
    assert primary_calls == 2
    assert secondary_calls == 2


def test_no_fallback_when_secondary_not_configured(fresh, monkeypatch):
    """Without any SECONDARY_* env, behavior matches the historical
    single-provider path: failure raises, no extra attempt."""
    from backend.services import llm
    import requests

    urls_hit: list[str] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        urls_hit.append(url)
        raise requests.RequestException("boom")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMError):
        llm.lookup_word_via_llm(
            lang="es", word="casa",
            explanation_primary="en", explanation_secondary=None,
        )
    # Single primary attempt, no secondary attempted.
    assert len(urls_hit) == 1


def test_secondary_failure_propagates(fresh, monkeypatch):
    """When both providers fail, the secondary's exception is raised."""
    from backend.services import llm
    import requests

    _enable_secondary(monkeypatch)

    def fake_post(url, json=None, headers=None, timeout=None):
        raise requests.RequestException(f"boom from {url}")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMError) as excinfo:
        llm.lookup_word_via_llm(
            lang="es", word="casa",
            explanation_primary="en", explanation_secondary=None,
        )
    assert "secondary.invalid" in str(excinfo.value)


def test_secondary_can_be_disabled_by_clearing_secondary_base_url(
    fresh, monkeypatch,
):
    """Setting SECONDARY_OPENAI_BASE_URL back to empty disables the
    fallback, even if the other secondary vars are set."""
    from backend.services import llm
    import requests
    from backend import config

    monkeypatch.setenv("SECONDARY_OPENAI_API_KEY", "k")
    monkeypatch.setattr(config, "SECONDARY_OPENAI_API_KEY", "k")
    # base URL stays empty (cleared by conftest).

    urls_hit: list[str] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        urls_hit.append(url)
        raise requests.RequestException("boom")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    with pytest.raises(llm.LLMError):
        llm.lookup_word_via_llm(
            lang="es", word="casa",
            explanation_primary="en", explanation_secondary=None,
        )
    assert len(urls_hit) == 1


def test_secondary_uses_secondary_model_name(fresh, monkeypatch):
    """The secondary client must send the SECONDARY_OPENAI_MODEL value
    in its request payload, not the primary's model — otherwise the
    wrong model would run on the secondary endpoint."""
    from backend.services import llm

    _enable_secondary(monkeypatch, model="secondary-special-model")
    good = {"senses": [{"pos": "noun", "definitions": [{"glossary": "ok"}]}]}
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        if "secondary.invalid" in url:
            captured["secondary_payload"] = json or {}
            return _mock_openai_response(_json.dumps(good))
        # Primary always succeeds, so we never fall back unless we force it.
        return _mock_openai_response(_json.dumps(good))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    # Force fallback: primary raises.
    import requests
    orig_fake = fake_post

    def maybe_fail(url, json=None, headers=None, timeout=None):
        if "example.invalid" in url:
            raise requests.RequestException("force fallback")
        return orig_fake(url, json=json, headers=headers, timeout=timeout)

    monkeypatch.setattr("backend.services.llm.requests.post", maybe_fail)
    llm.lookup_word_via_llm(
        lang="es", word="casa",
        explanation_primary="en", explanation_secondary=None,
    )
    assert captured["secondary_payload"]["model"] == "secondary-special-model"


def test_fallback_works_for_describe_path(fresh, monkeypatch):
    """The describe path goes through `_describe_complete_json`, not
    `complete_json`. Make sure the same fallback chain applies there."""
    from backend.services import llm
    import requests

    _enable_secondary(monkeypatch)
    good = {
        "description": "A picture.",
        "words": [{"word": "foo", "pos": "noun", "glossary": "bar"}],
    }
    urls_hit: list[str] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        urls_hit.append(url)
        if "example.invalid" in url:
            raise requests.RequestException("primary down")
        return _mock_openai_response(_json.dumps(good))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    # 1x1 PNG so the describe path gets past its mime check.
    out = llm.describe_image_via_llm(
        target_lang="es",
        image_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 16,
        mime_type="image/png",
        primary="en",
        secondary=None,
    )
    assert out["description"] == "A picture."
    assert len(urls_hit) == 2
    assert "example.invalid" in urls_hit[0]
    assert "secondary.invalid" in urls_hit[1]