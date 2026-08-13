"""Runs the Node-based page-state save/restore tests.

`page-state.js` is a pure helper (sessionStorage + globalThis stash)
used by the SPA router to preserve per-page state across in-tab
navigation. We shell out to Node and assert exit code 0 + all-tests-
passed in stdout, mirroring the cache, nav-drawer, and review-cache
shims.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_SCRIPT = REPO_ROOT / "tests" / "page_state.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_page_state_helper():
    result = subprocess.run(
        ["node", str(TEST_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"page-state tests failed:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    assert "failed" in result.stdout, result.stdout
    assert "0 failed" in result.stdout, f"unexpected output:\n{result.stdout}"
