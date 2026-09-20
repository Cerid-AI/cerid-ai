#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Gate G-E — a failing request may not answer with a successful-looking empty.

The backend twin of the ESLint rule ``cerid/no-error-as-empty-response``
(src/web/eslint-rules/). That rule stops the browser turning a failed
``fetch`` into ``[]``; this one stops the server *sending* the ``[]`` in the
first place, which is the half no frontend rule can reach.

Mechanism M4, "failure rendered as emptiness": an endpoint catches an
exception, returns ``[]`` / ``None`` / ``Response(items=[], total=0)`` with a
200, and the caller has no way to tell "your knowledge base has no briefs"
from "Neo4j is down". The client renders its empty state. Nothing logs an
error the operator sees, no status code changes, and every test that asserts
``response.status_code == 200`` still passes.

Scope: HTTP route handlers (``@<router>.<method>(...)``) and MCP tool
handlers (``@register_tool(...)``) only. Those are the functions whose return
value IS the wire response, so an empty return is indistinguishable from a
real empty result. An internal helper returning ``None`` on failure is a
different (and usually fine) thing — including it took the hit count from 8
to 291 and would have made this gate noise.

A hit is a ``return`` inside an ``except`` handler where:

  * the handler does not re-raise (a re-raising handler is the correct shape,
    whatever else it does first), AND
  * the returned value is empty-shaped — ``None``, ``""``, ``0``, ``0.0``,
    ``False``, ``[]``, ``{}``, ``set()``, or a dict/response-model
    construction whose every value is itself empty-shaped, AND
  * that value carries no degraded sibling: no key or keyword whose name
    reads as a status/degraded/error/reason/partial/stale channel.

So the fix is never "delete the except". It is either to re-raise (let the
error boundary answer), or to keep returning the empty payload and say so:
``Response(items=[], degraded=True, degraded_reason="neo4j unavailable")``.
That second form is what makes the emptiness honest, and it is exactly what
the sibling check accepts.

Allowlist: ``scripts/degraded_is_not_empty_allowlist.txt`` (path:line, one
reason each). Shrink-only.

Usage:
    python scripts/lint-degraded-is-not-empty.py            # report (exit 0)
    python scripts/lint-degraded-is-not-empty.py --check    # CI gate
    python scripts/lint-degraded-is-not-empty.py --update   # reseed allowlist
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "degraded_is_not_empty_allowlist.txt"
SCAN_ROOT = REPO_ROOT / "src" / "mcp"

_SKIP_DIR_PARTS = {"__pycache__", ".venv", "venv", "node_modules", "tests"}
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_TOOL_DECORATORS = {"register_tool", "mcp_tool"}

#: A key/keyword whose name reads as "this payload is not a normal success".
_DEGRADED_SIBLING_RE = re.compile(
    r"status|degrad|error|reason|detail|fail|partial|warn|message|stale|"
    r"fallback|success|unavailable|available|healthy|exc",
    re.IGNORECASE,
)

#: Zero-argument constructors that build an empty container.
_EMPTY_CTORS = {"list", "dict", "set", "tuple", "frozenset"}


class Violation(NamedTuple):
    file: str
    lineno: int
    handler: str
    kind: str  # "route" | "tool"
    detail: str

    def key(self) -> str:
        return f"{self.file}:{self.lineno}"


