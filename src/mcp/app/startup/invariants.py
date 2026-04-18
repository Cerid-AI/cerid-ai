# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Startup invariants — run at lifespan start and on each /health poll.

Reports observable facts beyond "connected", derived from the audit
findings of 2026-04-17:

    * Chroma collections with zero embeddings (10/13 found empty in audit).
    * Neo4j :VerificationReport nodes with no outgoing edges (audit: 16/16).
    * NLI model load status (see ``core.utils.nli.warmup``).

Each check is independently fault-tolerant — a broken subsystem contributes
an entry to ``errors`` rather than crashing the snapshot.

``healthy_invariants`` is the single boolean that ``/health`` uses to flip
to HTTP 503.  Orphan VerificationReports are *not* a hard failure post
Task 2 (historical data cannot be backfilled), but NLI load failure *is*
— verification, Self-RAG, and RAGAS all depend on it.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("ai-companion.invariants")

# Cypher reused from migrations/m0001 — a :VerificationReport is "orphan"
# when it has no outgoing provenance edge AND no source_urls array.  This
# matches the writer's post-Task-2 contract (every new report must have at
# least one of the two).
_ORPHAN_CYPHER = """
MATCH (v:VerificationReport)
WHERE NOT (v)-[:VERIFIED|EXTRACTED_FROM]->()
  AND (v.source_urls IS NULL OR size(v.source_urls) = 0)
RETURN count(v) AS orphans
"""


def _collection_name(c: Any) -> str | None:
    """Pull the collection name regardless of driver return shape."""
    if isinstance(c, dict):
        return c.get("name")
    return getattr(c, "name", None)


def _collection_count(c: Any) -> int:
    """Pull the collection item count; default to 0 on any driver error."""
    try:
        if isinstance(c, dict):
            return int(c.get("count", 0))
        count_attr = getattr(c, "count", None)
        if callable(count_attr):
            return int(count_attr())
        return int(count_attr or 0)
    except Exception:
        return 0


def _probe_chroma(chroma: Any) -> dict[str, Any]:
    """Probe: any collection with count == 0?  Returns the names for
    dashboards to render."""
    empty: list[str] = []
    cols = chroma.list_collections()
    for c in cols:
        name = _collection_name(c)
        if not name:
            continue
        if _collection_count(c) == 0:
            empty.append(name)
    return {"collections_empty": empty}


def _probe_neo4j(neo4j: Any) -> dict[str, Any]:
    """Probe: count of orphan VerificationReport nodes (post-Task-2 baseline)."""
    with neo4j.session() as session:
        row = session.run(_ORPHAN_CYPHER).single()
    orphans = 0
    if row is not None:
        # neo4j.Record supports dict-style access; MagicMock returns dicts.
        try:
            orphans = int(row["orphans"])
        except (KeyError, TypeError):
            orphans = int(row.get("orphans", 0)) if hasattr(row, "get") else 0
    return {"verification_report_orphans": orphans}


def _probe_nli() -> dict[str, Any]:
    """Probe: is the NLI model loaded?  (Task 14: replaces silent swallow
    in ``core.utils.nli.warmup``.)"""
    try:
        from core.utils import nli
        loaded = bool(getattr(nli, "_MODEL_LOADED", False))
        return {"nli_model_loaded": loaded}
    except Exception as exc:
        return {"nli_model_loaded": False, "nli_error": str(exc)}


def run_invariants(chroma: Any, redis: Any, neo4j: Any) -> dict[str, Any]:
    """Build a snapshot of observable invariants.

    Critical invariants (flip ``healthy_invariants`` to False when violated):
        * ``nli_model_loaded`` must be True.

    Non-critical (reported for dashboards, don't flip healthy):
        * ``collections_empty``
        * ``verification_report_orphans`` — historical data can't be
          backfilled; the migration (m0001) already ran.  New orphans would
          indicate a writer regression but we don't 503 on them here.

    Returns a dict that is always JSON-serializable and never raises.
    """
    snapshot: dict[str, Any] = {"errors": []}

    try:
        snapshot.update(_probe_chroma(chroma))
    except Exception as exc:
        logger.warning("chroma invariant probe failed: %s", exc)
        snapshot["errors"].append(f"chroma: {exc}")
        snapshot["collections_empty"] = []

    try:
        snapshot.update(_probe_neo4j(neo4j))
    except Exception as exc:
        logger.warning("neo4j invariant probe failed: %s", exc)
        snapshot["errors"].append(f"neo4j: {exc}")
        snapshot["verification_report_orphans"] = -1

    try:
        snapshot.update(_probe_nli())
    except Exception as exc:
        logger.warning("nli invariant probe failed: %s", exc)
        snapshot["errors"].append(f"nli: {exc}")
        snapshot["nli_model_loaded"] = False

    # Criticality gate — /health uses this to choose HTTP status.
    healthy = True
    if not snapshot.get("nli_model_loaded"):
        healthy = False
    snapshot["healthy_invariants"] = healthy
    return snapshot
