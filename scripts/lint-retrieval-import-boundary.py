#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Contract gate: freeze the set of modules that import the retrieval primitives.

Graduates root-cause cluster #1 ("no enforced canonical retrieval path") from
tasks/2026-06-29-rag-api-systemic-audit.md. The disease: the full retrieval
stack lives in the REST wrapper, so every non-REST consumer imports the core
``agent_query`` directly and silently drops consumer isolation / CRAG /
Self-RAG / cache. Every new surface re-bypasses *by construction*.

This gate does not fix the bypass (that is Phase 1 — collapse to one core
``agent_query_full``). It STOPS THE BLEEDING: it grandfathers the modules that
import a retrieval primitive today and fails CI when a NEW module imports one.
So the bypass surface can only shrink while Phase 1 migrates consumers — the
next sprint cannot quietly add bypass #6.

Governed names (imported via ``from ... import <name>``):
    agent_query, multi_domain_query   (core.agents.query_agent)
    query_knowledge                   (app.routers.query)

The allowlist (``retrieval_import_allowlist.txt``) holds the current importers
and may ONLY shrink. New importer -> fail; an allowlisted importer that stops
importing (Phase 1 migration) -> stale -> run --update to ratchet down.
Module-import evasion (``import query_agent; query_agent.agent_query``) is a
known limitation; new retrieval consumers are rare and reviewed.

Per-line suppression (last resort): ``# retrieval-import-allowed: <reason>``.

Usage:
    python scripts/lint-retrieval-import-boundary.py            # report
    python scripts/lint-retrieval-import-boundary.py --check    # CI gate
    python scripts/lint-retrieval-import-boundary.py --update    # reseed allowlist
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "retrieval_import_allowlist.txt"
SCAN_ROOTS = [REPO_ROOT / "src" / "mcp" / "app", REPO_ROOT / "src" / "mcp" / "core"]

_GOVERNED = {"agent_query", "multi_domain_query", "query_knowledge"}
_SUPPRESS_TOKEN = "retrieval-import-allowed"

# The modules that DEFINE these names are not importers — never flag them.
_DEFINING_SUFFIXES = (
    "core/agents/query_agent.py",
    "app/routers/query.py",
)


def _is_defining(rel: str) -> bool:
    return any(rel.endswith(s) for s in _DEFINING_SUFFIXES)


def collect_importers() -> list[str]:
    """Return sorted ``relmodule::symbol`` keys for every governed import."""
    keys: set[str] = set()
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.py")):
            parts = set(p.parts)
            if parts & {"__pycache__", "tests"} or p.name.startswith("test_"):
                continue
            rel = str(p.relative_to(REPO_ROOT)).replace("\\", "/")
            if _is_defining(rel):
                continue
            try:
                source = p.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(p))
            except (SyntaxError, UnicodeDecodeError):
                continue
            source_lines = source.splitlines()
            for node in ast.walk(tree):
                names: list[tuple[str, int]] = []
                if isinstance(node, ast.ImportFrom):
                    names = [(a.name, node.lineno) for a in node.names]
                elif isinstance(node, ast.Import):
                    names = [(a.name.split(".")[-1], node.lineno) for a in node.names]
                for name, lineno in names:
                    if name not in _GOVERNED:
                        continue
                    if 0 < lineno <= len(source_lines) and _SUPPRESS_TOKEN in source_lines[lineno - 1]:
                        continue
                    keys.add(f"{rel}::{name}")
    return sorted(keys)


def _load_allowlist() -> list[str]:
    if not ALLOWLIST_PATH.exists():
        return []
    return sorted(
        s for line in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines()
        if (s := line.strip()) and not s.startswith("#")
    )


def _write_allowlist(keys: list[str]) -> None:
    header = (
        "# Grandfather allowlist: modules importing a retrieval primitive\n"
        "# (agent_query / multi_domain_query / query_knowledge) TODAY.\n"
        "# Enforced by scripts/lint-retrieval-import-boundary.py --check.\n"
        "# This list may ONLY SHRINK. A new importer fails CI; a migrated\n"
        "# (now-stale) importer must be removed via --update. Phase 1 collapses\n"
        "# these onto one canonical agent_query_full (audit 2026-06-29, cluster #1).\n"
    )
    ALLOWLIST_PATH.write_text(header + "\n".join(sorted(keys)) + ("\n" if keys else ""), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="CI gate: exit 1 on new importer or stale entry")
    ap.add_argument("--update", action="store_true", help="Reseed the allowlist to the current importer set")
    args = ap.parse_args(argv)

    current = collect_importers()
    if args.update:
        _write_allowlist(current)
        print(f"wrote {ALLOWLIST_PATH.relative_to(REPO_ROOT)} ({len(current)} importers)")
        return 0

    allow_set, current_set = set(_load_allowlist()), set(current)
    new_importers = sorted(current_set - allow_set)
    # Stale only when the module still exists (public-strip-safe, like the
    # route-response-model gate): a module absent from this tree is not "stale".
    stale = sorted(
        k for k in (allow_set - current_set)
        if (REPO_ROOT / k.split("::", 1)[0]).exists()
    )

    if not args.check:
        print(f"[retrieval-import-boundary] {len(current)} importer(s); "
              f"{len(new_importers)} new; {len(stale)} stale.")
        return 0

    if not new_importers and not stale:
        print(f"[retrieval-import-boundary] OK — {len(current)} grandfathered importer(s) "
              "(surface frozen; may only shrink).")
        return 0
    if new_importers:
        print(f"\n::error::[retrieval-import-boundary] {len(new_importers)} NEW module(s) import a "
              "retrieval primitive directly — route the new consumer through the canonical retrieval "
              "surface instead of importing agent_query/multi_domain_query/query_knowledge "
              "(or, with justification, add `# retrieval-import-allowed: <reason>`).", file=sys.stderr)
        for k in new_importers:
            print(f"  NEW   {k}", file=sys.stderr)
    if stale:
        print(f"\n::error::[retrieval-import-boundary] {len(stale)} stale entr(y/ies) — these modules no "
              "longer import the primitive (Phase 1 migration). Ratchet down with "
              "`python scripts/lint-retrieval-import-boundary.py --update`.", file=sys.stderr)
        for k in stale:
            print(f"  STALE {k}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
