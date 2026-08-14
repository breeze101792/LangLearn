"""Runs the Node-based page-module smoke test.

This is the regression guard for the class of bugs that broke the
SPA three times during the page-state patch: a syntax error, a
variable shadowing, or an undeclared reference in a page module
silently breaks the whole SPA because the dynamic import in the
router never resolves. The smoke test imports every page module and
calls its render entry point against a JSDOM-provided host, so any
synchronous failure surfaces as a test failure.

Run with the standard pytest invocation:

    .venv_<host>/bin/python -m pytest
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_SCRIPT = REPO_ROOT / "tests" / "pages_load.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_pages_load():
    result = subprocess.run(
        ["node", str(TEST_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"pages_load smoke test failed:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    assert "failed" in result.stdout, result.stdout
    assert "0 failed" in result.stdout, f"unexpected output:\n{result.stdout}"
