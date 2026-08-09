#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Contract gate: no NEW duplicate Pydantic model class name across the surface.

Graduates the contract-divergence finding from
tasks/2026-06-29-rag-api-systemic-audit.md (cluster: enforcement-coverage
inversion). When two modules each define a ``BaseModel`` subclass with the same
name (e.g. two ``AgentQueryRequest``), the request/response contracts silently
diverge — a consumer typed against one gets the other's shape. Today 4 names
collide; this gate grandfathers them and fails CI on any NEW collision, so the
divergence surface can only shrink.

Key shape: ``ClassName::<sorted comma-joined modules>``. A stale entry (a name
no longer colliding) must be removed via --update (the ratchet). Public-strip-
safe: a stale entry is skipped when any of its modules is absent from this tree
(the public mirror strips internal-only routers).

Usage:
    python scripts/lint-model-name-uniqueness.py            # report
    python scripts/lint-model-name-uniqueness.py --check    # CI gate
    python scripts/lint-model-name-uniqueness.py --update    # reseed allowlist
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "model_name_dup_allowlist.txt"
SCAN_ROOTS = [REPO_ROOT / "src" / "mcp" / "app", REPO_ROOT / "src" / "mcp" / "core"]


def _is_basemodel(cls: ast.ClassDef) -> bool:
    for b in cls.bases:
        if isinstance(b, ast.Name) and b.id == "BaseModel":
            return True
        if isinstance(b, ast.Attribute) and b.attr == "BaseModel":
            return True
    return False


def collect_collisions() -> list[str]:
    """Return ``Name::mod1,mod2`` keys for every BaseModel name in >1 module."""
    by_name: dict[str, set[str]] = defaultdict(set)
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.py")):
            parts = set(p.parts)
            if parts & {"__pycache__", "tests"} or p.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            except (SyntaxError, UnicodeDecodeError):
                continue
            rel = str(p.relative_to(REPO_ROOT)).replace("\\", "/")
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and _is_basemodel(node):
                    by_name[node.name].add(rel)
    return sorted(
        f"{name}::{','.join(sorted(mods))}"
        for name, mods in by_name.items()
        if len(mods) > 1
    )


def _load_allowlist() -> list[str]:
    if not ALLOWLIST_PATH.exists():
        return []
    return sorted(
        s for line in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines()
        if (s := line.strip()) and not s.startswith("#")
    )


def _write_allowlist(keys: list[str]) -> None:
    header = (
        "# Grandfather allowlist: Pydantic model class names defined in >1 module TODAY.\n"
        "# Enforced by scripts/lint-model-name-uniqueness.py --check.\n"
        "# May ONLY SHRINK: a new collision fails CI; a resolved one must be removed\n"
        "# via --update. Rename the duplicates to distinct names to burn down (Phase 5).\n"
    )
    ALLOWLIST_PATH.write_text(header + "\n".join(sorted(keys)) + ("\n" if keys else ""), encoding="utf-8")


def _modules_of(key: str) -> list[str]:
    return key.split("::", 1)[1].split(",") if "::" in key else []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="CI gate: exit 1 on new collision or stale entry")
    ap.add_argument("--update", action="store_true", help="Reseed the allowlist to current collisions")
    args = ap.parse_args(argv)

    current = collect_collisions()
    if args.update:
        _write_allowlist(current)
        print(f"wrote {ALLOWLIST_PATH.relative_to(REPO_ROOT)} ({len(current)} collisions)")
        return 0

    allow_set, current_set = set(_load_allowlist()), set(current)
    new = sorted(current_set - allow_set)
    stale = sorted(
        k for k in (allow_set - current_set)
        if all((REPO_ROOT / m).exists() for m in _modules_of(k))
    )

    if not args.check:
        print(f"[model-name-uniqueness] {len(current)} colliding name(s); {len(new)} new; {len(stale)} stale.")
        return 0
    if not new and not stale:
        print(f"[model-name-uniqueness] OK — {len(current)} grandfathered collision(s) (may only shrink).")
        return 0
    if new:
        print(f"\n::error::[model-name-uniqueness] {len(new)} NEW duplicate model name(s) — rename so each "
              "BaseModel class name is unique across modules (a consumer typed against one must not get "
              "the other's shape).", file=sys.stderr)
        for k in new:
            print(f"  NEW   {k}", file=sys.stderr)
    if stale:
        print(f"\n::error::[model-name-uniqueness] {len(stale)} stale entr(y/ies) now resolved — ratchet down "
              "with `python scripts/lint-model-name-uniqueness.py --update`.", file=sys.stderr)
        for k in stale:
            print(f"  STALE {k}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
