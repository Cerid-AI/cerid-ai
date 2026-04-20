#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Generate docs/ROUTER_REGISTRY.md from FastAPI route decorators.

AST-only: no imports of app.* modules, so the script runs in a plain
python:3.11-slim container without installing MCP runtime deps. Scans
every ``.py`` under ``src/mcp/app/routers/`` and ``src/mcp/routers/``
for APIRouter() instantiations and @router.<method>("/path") decorators.

Usage:
    python scripts/gen_router_registry.py                   # regenerate internal version
    python scripts/gen_router_registry.py --check           # CI drift guard
    python scripts/gen_router_registry.py --public --stdout # emit public-safe version to stdout

The ``--public`` filter drops every route whose module is internal-only
(``*_internal.py`` files and anything under ``src/mcp/routers/``, which
is billing-only per .sync-manifest.yaml). Used by ``scripts/sync-repos.py``
when propagating the registry to the public repo.

Parallel pattern to ``gen_env_example.py``. Add to CI as a drift job
so any new router forces the doc to regenerate.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import fnmatch
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = REPO_ROOT / "docs" / "ROUTER_REGISTRY.md"
MANIFEST_PATH = REPO_ROOT / ".sync-manifest.yaml"
SCAN_DIRS = [
    REPO_ROOT / "src" / "mcp" / "app" / "routers",
    # Post-Sprint F: the only router file remaining under src/mcp/routers/
    # is billing.py, which stays there per .sync-manifest.yaml so the
    # public-sync strip operation drops it from the OSS distribution.
    REPO_ROOT / "src" / "mcp" / "routers",
]

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _load_internal_only_patterns() -> list[str]:
    """Parse ``internal_only:`` entries from .sync-manifest.yaml.

    The manifest is the single source of truth for what ships to the public
    OSS distribution. Routers listed there must be tagged ``internal`` in
    the registry so the ``--public`` filter drops them. Minimal parser —
    just extracts the list-of-strings under the ``internal_only:`` key.
    No pyyaml dependency.
    """
    if not MANIFEST_PATH.exists():
        return []
    patterns: list[str] = []
    in_section = False
    for line in MANIFEST_PATH.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" ") and stripped.endswith(":"):
            in_section = stripped[:-1] == "internal_only"
            continue
        if in_section and stripped.startswith("- "):
            patterns.append(stripped[2:].strip())
    return patterns


def _matches_internal_pattern(relpath: str, patterns: list[str]) -> bool:
    """True if *relpath* matches any manifest internal_only pattern."""
    for pat in patterns:
        if "**" in pat:
            # Minimal ** support: 'dir/**' and 'a/**/x' shapes
            if pat.endswith("/**"):
                dir_prefix = pat[:-3]
                if relpath == dir_prefix or relpath.startswith(dir_prefix + "/"):
                    return True
            elif pat.startswith("**/"):
                if fnmatch.fnmatch(relpath, pat[3:]) or pat[3:] in relpath:
                    return True
        elif fnmatch.fnmatch(relpath, pat):
            return True
    return False


_INTERNAL_PATTERNS = _load_internal_only_patterns()


def _is_router_decorator(decorator: ast.expr) -> tuple[str, str | None] | None:
    """Return (METHOD, path) if ``decorator`` is ``@router.<method>("/path")``.

    Handles both the bare form and nested (the decorator call wrapping a
    value, e.g. ``@router.get("/x", response_model=Foo)``).
    """
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    # @router.<method>(...)  OR  @<obj>.<method>(...)
    if isinstance(func, ast.Attribute):
        method = func.attr.lower()
        if method not in _HTTP_METHODS:
            return None
        # Require the receiver to be a Name (typically "router") — reject
        # deeper chains to avoid matching unrelated .get() calls.
        if not isinstance(func.value, ast.Name):
            return None
        path = None
        if decorator.args:
            arg0 = decorator.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                path = arg0.value
        return method.upper(), path
    return None


def _extract_router_tags(tree: ast.AST) -> list[str]:
    """Pull out tags= from the APIRouter(...) call, if present."""
    tags: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            (isinstance(func, ast.Name) and func.id == "APIRouter")
            or (isinstance(func, ast.Attribute) and func.attr == "APIRouter")
        ):
            continue
        for kw in node.keywords:
            if kw.arg == "tags" and isinstance(kw.value, (ast.List, ast.Tuple)):
                for el in kw.value.elts:
                    if isinstance(el, ast.Constant) and isinstance(el.value, str):
                        tags.append(el.value)
    return tags


