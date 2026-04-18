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


def _probe_collection_dim(collection: Any) -> int | None:
    """Return the embedding dimension of a Chroma collection, or None.

    Chroma exposes dim in different shapes across versions.  We try, in
    order: ``metadata['dimension']``, ``metadata['dim']``, and a single
    ``.peek(1)`` probe whose first embedding width we measure.  All
    probes are wrapped in broad ``try`` — a None return means "unknown"
    and the caller must treat it as non-fatal.
    """
    try:
        md = getattr(collection, "metadata", None) or {}
        for key in ("dimension", "dim", "hnsw:dim"):
            if key in md and md[key] is not None:
                return int(md[key])
    except Exception:
        pass
    try:
        peek = collection.peek(1)
        emb = (peek or {}).get("embeddings") or []
        if emb and emb[0] is not None:
            return int(len(emb[0]))
    except Exception:
        pass
    return None


def run_startup_dim_check() -> dict[str, Any]:
    """Iterate Chroma collections at boot; warn on dim mismatches.

    The collection layer can silently trap old embeddings after a model
    swap — the embedder produces 768-dim vectors but the collection was
    initialised at 384.  CI never catches this (empty collections start
    life without a dim); prod surfaces it as opaque add/query errors.

    This check runs in the lifespan ``run_in_executor`` and MUST NOT
    raise: any failure degrades to a log warning.  Returns a summary
    dict for observability dashboards.
    """
    summary: dict[str, Any] = {"mismatches": [], "errors": []}
    try:
        from app.deps import get_chroma
        from core.utils.embeddings import get_embedding_dim

        expected = int(get_embedding_dim())
        chroma = get_chroma()
        for c in chroma.list_collections():
            name = _collection_name(c) or "<unknown>"
            actual = _probe_collection_dim(c)
            if actual is not None and actual != expected:
                summary["mismatches"].append(
                    {"collection": name, "expected": expected, "actual": actual}
                )
                logger.warning(
                    "collection %r dim mismatch: actual=%d expected=%d "
                    "(rerun admin/collections/repair to remediate)",
                    name, actual, expected,
                )
    except Exception as exc:
        summary["errors"].append(str(exc))
        logger.warning("startup dim check errored (non-fatal): %s", exc)
    return summary


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
