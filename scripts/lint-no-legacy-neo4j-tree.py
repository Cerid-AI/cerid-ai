#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Guard against resurrection of the legacy ``src/mcp/db/neo4j`` shim tree.

Prior to the Neo4j unification sprint, the repo carried two Neo4j data-layer
trees:

  * ``src/mcp/db/neo4j/``   — bridge shims re-exporting the canonical tree
  * ``src/mcp/app/db/neo4j/`` — the real implementation

The shim tree was deleted once all 17 callers migrated to the canonical
``app.db.neo4j.*`` path. An ``import-linter`` contract on imports alone
wouldn't catch a silent resurrection (a re-added tree with no callers
would slip through). This script fails non-zero if the legacy path
exists at all, which is the strongest invariant available.

Usage:
    python scripts/lint-no-legacy-neo4j-tree.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATH = REPO_ROOT / "src" / "mcp" / "db" / "neo4j"


def main() -> int:
    if LEGACY_PATH.exists():
        print(
            f"ERROR: legacy shim tree has reappeared at {LEGACY_PATH.relative_to(REPO_ROOT)}\n"
            "       Neo4j data-layer code belongs at src/mcp/app/db/neo4j/.\n"
            "       See docs/COMPLETED_PHASES.md (Neo4j tree unification) for context.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
