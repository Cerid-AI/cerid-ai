#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Lint for hardcoded model-id string literals in src/mcp/.

Model ids pinned as string literals at call sites bypass the catalog's
weekly auto-refresh and accumulate stale version debt. Graduates the
RC-6 finding from tasks/2026-06-12-rag-quality-program-plan.md §2.2.

Detected patterns (string literals in code only; not comments or docstrings):

  Provider-prefixed forms
    openai/<anything>
    anthropic/<anything>
    meta-llama/<anything>
    x-ai/<anything>
    google/gemini<anything>
    openrouter/<anything>

  Bare family ids (version-bearing literals that can drift)
    gpt-4o* (e.g. gpt-4o, gpt-4o-mini, gpt-4o-2024-11)
    claude-*-N.N  (any claude- id containing a major.minor version)
    llama-3*      (e.g. llama-3.2-3b, llama-3.3-70b)
    grok-N*       (e.g. grok-4, grok-4.20)

Allowed registry locations (hardcoded ids are legitimate there):

  config/providers.py          — canonical provider definitions
  config/settings.py           — model-family defaults (dotted-version policy)
  core/routing/model_catalog.py — catalog and resolve_latest
  core/routing/smart_router.py  — tier tables (FREE/CHEAP/CAPABLE/…)
  core/routing/model_compat.py  — compat shims between naming schemes
  core/routing/model_providers.py — provider routing tables
  app/routers/models.py        — DEFAULT_ASSIGNMENTS for the weekly refresh job
  utils/model_registry.py      — per-provider model lists
  utils/metrics.py             — pricing tables
  core/processor/cost.py       — cost tables
  core/agents/audit.py         — audit-log pricing tables
  tests/**                     — test fixtures / parametrize ids

Default mode is warn-only (exit 0). Promote to a hard failure with
``--strict`` once the existing bypass sites (tracked as 2.2 bypass cleanup)
are remediated or annotated.

Per-line inline suppression:
    # model-literal-allowed: <reason>

Usage:
    python scripts/lint-no-hardcoded-models.py src/mcp/             # CI (warn)
    python scripts/lint-no-hardcoded-models.py --strict src/mcp/    # promote
    python scripts/lint-no-hardcoded-models.py --warn-only src/mcp/ # explicit warn (same as default)
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Patterns that identify a string as a model-id literal
# ---------------------------------------------------------------------------

# Provider-prefixed: prefix + at least one non-slash character required.
# "openrouter/" alone (used as a prefix sentinel in startswith/removeprefix
# calls) is a string manipulation helper, not a model id — it must not fire.
_PROVIDER_PREFIX_RE = re.compile(
    r"^(?:"
    r"openai/[^/]"               # openai/<model-name-starts>
    r"|anthropic/[^/]"           # anthropic/<…>
    r"|meta-llama/[^/]"          # meta-llama/<…>
    r"|x-ai/[^/]"                # x-ai/<…>
    r"|google/gemini[^\s]"       # google/gemini<…>  (gemini- or gemini2 etc.)
    r"|openrouter/[^/]"          # openrouter/<provider>/… (but not bare "openrouter/")
    r")"
)

# Bare family patterns (applied to the full string value)
_BARE_FAMILY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^gpt-4o"),                           # gpt-4o, gpt-4o-mini, gpt-4o-2024-…
    re.compile(r"^claude-[a-z].*-\d+\.\d+"),          # claude-sonnet-4.6, claude-opus-4.0
    re.compile(r"^llama-3"),                           # llama-3.2-3b, llama-3.3-70b-instruct
    re.compile(r"^grok-\d"),                           # grok-4, grok-4.20, grok-4.20-multi-agent
]


def _is_model_literal(value: str) -> bool:
    """Return True if ``value`` looks like a model-id string literal."""
    if _PROVIDER_PREFIX_RE.match(value):
        return True
    return any(p.match(value) for p in _BARE_FAMILY_PATTERNS)


# ---------------------------------------------------------------------------
# Allowed registry modules (path suffixes, matched against str(path))
# ---------------------------------------------------------------------------

_ALLOWED_SUFFIXES: tuple[str, ...] = (
    # Provider & catalog registries
    "config/providers.py",
    "config/settings.py",
    "core/routing/model_catalog.py",
    "core/routing/smart_router.py",
    "core/routing/model_compat.py",
    "core/routing/model_providers.py",
    # Assignment & role machinery
    "app/routers/models.py",
    "utils/model_registry.py",
    # Pricing / cost tables
    "utils/metrics.py",
    "core/processor/cost.py",
    "core/agents/audit.py",
)

_SUPPRESS_TOKEN = "model-literal-allowed"


def _path_is_allowed(path: Path) -> bool:
    """Return True if this file is an allowed registry location."""
    path_str = str(path).replace("\\", "/")
    for suffix in _ALLOWED_SUFFIXES:
        if path_str.endswith(suffix):
            return True
    # tests/**
    parts = path.parts
    return "tests" in parts


# ---------------------------------------------------------------------------
# Docstring detection helpers (we skip string literals used as docstrings)
# ---------------------------------------------------------------------------

def _collect_docstring_nodes(tree: ast.Module) -> set[int]:
    """Return the set of linenos for string nodes that are module/class/func docstrings."""
    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body  # type: ignore[attr-defined]
        if body and isinstance(body[0], ast.Expr):
            val = body[0].value
            # Python 3.8+: ast.Constant; pre-3.8: ast.Str — both land here
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                docstring_lines.add(val.lineno)
    return docstring_lines


# ---------------------------------------------------------------------------
# Per-line suppression (reads the raw source lines)
# ---------------------------------------------------------------------------

def _line_is_suppressed(source_lines: list[str], lineno: int) -> bool:
    """Return True if the source line carries the inline suppression token."""
    if lineno <= 0 or lineno > len(source_lines):
        return False
    return _SUPPRESS_TOKEN in source_lines[lineno - 1]


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

class Finding(NamedTuple):
    file: str
    lineno: int
    value: str


def _format_finding(f: Finding) -> str:
    return (
        f"{f.file}:{f.lineno}: [hardcoded-model] "
        f"model-id literal {f.value!r} — use a role/tier constant or registry lookup"
    )


# ---------------------------------------------------------------------------
# File-level checker
# ---------------------------------------------------------------------------

def check_file(path: Path) -> list[Finding]:
    """Return all hardcoded-model findings in ``path``."""
    if _path_is_allowed(path):
        return []
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    source_lines = source.splitlines()
    docstring_linenos = _collect_docstring_nodes(tree)
    findings: list[Finding] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        if not isinstance(node.value, str):
            continue
        lineno = node.lineno
        if lineno in docstring_linenos:
            continue
        if not _is_model_literal(node.value):
            continue
        if _line_is_suppressed(source_lines, lineno):
            continue
        findings.append(Finding(str(path), lineno, node.value))

    return findings


# ---------------------------------------------------------------------------
# Filesystem walk
# ---------------------------------------------------------------------------

def iter_py_files(root: Path) -> Iterator[Path]:
    for p in root.rglob("*.py"):
        parts = set(p.parts)
        if parts & {
            "__pycache__", ".venv", "venv", "node_modules",
            ".mypy_cache", ".pytest_cache", ".ruff_cache",
        }:
            continue
        yield p


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="File or directory paths to scan")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on findings (default: warn-only, exit 0).",
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Explicitly warn-only (exit 0). This is already the default; "
             "the flag exists for Makefile documentation clarity.",
    )
    args = parser.parse_args(argv)

    findings: list[Finding] = []
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
        f"\n[{label}] {len(findings)} hardcoded model-id literal(s) outside registry modules.",
        file=stream,
    )
    for f in sorted(findings, key=lambda x: (x.file, x.lineno)):
        print(_format_finding(f), file=stream)
    print(
        f"\nTo silence a line: add `# {_SUPPRESS_TOKEN}: <reason>` to the end of the line.",
        file=stream,
    )
    print(
        "To fix: replace the literal with a role constant, smart-router tier entry, "
        "or a registry lookup (see docs/CONVENTIONS.md §model-pinning-policy).",
        file=stream,
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
