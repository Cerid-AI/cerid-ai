#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract gate: every JSON @router route must declare a response_model.

Graduates cluster "Enforcement-coverage inversion" from
tasks/2026-06-29-rag-api-systemic-audit.md. The systemic disease is that
untyped endpoints accrete with zero CI signal — the only typing gate
(sdk-openapi-drift) covers /sdk/v1/* only, so /agent/query and ~half the
surface have no response_model and no contract. This AST gate closes that
hole for the WHOLE router surface.

Mechanism (the ratchet):

* AST-scan every ``@router.<method>("/path")`` under src/mcp/app/routers/
  and src/mcp/routers/ (no app.* import needed — runs in a slim container).
* A route is *untyped* when its decorator has no ``response_model=`` AND the
  handler does not return a custom Response (Response / JSONResponse / streaming
  / file / redirect / plain / html / SSE) AND it is not a no-body status
  (204/205/304) — those legitimately set their own body or have none.
* The grandfather allowlist (``route_response_model_allowlist.txt``) holds
  the routes that are untyped TODAY. The gate fails when:
    - a NEW untyped route appears that is not allowlisted, OR
    - an allowlisted route is now typed/removed (a stale entry) — forcing
      the allowlist to monotonically SHRINK toward zero by 1.0.

So existing debt does not break CI, but no new untyped endpoint can land
and the debt can only go down. Same harness shape as
scripts/lint-no-hardcoded-models.py.

Usage:
    python scripts/lint-route-response-model.py            # report current state (exit 0)
    python scripts/lint-route-response-model.py --check    # CI gate (exit 1 on new debt or stale allowlist)
    python scripts/lint-route-response-model.py --update    # reseed the allowlist to current state
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "route_response_model_allowlist.txt"
SCAN_DIRS = [
    REPO_ROOT / "src" / "mcp" / "app" / "routers",
    REPO_ROOT / "src" / "mcp" / "routers",
]

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# Response subclasses that set their own body — response_model does not apply.
# Includes the base ``Response`` and ``JSONResponse`` (used for custom status /
# headers / raw content), which pass their body through unfiltered.
_CUSTOM_RESPONSE_CLASSES = {
    "Response", "JSONResponse",
    "StreamingResponse", "FileResponse", "RedirectResponse",
    "PlainTextResponse", "HTMLResponse", "EventSourceResponse",
}

# Status codes that MUST NOT carry a response body — FastAPI rejects a
# response_model on these at import time, so they are legitimately exempt.
_NO_BODY_STATUS = {204, 205, 304}

# Per-line suppression token (rare — prefer the allowlist).
_SUPPRESS_TOKEN = "response-model-allowed"


def _route_decorator(decorator: ast.expr) -> tuple[str, str | None, bool, bool] | None:
    """Return (METHOD, path, has_response_model, exempt_via_response_class) or None.

    Mirrors gen_router_registry._is_router_decorator, plus the keyword scan.
    """
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not isinstance(func, ast.Attribute):
        return None
    method = func.attr.lower()
    if method not in _HTTP_METHODS:
        return None
    if not isinstance(func.value, ast.Name):
        return None
    path = None
    if decorator.args:
        arg0 = decorator.args[0]
        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
            path = arg0.value
    has_response_model = False
    exempt = False
    for kw in decorator.keywords:
        if kw.arg == "response_model" and not _is_none(kw.value):
            has_response_model = True
        if kw.arg == "response_class" and _name_of(kw.value) in _CUSTOM_RESPONSE_CLASSES:
            exempt = True
        # A no-body status (204/205/304) cannot carry a response_model.
        if kw.arg == "status_code" and isinstance(kw.value, ast.Constant) and kw.value.value in _NO_BODY_STATUS:
            exempt = True
    return method.upper(), path, has_response_model, exempt


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _name_of(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _returns_custom_response(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the handler body references a custom Response subclass."""
    for node in ast.walk(fn):
        if isinstance(node, (ast.Name, ast.Attribute)):
            if _name_of(node) in _CUSTOM_RESPONSE_CLASSES:
                return True
    return False


def _line_suppressed(source_lines: list[str], lineno: int) -> bool:
    return 0 < lineno <= len(source_lines) and _SUPPRESS_TOKEN in source_lines[lineno - 1]


def _key(module: str, handler: str, method: str, path: str) -> str:
    # rstrip so an empty-path route (@router.post("")) does not carry a
    # trailing space that _load_allowlist's strip() would later remove —
    # otherwise seed != reload and the route flaps as both new and stale.
    return f"{module}::{handler}::{method} {path}".rstrip()


def collect_untyped() -> list[str]:
    """Return sorted keys of every untyped (non-custom-response) JSON route."""
    untyped: list[str] = []
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            try:
                source = p.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(p))
            except (SyntaxError, UnicodeDecodeError):
                continue
            source_lines = source.splitlines()
            rel = str(p.relative_to(REPO_ROOT)).replace("\\", "/")
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                custom_body = _returns_custom_response(node)
                for dec in node.decorator_list:
                    parsed = _route_decorator(dec)
                    if parsed is None:
                        continue
                    method, path, has_rm, exempt = parsed
                    if has_rm or exempt or custom_body:
                        continue
                    if _line_suppressed(source_lines, dec.lineno):
                        continue
                    untyped.append(_key(rel, node.name, method, path or ""))
    return sorted(untyped)


def _load_allowlist() -> list[str]:
    if not ALLOWLIST_PATH.exists():
        return []
    out = []
    for line in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return sorted(out)


def _write_allowlist(keys: list[str]) -> None:
    header = (
        "# Grandfather allowlist: JSON @router routes that lack a response_model TODAY.\n"
        "# Enforced by scripts/lint-route-response-model.py --check.\n"
        "# This list may ONLY SHRINK. Adding a response_model removes the entry;\n"
        "# the gate fails on any new untyped route or any stale (now-typed) entry.\n"
        "# Burn this to zero by 1.0 (tasks/2026-06-29-rag-api-systemic-audit.md, Phase 5).\n"
    )
    ALLOWLIST_PATH.write_text(header + "\n".join(sorted(keys)) + ("\n" if keys else ""), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="CI gate: exit 1 on new debt or stale allowlist entries")
    ap.add_argument("--update", action="store_true", help="Reseed the allowlist to the current untyped set")
    args = ap.parse_args(argv)

    current = collect_untyped()

    if args.update:
        _write_allowlist(current)
        print(f"wrote {ALLOWLIST_PATH.relative_to(REPO_ROOT)} ({len(current)} untyped routes)")
        return 0

    allow = _load_allowlist()
    allow_set, current_set = set(allow), set(current)
    new_debt = sorted(current_set - allow_set)      # untyped routes not grandfathered
    # A stale entry counts only when its module is actually present in this tree.
    # The public mirror strips internal-only routers (*_internal.py, src/mcp/routers/*),
    # so the synced allowlist will list modules that don't exist there — skip those
    # rather than flag them, or the synced gate would fail public CI for routes that
    # were never public. (Audit 2026-06-29: the two-repo divergence edge, applied to
    # the gate itself.)
    stale = sorted(
        k for k in (allow_set - current_set)
        if (REPO_ROOT / k.split("::", 1)[0]).exists()
    )

    if not args.check:
        print(f"[route-response-model] {len(current)} untyped JSON route(s); "
              f"{len(allow)} allowlisted; {len(new_debt)} new; {len(stale)} stale.")
        return 0

    if not new_debt and not stale:
        print(f"[route-response-model] OK — {len(current)} untyped routes, all grandfathered "
              f"(allowlist must only shrink toward 1.0).")
        return 0

    if new_debt:
        print(f"\n::error::[route-response-model] {len(new_debt)} NEW untyped JSON route(s) — "
              "add a response_model= to the decorator (typed Pydantic model), or for a custom "
              "Response use response_class=, or (last resort) add `# response-model-allowed: <reason>`.",
              file=sys.stderr)
        for k in new_debt:
            print(f"  NEW  {k}", file=sys.stderr)
    if stale:
        print(f"\n::error::[route-response-model] {len(stale)} stale allowlist entr(y/ies) — "
              "these routes are now typed or removed. Run "
              "`python scripts/lint-route-response-model.py --update` to ratchet the allowlist down.",
              file=sys.stderr)
        for k in stale:
            print(f"  STALE {k}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
