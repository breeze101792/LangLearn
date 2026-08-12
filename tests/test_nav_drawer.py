"""Runs the Node-based drawer state-machine tests.

The drawer state is implemented in pure JS (frontend/static/js/components/
drawer-state.js) so it can be exercised without a browser. We shell out to
Node and assert exit code 0 + all-tests-passed in stdout.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_SCRIPT = REPO_ROOT / "tests" / "nav_drawer.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_nav_drawer_state_machine():
    result = subprocess.run(
        ["node", str(TEST_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"drawer state machine tests failed:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    assert "failed" in result.stdout, result.stdout
    assert "0 failed" in result.stdout, f"unexpected output:\n{result.stdout}"
