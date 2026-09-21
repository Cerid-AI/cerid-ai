#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Gate G-H — a model/provider label must come from what actually ran.

The mechanism (2026-09-02 audit): a response tells the operator which model
or provider served the request, and the value is a module constant, a string
literal, or an ``os.getenv(NAME, "default")`` fallback. The label then reports
the *configured intention* — often the intention as of the last container
restart — rather than the observed serving path. When the router falls back,
when the wizard rotates a provider, when Ollama is down and OpenRouter
answers, the label does not move. It is a measurement instrument reading its
own default.

That makes it worse than no label: an operator debugging "why are my answers
different" reads a confident, wrong provenance and rules out the actual
cause. It also silently corrupts every downstream number keyed on it (cost
attribution, per-model quality, the recommender's flags_enabled set).

Scope, deliberately narrow: keys named model / provider / serving / backend /
engine (and their ``_name`` / ``_id`` / ``_used`` forms) inside a payload
LEXICALLY UNDER a ``return`` in an HTTP route handler or an MCP tool handler.
Those are labels on the wire. The same constant used to *choose* a model is
not a claim about what ran, so it is out of scope — and including it took the
hit count from 5 to 135, mostly FastAPI's own ``response_model=`` kwarg.

Flagged value shapes:

  literal          ``"provider": "ollama"`` — frozen at edit time.
  module-constant  ``"model": DEFAULT_MODEL`` where the name is bound at
                   module scope (assignment or ``from x import NAME``) —
                   frozen at import time.
  getenv-default   ``"provider": os.getenv("X", "sidecar")`` — reports the
                   default whenever the env var is unset, which is the exact
                   case where the operator most needs the truth. An empty
                   default (``""``) is not flagged: falsy reads as "unset"
                   downstream rather than as a confident wrong answer.

Accepted: anything read from the call that produced the answer — a local
bound from the client response, a parameter, ``result.model``,
``getattr(config, "X")`` with no literal default. The fix is always the
same: thread the value the call actually used back into the payload.

Allowlist: ``scripts/model_label_provenance_allowlist.txt`` (path:line, one
reason each). Shrink-only.

Usage:
    python scripts/lint-model-label-provenance.py            # report (exit 0)
    python scripts/lint-model-label-provenance.py --check    # CI gate
    python scripts/lint-model-label-provenance.py --update   # reseed allowlist
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "model_label_provenance_allowlist.txt"
SCAN_ROOT = REPO_ROOT / "src" / "mcp"

_SKIP_DIR_PARTS = {"__pycache__", ".venv", "venv", "node_modules", "tests"}
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_TOOL_DECORATORS = {"register_tool", "mcp_tool"}

#: Keys that make a claim about what served the request.
_LABEL_KEY_RE = re.compile(
    r"(^|_)(model|provider|serving|backend|engine)(s)?(_(name|id|used))?$", re.IGNORECASE
)
#: FastAPI's own decorator kwargs — a schema declaration, not a wire label.
_NOT_A_LABEL = {
    "response_model",
    "response_model_exclude_none",
    "response_model_exclude_unset",
    "response_model_by_alias",
}


class Violation(NamedTuple):
    file: str
    lineno: int
    handler: str
    label: str
    kind: str  # "literal" | "module-constant" | "getenv-default"

    def key(self) -> str:
        return f"{self.file}:{self.lineno}"


def module_level_names(tree: ast.Module) -> set[str]:
    """Names bound once at import: assignments and ``from x import NAME``."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
    return names


def classify_value(value: ast.expr, module_names: set[str]) -> str | None:
    """Return the violation kind for a label's value, or None if it is live."""
    if isinstance(value, ast.Constant):
        return "literal" if isinstance(value.value, str) and value.value else None
    if isinstance(value, ast.Name):
        return "module-constant" if value.id in module_names else None
    if isinstance(value, ast.Call):
        func = value.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        is_env_get = name == "getenv" or (
            name == "get"
            and isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "environ"
        )
        if is_env_get and len(value.args) > 1:
            default = value.args[1]
            # An empty default is a genuine "unset" sentinel, not a claim.
            if isinstance(default, ast.Constant) and isinstance(default.value, str) and default.value:
                return "getenv-default"
    return None


def handler_kind(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
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


def _labelled_pairs(node: ast.AST) -> list[tuple[str, ast.expr]]:
    pairs: list[tuple[str, ast.expr]] = []
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                pairs.append((key.value, value))
    elif isinstance(node, ast.Call):
        pairs += [(kw.arg, kw.value) for kw in node.keywords if kw.arg]
    return [
        (name, value)
        for name, value in pairs
        if name not in _NOT_A_LABEL and _LABEL_KEY_RE.search(name)
    ]


def check_source(source: str, rel: str) -> list[Violation]:
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError:
        return []
    module_names = module_level_names(tree)
    violations: list[Violation] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if handler_kind(func) is None:
            continue
        for ret in ast.walk(func):
            if not isinstance(ret, ast.Return) or ret.value is None:
                continue
            for node in ast.walk(ret):
                for label, value in _labelled_pairs(node):
                    kind = classify_value(value, module_names)
                    if kind is None:
                        continue
                    violations.append(
                        Violation(rel, value.lineno, func.name, label, kind)
                    )
    return violations


def collect_all() -> list[Violation]:
    out: list[Violation] = []
    for path in sorted(SCAN_ROOT.rglob("*.py")):
        if set(path.parts) & _SKIP_DIR_PARTS:
            continue
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
# model_label_provenance_allowlist.txt — model/provider labels that are not
# read from what actually ran.
# Enforced by scripts/lint-model-label-provenance.py --check (gate G-H,
# tasks/2026-09-02-audit-findings.json).
#
# Format: <path>:<lineno>  # <one-line reason>
# Shrink-only — threading the value the call actually used into the payload
# removes the line; a stale entry fails the gate.
#
# Regenerate (preserves reasons for still-live keys) with:
#   python scripts/lint-model-label-provenance.py --update
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
            f"[model-label-provenance] {len(by_key)} hit(s); {len(allow)} allowlisted; "
            f"{len(new_hits)} new; {len(stale)} stale."
        )
        return 0

    if not new_hits and not stale:
        print(
            f"[model-label-provenance] OK — {len(by_key)} hit(s), all grandfathered "
            "(allowlist must only shrink)."
        )
        return 0

    if new_hits:
        print(
            f"\n::error::[model-label-provenance] {len(new_hits)} NEW model/provider label(s) "
            "assigned from a constant or an env default instead of from the call that produced "
            "the answer. The label will not move when the router falls back, so it reports the "
            "configured intention as though it were the observed serving path.",
            file=sys.stderr,
        )
        for k in new_hits:
            v = by_key[k]
            print(
                f"  NEW  {v.file}:{v.lineno}: [{v.kind}] {v.handler}() returns "
                f"'{v.label}' from a value fixed before the request ran",
                file=sys.stderr,
            )
    if stale:
        print(
            f"\n::error::[model-label-provenance] {len(stale)} stale allowlist entr(y/ies). Run "
            "`python scripts/lint-model-label-provenance.py --update` to ratchet down.",
            file=sys.stderr,
        )
        for k in stale:
            print(f"  STALE {k}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
