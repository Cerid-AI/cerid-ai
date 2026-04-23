#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lint for module-level ``os.getenv(...)`` captures (Phase 2.5 — lessons.md).

The 2026-04-22 beta-test incident: ``app/routers/chat.py:32`` carried

    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

at module scope. The setup wizard rotated the key at runtime and patched
``os.environ``, but the module-level constant was already frozen at
import time, so every chat request used the stale boot-time key — making
the bug look like "chats are failing" when the model-router was correct.

Rule of thumb (from ``tasks/lessons.md``): if a value can change at
runtime (env var written by wizard, config reload, etc.), reading it
once at import time is a bug. Module-level capture is fine for TRUE
constants (URLs, enum values, hard-coded defaults) — never for anything
a user can edit.

Detection
---------

Top-level statements only. Flags:

    NAME = os.getenv("X", "default")
    NAME: str = os.getenv("X")

Skipped:
* ``src/mcp/config/`` — the canonical settings module pattern;
  defaults are documented constants, not user-editable.
* Lines carrying ``# env-capture-allowed: <reason>``.

Default mode is warn-only (exit 0). Promote to a hard failure with
``--strict`` once the existing call sites are remediated or annotated.

Usage:
    python scripts/lint-no-module-env-captures.py src/mcp/             # CI (warn)
    python scripts/lint-no-module-env-captures.py --strict src/mcp/    # promote
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

_OPT_OUT_TOKEN = "env-capture-allowed"
_SKIP_DIR_PARTS = {"config"}  # src/mcp/config/* is the canonical settings module


class Capture(NamedTuple):
    file: str
    lineno: int
    name: str


def _is_os_getenv(call: ast.expr) -> bool:
    if not isinstance(call, ast.Call):
        return False
    f = call.func
    return (
        isinstance(f, ast.Attribute)
        and f.attr == "getenv"
        and isinstance(f.value, ast.Name)
        and f.value.id == "os"
    )


def _target_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    if isinstance(node, ast.AnnAssign):
        return [node.target.id] if isinstance(node.target, ast.Name) else []
    out: list[str] = []
    for tgt in node.targets:
        if isinstance(tgt, ast.Name):
            out.append(tgt.id)
    return out


def check_file(path: Path) -> list[Capture]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    lines = source.splitlines()
    captures: list[Capture] = []
    for node in tree.body:  # top-level only
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if node.value is None or not _is_os_getenv(node.value):
            continue
        if 0 < node.lineno <= len(lines) and _OPT_OUT_TOKEN in lines[node.lineno - 1]:
            continue
        for name in _target_names(node):
            captures.append(Capture(str(path), node.lineno, name))
    return captures


def iter_py_files(root: Path) -> Iterator[Path]:
    for p in root.rglob("*.py"):
        parts = set(p.parts)
        if parts & {"__pycache__", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache"}:
            continue
        if parts & _SKIP_DIR_PARTS:
            continue
        yield p


def format_capture(c: Capture) -> str:
    return f"{c.file}:{c.lineno}: [module-env-capture] {c.name} = os.getenv(...) at module scope"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="File or directory paths to scan")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on findings (default: warn-only).",
    )
    args = parser.parse_args(argv)

    captures: list[Capture] = []
    for raw_path in args.paths:
        path = Path(raw_path)
        if path.is_file():
            captures.extend(check_file(path))
        elif path.is_dir():
            for py in iter_py_files(path):
                captures.extend(check_file(py))

    if not captures:
        return 0

    stream = sys.stderr if args.strict else sys.stdout
    label = "FAIL" if args.strict else "WARN"
    print(
        f"\n[{label}] {len(captures)} module-level os.getenv capture(s) outside src/mcp/config/. "
        f"Values that can change at runtime (API keys, secrets) must be read at use site, not import time.",
        file=stream,
    )
    for c in captures:
        print(format_capture(c), file=stream)
    print(
        f"\nTo silence: add `# {_OPT_OUT_TOKEN}: <reason>` to the line. "
        "To fix: read os.getenv() inline at each use site, or wrap in a small accessor function.",
        file=stream,
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
