#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Forbid module-level ``os.getenv`` for env vars users can mutate at runtime.

Graduates lesson ``tasks/lessons.md::"Module-level `os.getenv(\\"API_KEY\\")`
captures stale values"``. Module-level capture freezes the value at import
time. The setup wizard writes new values to .env and patches
``os.environ`` at runtime so live calls see them — but module-level
constants stay stale. Symptom (2026-04-22): every chat request kept using
the boot-time OPENROUTER_API_KEY even after the wizard reported the new
one valid.

Banned env vars (read at every use site, not module scope):

* ``OPENROUTER_API_KEY``     — user can rotate via setup wizard
* ``BIFROST_*``              — same path
* ``INTERNAL_LLM_PROVIDER``  — live-mutable per v0.93.9
* ``INTERNAL_LLM_MODEL``     — same
* ``EMBEDDINGS_PROVIDER``    — same
* ``RERANK_PROVIDER``        — same
* ``QUENCHFORGE_*_MODEL``    — same
* Anything ending in ``_API_KEY`` (generic catch-all)

Module-level capture of TRUE constants (e.g. URLs, enum values,
hard-coded defaults) is allowed. The lint scope is *only* module-scope
reads of the banned names; reads inside functions, classes, or
properties pass.

Usage::

    python3 scripts/lint-no-module-getenv-mutable.py
    python3 scripts/lint-no-module-getenv-mutable.py --warn-only
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src" / "mcp"

# Explicit allowlist of mutable env vars. The trailing ``_API_KEY`` substring
# match is a generic catch-all in addition to these.
_MUTABLE_VARS = {
    "OPENROUTER_API_KEY",
    "INTERNAL_LLM_PROVIDER",
    "INTERNAL_LLM_MODEL",
    "EMBEDDINGS_PROVIDER",
    "RERANK_PROVIDER",
    "QUENCHFORGE_EMBED_MODEL",
    "QUENCHFORGE_RERANK_MODEL",
    "QUENCHFORGE_CODE_EMBED_MODEL",
    "QUENCHFORGE_DEFAULT_MODEL",
    "OLLAMA_URL",
    "OLLAMA_DEFAULT_MODEL",
    "BIFROST_URL",
    "BIFROST_API_KEY",
}


def _is_mutable(name: str | None) -> bool:
    if name is None:
        return False
    return name in _MUTABLE_VARS or name.endswith("_API_KEY")


class _ModuleScopeGetenvFinder(ast.NodeVisitor):
    """Walks module-level only; descends into class/function bodies separately
    using a flag so we know whether we're still at module scope."""

    def __init__(self, path: Path):
        self.path = path
        self.findings: list[str] = []
        self._at_module_scope = True

    def _check_call(self, node: ast.Call) -> None:
        if not self._at_module_scope:
            return
        # Match os.getenv(...) and getenv(...) and os.environ[...]
        func = node.func
        target_name: str | None = None
        if isinstance(func, ast.Attribute) and func.attr == "getenv":
            if node.args:
                target_name = _const_str(node.args[0])
        elif isinstance(func, ast.Name) and func.id == "getenv":
            if node.args:
                target_name = _const_str(node.args[0])
        else:
            return
        if _is_mutable(target_name):
            self.findings.append(
                f"{self.path.relative_to(REPO_ROOT)}:{node.lineno}: "
                f"module-level os.getenv({target_name!r}) — read at each use site "
                "(this env var is user-mutable)"
            )

    def visit_Call(self, node: ast.Call) -> None:
        self._check_call(node)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if not self._at_module_scope:
            self.generic_visit(node)
            return
        # os.environ["X"] or os.environ.get("X")
        v = node.value
        if (
            isinstance(v, ast.Attribute)
            and v.attr == "environ"
            and isinstance(node.slice, ast.Constant)
        ):
            name = node.slice.value
            if isinstance(name, str) and _is_mutable(name):
                self.findings.append(
                    f"{self.path.relative_to(REPO_ROOT)}:{node.lineno}: "
                    f"module-level os.environ[{name!r}] — read at each use site"
                )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        prev = self._at_module_scope
        self._at_module_scope = False
        self.generic_visit(node)
        self._at_module_scope = prev

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        prev = self._at_module_scope
        self._at_module_scope = False
        self.generic_visit(node)
        self._at_module_scope = prev

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Class body is still "module scope-ish" — class-level attrs are
        # frozen at import time too. Keep scanning.
        self.generic_visit(node)


def _const_str(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--warn-only", action="store_true")
    args = ap.parse_args()

    findings: list[str] = []
    for py in SOURCE_ROOT.rglob("*.py"):
        if "__pycache__" in py.parts or "tests" in py.parts:
            continue
        # Skip the project-canonical settings.py — it deliberately captures
        # at module scope as the SOT for those values; per-call readers
        # downstream consult settings.X, not os.getenv directly.
        if py.name == "settings.py" and py.parent.name == "config":
            continue
        try:
            tree = ast.parse(py.read_text(), filename=str(py))
        except SyntaxError:
            continue
        finder = _ModuleScopeGetenvFinder(py)
        finder.visit(tree)
        findings.extend(finder.findings)

    if findings:
        print(
            "Module-level os.getenv of user-mutable env vars — graduates lessons.md L2:\n"
            "(setup wizard / live-mutable settings will fail to take effect)\n"
        )
        for f in findings:
            print(f"  {f}")
        return 0 if args.warn_only else 1

    print("no-module-getenv-mutable: all source modules compliant.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
