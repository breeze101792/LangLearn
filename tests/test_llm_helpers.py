"""Pure-unit tests for LLM-service helpers and seam points that don't
require HTTP plumbing.

Coverage these tests add on top of ``test_llm.py`` /
``test_llm_full.py`` / ``test_llm_batching.py``:

- ``apply_explanation_rules`` — the four-case rule table (L vs P, S set
  vs null) plus the seed-shaped and single-item payload shapes.
- ``seed_schema`` — ``require_primary`` flip toggles which fields the
  schema requires; both shapes (structures + phrases) are present and
  the schema validates minimal examples.
- ``_strip_markdown_fence`` — strips ``json`` / plain fences; passes
  plain JSON through.
- ``generate_structures_via_llm`` and ``generate_phrases_via_llm`` —
  argument validation (``n < 0``, ``batch_size < 1``), short-circuits
  on ``n == 0``, batching across the LLM (multiple calls when
  ``n > batch_size``), and ``require_primary`` interaction with the
  schema built per call.
- ``apply_explanations_via_llm`` — argument validation, batched calls
  across structures and phrases independently.

Note: we exercise ``generate_structures_via_llm`` and
``generate_phrases_via_llm`` through the public API rather than poking
private callables (the format drivers are private for a reason).
"""

from __future__ import annotations

import json as _json
from unittest import mock

import pytest


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    """Override conftest's empty OPENAI_API_KEY so live-LLM branches in
    generate_*/apply_explanations_via_llm can be exercised without
    hitting the network (callers monkeypatch ``requests.post``)."""
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    from backend import db
    db.init_schema()
    return tmp_path


def _mock_openai_response(content: str, status_code: int = 200):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return resp


def _patch_post(monkeypatch, payload):
    """Make every ``requests.post`` return ``json.dumps(payload)``."""
    body = _json.dumps(payload)

    def fake_post(url, json=None, headers=None, timeout=None):
        return _mock_openai_response(body)

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)


# ---- apply_explanation_rules -------------------------------------------


def test_apply_rules_drops_primary_when_lang_matches_primary(monkeypatch):
    """L == P: primary should be dropped regardless of S."""
    from backend.services import llm

    payload = {
        "structures": [{"explanation_primary": "x", "explanation_secondary": "y"}],
        "phrases": [{"explanation_primary": "x", "explanation_secondary": "y"}],
    }
    llm.apply_explanation_rules(
        payload, lang="en", primary="en", secondary="zh",
    )
    for item in payload["structures"] + payload["phrases"]:
        assert item["explanation_primary"] is None
        # Secondary in a different language should be kept.
        assert item["explanation_secondary"] == "y"


def test_apply_rules_drops_secondary_when_secondary_matches_primary(monkeypatch):
    """If P == S, secondary is redundant — drop it."""
    from backend.services import llm

    payload = {
        "structures": [{"explanation_primary": "x", "explanation_secondary": "y"}],
    }
    llm.apply_explanation_rules(
        payload, lang="en", primary="en", secondary="en",
    )
    assert payload["structures"][0]["explanation_primary"] is None
    assert payload["structures"][0]["explanation_secondary"] is None


def test_apply_rules_drops_secondary_when_secondary_is_none(monkeypatch):
    """Null secondary means skip — primary may still be kept."""
    from backend.services import llm

    payload = {
        "structures": [{"explanation_primary": "x", "explanation_secondary": "y"}],
    }
    llm.apply_explanation_rules(
        payload, lang="es", primary="en", secondary=None,
    )
    # L != P → keep primary; null S → drop secondary.
    assert payload["structures"][0]["explanation_primary"] == "x"
    assert payload["structures"][0]["explanation_secondary"] is None


def test_apply_rules_drops_both_when_primary_is_none(monkeypatch):
    """Null primary ⇒ never generate primary. Secondary is a different
    question: if the user set one, we still want to generate it (and the
    LLM is happy to fill it), even with primary null. This pins that
    asymmetry so a future refactor doesn't silently drop it."""
    from backend.services import llm

    payload = {
        "structures": [{"explanation_primary": "x", "explanation_secondary": "y"}],
        "phrases": [{"explanation_primary": "x", "explanation_secondary": "y"}],
    }
    llm.apply_explanation_rules(
        payload, lang="es", primary=None, secondary="zh",
    )
    for item in payload["structures"] + payload["phrases"]:
        assert item["explanation_primary"] is None
        # Secondary is still wanted.
        assert item["explanation_secondary"] == "y"


