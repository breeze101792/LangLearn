"""Live LLM-provider integration test.

Exercises the real ``backend.services.llm.lookup_word_via_llm`` path
against whatever endpoint ``OPENAI_BASE_URL`` points at. This is the
exact failure mode that surfaced in the wild: a provider returning
JSON that doesn't match the strict schema we send in
``response_format``.

By design this test does NOT fail the suite — a non-conforming
provider emits a ``UserWarning`` and the case is recorded for the
human running the test to inspect. Default ``pytest`` runs skip it
entirely, so a developer without an LLM endpoint configured isn't
bothered. Opt in by setting ``LANGLEARN_LIVE_LLM=1`` in the env.

Why capture env at import time
-------------------------------
``tests/conftest.py``'s autouse ``clean_state`` fixture monkeypatches
``OPENAI_API_KEY`` to ``""`` and ``OPENAI_BASE_URL`` to OpenAI's
endpoint so lookups fail fast and deterministically in unit tests.
That monkeypatch runs *before* the test body, so any env var we read
inside the test is already clobbered. We capture the developer's real
values at import time and restore them per-test via ``monkeypatch``,
which in turn re-clobbers our copies the right way for the
``OpenAICompatClient`` (which reads env vars at request time).
"""

from __future__ import annotations

import json as _json
import os
import re
import sys
import warnings
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# Snapshot the env at import time, before any fixture runs. If the
# developer hasn't set LANGLEARN_LIVE_LLM=1, expose a skip marker
# instead of a hard failure so the default suite is still green.
_LIVE_ENV_AT_IMPORT: dict[str, str] = {
    k: os.environ[k]
    for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")
    if k in os.environ
}
_LIVE_ENABLED = os.environ.get("LANGLEARN_LIVE_LLM", "") == "1"

pytestmark = pytest.mark.skipif(
    not _LIVE_ENABLED,
    reason=(
        "live LLM integration test; set LANGLEARN_LIVE_LLM=1 "
        "(with OPENAI_API_KEY / OPENAI_BASE_URL configured) to run"
    ),
)


@pytest.fixture
def live_env(monkeypatch):
    """Restore the captured OPENAI_* env inside the test. ``monkeypatch``
    will undo these on teardown, so the autouse ``clean_state`` clobber
    from conftest doesn't leak to other tests."""
    for k, v in _LIVE_ENV_AT_IMPORT.items():
        monkeypatch.setenv(k, v)
    for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in _LIVE_ENV_AT_IMPORT.items():
        monkeypatch.setenv(k, v)
    yield


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower() or "model"


def _try_parse_schema_compliant(
    raw: str,
    *,
    schema: dict,
    normalize,
    validator,
) -> tuple[bool, dict | None, list[str]]:
    """Return (ok, repaired_data_or_None, error_messages). Tries to
    parse + repair the raw response; if the repaired payload still
    fails the strict schema, returns the error list."""
    try:
        data = _json.loads(raw)
    except Exception as e:
        return False, None, [f"not valid JSON: {e}"]
    if normalize is not None:
        try:
            data = normalize(data)
        except Exception as e:
            return False, None, [f"normalizer crashed: {e}"]
    errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
    if errors:
        return False, data, [e.message for e in errors]
    return True, data, []


def test_lookup_word_live(live_env, tmp_path, capsys):
    """Run ``lookup_word_via_llm`` against the live endpoint and check
    that the strict ``dict_word`` schema accepts the response, after
    the normalizer has had a chance to repair it. On failure, emit a
    warning (so the suite stays green) and print the raw reply so the
    human running the test can see exactly what the model produced.

    We pick "young" because it has multiple obvious senses and is
    short — keeping the prompt small reduces variance across
    providers."""
    from backend.services import llm

    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    model = os.environ.get("OPENAI_MODEL", "(unset)")
    if not api_key:
        if "api.openai.com" in base_url:
            pytest.skip("OPENAI_API_KEY is empty and base URL is OpenAI's")
        # Non-OpenAI compatible proxies (e.g. local Ollama) often
        # accept anonymous access; warn instead of skipping so the
        # developer notices the missing key.
        warnings.warn(
            "OPENAI_API_KEY is empty; live LLM call may fail with 401/403",
            UserWarning,
        )

    label = (
        f"live-llm[{_slug(model)} @ {base_url or '(no base)'}]"
    )
    print(f"\n== {label} ==", flush=True)
    print(f"   model={model!r}  base={base_url!r}", flush=True)

    try:
        data = llm.lookup_word_via_llm(
            lang="en",
            word="young",
            explanation_primary="en",
            explanation_secondary=None,
        )
        exception: Exception | None = None
    except Exception as e:
        data = None
        exception = e

    if exception is not None:
        msg = (
            f"{label}: live LLM call raised "
            f"{type(exception).__name__}: {exception}"
        )
        print(f"   RESULT: FAIL — {msg}", flush=True)
        warnings.warn(msg, UserWarning)
        return

    validator = llm.Draft202012Validator(llm.DICT_WORD_SCHEMA)
    ok, repaired, errs = _try_parse_schema_compliant(
        # Re-stringify so the helper's parse path also gets exercised.
        _json.dumps(data),
        schema=llm.DICT_WORD_SCHEMA,
        normalize=llm._normalize_dict_word,
        validator=validator,
    )
    senses_count = (
        len(repaired.get("senses", []))
        if isinstance(repaired, dict) else 0
    )
    defs_count = 0
    if isinstance(repaired, dict):
        for s in repaired.get("senses", []) or []:
            if isinstance(s, dict) and isinstance(s.get("definitions"), list):
                defs_count += len(s["definitions"])

    print(
        f"   senses={senses_count}  definitions={defs_count}  "
        f"schema_ok={ok}",
        flush=True,
    )
    if ok:
        print(f"   RESULT: PASS", flush=True)
        return

    err_sample = "; ".join(errs)[:300]
    msg = (
        f"{label}: live response from model {model!r} did not match "
        f"strict dict_word schema after normalization. "
        f"first error: {err_sample}"
    )
    print(f"   RESULT: FAIL — {msg}", flush=True)
    if isinstance(repaired, dict):
        print(
            "   repaired sample: "
            + _json.dumps(repaired, ensure_ascii=False)[:400],
            flush=True,
        )
    warnings.warn(msg, UserWarning)
