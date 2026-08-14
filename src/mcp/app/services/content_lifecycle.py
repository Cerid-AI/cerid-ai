# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Content-lifecycle coordinator — the single contract every delete-shaped and
hide-shaped surface funnels through (audit cluster CL-2).

Retrieval fans out over four stores (Neo4j ``:Artifact`` node, Chroma vector
chunks, BM25 keyword index, SPLADE sparse index) plus three caches (flat query
cache C1, semantic cache C2, graph serving cache C3). Historically every
delete/hide path honored a different, incomplete subset of those surfaces —
``session_wipe`` touched two of the four stores; no delete path outside re-ingest
ever called ``remove_chunks`` on the lexical indexes; soft-delete set an
``archived`` flag the vector arm never read. There was no enumerated contract and
no divergence probe, so the gaps were invisible to CI.

This module fixes the *class*, not the symptoms:

* :data:`REGISTRY` is the ONE enumerated list of chunk-bearing retrieval
  participants. Registering a store here once means every removal path AND the
  divergence probe inherit it — no more per-sprint subset.
* :func:`remove_content` / :func:`remove_orphan_chunks` fan a hard delete across
  every registered participant (physically dropping BM25/SPLADE on-disk JSONL
  lines, not just skipping at query time) and bust the query-result caches.
* :func:`hide_content` centralizes the soft-delete ``archived`` write and busts
  caches; the vector arm's query-time filter lives in
  ``core/agents/query_agent.py`` (the archived term in the post-retrieval join).

Layer note: this coordinator is app-layer (not ``core/``) because it must call
app-bound helpers — ``app.db.neo4j.artifacts.delete_artifact``,
``app.deps.get_*``, and ``utils.query_cache.invalidate_query_caches``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import config
from core.retrieval import bm25, sparse_index
from core.utils.swallowed import log_swallowed_error

_GRAPH_SERVING_CACHE_PATTERN = "cerid:graph:emb3d:*"


# --------------------------------------------------------------------------- #
# Enumerated participants registry — the anti-drift core.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Ctx:
    """Everything a participant needs to remove or probe a chunk set."""
    chunk_ids: list[str]
    domain: str
    chroma: Any


@dataclass(frozen=True)
class Participant:
    """A chunk-bearing retrieval store that every removal must reconcile.

    ``remove`` drops the chunk_ids and returns the count removed; ``residual``
    counts how many of the chunk_ids are STILL present (used by the four-store
    divergence probe / preservation test). Both are best-effort and must never
    raise — a store failing is logged and surfaced, never allowed to abort the
    fan-out (a logged-partial removal beats a silently-aborted one).
    """
    name: str
    remove: Callable[[_Ctx], int]
    residual: Callable[[_Ctx], int]


def _chroma_collections(ctx: _Ctx) -> list[Any]:
    """The collection(s) an artifact's chunks live in. Prefer its own domain
    collection; fall back to every collection when the domain is missing (chunk
    ids are globally unique, so a cross-collection delete is safe). Mirrors the
    established pattern in ``retention.apply_retention_plan``."""
    if ctx.domain:
        return [ctx.chroma.get_or_create_collection(name=config.collection_name(ctx.domain))]
    return list(ctx.chroma.list_collections())


def _chroma_remove(ctx: _Ctx) -> int:
    if not ctx.chunk_ids:
        return 0
    removed = 0
    for collection in _chroma_collections(ctx):
        try:
            collection.delete(ids=ctx.chunk_ids)
            removed = len(ctx.chunk_ids)
        except Exception as exc:  # noqa: BLE001 — per-collection delete is best-effort
            log_swallowed_error("content_lifecycle.chroma_remove", exc)
    return removed


def _chroma_residual(ctx: _Ctx) -> int:
    if not ctx.chunk_ids:
        return 0
    found: set[str] = set()
    for collection in _chroma_collections(ctx):
        try:
            got = collection.get(ids=ctx.chunk_ids)
            for cid in (got or {}).get("ids", []) or []:
                found.add(cid)
        except Exception as exc:  # noqa: BLE001 — best-effort probe
            log_swallowed_error("content_lifecycle.chroma_residual", exc)
    return len(found)


def _bm25_remove(ctx: _Ctx) -> int:
    try:
        return bm25.remove_chunks(ctx.domain, ctx.chunk_ids)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("content_lifecycle.bm25_remove", exc)
        return 0