def test_apply_rules_handles_single_item_payload(monkeypatch):
    """A single fill-shaped payload has the explanation fields at top level."""
    from backend.services import llm

    payload = {"explanation_primary": "x", "explanation_secondary": "y"}
    llm.apply_explanation_rules(
        payload, lang="en", primary="en", secondary=None,
    )
    assert payload["explanation_primary"] is None
    assert payload["explanation_secondary"] is None


def test_apply_rules_no_op_when_payload_has_no_explanation_fields(monkeypatch):
    """If neither seed-shape keys nor explanation_* fields are present,
    ``apply_explanation_rules`` is a no-op (no KeyError)."""
    from backend.services import llm

    payload = {"unrelated": 1}
    llm.apply_explanation_rules(
        payload, lang="es", primary="en", secondary=None,
    )
    assert payload == {"unrelated": 1}


def test_apply_rules_tolerates_non_dict_items(monkeypatch):
    """Non-dict entries (strays from a future schema) are skipped silently."""
    from backend.services import llm

    payload = {
        "structures": ["bogus", {"explanation_primary": "x"}],
        "phrases": [None, {"explanation_primary": "x"}],
    }
    llm.apply_explanation_rules(
        payload, lang="es", primary="en", secondary=None,
    )
    # First item in each list is non-dict — must not raise.
    assert payload["structures"][0] == "bogus"
    assert payload["phrases"][0] is None
    # Dict entries should still be processed.
    assert payload["structures"][1]["explanation_primary"] == "x"
    assert payload["phrases"][1]["explanation_primary"] == "x"


# ---- seed_schema --------------------------------------------------------


def test_seed_schema_require_primary_adds_primary_to_required(monkeypatch):
    from backend.services import llm
    from jsonschema import Draft202012Validator

    s = llm.seed_schema(require_primary=True)
    validator = Draft202012Validator(s)
    # A minimal payload that includes explanation_primary is valid.
    validator.validate({
        "structures": [{
            "pattern": "S V O", "example_sentence": "x",
            "explanation": "e", "explanation_primary": "p",
        }],
        "phrases": [{
            "phrase": "hi", "example_sentence": "x",
            "explanation": "e", "explanation_primary": "p",
        }],
    })
    # Drop explanation_primary → must fail validation.
    bad = {
        "structures": [{
            "pattern": "S V O", "example_sentence": "x",
            "explanation": "e",
        }],
        "phrases": [{
            "phrase": "hi", "example_sentence": "x",
            "explanation": "e",
        }],
    }
    with pytest.raises(Exception):
        validator.validate(bad)


def test_seed_schema_require_false_drops_primary_from_required(monkeypatch):
    """When ``require_primary=False``, explanation_primary must NOT be in
    the required set, and a payload that omits it must validate."""
    from backend.services import llm
    from jsonschema import Draft202012Validator

    s = llm.seed_schema(require_primary=False)
    validator = Draft202012Validator(s)
    validator.validate({
        "structures": [{
            "pattern": "S V O", "example_sentence": "x",
            "explanation": "e",
        }],
        "phrases": [{
            "phrase": "hi", "example_sentence": "x",
            "explanation": "e",
        }],
    })


# ---- _strip_markdown_fence ---------------------------------------------


def test_strip_markdown_fence_plain_json_passes_through(monkeypatch):
    from backend.services import llm
    raw = '{"a": 1}'
    assert llm._strip_markdown_fence(raw) == raw


def test_strip_markdown_fence_strips_json_fence(monkeypatch):
    from backend.services import llm
    raw = '```json\n{"a": 1}\n```'
    assert llm._strip_markdown_fence(raw) == '{"a": 1}'


def test_strip_markdown_fence_strips_plain_fence(monkeypatch):
    from backend.services import llm
    raw = '```\n{"a": 1}\n```'
    assert llm._strip_markdown_fence(raw) == '{"a": 1}'


def test_strip_markdown_fence_strips_fence_without_trailing_newline(monkeypatch):
    """Some proxies emit a one-line fence with no closing newline before
    the trailing ```. Should still be stripped."""
    from backend.services import llm
    raw = '```json\n{"a": 1}```'
    assert llm._strip_markdown_fence(raw) == '{"a": 1}'


def test_strip_markdown_fence_single_fence_returns_self(monkeypatch):
    """An opening fence with no newline (truncated response) returns the
    input unchanged rather than raising."""
    from backend.services import llm
    raw = '```json'
    assert llm._strip_markdown_fence(raw) == '```json'


# ---- generate_*_via_llm argument validation ---------------------------


def test_generate_structures_zero_returns_empty_without_calling_llm(monkeypatch):
    """``n=0`` should short-circuit, never call the LLM."""
    from backend.services import llm

    called = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        called["n"] += 1
        return _mock_openai_response("{}")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.generate_structures_via_llm(lang="es", n=0)
    assert out == []
    assert called["n"] == 0


