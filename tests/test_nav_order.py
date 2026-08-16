"""Tests for the top-bar / mobile-drawer nav ordering.

The nav is built from a static ``ROUTES`` array in
``frontend/static/js/components/nav-links.js`` and the matching
``frontend/static/js/components/nav-drawer.js``. This module reads
those files as text and pins the relative position of the Assist entry
(a combined Analyze + Refine + Translate page) so a future re-ordering
doesn't accidentally bury it. We don't spin up a JS test harness here —
the endpoints are already covered by ``test_analyze.py``,
``test_refine.py``, and ``test_translate.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
NAV_LINKS_JS = REPO_ROOT / "frontend" / "static" / "js" / "components" / "nav-links.js"
NAV_DRAWER_JS = REPO_ROOT / "frontend" / "static" / "js" / "components" / "nav-drawer.js"


def _route_block(source: str) -> list[str]:
    """Extract the route labels from a nav-links.js / nav-drawer.js
    source. We look for ``{ hash: "#/foo", label: "Bar" }`` lines inside
    the ``ROUTES`` array, in source order. Quotes may be ``"`` or
    ``'`` and the line may end with a trailing comma we must not
    treat as part of the label."""
    labels: list[str] = []
    for line in source.splitlines():
        line = line.strip()
        if not line.startswith("{ hash:"):
            continue
        try:
            after = line.split("label:", 1)[1]
        except IndexError:
            continue
        after = after.lstrip()
        if not after or after[0] not in ("'", '"'):
            continue
        quote = after[0]
        # Find the matching closing quote, respecting no escapes (the
        # nav labels are plain ASCII words, so a simple scan is fine).
        try:
            end = after.index(quote, 1)
        except ValueError:
            continue
        labels.append(after[1:end])
    return labels


def _load(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_nav_links_includes_assist():
    labels = _route_block(_load(NAV_LINKS_JS))
    assert "Assist" in labels, f"Assist missing from {NAV_LINKS_JS}: {labels}"


def test_nav_drawer_includes_assist():
    labels = _route_block(_load(NAV_DRAWER_JS))
    assert "Assist" in labels, f"Assist missing from {NAV_DRAWER_JS}: {labels}"


def _index_of(labels: list[str], target: str) -> int:
    try:
        return labels.index(target)
    except ValueError as e:
        raise AssertionError(
            f"{target!r} not in nav order: {labels}"
        ) from e


@pytest.mark.parametrize("nav_path", [NAV_LINKS_JS, NAV_DRAWER_JS])
def test_assist_appears_after_dictionary(nav_path: Path):
    """Assist (Analyze + Refine + Translate) must sit right after Dictionary.
    The user explicitly asked for this ordering; a future shuffle that
    moves it to the bottom (e.g. alongside Settings) is exactly the
    regression we want to catch."""
    labels = _route_block(_load(nav_path))
    i_dict = _index_of(labels, "Dictionary")
    i_assist = _index_of(labels, "Assist")
    assert i_assist == i_dict + 1, (
        f"Assist should sit directly after Dictionary in {nav_path.name}, "
        f"got order: {labels}"
    )


@pytest.mark.parametrize("nav_path", [NAV_LINKS_JS, NAV_DRAWER_JS])
def test_top_bar_and_drawer_share_the_same_order(nav_path: Path):
    """The top bar and the mobile drawer should list routes in the
    same order. Otherwise a user gets one nav on desktop and a
    different one on mobile, which is just confusing."""
    a = _route_block(_load(NAV_LINKS_JS))
    b = _route_block(_load(nav_path))
    if nav_path == NAV_LINKS_JS:
        return  # this case is trivially true
    assert a == b, (
        f"top bar and drawer differ: top_bar={a}, drawer={b}"
    )
