#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lint that every Pro-tier feature flag has at least one runtime gate.

Background: until Phase 1 of the 2026-05-20 Pro Tier Implementation Plan,
the 12 Pro flags in ``config.features.FEATURE_FLAGS`` were declared but
zero call-sites consulted them at user-facing entry points. Paying for
Pro changed no behavior. This lint enforces the bucket-migration discipline
going forward.

For every flag mapped to the Pro tier (per ``_PRO_TIER_FLAGS`` in
``config.features``) we require at least one of the following call patterns
to appear anywhere under ``src/mcp/`` or the top-level ``plugins/`` tree::

    require_feature("<flag>")
    is_feature_enabled("<flag>")
    check_feature("<flag>")

A back-compat allowlist exists for flags that have a planned implementation
phase but no shipped code yet — they're declared in
``scripts/pro_gating_allowlist.txt``. The allowlist shrinks as Pro features
land. New unallowlisted Pro flags must have at least one gate to pass.

Usage::

    ./scripts/lint-pro-gating.py          # CI mode (exit 1 on failures)
    ./scripts/lint-pro-gating.py --list   # print all gates discovered
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_ROOT = REPO_ROOT / "src" / "mcp"
# First-party commercial (BSL-1.1) plugins live in the top-level plugins/ tree
# and carry their own gates (e.g. apple_notes_reader, calendar_sync). Scan it
# too so those gates are asserted, not just the ones under src/mcp/.
PLUGINS_ROOT = REPO_ROOT / "plugins"
ALLOWLIST = REPO_ROOT / "scripts" / "pro_gating_allowlist.txt"
FEATURES_PY = MCP_ROOT / "config" / "features.py"

GATE_FUNCTIONS = frozenset({"require_feature", "is_feature_enabled", "check_feature"})


def discover_gated_flags(roots: list[Path]) -> set[str]:
    """Walk every .py file under roots and collect the literal string args
    passed to any of GATE_FUNCTIONS."""
    gated: set[str] = set()
    for root in roots:
        for py_path in root.rglob("*.py"):
            # skip the lint script itself + tests of features.py + features.py
            if py_path.name in {"features.py", "lint-pro-gating.py"}:
                continue
            try:
                tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn_name = _get_call_name(node.func)
                if fn_name not in GATE_FUNCTIONS:
                    continue
                if not node.args:
                    continue
                first = node.args[0]
                # Constant string literal: gate("flag_name")
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    gated.add(first.value)
                # Decorator form: @require_feature("flag_name") — same shape
    return gated


def _get_call_name(node: ast.AST) -> str | None:
    """Return the simple function name from a Call.func node, or None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _assignment_value(node: ast.AST, name: str) -> ast.AST | None:
    """Return the RHS value node if `node` assigns (plain or annotated) to `name`."""
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return node.value
    elif isinstance(node, ast.AnnAssign):
        if isinstance(node.target, ast.Name) and node.target.id == name:
            return node.value
    return None


def pro_flags_from_source(features_py: Path) -> set[str]:
    """Statically derive the Pro-tier flag set, mirroring
    ``config.features._PRO_TIER_FLAGS`` WITHOUT importing the app package —
    importing it pulls heavy runtime deps (e.g. httpx, via config.settings)
    that the lint CI job does not install. Reads FEATURE_BUCKETS plus the
    _PRO_TIER_FLAGS definition by AST so the ``pro_`` prefix rule and the
    back-compat alias stay sourced from the code, not duplicated here."""
    tree = ast.parse(features_py.read_text(encoding="utf-8"), filename=str(features_py))
    buckets: dict[str, list[str]] = {}
    prefix = "pro_"
    extra: set[str] = set()
    for node in ast.walk(tree):
        buckets_val = _assignment_value(node, "FEATURE_BUCKETS")
        if isinstance(buckets_val, ast.Dict):
            for key, value in zip(buckets_val.keys, buckets_val.values):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and isinstance(value, ast.List)
                ):
                    buckets[key.value] = [
                        el.value
                        for el in value.elts
                        if isinstance(el, ast.Constant) and isinstance(el.value, str)
                    ]
        pro_val = _assignment_value(node, "_PRO_TIER_FLAGS")
        if pro_val is not None:
            for sub in ast.walk(pro_val):
                # prefix comes from `bucket_name.startswith("pro_")`
                if (
                    isinstance(sub, ast.Call)
                    and _get_call_name(sub.func) == "startswith"
                    and sub.args
                    and isinstance(sub.args[0], ast.Constant)
                    and isinstance(sub.args[0].value, str)
                ):
                    prefix = sub.args[0].value
                # back-compat aliases from set literals, e.g. {"calendar_sync"}
                if isinstance(sub, ast.Set):
                    extra |= {
                        el.value
                        for el in sub.elts
                        if isinstance(el, ast.Constant) and isinstance(el.value, str)
                    }
    pro = {flag for name, flags in buckets.items() if name.startswith(prefix) for flag in flags}
    return pro | extra


def load_allowlist() -> set[str]:
    if not ALLOWLIST.exists():
        return set()
    out: set[str] = set()
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        # strip trailing inline comments + whitespace
        line = raw.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="Print all discovered gates and exit")
    args = parser.parse_args()

    # Derive Pro flags by static analysis — importing config.features would pull
    # heavy runtime deps (httpx via config.settings) the lint CI job doesn't install.
    if not FEATURES_PY.exists():
        print(f"ERROR: {FEATURES_PY.relative_to(REPO_ROOT)} not found", file=sys.stderr)
        return 2
    pro_flags = pro_flags_from_source(FEATURES_PY)
    if not pro_flags:
        print(
            f"ERROR: no Pro-tier flags parsed from {FEATURES_PY.relative_to(REPO_ROOT)}",
            file=sys.stderr,
        )
        return 2
    gated = discover_gated_flags([MCP_ROOT, PLUGINS_ROOT])
    allowlist = load_allowlist()

    if args.list:
        print(f"Pro flags ({len(pro_flags)}):")
        for f in sorted(pro_flags):
            marker = "✓" if f in gated else ("☐ (allowed)" if f in allowlist else "✗")
            print(f"  {marker} {f}")
        print()
        print(f"All discovered gates ({len(gated)}):")
        for f in sorted(gated):
            print(f"  {f}")
        return 0

    ungated = pro_flags - gated - allowlist
    if not ungated:
        print(f"OK: all {len(pro_flags)} Pro flags have at least one gate "
              f"(allowlisted: {len(allowlist & pro_flags)})")
        return 0

    print("FAIL: Pro flags declared without any runtime gate:", file=sys.stderr)
    for flag in sorted(ungated):
        print(f"  - {flag}", file=sys.stderr)
    print(
        "\nFix options:",
        "  1. Wire @require_feature(<flag>) at a user-facing endpoint",
        "  2. Add is_feature_enabled(<flag>) check at the service entry point",
        "  3. If the feature is planned for a later phase, add to",
        f"     {ALLOWLIST.relative_to(REPO_ROOT)} with a one-line note",
        sep="\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
