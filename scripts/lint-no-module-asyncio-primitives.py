#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Forbid asyncio Semaphores, Locks, Events and Conditions at module or class scope.

Such a primitive binds to the first event loop that has to wait on it, and any
other loop that then waits on it raises "is bound to a different event loop".
The server runs several loops (uvicorn's, async_bridge's, asyncio.run in sync
callers, and one per pytest test). Two of these failed verification in public
CI (#531), and thirteen more were found afterwards. Declare the primitive with
``core.utils.loop_local.LoopLocal(lambda: asyncio.Semaphore(n))`` and take it
with ``.get()`` inside the coroutine.

Usage::

    python3 scripts/lint-no-module-asyncio-primitives.py [PATH ...]
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO_ROOT / "src" / "mcp"
PRIMITIVES = {"Semaphore", "BoundedSemaphore", "Lock", "Event", "Condition"}
SKIP_PARTS = {"tests", ".venv", "node_modules", "__pycache__"}


def _is_primitive_call(node: ast.AST | None, aliases: set[str]) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id == "asyncio" and func.attr in PRIMITIVES:
            return f"asyncio.{func.attr}"
    if isinstance(func, ast.Name) and func.id in aliases:
        return func.id
    return None


def _scope_statements(body: list[ast.stmt]):
    """Statements that run at this scope: descends into if/try/with blocks and
    class bodies, never into functions or lambdas."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, ast.ClassDef):
            yield from _scope_statements(stmt.body)
        elif isinstance(stmt, (ast.If, ast.For, ast.While, ast.With, ast.AsyncWith)):
            yield from _scope_statements(stmt.body)
            yield from _scope_statements(getattr(stmt, "orelse", []))
        elif isinstance(stmt, ast.Try):
            for block in (stmt.body, stmt.orelse, stmt.finalbody):
                yield from _scope_statements(block)
            for handler in stmt.handlers:
                yield from _scope_statements(handler.body)


def check_source(source: str, name: str) -> list[str]:
    tree = ast.parse(source, filename=name)
    aliases = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "asyncio"
        for alias in node.names
        if alias.name in PRIMITIVES
    }
    findings = []
    for stmt in _scope_statements(tree.body):
        value = stmt.value if isinstance(stmt, (ast.Assign, ast.AnnAssign)) else None
        kind = _is_primitive_call(value, aliases)
        if kind:
            findings.append(
                f"{name}:{stmt.lineno}: {kind}() at module or class scope binds to one event loop; "
                "use core.utils.loop_local.LoopLocal(lambda: ...) and .get()"
            )
    return findings


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv] or [DEFAULT_ROOT]
    findings: list[str] = []
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            if SKIP_PARTS & set(path.parts):
                continue
            try:
                rel = path.relative_to(REPO_ROOT)
            except ValueError:
                rel = path
            findings.extend(check_source(path.read_text(encoding="utf-8"), str(rel)))
    for line in findings:
        print(line)
    if findings:
        print(f"{len(findings)} module- or class-scope asyncio primitive(s)", file=sys.stderr)
        return 1
    print("no module- or class-scope asyncio primitives")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