def test_generate_phrases_zero_returns_empty_without_calling_llm(monkeypatch):
    from backend.services import llm

    called = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        called["n"] += 1
        return _mock_openai_response("{}")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.generate_phrases_via_llm(lang="es", n=0)
    assert out == []
    assert called["n"] == 0


def test_generate_structures_rejects_negative_n(monkeypatch):
    from backend.services import llm
    with pytest.raises(ValueError, match="n must be >= 0"):
        llm.generate_structures_via_llm(lang="es", n=-1)


def test_generate_phrases_rejects_negative_n(monkeypatch):
    from backend.services import llm
    with pytest.raises(ValueError, match="n must be >= 0"):
        llm.generate_phrases_via_llm(lang="es", n=-1)


def test_generate_structures_rejects_zero_batch_size(monkeypatch):
    from backend.services import llm
    with pytest.raises(ValueError, match="batch_size must be >= 1"):
        llm.generate_structures_via_llm(lang="es", n=1, batch_size=0)


def test_generate_phrases_rejects_zero_batch_size(monkeypatch):
    from backend.services import llm
    with pytest.raises(ValueError, match="batch_size must be >= 1"):
        llm.generate_phrases_via_llm(lang="es", n=1, batch_size=0)


# ---- generate_*_via_llm batching ---------------------------------------