def _scan_file(path: Path, repo_root: Path) -> list[dict[str, str]]:
    """Return a list of route dicts for every @router decorator in ``path``."""
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    tags = _extract_router_tags(tree)
    tag_str = ",".join(tags) if tags else ""
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        rel = path
    # A route is internal-only when either its file has the ``_internal``
    # suffix OR it lives under ``src/mcp/routers/`` (post-Sprint F: the
    # whole directory is stripped from public; only ``billing.py`` lives
    # there, which is Pro/Enterprise-only).
    rel_str = str(rel).replace("\\", "/")
    internal = (
        "_internal" in path.stem
        or rel_str.startswith("src/mcp/routers/")
        or _matches_internal_pattern(rel_str, _INTERNAL_PATTERNS)
    )
    routes: list[dict[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            result = _is_router_decorator(dec)
            if result is None:
                continue
            method, route_path = result
            routes.append({
                "method": method,
                "path": route_path or "",
                "handler": node.name,
                "module": str(rel),
                "tags": tag_str,
                "internal": "internal" if internal else "",
            })
    return routes


def _collect() -> list[dict[str, str]]:
    all_routes: list[dict[str, str]] = []
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            all_routes.extend(_scan_file(p, REPO_ROOT))
    # Stable sort: module, path, method.
    all_routes.sort(key=lambda r: (r["module"], r["path"], r["method"]))
    return all_routes


def _render(routes: list[dict[str, str]], *, public: bool = False) -> str:
    if public:
        routes = [r for r in routes if r["internal"] != "internal"]
    intro_public = (
        "Every `@router.*` decorator shipped in the public (OSS Apache-2.0) distribution.\n"
        "Internal-only routers (billing, trading SDK, ops endpoints) are stripped and\n"
        "not documented here; see the internal repo for the full registry."
    )
    intro_internal = (
        "Every `@router.*` decorator under `src/mcp/app/routers/` and `src/mcp/routers/`.\n"
        "Internal-only routers are marked `internal` in the Build column and live in\n"
        "`*_internal.py` files (plus `src/mcp/routers/*`) stripped from the public distribution."
    )
    lines = [
        "# Router Registry",
        "",
        "> **Auto-generated** by `scripts/gen_router_registry.py`.",
        "> Regenerate with: `python scripts/gen_router_registry.py`.",
        "> CI drift gate: `python scripts/gen_router_registry.py --check`.",
        "",
        intro_public if public else intro_internal,
        "",
        f"**Total routes:** {len(routes)}",
        "",
        "| Method | Path | Handler | Module | Tags | Build |",
        "|--------|------|---------|--------|------|-------|",
    ]
    for r in routes:
        lines.append(
            "| {method} | `{path}` | `{handler}` | `{module}` | {tags} | {internal} |".format(**r)
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="CI drift mode — exit 1 on mismatch")
    ap.add_argument("--public", action="store_true", help="Filter internal-only routes (used by sync-repos.py)")
    ap.add_argument("--stdout", action="store_true", help="Emit to stdout instead of writing OUTPUT_FILE")
    args = ap.parse_args()

    rendered = _render(_collect(), public=args.public)

    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    if args.check:
        if not OUTPUT_FILE.exists():
            print(
                f"::error::{OUTPUT_FILE.relative_to(REPO_ROOT)} missing — "
                "run: python scripts/gen_router_registry.py",
                file=sys.stderr,
            )
            return 1
        current = OUTPUT_FILE.read_text()
        if current != rendered:
            diff = "\n".join(
                difflib.unified_diff(
                    current.splitlines(),
                    rendered.splitlines(),
                    fromfile=str(OUTPUT_FILE.relative_to(REPO_ROOT)),
                    tofile="expected",
                    lineterm="",
                )
            )
            print(
                f"::error::{OUTPUT_FILE.relative_to(REPO_ROOT)} is out of date — "
                "regenerate with: python scripts/gen_router_registry.py\n"
                + diff,
                file=sys.stderr,
            )
            return 1
        return 0

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(rendered)
    print(f"wrote {OUTPUT_FILE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
