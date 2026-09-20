#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Gate G-A — no two routers may register the same ``(method, path)``.

The mechanism this closes (2026-09-02 audit): FastAPI resolves an incoming
request against ``app.routes`` in registration order, so the router included
FIRST silently wins. The OpenAPI schema, by contrast, is built into a dict
keyed by path then method, so the router included LAST overwrites the entry
and its ``response_model`` is what gets published. The result is a documented
contract that nothing serves, with no error, no warning, and no failing test
anywhere in the suite — the SDK is generated from the loser's schema while
the winner's handler answers the call.

There is nothing in FastAPI, in the router-registry drift gate, or in the
route-response-model gate that can see this: each of them looks at one route
at a time. Only a cross-module collision check can.

Detection
---------
AST-only (no ``app.*`` imports, same constraint as ``gen_router_registry.py``
so the gate runs in a bare ``python:3.11-slim``). For every module under
``src/mcp/app/routers/`` and ``src/mcp/routers/``:

  * bind each module-level ``<var> = APIRouter(prefix="/p")`` to its prefix
    (a module may declare more than one router — ``agent_console.py`` does);
  * for every ``@<var>.<method>("/path")`` decorator, the registered route is
    ``prefix + path``.

Paths are compared after normalising each path parameter to ``{}``, because
that is how Starlette compares them: ``/artifacts/{id}`` and
``/artifacts/{artifact_id}`` are the SAME route to the router, differing only
in the name bound to the handler argument. A trailing slash is stripped for
the same reason.

Two decorators in the SAME module are also a collision (the second shadows
the first in exactly the same way), so this is not merely a cross-file check.

Allowlist
---------
``scripts/route_registered_once_allowlist.txt``, keyed ``METHOD <normalised
path>`` with a one-line reason. Shrink-only: an entry that is no longer a
collision is stale and fails ``--check``, so the allowlist cannot outlive the
collisions it grandfathers.

Usage:
    python scripts/lint-route-registered-once.py            # report (exit 0)
    python scripts/lint-route-registered-once.py --check    # CI gate
    python scripts/lint-route-registered-once.py --update   # reseed allowlist
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "route_registered_once_allowlist.txt"
SCAN_DIRS = [
    REPO_ROOT / "src" / "mcp" / "app" / "routers",
    REPO_ROOT / "src" / "mcp" / "routers",
]

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_PATH_PARAM_RE = re.compile(r"\{[^}]*\}")


class Site(NamedTuple):
    """One ``@router.<method>("/path")`` decorator."""

    file: str
    lineno: int
    handler: str
    raw_path: str

    def describe(self) -> str:
        return f"{self.file}:{self.lineno} {self.handler}() -> '{self.raw_path}'"


class Collision(NamedTuple):
    method: str
    path: str  # normalised
    sites: tuple[Site, ...]

    def key(self) -> str:
        return f"{self.method} {self.path}"


def normalise_path(path: str) -> str:
    """Starlette-equivalent path identity: parameter NAMES do not matter."""
    collapsed = _PATH_PARAM_RE.sub("{}", path)
    stripped = collapsed.rstrip("/")
    return stripped or "/"


def _router_prefixes(tree: ast.Module) -> dict[str, str]:
    """``{var_name: prefix}`` for every module-level ``APIRouter(...)``."""
    prefixes: dict[str, str] = {}
    for node in tree.body:
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
        ):
            continue
        func = node.value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "APIRouter":
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                prefix = kw.value.value
        prefixes[node.targets[0].id] = prefix
    return prefixes


def _route_decorator(dec: ast.expr, prefixes: dict[str, str]) -> tuple[str, str] | None:
    """Return ``(METHOD, prefix+path)`` for a known router's decorator."""
    if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
        return None
    method = dec.func.attr.lower()
    if method not in _HTTP_METHODS:
        return None
    if not isinstance(dec.func.value, ast.Name) or dec.func.value.id not in prefixes:
        return None
    if not dec.args or not isinstance(dec.args[0], ast.Constant):
        return None
    path = dec.args[0].value
    if not isinstance(path, str):
        return None
    return method.upper(), prefixes[dec.func.value.id] + path


