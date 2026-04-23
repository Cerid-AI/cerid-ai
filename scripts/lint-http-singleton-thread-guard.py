#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lint for module-level ``httpx.AsyncClient`` / ``httpx.Client`` singletons.

The 2026-04-22 beta-test incident (part 2): a module-level
``_client = httpx.AsyncClient(...)`` singleton in ``llm_client.py``
was bound to whichever event loop first called ``_get_client()``. When
``core/utils/contextual.py::_run_coro_isolated`` spun up a throwaway
event loop inside a ThreadPoolExecutor worker, the singleton stuck to
that loop. The worker exited, the loop closed, but ``_client.is_closed``
stayed False — the next request on the main FastAPI loop reused the
stale singleton and got ``RuntimeError: Event loop is closed`` from
httpx internals.

The fix in ``llm_client.py`` was to gate the singleton on
``threading.current_thread() is threading.main_thread()`` and use a
one-shot client in worker threads. This linter prevents the bug from
returning by forbidding bare module-level constructors entirely:
all access must go through a thread-aware accessor function.

Detection
---------

Top-level statements only. Flags constructor calls assigned to a name:

    _client = httpx.AsyncClient(timeout=...)        # FLAG
    client: httpx.Client = httpx.Client()           # FLAG

Skipped:
* Type-only declarations:  ``_client: httpx.AsyncClient | None = None``
* Lines carrying ``# httpx-singleton-allowed: <reason>``.

Default mode is warn-only (exit 0). Promote to a hard failure with
``--strict`` once any legitimate exception sites are annotated.

Usage:
    python scripts/lint-http-singleton-thread-guard.py src/mcp/             # CI (warn)
    python scripts/lint-http-singleton-thread-guard.py --strict src/mcp/    # promote
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

_OPT_OUT_TOKEN = "httpx-singleton-allowed"
_HTTPX_CLIENT_CLASSES = {"AsyncClient", "Client"}


class Singleton(NamedTuple):
    file: str
    lineno: int
    name: str


def _is_httpx_client_call(call: ast.expr) -> bool:
    """True for ``httpx.AsyncClient(...)`` / ``httpx.Client(...)`` calls."""
    if not isinstance(call, ast.Call):
        return False
    f = call.func
    return (
        isinstance(f, ast.Attribute)
        and f.attr in _HTTPX_CLIENT_CLASSES
        and isinstance(f.value, ast.Name)
        and f.value.id == "httpx"
    )


def _target_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    if isinstance(node, ast.AnnAssign):
        return [node.target.id] if isinstance(node.target, ast.Name) else []
    out: list[str] = []
    for tgt in node.targets:
        if isinstance(tgt, ast.Name):
            out.append(tgt.id)
    return out


def check_file(path: Path) -> list[Singleton]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    lines = source.splitlines()
    found: list[Singleton] = []
    for node in tree.body:  # top-level only
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if node.value is None or not _is_httpx_client_call(node.value):
            continue
        if 0 < node.lineno <= len(lines) and _OPT_OUT_TOKEN in lines[node.lineno - 1]:
            continue
        for name in _target_names(node):
            found.append(Singleton(str(path), node.lineno, name))
    return found


def iter_py_files(root: Path) -> Iterator[Path]:
    for p in root.rglob("*.py"):
        parts = set(p.parts)
        if parts & {"__pycache__", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache"}:
            continue
        yield p


def format_singleton(s: Singleton) -> str:
    return (
        f"{s.file}:{s.lineno}: [httpx-module-singleton] "
        f"{s.name} = httpx.<Client>(...) at module scope — bind a thread-aware accessor instead"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="File or directory paths to scan")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on findings (default: warn-only).",
    )
    args = parser.parse_args(argv)

    findings: list[Singleton] = []
    for raw_path in args.paths:
        path = Path(raw_path)
        if path.is_file():
            findings.extend(check_file(path))
        elif path.is_dir():
            for py in iter_py_files(path):
                findings.extend(check_file(py))

    if not findings:
        return 0

    stream = sys.stderr if args.strict else sys.stdout
    label = "FAIL" if args.strict else "WARN"
    print(
        f"\n[{label}] {len(findings)} module-level httpx client singleton(s) without a "
        "thread-aware accessor. These get poisoned by throwaway event loops in worker threads "
        "(see tasks/lessons.md → 'Module-level httpx singletons get poisoned').",
        file=stream,
    )
    for s in findings:
        print(format_singleton(s), file=stream)
    print(
        f"\nTo silence: add `# {_OPT_OUT_TOKEN}: <reason>` to the line. "
        "To fix: route through a `_get_client()` helper that gates on "
        "`threading.current_thread() is threading.main_thread()` (see core/utils/llm_client.py).",
        file=stream,
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
