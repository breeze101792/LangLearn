"""Tests for the Leitner scheduler."""

from __future__ import annotations

import datetime as _dt

import pytest


def test_grade_easy_promotes_and_demotes_hard():
    from backend.services import leitner

    new_box, due = leitner.grade(1, "easy")
    assert new_box == 2
    assert " " in due  # ISO format with space

    new_box, _ = leitner.grade(5, "easy")
    assert new_box == 5  # clamped

    new_box, _ = leitner.grade(3, "hard")
    assert new_box == 1  # floored

    new_box, _ = leitner.grade(2, "hard")
    assert new_box == 1


def test_grade_invalid_value_raises():
    from backend.services import leitner

    with pytest.raises(ValueError):
        leitner.grade(1, "ok")


def test_is_due_parses_iso():
    from backend.services import leitner

    now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    past = (now - _dt.timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    future = (now + _dt.timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    assert leitner.is_due(past) is True
    assert leitner.is_due(future) is False


def test_due_iso_increments_correctly():
    from backend.services import leitner

    base = _dt.datetime(2025, 1, 1, 12, 0, 0)
    due = leitner.due_iso(1, now=base)
    parsed = _dt.datetime.strptime(due, "%Y-%m-%d %H:%M:%S")
    assert parsed == base + _dt.timedelta(days=1)
    due = leitner.due_iso(5, now=base)
    parsed = _dt.datetime.strptime(due, "%Y-%m-%d %H:%M:%S")
    assert parsed == base + _dt.timedelta(days=30)