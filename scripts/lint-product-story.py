#!/usr/bin/env python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Drift gate for ``docs/PRODUCT_STORY.md``.

Asserts that the canonical product narrative stays in sync with what
the codebase actually ships. The gate runs in CI and fails the build
when any of the following is true:

* The file does not exist.
* The header is missing a ``> **Last reviewed:** YYYY-MM-DD`` line.
* The last-reviewed date is more than ``--max-age-days`` (default 90)
  in the past.
* Any of the five canonical primitive headings is missing.

The primitive list is the one promised in the v0.92 cohesion release
notes and is intentionally hard-coded here. Adding a new primitive
requires a deliberate edit to both this file and PRODUCT_STORY.md;
removing one requires a release-note explaining the regression.

Exit codes
----------
* 0 — gate passes (or ``--report-only`` flag is set and only soft issues
  are present).
* 1 — gate fails.

Usage
-----
::

    python scripts/lint-product-story.py
    python scripts/lint-product-story.py --max-age-days 180
    python scripts/lint-product-story.py --report-only
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

DEFAULT_DOC_PATH = Path("docs/PRODUCT_STORY.md")
DEFAULT_MAX_AGE_DAYS = 90

# Canonical primitive headings. Order matches the v0.92 release notes.
# Update both this list and PRODUCT_STORY.md in the same commit when the
# narrative changes.
PRIMITIVE_HEADINGS: tuple[str, ...] = (
    "### 1. Verification",
    "### 2. TrustScore",
    "### 3. Narrative Loop",
    "### 4. Wiki",
    "### 5. Background Processor",
)

# Permissive on surrounding whitespace and trailing parenthetical, e.g.
# ``> **Last reviewed:** 2026-05-10 (v0.92 plan)``.
_REVIEWED_RE = re.compile(
    r"^\s*>?\s*\*\*Last reviewed:\*\*\s+(\d{4}-\d{2}-\d{2})",
    re.MULTILINE,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--doc",
        type=Path,
        default=DEFAULT_DOC_PATH,
        help=f"Path to the product-story doc (default: {DEFAULT_DOC_PATH}).",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        help=f"Fail when 'Last reviewed' is older than this many days (default: {DEFAULT_MAX_AGE_DAYS}).",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Print findings but always exit 0 (soft-warn mode for new CI gates).",
    )
    return parser.parse_args(argv)


def collect_issues(text: str, max_age_days: int, today: date) -> list[str]:
    """Return human-readable failure messages; empty list means clean."""
    issues: list[str] = []
    match = _REVIEWED_RE.search(text)
    if not match:
        issues.append(
            "missing '> **Last reviewed:** YYYY-MM-DD' line in the header block",
        )
    else:
        try:
            reviewed = date.fromisoformat(match.group(1))
        except ValueError:
            issues.append(
                f"unparseable 'Last reviewed' date: {match.group(1)!r}",
            )
        else:
            age = today - reviewed
            if age > timedelta(days=max_age_days):
                issues.append(
                    f"'Last reviewed: {reviewed.isoformat()}' is {age.days} days old "
                    f"(max allowed: {max_age_days}). Walk the doc, refresh primitive "
                    f"descriptions to match shipped behaviour, and bump the date.",
                )

    for heading in PRIMITIVE_HEADINGS:
        if heading not in text:
            issues.append(f"missing canonical primitive heading: {heading!r}")

    return issues


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    doc_path: Path = args.doc
    if not doc_path.exists():
        msg = f"PRODUCT_STORY: missing canonical doc at {doc_path}"
        print(msg, file=sys.stderr)
        return 0 if args.report_only else 1

    text = doc_path.read_text(encoding="utf-8")
    issues = collect_issues(text, max_age_days=args.max_age_days, today=date.today())
    if not issues:
        print(f"PRODUCT_STORY: OK ({doc_path}) — all 5 primitives present, last review within window.")
        return 0

    print(f"PRODUCT_STORY: {len(issues)} issue(s) in {doc_path}:", file=sys.stderr)
    for issue in issues:
        print(f"  - {issue}", file=sys.stderr)
    print(
        "\nTo fix: walk docs/PRODUCT_STORY.md, ensure each primitive section is "
        "current, and update the 'Last reviewed:' header in the same commit.",
        file=sys.stderr,
    )
    return 0 if args.report_only else 1


if __name__ == "__main__":
    sys.exit(main())