def collect_routes(
    scan_dirs: list[Path] | None = None, rel_to: Path | None = None
) -> dict[tuple[str, str], list[Site]]:
    """``{(METHOD, normalised path): [Site, ...]}`` over *scan_dirs*.

    The two parameters exist so the plant-fault test can point the same
    detector at a synthetic tree instead of the repo.
    """
    scan_dirs = SCAN_DIRS if scan_dirs is None else scan_dirs
    rel_to = REPO_ROOT if rel_to is None else rel_to
    routes: dict[tuple[str, str], list[Site]] = defaultdict(list)
    for base in scan_dirs:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            rel = str(path.relative_to(rel_to))
            for method, full in _scan_tree(tree, _router_prefixes(tree)):
                fn, lineno, raw = full
                routes[(method, normalise_path(raw))].append(Site(rel, lineno, fn, raw))
    return routes


def _scan_tree(
    tree: ast.Module, prefixes: dict[str, str]
) -> list[tuple[str, tuple[str, int, str]]]:
    out: list[tuple[str, tuple[str, int, str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            hit = _route_decorator(dec, prefixes)
            if hit is None:
                continue
            method, full_path = hit
            out.append((method, (node.name, node.lineno, full_path)))
    return out


def collect_all(
    scan_dirs: list[Path] | None = None, rel_to: Path | None = None
) -> list[Collision]:
    collisions = [
        Collision(method, path, tuple(sorted(sites)))
        for (method, path), sites in collect_routes(scan_dirs, rel_to).items()
        if len(sites) > 1
    ]
    return sorted(collisions, key=lambda c: c.key())


def _load_allowlist() -> dict[str, str]:
    allow: dict[str, str] = {}
    if not ALLOWLIST_PATH.exists():
        return allow
    for raw in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "#" not in line:
            continue
        key, _, reason = line.partition("#")
        allow[key.strip()] = reason.strip()
    return allow


_HEADER = """\
# route_registered_once_allowlist.txt — grandfathered (method, path) collisions
# Enforced by scripts/lint-route-registered-once.py --check (gate G-A,
# tasks/2026-09-02-audit-findings.json).
#
# Format: <METHOD> <normalised path>  # <one-line reason citing a finding id>
# The path is normalised: every {param} becomes {} because Starlette matches
# on shape, not on the name bound to the handler argument.
#
# Shrink-only — removing one router's decorator removes its line; the gate
# fails on any stale entry (no longer a collision) or any new collision.
#
# Regenerate (preserves reasons for still-live keys) with:
#   python scripts/lint-route-registered-once.py --update
"""


def _write_allowlist(collisions: list[Collision]) -> None:
    existing = _load_allowlist()
    lines = [_HEADER.rstrip("\n"), ""]
    for c in collisions:
        lines.append(f"{c.key()}  # {existing.get(c.key(), 'TODO: cite an audit finding id')}")
    ALLOWLIST_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_collision(c: Collision) -> str:
    sites = "\n".join(f"      {s.describe()}" for s in c.sites)
    return f"{c.key()} registered {len(c.sites)}x:\n{sites}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--check", action="store_true", help="CI gate: exit 1 on new or stale entries")
    ap.add_argument("--update", action="store_true", help="Reseed the allowlist to the current hit set")
    args = ap.parse_args(argv)

    current = collect_all()

    if args.update:
        _write_allowlist(current)
        print(f"wrote {ALLOWLIST_PATH.relative_to(REPO_ROOT)} ({len(current)} collisions)")
        return 0

    allow = _load_allowlist()
    by_key = {c.key(): c for c in current}
    new_hits = sorted(k for k in by_key if k not in allow)
    stale = sorted(k for k in allow if k not in by_key)

    if not args.check:
        print(
            f"[route-registered-once] {len(by_key)} collision(s); {len(allow)} allowlisted; "
            f"{len(new_hits)} new; {len(stale)} stale."
        )
        return 0

    if not new_hits and not stale:
        print(
            f"[route-registered-once] OK — {len(by_key)} collision(s), all grandfathered "
            "(allowlist must only shrink)."
        )
        return 0

    if new_hits:
        print(
            f"\n::error::[route-registered-once] {len(new_hits)} NEW (method, path) collision(s). "
            "Two routers register the same route: the one included FIRST in main.py serves the "
            "request, the one included LAST publishes its response_model to OpenAPI and the SDK. "
            "Give one of them a distinct path, or (if reviewed as pre-existing debt) add an "
            "allowlist entry citing an audit finding id.",
            file=sys.stderr,
        )
        for k in new_hits:
            print(f"  NEW  {format_collision(by_key[k])}", file=sys.stderr)
    if stale:
        print(
            f"\n::error::[route-registered-once] {len(stale)} stale allowlist entr(y/ies) — no "
            "longer a collision. Run `python scripts/lint-route-registered-once.py --update` to "
            "ratchet the allowlist down.",
            file=sys.stderr,
        )
        for k in stale:
            print(f"  STALE {k}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
