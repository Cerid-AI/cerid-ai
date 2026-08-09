# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Tests for the product-story drift gate (``scripts/lint-product-story.py``)."""
from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path
from textwrap import dedent

import pytest

# Load the script as a module without adding scripts/ to sys.path globally.
_LINT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "lint-product-story.py"
_spec = importlib.util.spec_from_file_location("_lint_product_story", _LINT_PATH)
assert _spec and _spec.loader, f"could not load {_LINT_PATH}"
_LINT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_LINT)  # type: ignore[union-attr]

REVIEWED_TODAY = "2026-05-11"


def _doc_with_all_primitives(reviewed: str = REVIEWED_TODAY) -> str:
    return dedent(
        f"""\
        # Cerid AI — Product Story

        > **Last reviewed:** {reviewed} (v0.92 plan)
        > **Canonical narrative.** Drift gate: `scripts/lint-product-story.py`
        > asserts this file exists, has a `## Last reviewed:` line within 90 days
        > of the most recent release tag, and references the five primitives.

        ## The five primitives

        ### 1. Verification — per-claim
        Body…

        ### 2. TrustScore — system-level
        Body…

        ### 3. Narrative Loop — daily brief + weekly synthesis
        Body…

        ### 4. Wiki — entity pages + contradictions
        Body…

        ### 5. Background Processor — continuous, throttled
        Body…
        """,
    )


def test_clean_doc_returns_no_issues() -> None:
    text = _doc_with_all_primitives()
    today = date.fromisoformat(REVIEWED_TODAY)
    issues = _LINT.collect_issues(text, max_age_days=90, today=today)
    assert issues == []


def test_missing_last_reviewed_is_flagged() -> None:
    text = _doc_with_all_primitives().replace(
        f"> **Last reviewed:** {REVIEWED_TODAY} (v0.92 plan)\n", "",
    )
    issues = _LINT.collect_issues(text, max_age_days=90, today=date.today())
    assert any("missing '> **Last reviewed:**" in i for i in issues)


def test_stale_doc_is_flagged() -> None:
    text = _doc_with_all_primitives(reviewed="2025-01-01")
    today = date.fromisoformat(REVIEWED_TODAY)
    issues = _LINT.collect_issues(text, max_age_days=90, today=today)
    assert any("'Last reviewed: 2025-01-01' is" in i for i in issues)
    assert any("days old" in i for i in issues)


def test_fresh_doc_within_window_passes() -> None:
    today = date.fromisoformat(REVIEWED_TODAY)
    boundary = (today - timedelta(days=89)).isoformat()
    text = _doc_with_all_primitives(reviewed=boundary)
    issues = _LINT.collect_issues(text, max_age_days=90, today=today)
    assert issues == []


def test_missing_primitive_is_flagged() -> None:
    text = _doc_with_all_primitives().replace(
        "### 4. Wiki — entity pages + contradictions\nBody…\n\n",
        "",
    )
    issues = _LINT.collect_issues(text, max_age_days=90, today=date.fromisoformat(REVIEWED_TODAY))
    assert any("missing canonical primitive heading: '### 4. Wiki'" in i for i in issues)


def test_malformed_date_is_flagged() -> None:
    text = _doc_with_all_primitives().replace(REVIEWED_TODAY, "not-a-date")
    issues = _LINT.collect_issues(text, max_age_days=90, today=date.today())
    # The regex requires \d{4}-\d{2}-\d{2}, so "not-a-date" makes the
    # whole match disappear; we expect the missing-line message instead.
    assert any("missing '> **Last reviewed:**" in i for i in issues)


def test_invalid_date_format_is_flagged() -> None:
    text = _doc_with_all_primitives().replace(REVIEWED_TODAY, "2026-13-99")
    issues = _LINT.collect_issues(text, max_age_days=90, today=date.today())
    assert any("unparseable 'Last reviewed' date" in i for i in issues)


def test_real_doc_passes_in_repo() -> None:
    """Sanity check that the actual committed doc is clean today."""
    repo_root = Path(__file__).resolve().parents[3]
    doc = repo_root / "docs" / "PRODUCT_STORY.md"
    if not doc.exists():
        pytest.skip("docs/PRODUCT_STORY.md not present in this checkout")
    text = doc.read_text(encoding="utf-8")
    issues = _LINT.collect_issues(text, max_age_days=90, today=date.today())
    assert issues == [], f"docs/PRODUCT_STORY.md drift gate fails: {issues}"
