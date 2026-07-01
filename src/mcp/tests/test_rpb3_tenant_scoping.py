# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""RPB-3 — every ChromaDB ``collection.query`` in the memory/retrieval path is
tenant-scoped via ``with_tenant_scope``.

Provable single-user NO-OP (``with_tenant_scope`` returns ``where`` unchanged in
single-user mode), so this changes nothing for the 1.0 single-user build; it
closes the latent multi-user cross-tenant read the audit (2026-06-29) flagged.
The source guard fails if a new query site forgets to scope.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_MCP = Path(__file__).resolve().parent.parent

# The files carrying the tenant-sensitive collection.query sites (RPB-3 spec).
_SCOPED_FILES = [
    "core/agents/rectify.py",
    "core/agents/memory.py",
    "core/agents/memory_consolidation.py",
    "core/agents/hallucination/verification.py",
    "utils/dedup.py",
]


def test_with_tenant_scope_is_single_user_noop():
    from core.context.identity import with_tenant_scope

    assert with_tenant_scope(None) is None
    where = {"memory_type": {"$in": ["fact", "decision"]}}
    assert with_tenant_scope(where) == where  # unchanged in single-user mode


def test_every_collection_query_is_tenant_scoped():
    """Each collection.query in these files must be paired with with_tenant_scope."""
    offenders: list[str] = []
    for rel in _SCOPED_FILES:
        src = (_MCP / rel).read_text(encoding="utf-8")
        n_query = src.count("collection.query(")
        n_scope = src.count("with_tenant_scope(")
        if n_query > n_scope:
            offenders.append(f"{rel}: {n_query} collection.query but only {n_scope} with_tenant_scope")
    assert not offenders, "un-tenant-scoped collection.query site(s): " + "; ".join(offenders)
