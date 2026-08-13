"""Runs the Node-based review-cache helper tests.

`findCachedRecord` (frontend/static/js/components/review-cache.js) is a pure
helper that reads from the dictionary cache and returns both the entry and
the provider `.source`. The review page uses it to highlight the matching
segment in the provider switcher; the regression test in the Node script
covers the case where dropping `.source` left the wrong segment highlighted.

We shell out to Node and assert exit code 0 + all-tests-passed in stdout,
mirroring the cache and nav-drawer shims.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_SCRIPT = REPO_ROOT / "tests" / "review_cache.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_review_cache_helper():
    result = subprocess.run(
        ["node", str(TEST_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"review-cache tests failed:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    assert "failed" in result.stdout, result.stdout
    assert "0 failed" in result.stdout, f"unexpected output:\n{result.stdout}"
