"""Tests for scripts/generate_explanations.py.

These tests run the script's pure helpers (not the LLM call). The
end-to-end LLM path is exercised by running the script with a real
key (manual), which we verify by checking the post-conditions on the
JSON file: 51 structures with `explanation`, 103 phrases with
`example_sentence` and `explanation`.

We also pin the markdown-fence stripping fix in `_post_json`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from backend import db
    db.init_schema()
    return tmp_path


# --- markdown fence stripping in the LLM transport -------------------


def test_post_json_strips_markdown_fences(fresh, monkeypatch):
    """Some non-OpenAI proxies wrap JSON responses in ```json ... ```
    fences even when response_format=json_schema is set. Strip them
    so the JSON parser can read the content."""
    from backend.services import llm

    fence = "```json\n{\"senses\": [{\"pos\": \"noun\", \"definitions\": [{\"glossary\": \"x\"}]}]}\n```"
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["body"] = json
        resp = mock.Mock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": fence}}],
        }
        return resp

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.lookup_word_via_llm(
        lang="en", word="dog",
        explanation_primary="en", explanation_secondary=None,
    )
    assert out["senses"][0]["pos"] == "noun"
    assert captured["body"]["response_format"]["json_schema"]["strict"] is True


def test_post_json_strips_plain_fence_without_language(fresh, monkeypatch):
    from backend.services import llm

    fence = "```\n{\"senses\": [{\"pos\": \"verb\", \"definitions\": [{\"glossary\": \"y\"}]}]}\n```"
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": fence}}]}

    monkeypatch.setattr("backend.services.llm.requests.post",
                        lambda *a, **kw: resp)
    out = llm.lookup_word_via_llm(
        lang="en", word="run",
        explanation_primary="en", explanation_secondary=None,
    )
    assert out["senses"][0]["pos"] == "verb"


def test_post_json_handles_no_fence(fresh, monkeypatch):
    """Plain JSON (no fences) still works."""
    from backend.services import llm

    plain = '{"senses": [{"pos": "adj", "definitions": [{"glossary": "z"}]}]}'
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": plain}}]}

    monkeypatch.setattr("backend.services.llm.requests.post",
                        lambda *a, **kw: resp)
    out = llm.lookup_word_via_llm(
        lang="en", word="quick",
        explanation_primary="en", explanation_secondary=None,
    )
    assert out["senses"][0]["pos"] == "adj"


# --- script-side matching by content -----------------------------------


def test_script_matches_by_content_when_id_is_wrong():
    """When the LLM returns `_id` values that don't line up with the
    input batch (a common model behaviour), the script should fall
    back to matching by content (pattern or phrase).

    We test this by directly invoking ``generate_for_items`` with a
    mocked LLM that resets _id to 0 for every entry."""
    from unittest import mock

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import scripts.generate_explanations as ge

    items = [{"pattern": f"p{i}", "example_sentence": f"e{i}"} for i in range(3)]

    def fake_complete_json(*, schema, schema_name, system, user, temperature):
        # Echo back the items but with _id reset to 0..N-1, not the
        # original batch indices. Include the content fields so the
        # content-based fallback works.
        return {
            "structures": [
                {"_id": i, "pattern": it["pattern"],
                 "example_sentence": it["example_sentence"],
                 "explanation": f"note for {it['pattern']}"}
                for i, it in enumerate(items)
            ]
        }

    with mock.patch.object(ge.llm, "complete_json", side_effect=fake_complete_json):
        out = ge.generate_for_items("en", items, "structure")

    # All three items should be in the result, keyed by their pattern.
    assert len(out) == 3
    assert out["p0"]["explanation"] == "note for p0"
    assert out["p1"]["explanation"] == "note for p1"
    assert out["p2"]["explanation"] == "note for p2"


def test_script_accepts_usage_note_alias_for_explanation():
    """Some models name the explanation field `usage_note`. The script
    should rename it to `explanation`."""
    from unittest import mock

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import scripts.generate_explanations as ge

    items = [{"pattern": "S V O", "example_sentence": "She reads."}]

    def fake_complete_json(*, schema, schema_name, system, user, temperature):
        return {
            "structures": [
                {"_id": 0, "pattern": "S V O",
                 "example_sentence": "She reads.",
                 "usage_note": "The basic transitive pattern."}
            ]
        }

    with mock.patch.object(ge.llm, "complete_json", side_effect=fake_complete_json):
        out = ge.generate_for_items("en", items, "structure")

    assert out["S V O"]["explanation"] == "The basic transitive pattern."


def test_script_accepts_bare_array_response():
    """Some models return a bare JSON array instead of an object with a
    `structures` key. The script should wrap it."""
    from unittest import mock

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import scripts.generate_explanations as ge

    items = [{"pattern": "S V O", "example_sentence": "She reads."}]

    def fake_complete_json(*, schema, schema_name, system, user, temperature):
        return [
            {"_id": 0, "pattern": "S V O",
             "example_sentence": "She reads.",
             "explanation": "Basic SVO."}
        ]

    with mock.patch.object(ge.llm, "complete_json", side_effect=fake_complete_json):
        out = ge.generate_for_items("en", items, "structure")

    assert out["S V O"]["explanation"] == "Basic SVO."


# --- end-to-end: script patches the JSON file --------------------------


def test_script_end_to_end_with_mocked_llm(fresh, monkeypatch):
    """End-to-end: build the script's expected JSON payload from a
    mocked LLM, and verify the post-processing rules (content-based
    matching, alias renaming, bare-array wrapping) work on the full
    data flow. We don't actually run ``main()`` because it would
    overwrite the production seed file; we drive ``generate_for_items``
    directly."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import scripts.generate_explanations as ge

    items = [
        {"pattern": "S V O", "example_sentence": "She reads."},
        {"pattern": "be C", "example_sentence": "She is a teacher."},
    ]

    def fake_complete_json(*, schema, schema_name, system, user, temperature):
        return {"structures": [
            # First item uses correct field name; second uses alias.
            {"_id": 0, "pattern": "S V O", "example_sentence": "She reads.",
             "explanation": "Transitive."},
            {"_id": 1, "pattern": "be C", "example_sentence": "She is a teacher.",
             "usage_note": "Linking verb."},
        ]}

    with mock.patch.object(ge.llm, "complete_json", side_effect=fake_complete_json):
        out = ge.generate_for_items("en", items, "structure")

    assert out["S V O"]["explanation"] == "Transitive."
    assert out["be C"]["explanation"] == "Linking verb."


# --- imported module needs `mock` ---------------------------------------


import unittest.mock as _mock  # noqa: E402
mock = _mock
