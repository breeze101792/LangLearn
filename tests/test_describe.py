"""Tests for the Describe blueprint and ``llm.describe_image_via_llm``."""

from __future__ import annotations

import base64
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


# A 1x1 transparent PNG. Small enough to keep the test fast but valid so
# the blueprint accepts the upload.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _mock_openai_response(content: str):
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


# --- LLM service ---------------------------------------------------------


def test_describe_image_happy_path(fresh, monkeypatch):
    from backend.services import llm

    payload = {
        "description": "A small dog sits on a wooden bench in a sunny park.",
        "description_primary": "A dog on a bench in the park.",
        "description_secondary": "公園裡的長凳上坐著一隻小狗。",
        "words": [
            {
                "word": "bench",
                "pos": "noun",
                "glossary": "A long seat for several people.",
                "example": "Sit on the bench.",
                "explanation_primary": "A long seat.",
                "explanation_secondary": "長椅。",
            },
            {
                "word": "sunny",
                "pos": "adjective",
                "glossary": "Full of sunlight.",
                "example": "It is a sunny day.",
                "explanation_primary": "Bright with sun.",
            },
        ],
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.describe_image_via_llm(
        target_lang="en", image_bytes=_PNG_BYTES, mime_type="image/png",
        primary="zh", secondary="zh",
    )
    assert "dog" in out["description"]
    assert len(out["words"]) == 2
    assert out["words"][0]["word"] == "bench"
    assert out["words"][0]["glossary"] == "A long seat for several people."
    assert out["words"][0]["example"] == "Sit on the bench."
    # primary == secondary == "zh" is a redundant pair, so the rules
    # engine nulls out explanation_secondary. explanation_primary
    # survives because the target language (en) != primary (zh).
    assert out["words"][0]["explanation_primary"] == "A long seat."
    assert out["words"][0]["explanation_secondary"] is None


def test_describe_image_nulls_redundant_primary(fresh, monkeypatch):
    """When the target language equals the user's primary native, the
    LLM may still return description_primary / explanation_primary; the
    shared rules engine must null them out so the UI doesn't show a
    redundant gloss."""
    from backend.services import llm

    payload = {
        "description": "A dog on a bench.",
        "description_primary": "should-be-nulled",
        "words": [
            {
                "word": "bench", "pos": "noun", "glossary": "A seat.",
                "explanation_primary": "should-be-nulled",
            },
        ],
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.describe_image_via_llm(
        target_lang="en", image_bytes=_PNG_BYTES, mime_type="image/png",
        primary="en", secondary=None,
    )
    assert out["description_primary"] is None
    assert out["words"][0]["explanation_primary"] is None


def test_describe_image_rejects_empty_bytes(fresh):
    from backend.services import llm
    with pytest.raises(ValueError):
        llm.describe_image_via_llm(
            target_lang="en", image_bytes=b"", mime_type="image/png",
        )


def test_describe_image_rejects_bad_mime(fresh):
    from backend.services import llm
    with pytest.raises(ValueError):
        llm.describe_image_via_llm(
            target_lang="en", image_bytes=_PNG_BYTES,
            mime_type="application/pdf",
        )


def test_describe_image_normalizes_aliases(fresh, monkeypatch):
    """A model that returns `caption` / `vocabulary` / `lemma` /
    `definition` aliases should be normalized to the canonical shape."""
    from backend.services import llm

    payload = {
        "caption": "A cat on a mat.",
        "vocabulary": [
            {
                "lemma": "cat",
                "part_of_speech": "noun",
                "definition": "A small furry animal.",
                "sentence": "The cat sleeps.",
            },
        ],
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.describe_image_via_llm(
        target_lang="en", image_bytes=_PNG_BYTES, mime_type="image/png",
        primary="zh", secondary=None,
    )
    assert out["description"] == "A cat on a mat."
    assert len(out["words"]) == 1
    assert out["words"][0]["word"] == "cat"
    assert out["words"][0]["pos"] == "noun"
    assert out["words"][0]["glossary"] == "A small furry animal."
    assert out["words"][0]["example"] == "The cat sleeps."


def test_describe_image_drops_word_without_glossary(fresh, monkeypatch):
    """A word item missing `glossary` (after normalization) is dropped
    so we don't pass the strict validator a half-formed entry."""
    from backend.services import llm

    payload = {
        "description": "A picture.",
        "words": [
            {"word": "ok", "pos": "noun", "glossary": "fine"},
            {"word": "bad", "pos": "noun"},
        ],
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.describe_image_via_llm(
        target_lang="en", image_bytes=_PNG_BYTES, mime_type="image/png",
        primary="zh", secondary=None,
    )
    assert len(out["words"]) == 1
    assert out["words"][0]["word"] == "ok"


def test_describe_image_sends_multimodal_message(fresh, monkeypatch):
    """The chat payload must carry an `image_url` content part with a
    base64 data URL so the vision-capable model can see the picture."""
    from backend.services import llm

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        body = json or {}
        captured["messages"] = body["messages"]
        return _mock_openai_response(_json.dumps({
            "description": "x", "words": [],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.describe_image_via_llm(
        target_lang="en", image_bytes=_PNG_BYTES, mime_type="image/png",
        primary="zh", secondary=None,
    )
    user_msg = captured["messages"][1]
    assert user_msg["role"] == "user"
    parts = user_msg["content"]
    assert isinstance(parts, list)
    text_parts = [p for p in parts if p.get("type") == "text"]
    image_parts = [p for p in parts if p.get("type") == "image_url"]
    assert text_parts and image_parts
    url = image_parts[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")


def test_describe_uses_dedicated_timeout_and_no_retries(fresh, monkeypatch):
    """Same policy as Analyze/Translate: dedicated timeout, no retries."""
    from backend.services import llm

    captured = {}

    def spy_chat_messages(self, **kwargs):  # noqa: ANN001
        captured["timeout"] = kwargs.get("timeout")
        return _json.dumps({"description": "x", "words": []})

    monkeypatch.setattr(llm.OpenAICompatClient, "chat_messages", spy_chat_messages)
    out = llm.describe_image_via_llm(
        target_lang="en", image_bytes=_PNG_BYTES, mime_type="image/png",
        primary="zh", secondary=None,
    )
    assert out["description"] == "x"
    assert captured["timeout"] == llm.DESCRIBE_TIMEOUT_SECONDS
    assert captured["timeout"] >= 180


# --- HTTP layer ----------------------------------------------------------


def test_describe_endpoint_rejects_missing_file():
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/describe", data={}, content_type="multipart/form-data")
    assert r.status_code == 400
    body = r.get_json()
    assert body["code"] == "invalid_input"


def test_describe_endpoint_rejects_bad_mime(monkeypatch):
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    app = create_app()
    client = app.test_client()
    r = client.post(
        "/api/describe",
        data={"file": (__import__("io").BytesIO(b"not an image"), "x.pdf", "application/pdf")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    body = r.get_json()
    assert body["code"] == "invalid_input"


def test_describe_endpoint_rejects_oversized(monkeypatch):
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    app = create_app()
    client = app.test_client()
    big = b"\x00" * (31 * 1024 * 1024)
    r = client.post(
        "/api/describe",
        data={"file": (__import__("io").BytesIO(big), "big.png", "image/png")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


def test_describe_endpoint_returns_description_and_words(monkeypatch):
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import settings as settings_svc
    init_schema()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    settings_svc.update_settings({
        "active_language": "en",
        "explanation_primary": "zh",
        "explanation_secondary": None,
    })
    payload = {
        "description": "A small dog sits on a wooden bench in a sunny park.",
        "description_primary": "公園裡的長凳上坐著一隻小狗。",
        "words": [
            {
                "word": "bench",
                "pos": "noun",
                "glossary": "A long seat for several people.",
                "example": "Sit on the bench.",
                "explanation_primary": "長椅。",
            },
        ],
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(_json.dumps(payload))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    app = create_app()
    client = app.test_client()
    r = client.post(
        "/api/describe",
        data={"file": (__import__("io").BytesIO(_PNG_BYTES), "pic.png", "image/png")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["ok"] is True
    data = body["data"]
    assert "dog" in data["description"]
    assert data["description_primary"] == "公園裡的長凳上坐著一隻小狗。"
    assert data["words"][0]["word"] == "bench"
    assert data["words"][0]["explanation_primary"] == "長椅。"


def test_describe_endpoint_defaults_lang_to_active_language(monkeypatch):
    from backend.app import create_app
    from backend.db import init_schema
    from backend.services import settings as settings_svc
    init_schema()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    settings_svc.update_settings({
        "active_language": "fr",
        "explanation_primary": "en",
        "explanation_secondary": None,
    })
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        body = json or {}
        captured["user_msg"] = body["messages"][1]["content"][0]["text"]
        return _mock_openai_response(_json.dumps({
            "description": "x", "words": [],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    app = create_app()
    client = app.test_client()
    r = client.post(
        "/api/describe",
        data={"file": (__import__("io").BytesIO(_PNG_BYTES), "pic.png", "image/png")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert "French" in captured["user_msg"]


def test_describe_endpoint_llm_error_maps_to_502(monkeypatch):
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    app = create_app()
    client = app.test_client()
    import requests

    def fake_post(url, json=None, headers=None, timeout=None):
        raise requests.RequestException("boom")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    r = client.post(
        "/api/describe",
        data={"file": (__import__("io").BytesIO(_PNG_BYTES), "pic.png", "image/png")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 502
    body = r.get_json()
    assert body["ok"] is False
    assert body["code"] == "llm_error"


def test_describe_save_word_round_trip():
    """Hitting the per-item save endpoint on a word extracted by
    Describe should land in the DB and be visible through the vocab
    list endpoint — same as Analyze."""
    from backend.app import create_app
    from backend.db import init_schema
    init_schema()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/vocab/add-from-entry", json={
        "lang": "en",
        "word": "bench",
        "source": "llm",
        "pos": "noun",
        "glossary": "A long seat for several people.",
        "example": "Sit on the bench.",
    })
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["created"] is True
    items = client.get("/api/vocab?lang=en").get_json()["data"]["items"]
    row = next(i for i in items if i["id"] == data["id"])
    assert row["word"] == "bench"
    assert row["pos"] == "noun"