def _sparse_remove(ctx: _Ctx) -> int:
    try:
        return sparse_index.remove_chunks(ctx.domain, ctx.chunk_ids)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("content_lifecycle.sparse_remove", exc)
        return 0


def _lexical_residual(module: Any, ctx: _Ctx) -> int:
    """Count chunk_ids still indexed in a bm25/sparse domain index. Best-effort:
    reads the index's doc-id set (the public surface is add/remove/search, so a
    membership probe reads the maintained id set)."""
    if not ctx.chunk_ids:
        return 0
    try:
        idx = module.get_index(ctx.domain)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("content_lifecycle.lexical_residual_index", exc)
        return 0
    id_set = getattr(idx, "_doc_id_set", None)
    if id_set is None:
        doc_ids = getattr(idx, "_doc_ids", None)
        id_set = set(doc_ids) if doc_ids is not None else None
    if id_set is None:
        return 0
    return sum(1 for cid in ctx.chunk_ids if cid in id_set)


REGISTRY: list[Participant] = [
    Participant("chroma", _chroma_remove, _chroma_residual),
    Participant("bm25", _bm25_remove, lambda ctx: _lexical_residual(bm25, ctx)),
    Participant("sparse", _sparse_remove, lambda ctx: _lexical_residual(sparse_index, ctx)),
]


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass
class RemovalResult:
    found: bool
    artifact_id: str | None = None
    domain: str = ""
    chunk_ids: list[str] = field(default_factory=list)
    removed: dict[str, int] = field(default_factory=dict)   # participant -> count removed

    @property
    def chunk_count(self) -> int:
        return len(self.chunk_ids)


# --------------------------------------------------------------------------- #
# Cache invalidation (query-result caches C1+C2 via the unified contract; the
# graph serving cache C3 via a direct pattern bust — C3's job-ownership stays CL-6).
# --------------------------------------------------------------------------- #
def invalidate_caches(trigger: str, redis: Any | None = None) -> None:
    """Best-effort bust of the query-result caches (C1+C2) and the graph serving
    cache (C3).

    Public: also called by delete paths outside this module that bypass
    :func:`remove_content` (e.g. ``session_wipe._delete_verified_memory``,
    which deletes a verified-memory Chroma doc + ``:Memory`` node directly
    since neither carries ``chunk_ids`` for the fan-out path — AF-096).

    Cache invalidation is freshness, not correctness: the removal that calls this
    has ALREADY dropped the node + chunks from the stores, so a redis outage here
    must NEVER propagate and make a successful hard delete look failed (which
    would under-count retention/quarantine purges). Every failure — including an
    unavailable redis — is logged and swallowed. Resolves the redis handle
    internally so callers never touch an unguarded ``get_redis()``.
    """
    try:
        from app.deps import get_redis
        client = redis if redis is not None else get_redis()
    except Exception as exc:  # noqa: BLE001 — redis unavailable; delete already succeeded
        log_swallowed_error("content_lifecycle.cache_bust_redis_unavailable", exc)
        return

    try:
        # Threaded, not blocking: both invalidators SCAN the whole Redis
        # keyspace, so on a large DB this added 30-150s to every delete while
        # the stores themselves were already consistent in <1s. The ingest
        # re-ingest path (ingestion.py) uses the threaded variant for exactly
        # this reason; deletes were left on the blocking one.
        from utils.query_cache import invalidate_query_caches_threaded
        invalidate_query_caches_threaded(trigger=trigger, redis=client)  # C1 + C2
    except Exception as exc:  # noqa: BLE001 — query-cache bust is best-effort freshness
        log_swallowed_error("content_lifecycle.query_cache_bust", exc)

    try:
        for key in client.scan_iter(match=_GRAPH_SERVING_CACHE_PATTERN):
            client.delete(key)
    except Exception as exc:  # noqa: BLE001 — C3 bust is best-effort viz freshness
        log_swallowed_error("content_lifecycle.graph_serving_cache_bust", exc)


# --------------------------------------------------------------------------- #
# Public operations
# --------------------------------------------------------------------------- #
def _fan_out_removal(chunk_ids: list[str], domain: str, chroma: Any) -> dict[str, int]:
    ctx = _Ctx(chunk_ids=chunk_ids, domain=domain or "", chroma=chroma)
    return {p.name: p.remove(ctx) for p in REGISTRY}


