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

# The writer contract guarantees that every saved :VerificationReport
# carries provenance via at least ONE of three channels:
#
#   1. [:VERIFIED] / [:EXTRACTED_FROM] edges      (kb_nli path)
#   2. ``source_urls`` array                      (web_search path)
#   3. ``verification_methods`` array             (cross_model + any path)
#
# An orphan is a node missing ALL THREE. Keeping the probe aligned with
# the writer's contract (and with the m0002 cleanup migration) means the
# count is a true regression signal: a non-zero result after v0.84.1
# implies a writer regression, nothing else.
_ORPHAN_CYPHER = """
MATCH (v:VerificationReport)
WHERE NOT (v)-[:VERIFIED|EXTRACTED_FROM]->()
  AND (v.source_urls IS NULL OR size(v.source_urls) = 0)
  AND (v.verification_methods IS NULL OR size(v.verification_methods) = 0)
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


_startup_logger = logging.getLogger("ai-companion.startup")


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


def validate_collection_dimensions(client: Any, expected_dim: int) -> list[dict[str, Any]]:
    """Return a list of mismatches for Chroma collections whose stored
    embedding dim differs from ``expected_dim``.

    * Empty collections (peek returns no embeddings) are skipped — the
      dim isn't observable until the first doc lands.
    * Mismatches are logged at ERROR level to ``ai-companion.startup``
      with the tag ``embedding_dim_mismatch`` and a pointer to
      ``/admin/collections/repair`` so the operator can act.
    * Returns shape: ``[{"collection": str, "actual_dim": int, "expected_dim": int}]``.
    """
    mismatches: list[dict[str, Any]] = []
    for c in client.list_collections():
        name = _collection_name(c) or "<unknown>"
        actual = _probe_collection_dim(c)
        if actual is None:
            # Empty/unknown — don't report.
            continue
        if actual != expected_dim:
            mismatches.append(
                {"collection": name, "actual_dim": actual, "expected_dim": expected_dim}
            )
            _startup_logger.error(
                "embedding_dim_mismatch: collection=%r actual=%d expected=%d — "
                "run POST /admin/collections/repair to re-ingest under the current embedder",
                name, actual, expected_dim,
            )
    return mismatches


def run_startup_dim_check() -> list[dict[str, Any]]:
    """Iterate Chroma collections at boot; report dim mismatches.

    The collection layer can silently trap old embeddings after a model
    swap — the embedder produces 768-dim vectors but the collection was
    initialised at 384.  CI never catches this (empty collections start
    life without a dim); prod surfaces it as opaque add/query errors.

    This check runs in the lifespan ``run_in_executor`` and MUST NOT
    raise: any failure degrades to an info log and returns ``[]``.
    Soft-fail semantics are deliberate — hard-failing would lock the
    operator out of the ``/admin/collections/repair`` endpoint the
    mismatch log message points to.
    """
    try:
        # Look up validator through the package namespace so tests that
        # do ``patch.object(app.startup, "validate_collection_dimensions", ...)``
        # can intercept the call.  (A same-module bare name would resolve
        # before the patch applies.)
        import app.startup as _startup_pkg
        from app.deps import get_chroma
        from core.utils.embeddings import get_embedding_dim

        expected = int(get_embedding_dim())
        chroma = get_chroma()
        return _startup_pkg.validate_collection_dimensions(chroma, expected)
    except Exception as exc:
        _startup_logger.info("startup dim check skipped (non-fatal): %s", exc)
        return []


def _probe_nli() -> dict[str, Any]:
    """Probe: is the NLI model loaded?  (Task 14: replaces silent swallow
    in ``core.utils.nli.warmup``.)"""
    try:
        from core.utils import nli
        loaded = bool(getattr(nli, "_MODEL_LOADED", False))
        return {"nli_model_loaded": loaded}
    except Exception as exc:
        return {"nli_model_loaded": False, "nli_error": str(exc)}


def _probe_internal_modules() -> dict[str, Any]:
    """Probe: which internal-only bootstrap modules loaded at startup?

    Tells operators — via ``/health.invariants.internal_modules`` —
    whether the running server is the internal build (Pro/Enterprise
    plus trading/boardroom) or the public build. A partial result
    (some True, some False) indicates a **build regression**: an
    internal-only module went missing between the build and the run.
    """
    try:
        from app.internal_modules import snapshot
        return {"internal_modules": snapshot()}
    except Exception as exc:
        return {"internal_modules": {}, "internal_modules_error": str(exc)}


def _probe_swallowed_errors(redis: Any) -> dict[str, Any]:
    """Probe: count of swallowed exceptions per module in the last hour.

    Exposed at ``/health.invariants.swallowed_errors_last_hour`` as a
    ``{module: count}`` dict. A rising count on a specific module
    ("ingestion.ai_categorize" spikes to 500/hr) is the visible signal
    that a silent-degradation class is actually firing — the whole
    point of ``log_swallowed_error``. Dashboards can alert on
    module-specific thresholds.
    """
    try:
        from core.utils.swallowed import swallowed_error_counts
        return {"swallowed_errors_last_hour": swallowed_error_counts(redis)}
    except Exception as exc:
        return {"swallowed_errors_last_hour": {}, "swallowed_errors_error": str(exc)}


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

    try:
        snapshot.update(_probe_internal_modules())
    except Exception as exc:
        logger.warning("internal_modules invariant probe failed: %s", exc)
        snapshot["errors"].append(f"internal_modules: {exc}")
        snapshot["internal_modules"] = {}

    try:
        snapshot.update(_probe_swallowed_errors(redis))
    except Exception as exc:
        logger.warning("swallowed_errors invariant probe failed: %s", exc)
        snapshot["errors"].append(f"swallowed_errors: {exc}")
        snapshot["swallowed_errors_last_hour"] = {}

    # Criticality gate — /health uses this to choose HTTP status.
    healthy = True
    if not snapshot.get("nli_model_loaded"):
        healthy = False
    snapshot["healthy_invariants"] = healthy
    return snapshot