def is_empty_shaped(node: ast.expr) -> bool:
    """True if *node* is a payload a caller cannot tell from a real empty one."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bytes):
            return False
        return node.value is None or node.value in ("", 0, 0.0, False)
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys or all(
            v is not None and is_empty_shaped(v) for v in node.values
        )
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in _EMPTY_CTORS and not node.args and not node.keywords:
            return True
        # A response model built entirely from empty values:
        # StructuralGapsResponse(gaps=[], total=0)
        if node.keywords and not node.args:
            return all(is_empty_shaped(kw.value) for kw in node.keywords)
    return False


def sibling_names(node: ast.expr) -> list[str]:
    """Key / keyword names carried by a returned payload."""
    names: list[str] = []
    if isinstance(node, ast.Dict):
        names += [
            k.value
            for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        ]
    if isinstance(node, ast.Call):
        names += [kw.arg for kw in node.keywords if kw.arg]
    return names


def has_degraded_sibling(node: ast.expr) -> bool:
    return any(_DEGRADED_SIBLING_RE.search(name) for name in sibling_names(node))


def handler_kind(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """"route" for an HTTP endpoint, "tool" for an MCP tool, else None."""
    for dec in func.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if (
            isinstance(target, ast.Attribute)
            and target.attr.lower() in _HTTP_METHODS
            and isinstance(target.value, ast.Name)
        ):
            return "route"
        if isinstance(target, ast.Name) and target.id in _TOOL_DECORATORS:
            return "tool"
    return None


def _describe(node: ast.expr) -> str:
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.List):
        return "[]"
    if isinstance(node, ast.Dict):
        keys = sibling_names(node)
        return "{...}" if keys else "{}"
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "?")
        return f"{name}({', '.join(f'{k}=<empty>' for k in sibling_names(node))})"
    return "<empty>"


def check_source(source: str, rel: str) -> list[Violation]:
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError:
        return []
    violations: list[Violation] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        kind = handler_kind(func)
        if kind is None:
            continue
        for handler in ast.walk(func):
            if not isinstance(handler, ast.ExceptHandler):
                continue
            if any(isinstance(n, ast.Raise) for n in ast.walk(handler)):
                continue
            for node in ast.walk(handler):
                if not isinstance(node, ast.Return) or node.value is None:
                    continue
                if not is_empty_shaped(node.value):
                    continue
                if has_degraded_sibling(node.value):
                    continue
                violations.append(
                    Violation(
                        rel,
                        node.lineno,
                        func.name,
                        kind,
                        f"except-branch returns {_describe(node.value)} with no "
                        "degraded/status/error sibling — a caller cannot tell this "
                        "from a real empty result",
                    )
                )
    return violations


def iter_py_files(root: Path) -> list[Path]:
    return [p for p in sorted(root.rglob("*.py")) if not (set(p.parts) & _SKIP_DIR_PARTS)]


def collect_all() -> list[Violation]:
    out: list[Violation] = []
    for path in iter_py_files(SCAN_ROOT):
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        out.extend(check_source(source, str(path.relative_to(REPO_ROOT))))
    return sorted(out, key=lambda v: (v.file, v.lineno))


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
# degraded_is_not_empty_allowlist.txt — endpoints that answer failure with empty
# Enforced by scripts/lint-degraded-is-not-empty.py --check (gate G-E,
# tasks/2026-09-02-audit-findings.json). Backend twin of the ESLint rule
# cerid/no-error-as-empty-response.
#
# Format: <path>:<lineno>  # <one-line reason>
# Shrink-only — re-raising, or adding a degraded/status/reason sibling to the
# returned payload, removes the line; a stale entry fails the gate.
#
# Regenerate (preserves reasons for still-live keys) with:
#   python scripts/lint-degraded-is-not-empty.py --update
"""


def _write_allowlist(violations: list[Violation]) -> None:
    existing = _load_allowlist()
    lines = [_HEADER.rstrip("\n"), ""]
    for v in violations:
        lines.append(f"{v.key()}  # {existing.get(v.key(), 'TODO: cite an audit finding id')}")
    ALLOWLIST_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
        print(f"wrote {ALLOWLIST_PATH.relative_to(REPO_ROOT)} ({len(current)} hits)")
        return 0

    allow = _load_allowlist()
    by_key = {v.key(): v for v in current}
    new_hits = sorted(k for k in by_key if k not in allow)
    stale = sorted(
        k for k in allow if k not in by_key and (REPO_ROOT / k.split(":", 1)[0]).exists()
    )

    if not args.check:
        print(
            f"[degraded-is-not-empty] {len(by_key)} hit(s); {len(allow)} allowlisted; "
            f"{len(new_hits)} new; {len(stale)} stale."
        )
        return 0

    if not new_hits and not stale:
        print(
            f"[degraded-is-not-empty] OK — {len(by_key)} hit(s), all grandfathered "
            "(allowlist must only shrink)."
        )
        return 0

    if new_hits:
        print(
            f"\n::error::[degraded-is-not-empty] {len(new_hits)} NEW handler(s) answer a caught "
            "exception with a successful-looking empty payload. The caller cannot tell the "
            "outage from a real empty result. Either re-raise and let the error boundary answer, "
            "or keep the empty payload and mark it — add a degraded/status/reason sibling.",
            file=sys.stderr,
        )
        for k in new_hits:
            v = by_key[k]
            print(f"  NEW  {v.file}:{v.lineno}: [{v.kind}] {v.handler}(): {v.detail}", file=sys.stderr)
    if stale:
        print(
            f"\n::error::[degraded-is-not-empty] {len(stale)} stale allowlist entr(y/ies). Run "
            "`python scripts/lint-degraded-is-not-empty.py --update` to ratchet down.",
            file=sys.stderr,
        )
        for k in stale:
            print(f"  STALE {k}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