def test_generate_structures_batches_calls(fresh, monkeypatch):
    """With SEED_BATCH_SIZE=20 (default) and n=25, expect two LLM calls
    — one for 20, one for 5."""
    from backend.services import llm

    sizes: list[int] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        # The prompt contains "Generate N <kind>", parse it out.
        text = next(
            m["content"]
            for m in (json or {}).get("messages", [])
            if m.get("role") == "user"
        )
        n = int(text.split("Generate ")[1].split(" ")[0])
        sizes.append(n)
        return _mock_openai_response(_json.dumps({
            "structures": [{"pattern": f"P{n}",
                            "example_sentence": "x",
                            "explanation": "e",
                            "explanation_primary": "p"}] * n
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.generate_structures_via_llm(lang="es", n=25)
    assert sizes == [20, 5]
    # 20 + 5 items total.
    assert len(out) == 25


def test_generate_phrases_batches_calls(fresh, monkeypatch):
    from backend.services import llm

    sizes: list[int] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        text = next(
            m["content"]
            for m in (json or {}).get("messages", [])
            if m.get("role") == "user"
        )
        n = int(text.split("Generate ")[1].split(" ")[0])
        sizes.append(n)
        return _mock_openai_response(_json.dumps({
            "phrases": [{"phrase": f"P{n}",
                         "example_sentence": "x",
                         "explanation": "e",
                         "explanation_primary": "p"}] * n
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.generate_phrases_via_llm(lang="es", n=7)
    assert sizes == [7]
    assert len(out) == 7


def test_generate_structures_lang_equals_primary_omits_primary_in_prompt(fresh, monkeypatch):
    """When the target language equals the primary native language, the
    prompt must explicitly tell the model NOT to include
    explanation_primary — the post-processing pass can later clean up
    if the model is chatty, but the prompt is the primary guard."""
    from backend.services import llm

    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["user"] = next(
            m["content"]
            for m in (json or {}).get("messages", [])
            if m.get("role") == "user"
        )
        return _mock_openai_response(_json.dumps({
            "structures": [{"pattern": "S V O",
                            "example_sentence": "x",
                            "explanation": "e"}]
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.generate_structures_via_llm(lang="en", n=1, primary="en")
    user_prompt = captured["user"].lower()
    assert "explanation_primary" in user_prompt
    assert "do not include" in user_prompt or "do not" in user_prompt


def test_generate_phrases_lang_equals_primary_omits_primary_in_prompt(fresh, monkeypatch):
    from backend.services import llm

    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["user"] = next(
            m["content"]
            for m in (json or {}).get("messages", [])
            if m.get("role") == "user"
        )
        return _mock_openai_response(_json.dumps({
            "phrases": [{"phrase": "hi",
                         "example_sentence": "x",
                         "explanation": "e"}]
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.generate_phrases_via_llm(lang="en", n=1, primary="en")
    user_prompt = captured["user"].lower()
    assert "do not include" in user_prompt or "do not" in user_prompt


def test_generate_seed_payload_post_process_drops_primary_when_lang_eq_primary(fresh, monkeypatch):
    """``generate_seed_payload`` invokes ``apply_explanation_rules`` on
    the merged result. Even if the chatty model returns
    explanation_primary despite the prompt telling it not to, the
    post-processing rule (L == P) must null it out."""
    from backend.services import llm

    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        # Call 1: structures batch. Call 2: phrases batch.
        if calls["n"] == 1:
            return _mock_openai_response(_json.dumps({
                "structures": [{
                    "pattern": "S V O", "example_sentence": "x",
                    "explanation": "e",
                    "explanation_primary": "Oops, filled it",
                }],
            }))
        return _mock_openai_response(_json.dumps({
            "phrases": [],
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.generate_seed_payload(
        lang="en", n_structures=1, n_phrases=0, primary="en",
    )
    assert out["structures"][0]["explanation_primary"] is None


# ---- apply_explanations_via_llm argument validation --------------------


def test_apply_explanations_via_llm_rejects_zero_batch_size(monkeypatch):
    from backend.services import llm
    with pytest.raises(ValueError, match="batch_size must be >= 1"):
        llm.apply_explanations_via_llm(
            lang="es", structures=[{"id": 1}], phrases=[],
            batch_size=0,
        )


def test_apply_explanations_via_llm_rejects_negative_batch_size(monkeypatch):
    from backend.services import llm
    with pytest.raises(ValueError, match="batch_size must be >= 1"):
        llm.apply_explanations_via_llm(
            lang="es", structures=[{"id": 1}], phrases=[],
            batch_size=-5,
        )


def test_apply_explanations_a_chatty_model_returning_garbage_id_falls_through_to_schema_error(fresh, monkeypatch):
    """When the LLM emits a non-integer ``id``, the strict JSON schema
    rejects it before we ever reach the apply path. The downstream
    service layer has its own ``isinstance(row_id, int)`` filter as a
    second line of defense, but the LLM layer's job is to fail fast on
    bad inputs rather than silently drop them."""
    from backend.services import llm

    _patch_post(monkeypatch, {
        "structures": [{"id": "not-an-int",
                        "explanation_primary": "p",
                        "explanation_secondary": None,
                        "explanation": "e"}]
    })
    with pytest.raises(llm.LLMSchemaError):
        llm.apply_explanations_via_llm(
            lang="es",
            structures=[{"id": 1, "pattern": "p",
                         "example_sentence": "x", "explanation": "e"}],
            phrases=[],
            primary="en",
        )


def test_apply_explanations_structures_and_phrases_batched_independently(fresh, monkeypatch):
    """Structures and phrases each get their own series of LLM calls,
    sized to ``batch_size``. The two arrays are independent."""
    from backend.services import llm

    call_payloads: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        body = json or {}
        call_payloads.append(body)
        # Identify which call this is by the schema name we sent.
        schema_name = body.get(
            "response_format", {}
        ).get("json_schema", {}).get("name", "")
        if schema_name == "apply_explanations_structures":
            # Echo back one item per input row in the prompt.
            text = next(
                m["content"] for m in body.get("messages", [])
                if m.get("role") == "user"
            )
            ids = [int(x["id"])
                   for x in _json.loads(text[text.rindex('['):]).__iter__()] \
                   if False else []
            import re
            ids = [int(m) for m in re.findall(r'"id":\s*(\d+)', text)]
            return _mock_openai_response(_json.dumps({
                "structures": [
                    {"id": i, "explanation_primary": "p",
                     "explanation_secondary": None, "explanation": "e"}
                    for i in ids
                ]
            }))
        if schema_name == "apply_explanations_phrases":
            import re
            text = next(
                m["content"] for m in body.get("messages", [])
                if m.get("role") == "user"
            )
            ids = [int(m) for m in re.findall(r'"id":\s*(\d+)', text)]
            return _mock_openai_response(_json.dumps({
                "phrases": [
                    {"id": i, "explanation_primary": "p",
                     "explanation_secondary": None, "explanation": "e"}
                    for i in ids
                ]
            }))
        return _mock_openai_response(_json.dumps({}))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    structs = [{"id": i, "pattern": "p", "example_sentence": "x",
                "explanation": "e"} for i in range(1, 4)]
    phrases = [{"id": i, "phrase": "h", "example_sentence": "x",
                "explanation": "e"} for i in [10, 11]]
    out = llm.apply_explanations_via_llm(
        lang="es", structures=structs, phrases=phrases,
        batch_size=2, primary="en", secondary=None,
    )
    # 3 structures → ceil(3/2)=2 calls; 2 phrases → 1 call.
    assert len(call_payloads) == 3
    schema_names = [
        p.get("response_format", {}).get("json_schema", {}).get("name")
        for p in call_payloads
    ]
    assert schema_names.count("apply_explanations_structures") == 2
    assert schema_names.count("apply_explanations_phrases") == 1
    assert len(out["structures"]) == 3
    assert len(out["phrases"]) == 2
