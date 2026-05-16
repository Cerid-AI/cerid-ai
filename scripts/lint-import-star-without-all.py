#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Forbid ``from x import *`` in package __init__.py without an `__all__`.

Graduates lesson ``tasks/lessons.md::"`import *` skips underscore-prefixed
names"``. Python's ``import *`` silently skips names that start with an
underscore unless the source module declares an ``__all__`` listing them.
The bug surfaced as `_strip_html_tags` / `_strip_rtf` being absent after
the parsers package split, even though both were re-exported.

Rule: any ``from <module> import *`` inside an ``__init__.py`` is
suspect unless THAT __init__.py declares an ``__all__``. The check is
package-scoped because a top-level ``from foo import *`` outside a
package doesn't have the underscore-skip foot-gun in the same way.

Usage::

    python3 scripts/lint-import-star-without-all.py            # block
    python3 scripts/lint-import-star-without-all.py --warn-only
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = [REPO_ROOT / "src" / "mcp"]


def _is_documented_bridge(src: str) -> bool:
    """A documented "Re-export bridge" pattern opts out of the __all__ check.

    These shims exist purely for backward-compat with pre-Phase-C import
    paths (``from mcp.routers import ...``); the canonical source
    package documents its own ``__all__``. The marker requires the
    string ``Re-export bridge`` in the first 10 lines so the opt-out
    is a deliberate choice, not accidental.

    Convention documented in ``docs/CONVENTIONS.md::Re-export bridges``.
    """
    head = "\n".join(src.splitlines()[:10])
    return "Re-export bridge" in head


def _check_init(init: Path) -> list[str]:
    """Return findings for one __init__.py."""
    src = init.read_text()
    if _is_documented_bridge(src):
        return []
    tree = ast.parse(src, filename=str(init))

    has_all = any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
        for node in tree.body
    )
    if has_all:
        return []

    import_stars = [
        node for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "*" for alias in node.names)
    ]
    return [
        f"{init.relative_to(REPO_ROOT)}:{node.lineno}: "
        f"`from {node.module} import *` without `__all__` in this __init__.py "
        "— underscore-prefixed names silently skipped. If this is intentional "
        "back-compat re-export, prepend `Re-export bridge` to the file header "
        "(see docs/CONVENTIONS.md::Re-export bridges)."
        for node in import_stars
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--warn-only", action="store_true",
                    help="Print findings but exit 0 (CI roll-out mode)")
    args = ap.parse_args()

    findings: list[str] = []
    for root in SOURCE_ROOTS:
        for init in root.rglob("__init__.py"):
            if "__pycache__" in init.parts or "tests" in init.parts:
                continue
            try:
                findings.extend(_check_init(init))
            except SyntaxError as exc:
                # Skip — ruff/mypy already catches syntax errors
                print(f"  skipped (syntax error): {init}: {exc}", file=sys.stderr)

    if findings:
        print("`import *` without `__all__` — graduates lessons.md L1:\n")
        for f in findings:
            print(f"  {f}")
        return 0 if args.warn_only else 1

    print("import-*-without-all: all __init__.py files compliant.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