def remove_content(
    artifact_id: str,
    *,
    neo4j: Any | None = None,
    chroma: Any | None = None,
    redis: Any | None = None,
) -> RemovalResult:
    """HARD delete an artifact everywhere it is retrievable.

    Deletes the Neo4j ``:Artifact`` node (which yields the chunk_ids), fans the
    chunk deletion across every registry participant (Chroma + BM25 + SPLADE,
    physically dropping the on-disk JSONL lines), then busts the query-result
    caches (C1+C2) and the graph serving cache (C3). Every delete-shaped caller
    funnels through here so no store is ever left orphaned.
    """
    from app.db.neo4j.artifacts import delete_artifact
    from app.deps import get_chroma, get_neo4j

    neo4j = neo4j or get_neo4j()
    info = delete_artifact(neo4j, artifact_id)
    if not info.get("deleted"):
        return RemovalResult(found=False, artifact_id=artifact_id)

    chunk_ids = info.get("chunk_ids") or []
    domain = info.get("domain") or ""
    chroma = chroma or get_chroma()
    removed = _fan_out_removal(chunk_ids, domain, chroma)

    # Stores are now clean — the delete has succeeded. The cache bust below is
    # best-effort freshness and cannot fail this result (see invalidate_caches).
    invalidate_caches(trigger=f"lifecycle.remove:{artifact_id}", redis=redis)

    return RemovalResult(
        found=True, artifact_id=artifact_id, domain=domain,
        chunk_ids=chunk_ids, removed=removed,
    )


def remove_orphan_chunks(
    chunk_ids: list[str],
    domain: str,
    *,
    chroma: Any | None = None,
    redis: Any | None = None,
    bust_caches: bool = True,
) -> RemovalResult:
    """Remove chunk vectors/postings that have NO Neo4j node — the ingestion
    concurrent-duplicate rollback path (AF-070), where chunks were staged in the
    stores but the Neo4j ``create_artifact`` was rejected by the content-hash
    constraint. Same fan-out as :func:`remove_content`, minus the node delete.

    ``bust_caches`` defaults True for contract uniformity but callers rolling
    back never-retrievable *pending* chunks (which no query-result cache can
    reference) pass False to avoid flushing the whole query cache on every
    concurrent duplicate under bulk-ingest load.
    """
    from app.deps import get_chroma

    if not chunk_ids:
        return RemovalResult(found=False, domain=domain)
    chroma = chroma or get_chroma()
    removed = _fan_out_removal(chunk_ids, domain, chroma)

    if bust_caches:
        invalidate_caches(trigger="lifecycle.rollback_orphan_chunks", redis=redis)

    return RemovalResult(found=True, domain=domain, chunk_ids=list(chunk_ids), removed=removed)


def hide_content(
    artifact_id: str,
    *,
    neo4j: Any | None = None,
    redis: Any | None = None,
    archived_at: str | None = None,
    extra_props: dict[str, Any] | None = None,
) -> bool:
    """SOFT delete / quarantine — mark the artifact ``archived`` and make it
    unretrievable without dropping its chunks (reversible by clearing the flag).

    Centralizes the ``a.archived = true`` write that was previously inlined in
    each caller (fundamentals soft-delete, temporal quarantine) and busts the
    query-result caches so the hidden artifact is not served warm within the TTL.
    Query-time enforcement of ``archived`` on the vector arm lives in
    ``core/agents/query_agent.py`` (the archived term in the post-retrieval join);
    the ``vector-visible-archived`` divergence probe is the standing backstop.

    ``extra_props`` carries caller-specific fields (quarantine's ``purge_after`` /
    ``quarantine_reason``) merged onto the node in the same write.
    """
    from app.db.neo4j.artifacts import set_archived
    from app.deps import get_neo4j

    neo4j = neo4j or get_neo4j()
    ok = set_archived(neo4j, artifact_id, archived_at=archived_at, extra=extra_props)
    if ok:
        # Node is flagged — the hide has succeeded. The cache bust is best-effort
        # freshness and cannot fail this result (see invalidate_caches).
        invalidate_caches(trigger=f"lifecycle.hide:{artifact_id}", redis=redis)
    return ok
