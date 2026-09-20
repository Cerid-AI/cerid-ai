#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Gate G-F — .env.example may not publish a knob this repo knows is dead.

Two gates already exist and they disagree with each other in silence.

``lint-env-has-reader.py`` states the rule in its own docstring: "Every name
declared in config/settings.py or config/features.py must be read by
something before it may appear in .env.example." Its ALLOWLIST exempts names
from the reader test — and stops there. ``gen_env_example.py`` never consults
that allowlist, so it keeps generating the exact lines the first gate has on
record as having zero readers. Both report green. ``CERID_CLASSIFICATION``
sits in the Enterprise block of .env.example reading like a data-
classification control, and nothing anywhere reads it.

That is the failure the reader gate was written to stop, surviving inside the
gate's own bookkeeping: an allowlist entry is a note that a name is dead, and
the generator turns the same name into documentation telling an operator a
lever exists.

This gate closes the loop by joining them:

  1. read ``ALLOWLIST`` straight out of lint-env-has-reader.py — the live
     object, so an entry added there is in scope here the same commit;
  2. resolve each allowlisted symbol to the env var(s) it reads, by parsing
     the ``NAME = os.getenv("ENV_VAR", ...)`` assignments in
     config/settings.py and config/features.py (the symbol and the env var
     often differ: CLASSIFICATION_ENABLED reads CERID_CLASSIFICATION);
  3. fail if that env var is a key in .env.example.

Removing an entry from the reader gate's allowlist — by wiring a reader or
deleting the declaration — removes it from here too. There is no way to
satisfy both gates while shipping a dead knob to operators.

Allowlist: ``scripts/env_example_orphans_allowlist.txt`` (env var, one reason
each). Shrink-only.

Usage:
    python scripts/lint-env-example-orphans.py            # report (exit 0)
    python scripts/lint-env-example-orphans.py --check    # CI gate
    python scripts/lint-env-example-orphans.py --update   # reseed allowlist
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import re
import sys
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "env_example_orphans_allowlist.txt"
READER_GATE = REPO_ROOT / "scripts" / "lint-env-has-reader.py"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
DECLARATION_FILES = [
    REPO_ROOT / "src" / "mcp" / "config" / "settings.py",
    REPO_ROOT / "src" / "mcp" / "config" / "features.py",
]

#: `NAME=` or `# NAME=` (computed defaults are emitted commented out).
_ENV_KEY_RE = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.MULTILINE)


class Violation(NamedTuple):
    env_var: str
    symbol: str
    reason: str  # the reader gate's own note

    def key(self) -> str:
        return self.env_var


def orphan_symbols() -> dict[str, str]:
    """``{symbol: reason}`` from lint-env-has-reader.py's live ALLOWLIST."""
    if not READER_GATE.exists():
        return {}
    spec = importlib.util.spec_from_file_location("_lint_env_has_reader", READER_GATE)
    if spec is None or spec.loader is None:
        return {}
    module = importlib.util.module_from_spec(spec)
    sys.modules["_lint_env_has_reader"] = module
    spec.loader.exec_module(module)
    return dict(getattr(module, "ALLOWLIST", {}))


def symbol_to_env_vars(sources: dict[str, str]) -> dict[str, set[str]]:
    """``{symbol: {env var, ...}}`` from module-level declarations.

    ``CLASSIFICATION_ENABLED = os.getenv("CERID_CLASSIFICATION", "false")``
    means the reader gate's key and the .env.example key are different
    strings; comparing them directly is why this drift went unnoticed.
    """
    out: dict[str, set[str]] = {}
    for source in sources.values():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            else:
                continue
            if not targets or node.value is None:
                continue
            env_vars = _env_vars_read(node.value)
            for target in targets:
                if env_vars:
                    out.setdefault(target, set()).update(env_vars)
    return out


def _env_vars_read(expr: ast.expr) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(expr):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in ("getenv", "get"):
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.add(first.value)
    return found


def published_keys(env_example: str) -> set[str]:
    return set(_ENV_KEY_RE.findall(env_example))


def analyse(
    orphans: dict[str, str], declarations: dict[str, str], env_example: str
) -> list[Violation]:
    """Pure core, so the plant-fault test can supply all three inputs."""
    mapping = symbol_to_env_vars(declarations)
    published = published_keys(env_example)
    violations: list[Violation] = []
    for symbol, reason in orphans.items():
        for env_var in sorted(mapping.get(symbol, set())):
            if env_var in published:
                violations.append(Violation(env_var, symbol, reason))
    return sorted(violations, key=lambda v: v.env_var)


def collect_all() -> list[Violation]:
    if not ENV_EXAMPLE.exists():
        return []
    declarations = {
        str(p): p.read_text(encoding="utf-8") for p in DECLARATION_FILES if p.exists()
    }
    return analyse(orphan_symbols(), declarations, ENV_EXAMPLE.read_text(encoding="utf-8"))


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
# env_example_orphans_allowlist.txt — dead knobs still published to operators
# Enforced by scripts/lint-env-example-orphans.py --check (gate G-F, F206 in
# tasks/2026-09-02-audit-findings.json).
#
# Every line is a name lint-env-has-reader.py records as having zero readers
# that gen_env_example.py nevertheless writes into .env.example. An operator
# reading the file is told a lever exists that does nothing.
#
# Format: <ENV_VAR>  # <one-line reason>
# Shrink-only. The fix is upstream, and either half removes the line: delete
# the declaration, or wire a reader and drop it from the reader gate's
# ALLOWLIST.
#
# Regenerate (preserves reasons for still-live keys) with:
#   python scripts/lint-env-example-orphans.py --update
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
    stale = sorted(k for k in allow if k not in by_key)

    if not args.check:
        print(
            f"[env-example-orphans] {len(by_key)} dead knob(s) published; {len(allow)} "
            f"allowlisted; {len(new_hits)} new; {len(stale)} stale."
        )
        return 0

    if not new_hits and not stale:
        print(
            f"[env-example-orphans] OK — {len(by_key)} dead knob(s) published, all "
            "grandfathered (allowlist must only shrink)."
        )
        return 0

    if new_hits:
        print(
            f"\n::error::[env-example-orphans] {len(new_hits)} name(s) that "
            "lint-env-has-reader.py records as having ZERO readers are published in "
            ".env.example, telling an operator a lever exists that does nothing. Delete the "
            "declaration, or wire a reader and drop it from that gate's ALLOWLIST.",
            file=sys.stderr,
        )
        for k in new_hits:
            v = by_key[k]
            print(f"  NEW  {v.env_var} (declared as {v.symbol}) — {v.reason}", file=sys.stderr)
    if stale:
        print(
            f"\n::error::[env-example-orphans] {len(stale)} stale allowlist entr(y/ies) — the "
            "knob now has a reader or is gone. Run "
            "`python scripts/lint-env-example-orphans.py --update` to ratchet down.",
            file=sys.stderr,
        )
        for k in stale:
            print(f"  STALE {k}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
