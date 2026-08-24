"""Shared pytest fixtures.

`clean_state` resets module-level in-memory state (undo tokens, etc.) and
sets up a fresh data dir + DB before each test.
"""

from __future__ import annotations

import pytest

# Force ``backend.config`` to import *now* (before any test fixture runs)
# so ``config.load_dotenv()`` fires exactly once with the developer's
# process-wide env. Once that initial load is done, ``clean_state`` can
# safely ``monkeypatch.delenv`` ``LANGLEARN_PASSWORD`` without the next
# ``from backend import config`` (which many test modules trigger as
# their first statement) re-running ``load_dotenv`` and clobbering the
# cleared value. ``config.py`` also guards against re-loads via an
# attribute on the ``load_dotenv`` callable.
from backend import config as _config  # noqa: F401  (side-effect import)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGLEARN_DATA_DIR", str(tmp_path))
    # Isolate LLM config from any developer .env: tests that care about the
    # LLM set their own env. Point at OpenAI's endpoint with no key so lookups
    # fail fast and deterministically instead of hitting a real server.
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    # Disable the secondary LLM provider by default. Individual tests opt
    # back in via their own fixture to exercise the fallback chain.
    for k in ("SECONDARY_OPENAI_API_KEY", "SECONDARY_OPENAI_BASE_URL",
              "SECONDARY_OPENAI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    from backend import config as _cfg
    monkeypatch.setattr(_cfg, "SECONDARY_OPENAI_API_KEY", "")
    monkeypatch.setattr(_cfg, "SECONDARY_OPENAI_BASE_URL", "")
    monkeypatch.setattr(_cfg, "SECONDARY_OPENAI_MODEL", "")
    # config.load_dotenv() pulls in a developer .env; tests must not inherit
    # LANGLEARN_PASSWORD from it. Auth-gated tests re-enable it via their own
    # fixture.
    monkeypatch.delenv("LANGLEARN_PASSWORD", raising=False)
    from backend import db
    db.init_schema()
    # Reset module-level in-memory state.
    from backend.services import vocab as vocab_svc
    vocab_svc._undo_tokens.clear()
    from backend.services import auth_gate
    auth_gate._login_attempts.clear()
    # Mirror the real app's first-boot behavior so chain-execution tests
    # can rely on bundled dictionaries (WordNet for English) being
    # installed without per-test boilerplate. Tests that want to verify
    # uninstall behavior start from this baseline and then uninstall.
    from backend.services.dictionaries import installer as dict_installer
    dict_installer.auto_install_defaults()
    yield tmp_path