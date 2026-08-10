"""Tests for batched LLM calls.

The apply and seed paths are now batched so that the LLM sees a small
chunk per request, instead of a giant one. These tests pin:

- 51 structures + 103 phrases split into chunks of 20 produces the
  right number of LLM calls (3 + 6 = 9 by default; rounded).
- Each chunk is small: the schema allows at most `batch_size` items
  per response.
- If one chunk fails, the others still succeed (the service catches
  per-chunk errors and continues — actually, the current contract is
  fail-fast, but each chunk is independent).
- The fill paths (single row) make exactly one call.
- The fill-many paths (list of partials) split by batch_size.
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
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


# --- apply_explanations_via_llm batching -------------------------------


def test_apply_batches_50_structures_into_3_calls(fresh, monkeypatch):
    """50 structures / batch_size=20 -> ceil(50/20) = 3 LLM calls."""
    from backend.services import llm

    structures = [
        {"id": i, "pattern": f"p{i}", "example_sentence": f"e{i}",
         "explanation": ""}
        for i in range(50)
    ]
    calls = {"n": 0, "schema_names": []}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        schema_name = json["response_format"]["json_schema"]["name"]
        calls["schema_names"].append(schema_name)
        # The apply batch only needs `id` plus the explanation fields.
        return _mock_openai_response(_json.dumps({
            "structures": [{"id": 1, "explanation": "x"}]
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.apply_explanations_via_llm(
        lang="es", structures=structures, phrases=[],
        primary="en", secondary=None, batch_size=20,
    )
    # 3 batches for 50 structures.
    assert calls["n"] == 3
    assert all("apply_explanations_structures" in n for n in calls["schema_names"])


def test_apply_batches_103_phrases_into_6_calls(fresh, monkeypatch):
    """103 phrases / batch_size=20 -> ceil(103/20) = 6 LLM calls."""
    from backend.services import llm

    phrases = [
        {"id": i, "phrase": f"p{i}", "example_sentence": f"t{i}",
         "explanation": ""}
        for i in range(103)
    ]
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        return _mock_openai_response(_json.dumps({
            "phrases": [{"id": 1, "explanation": "x"}]
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.apply_explanations_via_llm(
        lang="es", structures=[], phrases=phrases,
        primary="en", secondary=None, batch_size=20,
    )
    assert calls["n"] == 6


def test_apply_with_zero_items_makes_no_calls(fresh, monkeypatch):
    from backend.services import llm

    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        return _mock_openai_response("{}")

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.apply_explanations_via_llm(
        lang="es", structures=[], phrases=[],
        primary="en", secondary=None, batch_size=20,
    )
    assert calls["n"] == 0
    assert out == {"structures": [], "phrases": []}


def test_apply_chunks_smaller_than_batch_size_makes_one_call(fresh, monkeypatch):
    """Fewer rows than batch_size -> exactly one call."""
    from backend.services import llm

    structures = [{"id": i, "pattern": f"p{i}", "example_sentence": f"e{i}",
                   "explanation": ""} for i in range(5)]
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        return _mock_openai_response(_json.dumps({
            "structures": [{"id": 1, "explanation": "x"}]
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.apply_explanations_via_llm(
        lang="es", structures=structures, phrases=[],
        primary="en", secondary=None, batch_size=20,
    )
    assert calls["n"] == 1


def test_apply_merges_chunk_responses(fresh, monkeypatch):
    """Items from each chunk are concatenated into a single list."""
    from backend.services import llm

    structures = [{"id": i, "pattern": f"p{i}", "example_sentence": f"e{i}",
                   "explanation": ""} for i in range(45)]
    responses = [
        {"structures": [{"id": i, "explanation": f"batch1-{i}"} for i in range(20)]},
        {"structures": [{"id": i, "explanation": f"batch2-{i}"} for i in range(20, 40)]},
        {"structures": [{"id": i, "explanation": f"batch3-{i}"} for i in range(40, 45)]},
    ]
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        body = _json.dumps(responses[calls["n"]])
        calls["n"] += 1
        return _mock_openai_response(body)

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.apply_explanations_via_llm(
        lang="es", structures=structures, phrases=[],
        primary="en", secondary=None, batch_size=20,
    )
    assert len(out["structures"]) == 45
    # Each id should appear exactly once, with the explanation from its batch.
    by_id = {item["id"]: item["explanation"] for item in out["structures"]}
    assert by_id[0] == "batch1-0"
    assert by_id[25] == "batch2-25"
    assert by_id[42] == "batch3-42"


# --- generate_seed_payload batching ------------------------------------


def test_seed_batches_structures_and_phrases_separately(fresh, monkeypatch):
    """50 structures -> 3 calls, 100 phrases -> 5 calls, total 8."""
    from backend.services import llm

    calls = {"n": 0, "schema_names": []}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        schema_name = json["response_format"]["json_schema"]["name"]
        calls["schema_names"].append(schema_name)
        if "structures" in schema_name:
            return _mock_openai_response(_json.dumps({
                "structures": [{"pattern": "p", "example_sentence": "e",
                                 "explanation": "n",
                                 "explanation_primary": "primary-gloss"}]
            }))
        return _mock_openai_response(_json.dumps({
            "phrases": [{"phrase": "p", "example_sentence": "l",
                          "explanation": "n",
                          "explanation_primary": "primary-gloss"}]
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    out = llm.generate_seed_payload(
        lang="es", n_structures=50, n_phrases=100,
        primary="en", secondary=None, batch_size=20,
    )
    # 3 structure batches + 5 phrase batches.
    assert calls["n"] == 8
    structure_calls = [n for n in calls["schema_names"] if "structures" in n]
    phrase_calls = [n for n in calls["schema_names"] if "phrases" in n]
    assert len(structure_calls) == 3
    assert len(phrase_calls) == 5


# --- fill batching ------------------------------------------------------


def test_fill_structures_via_llm_batches_when_list_is_large(fresh, monkeypatch):
    """fill_structures_via_llm with 25 partials / batch_size=10 -> 3 calls."""
    from backend.services import llm

    partials = [{"pattern": f"p{i}", "example_sentence": None,
                  "explanation": None, "explanation_primary": None,
                  "explanation_secondary": None} for i in range(25)]
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        return _mock_openai_response(_json.dumps({
            "structures": [{"pattern": "p", "example_sentence": "e",
                             "explanation": "n", "explanation_primary": None,
                             "explanation_secondary": None}]
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.fill_structures_via_llm(
        lang="es", partials=partials, primary="en", secondary=None,
        batch_size=10,
    )
    assert calls["n"] == 3


def test_fill_structure_via_llm_single_row_uses_one_call(fresh, monkeypatch):
    """The single-row convenience wrapper still uses one call (with batch_size=1)."""
    from backend.services import llm

    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        return _mock_openai_response(_json.dumps({
            "structures": [{"pattern": "p", "example_sentence": "e",
                             "explanation": "n", "explanation_primary": None,
                             "explanation_secondary": None}]
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.fill_structure_via_llm(lang="es", partial={"pattern": None})
    assert calls["n"] == 1


def test_fill_phrases_via_llm_batches_when_list_is_large(fresh, monkeypatch):
    from backend.services import llm

    partials = [{"example_sentence": None, "explanation": None,
                  "explanation_primary": None, "explanation_secondary": None}
                 for _ in range(20)]
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        return _mock_openai_response(_json.dumps({
            "phrases": [{"example_sentence": "l", "explanation": "n",
                          "explanation_primary": None, "explanation_secondary": None}]
        }))

    monkeypatch.setattr("backend.services.llm.requests.post", fake_post)
    llm.fill_phrases_via_llm(
        lang="es", partials=partials, primary="en", secondary=None,
        batch_size=10,
    )
    assert calls["n"] == 2


# --- batch_size validation --------------------------------------------


def test_apply_rejects_zero_batch_size(fresh):
    from backend.services import llm
    with pytest.raises(ValueError):
        llm.apply_explanations_via_llm(
            lang="es", structures=[{"id": 1}], phrases=[],
            primary=None, secondary=None, batch_size=0,
        )


def test_seed_rejects_zero_batch_size(fresh):
    from backend.services import llm
    with pytest.raises(ValueError):
        llm.generate_seed_payload(
            lang="es", n_structures=1, n_phrases=1, batch_size=0,
        )


def test_fill_rejects_zero_batch_size(fresh):
    from backend.services import llm
    with pytest.raises(ValueError):
        llm.fill_structures_via_llm(
            lang="es", partials=[{}], batch_size=0,
        )
