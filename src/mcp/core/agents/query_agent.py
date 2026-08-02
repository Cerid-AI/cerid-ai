# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Multi-domain knowledge base search with LLM reranking."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from typing import Any

import httpx
import numpy as np

import config
from core.context.identity import chunk_matches_tenant, with_tenant_scope
from core.contracts.stores import GraphStore
from core.observability.span_helpers import breadcrumb, span
from core.utils.cache import log_event
from core.utils.circuit_breaker import CircuitOpenError
from core.utils.embeddings import l2_distance_to_relevance
from core.utils.llm_parsing import parse_llm_json
from core.utils.swallowed import log_swallowed_error
from core.utils.text import STOPWORDS as _STOPWORDS
from core.utils.text import WORD_RE as _WORD_RE

logger = logging.getLogger("ai-companion.query_agent")

# Relaxed junk floor when the caller scoped retrieval to a specific file via
# metadata_filter — generic questions against one document must still return
# its chunks even at modest similarity.
_METADATA_SCOPED_MIN_RELEVANCE = 0.05


# ---------------------------------------------------------------------------
# Step timer for latency tracing
# ---------------------------------------------------------------------------

class StepTimer:
    """Lightweight latency tracker for pipeline steps.

    Zero overhead when disabled — the ``step()`` context manager becomes a no-op.
    """

    __slots__ = ("_enabled", "_timings", "_t0")

    def __init__(self, enabled: bool = False):
        self._enabled = enabled
        self._timings: dict[str, float] = {}
        self._t0 = time.monotonic() if enabled else 0.0

    @contextmanager
    def step(self, name: str):
        if not self._enabled:
            yield
            return
        start = time.monotonic()
        yield
        self._timings[name] = round(time.monotonic() - start, 4)

    def result(self) -> dict[str, float]:
        if not self._enabled:
            return {}
        self._timings["total"] = round(time.monotonic() - self._t0, 4)
        return dict(self._timings)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_chroma_result(
    content: str,
    relevance: float,
    chunk_id: str,
    domain: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Build a standardized result dict from a ChromaDB chunk."""
    # RAG Phase 1.1 — provenance spine. Every KB vector result carries a
    # canonical ``source_type`` ("pack" when the chunk originated from an
    # installed knowledge pack, else "kb") plus a best-effort ``created_at``
    # (chunk authored/created date, falling back to ingest date) so the
    # envelope, prompt, ranking, and verifier can all reason about staleness
    # and source class downstream.
    pack_id = metadata.get("pack_id", "")
    created_at = (
        metadata.get("created_at")
        or metadata.get("ingested_at")
        or None
    )
    return {
        "content": content,
        "relevance": round(relevance, 4),
        "artifact_id": metadata.get("artifact_id", ""),
        "filename": metadata.get("filename", ""),
        "domain": domain,
        "chunk_index": metadata.get("chunk_index", 0),
        "collection": config.collection_name(domain),
        "chunk_id": chunk_id,
        "source_type": "pack" if pack_id else "kb",
        "created_at": created_at,
        "pack_id": pack_id,
        "ingested_at": metadata.get("ingested_at", ""),
        "sub_category": metadata.get("sub_category", ""),
        "tags_json": metadata.get("tags_json", "[]"),
        "keywords": metadata.get("keywords", "[]"),
        "memory_type": metadata.get("memory_type", ""),
        # RAG C2.6 — preserve the parent linkage so the per-domain
        # post-ranking pass can substitute the parent text into ``content``
        # before reranking. Empty string when the row is not a child.
        "chunk_level": metadata.get("chunk_level", ""),
        "parent_chunk_id": metadata.get("parent_chunk_id", ""),
    }


# ---------------------------------------------------------------------------
# RAG C2.6 — parent-child retrieval helpers
# ---------------------------------------------------------------------------


def _parent_child_enabled() -> bool:
    """Return whether parent-child retrieval is active at runtime.

    Two gates, both must be True:
      1. Tier availability via ``is_feature_enabled("parent_child_retrieval")``
         (community-tier+ since the 2026-05-20 rebalance — RAG quality is
         plumbing, not a Pro axis, so this is True for all tiers today).
      2. Deployment opt-in via ``ENABLE_PARENT_CHILD_RETRIEVAL`` env var
         (default ``false``). Off-by-default because parent-child retrieval
         can have non-trivial perf implications at large KB sizes; operators
         turn it on per deployment after validating their corpus.

    Tests can flip either gate independently:
      - Patch ``config.features.FEATURE_FLAGS["parent_child_retrieval"]``
        to test tier-gating behaviour.
      - Patch ``ENABLE_PARENT_CHILD_RETRIEVAL`` env var to test deployment
        activation.

    Mirrors ``utils.chunker.parent_child_enabled`` for the chunker side of
    the pipeline.
    """
    import os

    from config.features import is_feature_enabled
    if not is_feature_enabled("parent_child_retrieval"):
        return False
    return os.getenv("ENABLE_PARENT_CHILD_RETRIEVAL", "false").lower() in (
        "true",
        "1",
    )


def _pc_fuse_child_filter(where: dict | None) -> dict | None:
    """Fuse ``{"chunk_level": "child"}`` into a Chroma ``where`` clause.

    Used by the multi-domain vector query so retrieval ranks only against
    child chunks when parent-child retrieval is on. Stacks cleanly with
    ``$and`` if other clauses are already present.
    """
    child_clause: dict[str, Any] = {"chunk_level": "child"}
    if where is None:
        return child_clause
    if "chunk_level" in where:
        return where
    if "$and" in where:
        for clause in where["$and"]:
            if isinstance(clause, dict) and "chunk_level" in clause:
                return where
        return {"$and": [*where["$and"], child_clause]}
    return {"$and": [where, child_clause]}


def _substitute_parent_content(
    results: list[dict[str, Any]],
    collection: Any,
) -> list[dict[str, Any]]:
    """Substitute parent-chunk text into each child result.

    For each result that has a non-empty ``parent_chunk_id``, fetch the
    parent's document from the same collection (in one batch) and replace
    the result's ``content`` field with the parent's text. The child's
    relevance score, chunk_id, and other metadata are preserved — the
    intent is to let the cross-encoder rerank against richer context.

    Results without a parent_chunk_id (legacy single-tier corpora, or
    parents that were already ranked directly) fall through unchanged.
    """
    if not results:
        return results

    parent_ids = sorted({
        r.get("parent_chunk_id", "")
        for r in results
        if r.get("parent_chunk_id")
    })
    if not parent_ids:
        return results

    parent_text_by_id: dict[str, str] = {}
    try:
        fetched = collection.get(ids=parent_ids, include=["documents"])
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "core.agents.query_agent.parent_fetch", e,
        )
        return results

    fetched_ids = fetched.get("ids", []) if isinstance(fetched, dict) else []
    fetched_docs = fetched.get("documents", []) if isinstance(fetched, dict) else []
    for pid, doc in zip(fetched_ids, fetched_docs):
        if isinstance(doc, str) and doc:
            parent_text_by_id[pid] = doc

    if not parent_text_by_id:
        return results

    # Copy each dict instead of mutating in place — a future shared/cached
    # result list won't be corrupted by this substitution pass.
    substituted: list[dict[str, Any]] = []
    for r in results:
        pid = r.get("parent_chunk_id", "")
        if pid and pid in parent_text_by_id:
            substituted.append({
                **r,
                "content": parent_text_by_id[pid],
                "parent_substituted": True,
            })
        else:
            substituted.append(r)
    return substituted


# ---------------------------------------------------------------------------
# Cross-domain affinity
# ---------------------------------------------------------------------------

def _get_adjacent_domains(requested: list[str]) -> dict[str, float]:
    """Return non-requested domains with their max affinity score."""
    requested_set = set(requested)
    adjacent: dict[str, float] = {}
    for req in requested:
        explicit = config.DOMAIN_AFFINITY.get(req, {})
        for other in config.DOMAINS:
            if other in requested_set:
                continue
            weight = explicit.get(other, config.CROSS_DOMAIN_DEFAULT_AFFINITY)
            adjacent[other] = max(adjacent.get(other, 0.0), weight)
    return adjacent


# ---------------------------------------------------------------------------
# Follow-up retrieval budget guards (CH4)
# ---------------------------------------------------------------------------
#
# A chat follow-up enriches the query with conversation terms and fans out
# across every domain (all but "conversations"). On CPU inference the
# multi-domain rerank of that longer query blows the wall-clock budget, and the
# budget guard then discards *everything* (ungrounded answer). Two non-arbitrary
# levers keep follow-ups within budget while staying coherent:
#   * prioritize the most-likely domains first and cap the *tail* — so a capped
#     retrieval keeps the most-relevant domains, never an arbitrary subset;
#   * trim per-domain candidate depth (fewer rerank candidates per collection).


@functools.lru_cache(maxsize=1)
def _domain_keyword_index() -> dict[str, frozenset[str]]:
    """Per-domain lowercase keyword set from the taxonomy (name + description
    words + sub-categories). Cached — the taxonomy is static after import."""
    from config.taxonomy import TAXONOMY

    idx: dict[str, frozenset[str]] = {}
    for name, meta in TAXONOMY.items():
        words = set(re.findall(r"[a-z]{3,}", name.lower()))
        words.update(re.findall(r"[a-z]{3,}", str(meta.get("description", "")).lower()))
        words.update(re.findall(r"[a-z]{3,}", " ".join(meta.get("sub_categories", [])).lower()))
        words.discard("general")  # too generic to discriminate between domains
        idx[name] = frozenset(words)
    return idx


def _prioritize_domains(query: str, domains: list[str], cap: int) -> list[str]:
    """Order ``domains`` most-likely-first for ``query`` via a cheap lexical
    match against the taxonomy, then keep the top ``cap`` *only when a relevance
    signal exists*. This bounds a follow-up's all-domain fan-out while keeping
    partial retrieval coherent — the dropped domains are the least relevant, and
    nothing is dropped when there is no basis to choose (no match, or cap<=0).
    Ties and unmatched domains preserve their original relative order.
    """
    idx = _domain_keyword_index()
    q_words = set(re.findall(r"[a-z]{3,}", query.lower()))
    scored = sorted(
        ((len(q_words & idx.get(d, frozenset())), -i, d) for i, d in enumerate(domains)),
        reverse=True,
    )
    ranked = [d for _, _, d in scored]
    top_score = scored[0][0] if scored else 0
    if cap > 0 and top_score > 0 and len(ranked) > cap:
        return ranked[:cap]
    return ranked


def _followup_retrieval_top_k(
    base_top_k: int,
    conversation_messages: list[dict[str, str]] | None,
    explicit_domains: list[str] | None,
) -> int:
    """Trim per-domain candidate depth on a follow-up (all-domain enriched)
    query so the multi-domain rerank stays within budget. No-op when an explicit
    domain filter already bounds the fan-out, when it isn't a follow-up, or when
    the configured cap is disabled / not below the base depth."""
    if conversation_messages and explicit_domains is None:
        cap = getattr(config, "AGENT_QUERY_FOLLOWUP_TOP_K", 0)
        if cap and cap < base_top_k:
            return cap
    return base_top_k


# ---------------------------------------------------------------------------
# Conversation-aware query enrichment
# ---------------------------------------------------------------------------


def _enrich_query(
    query: str,
    conversation_messages: list[dict[str, str]],
    max_context_messages: int = 5,
    max_terms: int = 10,
) -> str:
    """Enrich a query with recency-weighted terms from recent conversation messages.

    More recent messages contribute more terms to the enriched query, improving
    retrieval relevance for the current conversational context. Term slots are
    allocated via exponential decay (newest message gets ~half the budget).

    Returns the original query if no useful context terms are found.
    """
    if not conversation_messages:
        return query

    # Collect text from recent user messages only (skip system/assistant)
    user_texts: list[str] = []
    for msg in conversation_messages[-max_context_messages:]:
        if msg.get("role") == "user":
            user_texts.append(msg.get("content", ""))

    if not user_texts:
        return query

    # Allocate term slots per message using exponential decay (most recent = most slots)
    n = len(user_texts)
    if n == 1:
        slots = [max_terms]
    else:
        raw_weights = [0.5 ** i for i in range(n)]
        total_weight = sum(raw_weights)
        float_slots = [w / total_weight * max_terms for w in raw_weights]
        slots = [max(1, round(s)) for s in float_slots]
        # Adjust to hit exact total
        diff = max_terms - sum(slots)
        if diff > 0:
            slots[0] += diff
        elif diff < 0:
            for i in range(n - 1, -1, -1):
                if slots[i] > 1:
                    remove = min(slots[i] - 1, -diff)
                    slots[i] -= remove
                    diff += remove
                    if diff == 0:
                        break

    # Extract terms per message, respecting per-message slot allocation
    query_terms = {w.lower() for w in _WORD_RE.findall(query)}
    context_terms: list[str] = []
    seen: set = set()

    for idx, text in enumerate(reversed(user_texts)):  # Most recent first
        msg_limit = slots[idx] if idx < len(slots) else 1
        msg_count = 0
        words = _WORD_RE.findall(text)
        for word in words:
            lower = word.lower()
            if (
                len(lower) > 2
                and lower not in _STOPWORDS
                and lower not in query_terms
                and lower not in seen
            ):
                seen.add(lower)
                context_terms.append(lower)
                msg_count += 1
                if msg_count >= msg_limit:
                    break

    if not context_terms:
        return query

    return f"{query} {' '.join(context_terms)}"


# ---------------------------------------------------------------------------
# Phase O.1 — pending-chunk filter
# ---------------------------------------------------------------------------

# Chokepoint list: every Chroma query in the retrieval path must pass through
# ``_exclude_pending``.  Current sites (as of 2026-05-10):
#   1. multi_domain_query → query_domain (vector + BM25 hybrid main path)
#   2. graph_expand_results → _fetch_related (relationship traversal expansion)
#   3. graph_expand_results_via_entities (entity-neighbourhood expansion)
# The BM25-only fallback path (collection.get with explicit IDs) applies
# ``chunk_matches_tenant`` on the result metadata; it gets the committed-only
# guard by checking the ``cerid_state`` key there too (see BM25 path below).
_CERID_STATE_EXCLUDE_PENDING: dict[str, Any] = {"cerid_state": {"$ne": "pending"}}


def _exclude_pending(where: dict | None) -> dict | None:
    """Fuse the pending-exclusion clause into a Chroma ``where`` dict.

    Returns ``None`` when the environment variable
    ``CERID_FILTER_PENDING_CHUNKS`` is set to a falsy value (opt-out for
    tests that need to observe pending rows directly).  Otherwise always
    fuses ``{"cerid_state": {"$ne": "pending"}}`` using ChromaDB's
    ``$and`` so it stacks cleanly with tenant and domain filters.
    """
    import os
    if os.getenv("CERID_FILTER_PENDING_CHUNKS", "true").strip().lower() in (
        "false", "0", "no", "off"
    ):
        return where

    excl = _CERID_STATE_EXCLUDE_PENDING
    if where is None:
        return excl
    # Already carries the filter — don't double-wrap.
    if "cerid_state" in where:
        return where
    # Preserve existing $and lists
    if "$and" in where:
        # Check if cerid_state already present inside the $and list
        for clause in where["$and"]:
            if "cerid_state" in clause:
                return where
        return {"$and": [*where["$and"], excl]}
    return {"$and": [where, excl]}


# ---------------------------------------------------------------------------
# Multi-domain retrieval
# ---------------------------------------------------------------------------

async def multi_domain_query(
    query: str,
    domains: list[str] | None = None,
    top_k: int = 10,
    chroma_client: Any | None = None,
    metadata_filter: dict | None = None,
) -> list[dict[str, Any]]:
    """Query multiple ChromaDB collections in parallel and aggregate results."""
    if domains is None:
        domains = config.DOMAINS

    # Custom/client-defined domains are allowed: external clients use Cerid as
    # a backend and ingest to their own domain names. Built-in DOMAINS are the
    # default set; an unknown domain is queried when its collection exists and
    # degrades to empty results otherwise (see query_domain below). Warn, never
    # reject — a hard 400 forces external clients into shims (GA P0.1).
    custom_domains = [d for d in domains if d not in config.DOMAINS]
    if custom_domains:
        logger.warning(
            "multi_domain_query: non-built-in domain(s) %s — querying as custom "
            "client domains (built-in: %s)",
            custom_domains, config.DOMAINS,
        )

    if chroma_client is None:
        raise ValueError("chroma_client is required")

    # Pre-check which collections actually exist to skip missing domains fast
    try:
        existing_collections = {c.name for c in chroma_client.list_collections()}
    except Exception as exc:
        log_swallowed_error('core.agents.query_agent', exc)
        existing_collections = set()

    pc_enabled = _parent_child_enabled()

    async def query_domain(domain: str) -> list[dict[str, Any]]:
        """Query a single domain collection (vector + BM25 hybrid)."""
        col_name = config.collection_name(domain)
        if existing_collections and col_name not in existing_collections:
            return []  # Skip missing collections without HTTP round-trip
        try:
            collection = chroma_client.get_collection(name=col_name)

            # Phase O.1: exclude pending (un-committed) chunks from retrieval.
            # Order matters: with_tenant_scope MUST see the raw caller filter
            # so it can detect cross-tenant escape attempts (a caller-supplied
            # `tenant_id: <other>` nested inside `$and` would be invisible).
            # Layer the pending-exclude on AFTER tenant scoping.
            _where = _exclude_pending(with_tenant_scope(metadata_filter))
            # RAG C2.6 — when parent-child retrieval is on, rank only against
            # child chunks. The post-ranking pass below swaps each child's
            # ``content`` for the parent's text so downstream rerank +
            # context assembly operate on the richer parent context while
            # the relevance score still reflects the precise child match.
            if pc_enabled:
                _where = _pc_fuse_child_filter(_where)
            query_kwargs: dict[str, Any] = {
                "query_texts": [query],
                "n_results": top_k,
                "include": ["documents", "metadatas", "distances"],
                "where": _where,
            }
            # ChromaDB client uses sync HTTP — offload to thread to avoid
            # blocking the event loop when multiple domains query in parallel.
            results = await asyncio.to_thread(collection.query, **query_kwargs)

            formatted = []
            seen_ids: set = set()
            if results["ids"] and results["ids"][0]:
                for i, chunk_id in enumerate(results["ids"][0]):
                    distance = results["distances"][0][i] if results["distances"] else 1.0
                    relevance = l2_distance_to_relevance(distance)
                    metadata = results["metadatas"][0][i] if results["metadatas"] else {}

                    formatted.append(_format_chroma_result(
                        content=results["documents"][0][i],
                        relevance=relevance,
                        chunk_id=chunk_id,
                        domain=domain,
                        metadata=metadata,
                    ))
                    seen_ids.add(chunk_id)

            from core.retrieval import bm25 as bm25_mod
            from core.retrieval import sparse_index as sparse_mod
            bm25_avail = bm25_mod.is_available()
            # Cycle 3.2 — SPLADE-v3 sparse retrieval. Only consulted when
            # HYBRID_FUSION_MODE=tri_rrf so the default path stays
            # zero-cost (no encoder load, no JSONL probe).
            fusion_mode = getattr(config, "HYBRID_FUSION_MODE", "weighted_sum")
            tri_rrf = fusion_mode == "tri_rrf" and sparse_mod.is_available()

            if bm25_avail or tri_rrf:
                fetch_tasks: list = []
                if bm25_avail:
                    fetch_tasks.append(asyncio.to_thread(
                        bm25_mod.search_bm25, domain, query, top_k,
                    ))
                if tri_rrf:
                    fetch_tasks.append(asyncio.to_thread(
                        sparse_mod.search_sparse, domain, query, top_k,
                    ))
                fetch_results = await asyncio.gather(*fetch_tasks)

                # Unpack in registration order so the indices match.
                ridx = 0
                bm25_hits: list[tuple[str, float]] = []
                sparse_hits: list[tuple[str, float]] = []
                if bm25_avail:
                    bm25_hits = list(fetch_results[ridx])
                    ridx += 1
                if tri_rrf:
                    sparse_hits = list(fetch_results[ridx])
                    ridx += 1

                if bm25_hits or sparse_hits:
                    bm25_map = dict(bm25_hits)
                    sparse_map = dict(sparse_hits)

                    # Workstream E Phase 3 wire-in: HYBRID_FUSION_MODE selects
                    # between the legacy weighted-sum blend, two-way RRF
                    # (Cormack/Clarke/Buettcher 2009; the 2026 default in
                    # Elastic, OpenSearch, Azure AI Search, neo4j-graphrag),
                    # and Cycle-3.2's three-way variant that adds SPLADE-v3
                    # sparse as a third ranking signal. Default stays
                    # "weighted_sum" so this is a pure plumbing change —
                    # flip HYBRID_FUSION_MODE to enable.
                    fused_map: dict[str, float] = {}

                    if fusion_mode in {"rrf", "tri_rrf"}:
                        from core.retrieval.rrf import rrf_fuse_by_artifact
                        # GA P0.5 B1 — artifact-level RRF. Chunk-level fusion let a
                        # multi-chunk artifact consume several rank slots, both
                        # compounding its own score and demoting competitors (the
                        # documented Phase-3a regression). Build chunk→artifact
                        # from the vector hits (known); bm25/sparse-only chunks
                        # fall back to chunk-as-artifact (singletons, no inflation).
                        _chunk_art = {
                            e["chunk_id"]: (e.get("artifact_id") or e["chunk_id"])
                            for e in formatted
                        }

                        def _art_of(cid: str, _m: dict[str, str] = _chunk_art) -> str:
                            return _m.get(cid, cid)

                        vector_ranking = [
                            (entry["chunk_id"], entry["relevance"])
                            for entry in formatted
                        ]
                        rankings: list[list[tuple[str, float]]] = [vector_ranking]
                        weights: list[float] = [config.HYBRID_RRF_VECTOR_WEIGHT]
                        if bm25_hits:
                            rankings.append(bm25_hits)
                            weights.append(config.HYBRID_RRF_BM25_WEIGHT)
                        if tri_rrf and sparse_hits:
                            rankings.append(sparse_hits)
                            weights.append(
                                getattr(config, "HYBRID_RRF_SPARSE_WEIGHT", 1.0),
                            )
                        _art_fused = rrf_fuse_by_artifact(
                            rankings,
                            _art_of,
                            k=config.HYBRID_RRF_K,
                            weights=weights,
                        )
                        # Map each artifact's fused score back onto every ranked
                        # chunk (a chunk inherits its artifact's score).
                        fused_map = {
                            cid: _art_fused.get(_art_of(cid), 0.0)
                            for ranking in rankings
                            for cid, _ in ranking
                        }
                        for entry in formatted:
                            entry["relevance"] = round(
                                _art_fused.get(_art_of(entry["chunk_id"]), 0.0), 6,
                            )
                        # Drop entries already represented from bm25_map /
                        # sparse_map so the bm25-only fetch below only
                        # fires on net-new chunk_ids — same shape as the
                        # legacy path. tri_rrf merges sparse-only chunks
                        # into the same fallback path via sparse_map.
                        for entry in formatted:
                            bm25_map.pop(entry["chunk_id"], None)
                            sparse_map.pop(entry["chunk_id"], None)
                        # Sparse-only chunks travel through the same
                        # fetch loop as bm25-only; carry their best score
                        # into bm25_map so the existing code-path handles
                        # them uniformly.
                        for cid, score in sparse_map.items():
                            if cid not in bm25_map:
                                bm25_map[cid] = score
                    else:
                        # Legacy weighted-sum blend (default).
                        for entry in formatted:
                            kw_score = bm25_map.pop(entry["chunk_id"], 0.0)
                            vector_score = entry["relevance"]
                            entry["relevance"] = round(
                                config.HYBRID_VECTOR_WEIGHT * vector_score
                                + config.HYBRID_KEYWORD_WEIGHT * kw_score,
                                4,
                            )

                    if bm25_map:
                        try:
                            bm25_only_ids = list(bm25_map.keys())
                            # ChromaDB's client is sync HTTP — offload so the
                            # BM25-only chunk hydration doesn't block the event
                            # loop while domains query in parallel (CR-021).
                            fetched = await asyncio.to_thread(
                                collection.get,
                                ids=bm25_only_ids,
                                include=["documents", "metadatas"],
                            )
                            for j, cid in enumerate(fetched["ids"]):
                                if cid in seen_ids:
                                    continue
                                meta = fetched["metadatas"][j] if fetched["metadatas"] else {}
                                # Tenant scope is enforced at the BM25-only path too —
                                # ChromaDB's where-clause was bypassed for these IDs.
                                if not chunk_matches_tenant(meta):
                                    continue
                                # Phase O.1: exclude pending chunks on BM25-only path.
                                import os as _os
                                if _os.getenv("CERID_FILTER_PENDING_CHUNKS", "true").strip().lower() not in ("false", "0", "no", "off"):
                                    if meta.get("cerid_state") == "pending":
                                        continue
                                # Enforce metadata_filter on BM25-only results too
                                if metadata_filter and not all(
                                    meta.get(k) == v for k, v in metadata_filter.items()
                                ):
                                    continue
                                # In RRF / tri_rrf mode the bm25/sparse-only
                                # chunk's fused score already accounts for its
                                # rank; in legacy mode use the keyword-weighted
                                # bm25.
                                if fusion_mode in {"rrf", "tri_rrf"}:
                                    rel = round(
                                        fused_map.get(cid, bm25_map[cid]),
                                        6,
                                    )
                                else:
                                    rel = config.HYBRID_KEYWORD_WEIGHT * bm25_map[cid]
                                _bm25_entry = _format_chroma_result(
                                    content=fetched["documents"][j],
                                    relevance=rel,
                                    chunk_id=cid,
                                    domain=domain,
                                    metadata=meta,
                                )
                                # Keyword-arm-only candidate: its weighted_sum
                                # relevance is capped at HYBRID_KEYWORD_WEIGHT
                                # (below the absolute junk floor), so the floor
                                # exempts these and lets the reranker judge them.
                                _bm25_entry["bm25_only"] = True
                                formatted.append(_bm25_entry)
                                seen_ids.add(cid)
                        except Exception as e:  # noqa: BLE001 — observability boundary
                            log_swallowed_error(
                                "core.agents.query_agent.bm25_only_fetch", e,
                            )

            # RAG C2.6 — substitute parent text for each ranked child. The
            # child's relevance score is preserved (vector + BM25 fused
            # against the targeted small chunk); only ``content`` is swapped
            # so reranking + the context assembler operate on the richer
            # parent context. Children without a parent (legacy single-tier
            # rows in a mixed corpus) fall through unchanged.
            if pc_enabled and formatted:
                formatted = await asyncio.to_thread(
                    _substitute_parent_content, formatted, collection,
                )

            return formatted

        except Exception as e:
            log_swallowed_error('core.agents.query_agent', e)
            logger.warning(f"Error querying domain {domain}: {e}")
            return []

    tasks = [query_domain(domain) for domain in domains]
    domain_results = await asyncio.gather(*tasks)

    # Phase I — Custom Smart RAG: apply per-domain multipliers BEFORE
    # the cross-domain merge so the existing relevance ordering carries
    # the user's preferences. Pre-fetch the weight map once (zero-cost
    # when feature off or no weights set).
    weights: dict[str, float] = {}
    try:
        from utils.rag_weights import is_active as _smart_rag_active
        if _smart_rag_active():
            from utils.rag_weights import get_weights as _get_weights
            weights = _get_weights()
    except ImportError:
        pass

    if weights:
        for domain, results in zip(domains, domain_results, strict=True):
            kb_key = f"kb:{domain}"
            multiplier = weights.get(kb_key, 1.0)
            if abs(multiplier - 1.0) > 1e-9:
                for r in results:
                    if "relevance" in r:
                        r["relevance"] = round(
                            max(0.0, min(1.0, r["relevance"] * multiplier)), 4,
                        )

    all_results = [r for results in domain_results for r in results]

    return all_results


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate chunks, keeping highest relevance per (artifact_id, chunk_index).

    External source results (from CRAG gate) may lack artifact_id/chunk_index;
    these are treated as unique (never deduplicated against KB results).
    """
    groups: dict[tuple, list] = defaultdict(list)
    for result in results:
        aid = result.get("artifact_id")
        cidx = result.get("chunk_index")
        if aid is not None and cidx is not None:
            key = (aid, cidx)
        else:
            # External/memory results without KB keys — always unique
            key = ("_ext", id(result))
        groups[key].append(result)

    deduplicated = []
    for group in groups.values():
        best = max(group, key=lambda x: x["relevance"])
        deduplicated.append(best)

    return deduplicated


# ---------------------------------------------------------------------------
# Lightweight retrieval (verification fast-path)
# ---------------------------------------------------------------------------

async def lightweight_kb_query(
    query: str,
    domains: list[str] | None = None,
    top_k: int = 5,
    chroma_client: Any | None = None,
) -> list[dict[str, Any]]:
    """Fast KB retrieval for verification — vector + BM25 hybrid only.

    Skips: graph expansion, cross-encoder reranking, quality boost,
    MMR diversity, semantic cache, adaptive gate, query decomposition,
    context assembly.  Returns raw ranked results suitable for
    claim verification where only semantic similarity matters.
    """
    results = await multi_domain_query(
        query, domains=domains, top_k=top_k, chroma_client=chroma_client,
    )
    results = deduplicate_results(results)
    # Filter out noise — verification operates on these results directly
    # and low-relevance hits degrade claim verification accuracy.
    min_rel = config.VERIFICATION_MIN_RELEVANCE
    results = [r for r in results if r.get("relevance", 0.0) >= min_rel]
    results.sort(key=lambda x: x.get("relevance", 0.0), reverse=True)
    return results[:top_k]


# ---------------------------------------------------------------------------
# Graph-enhanced retrieval
# ---------------------------------------------------------------------------

async def graph_expand_results(
    results: list[dict[str, Any]],
    query: str,
    chroma_client: Any | None = None,
    neo4j_driver: Any | None = None,
    graph_store: GraphStore | None = None,
) -> list[dict[str, Any]]:
    """Expand results by traversing the knowledge graph for related artifacts.

    Requires either *graph_store* (preferred) or *neo4j_driver* (legacy).
    When *graph_store* is provided it takes precedence.
    """
    if graph_store is None and neo4j_driver is None:
        return results
    if not results:
        return results

    initial_ids = list({r["artifact_id"] for r in results if r.get("artifact_id")})
    if not initial_ids:
        return results

    try:
        if graph_store is not None:
            related = await graph_store.find_related_with_metadata(
                initial_ids,
                depth=config.GRAPH_TRAVERSAL_DEPTH,
                limit=config.GRAPH_MAX_RELATED,
            )
        else:
            logger.debug("graph_store not provided; skipping graph expansion")
            return results
    except Exception as e:
        log_swallowed_error('core.agents.query_agent', e)
        logger.warning(f"Graph traversal failed (continuing without): {e}")
        return results

    if not related:
        return results

    if chroma_client is None:
        raise ValueError("chroma_client is required for graph expansion")

    existing_ids = {r.get("chunk_id") for r in results}

    async def _fetch_related(rel_artifact: dict) -> list[dict[str, Any]]:
        """Fetch and score chunks for a single related artifact."""
        chunk_ids_json = rel_artifact.get("chunk_ids", "[]")
        chunk_ids = json.loads(chunk_ids_json) if chunk_ids_json else []
        if not chunk_ids:
            return []

        domain = rel_artifact["domain"]
        collection = chroma_client.get_collection(name=config.collection_name(domain))

        # Phase O.1: exclude pending chunks from graph expansion too.
        fetched = await asyncio.to_thread(
            collection.query,
            query_texts=[query],
            n_results=min(3, len(chunk_ids)),
            where=with_tenant_scope(
                _exclude_pending({"artifact_id": rel_artifact["id"]})
            ),
            include=["documents", "metadatas", "distances"],
        )

        if not fetched["ids"] or not fetched["ids"][0]:
            return []

        chunks: list[dict[str, Any]] = []
        for i, chunk_id in enumerate(fetched["ids"][0]):
            distance = fetched["distances"][0][i] if fetched["distances"] else 1.0
            raw_relevance = l2_distance_to_relevance(distance)
            depth_penalty = 1.0 / (1.0 + rel_artifact.get("relationship_depth", 1))
            relevance = round(
                raw_relevance * config.GRAPH_RELATED_SCORE_FACTOR * depth_penalty, 4
            )
            metadata = fetched["metadatas"][0][i] if fetched["metadatas"] else {}
            chunks.append({
                "content": fetched["documents"][0][i],
                "relevance": relevance,
                "artifact_id": rel_artifact["id"],
                "filename": rel_artifact["filename"],
                "domain": domain,
                "chunk_index": metadata.get("chunk_index", 0),
                "collection": config.collection_name(domain),
                "chunk_id": chunk_id,
                "graph_source": True,
                "relationship_type": rel_artifact.get("relationship_type", ""),
                "relationship_reason": rel_artifact.get("relationship_reason", ""),
            })
        return chunks

    # Fetch chunks for all related artifacts in parallel
    tasks = [_fetch_related(ra) for ra in related]
    all_fetched = await asyncio.gather(*tasks, return_exceptions=True)

    graph_results: list[dict[str, Any]] = []
    for batch in all_fetched:
        if isinstance(batch, BaseException):
            logger.debug("Failed to fetch chunks for related artifact: %s", batch)
            continue
        for chunk in batch:
            if chunk["chunk_id"] not in existing_ids:
                graph_results.append(chunk)
                existing_ids.add(chunk["chunk_id"])

    if graph_results:
        logger.info(f"Graph expansion added {len(graph_results)} related chunk(s)")

    return results + graph_results


async def graph_expand_results_via_entities(
    results: list[dict[str, Any]],
    query: str,
    chroma_client: Any | None = None,
    neo4j_driver: Any | None = None,
) -> list[dict[str, Any]]:
    """GraphRAG local-mode expansion: extend results with artifacts that
    share entities with the seed set.

    Used by step-6 when ``RETRIEVAL_MODE=local_graphrag``. Returns
    ``results`` unchanged when:

      - no seeds (nothing to expand from), or
      - the entity layer is empty (pre-backfill state — seed artifacts
        have no MENTIONS edges yet), or
      - chroma is unreachable / fetch fails.

    The query_agent caller treats "no expansion happened" as a signal
    to fall back to the baseline relationship-traversal expansion.
    """
    from core.retrieval.graphrag_retriever import entity_neighborhood_artifact_ids

    if not results or neo4j_driver is None or chroma_client is None:
        return results

    seed_ids = list({r["artifact_id"] for r in results if r.get("artifact_id")})
    if not seed_ids:
        return results

    try:
        related_pairs = await asyncio.to_thread(
            entity_neighborhood_artifact_ids,
            neo4j_driver,
            seed_ids,
            top_k=config.GRAPH_MAX_RELATED,
        )
    except Exception as e:
        log_swallowed_error('core.agents.query_agent', e)
        logger.warning("entity-neighborhood expansion failed: %s", e)
        return results

    if not related_pairs:
        return results

    # Resolve domain + filename for each related artifact in one Cypher round-trip.
    cypher = """
    UNWIND $ids AS aid
    MATCH (a:Artifact {id: aid})
    RETURN a.id AS id, a.domain AS domain, a.filename AS filename
    """
    try:
        records, _, _ = await asyncio.to_thread(
            neo4j_driver.execute_query, cypher, {"ids": [aid for aid, _ in related_pairs]},
        )
    except Exception as e:
        log_swallowed_error('core.agents.query_agent', e)
        logger.warning("artifact-resolution for entity expansion failed: %s", e)
        return results
    by_id = {r["id"]: dict(r) for r in records}

    existing_chunk_ids = {r.get("chunk_id") for r in results}
    existing_artifact_ids = set(seed_ids)
    expanded: list[dict[str, Any]] = []

    async def _fetch_for_artifact(
        artifact_id: str, shared_count: int,
    ) -> tuple[str, int, dict[str, Any], str, dict[str, Any]] | None:
        """Resolve + query one related artifact's chunks. Returns None to skip."""
        meta = by_id.get(artifact_id)
        if not meta or artifact_id in existing_artifact_ids:
            return None
        domain = meta["domain"]
        try:
            collection = chroma_client.get_collection(name=config.collection_name(domain))
        except Exception as e:  # noqa: BLE001 — collection-missing is a valid skip
            logger.debug("collection missing for domain %s: %s", domain, e)
            return None
        try:
            # Phase O.1: exclude pending chunks from entity-neighbourhood expansion.
            fetched = await asyncio.to_thread(
                collection.query,
                query_texts=[query],
                n_results=2,
                where=with_tenant_scope(
                    _exclude_pending({"artifact_id": artifact_id})
                ),
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:  # noqa: BLE001 — chroma failure → skip this artifact
            logger.debug("chroma fetch failed for %s: %s", artifact_id, e)
            return None
        return artifact_id, shared_count, meta, domain, fetched

    # Fan the per-artifact Chroma queries out concurrently rather than awaiting
    # them one at a time — they are independent round-trips (CR-082). Result
    # assembly below stays sequential (gather preserves order) so the chunk
    # dedup against existing_chunk_ids remains deterministic.
    fetch_results = await asyncio.gather(
        *(_fetch_for_artifact(aid, sc) for aid, sc in related_pairs)
    )

    for item in fetch_results:
        if item is None:
            continue
        artifact_id, shared_count, meta, domain, fetched = item
        if not fetched["ids"] or not fetched["ids"][0]:
            continue
        # Boost related-via-entity scores by shared-entity count (mild),
        # capped so they cannot outweigh primary vector hits.
        boost = min(0.05 * shared_count, 0.15)
        for i, chunk_id in enumerate(fetched["ids"][0]):
            if chunk_id in existing_chunk_ids:
                continue
            distance = fetched["distances"][0][i] if fetched["distances"] else 1.0
            base_relevance = l2_distance_to_relevance(distance)
            relevance = round(
                base_relevance * config.GRAPH_RELATED_SCORE_FACTOR + boost, 4
            )
            metadata = fetched["metadatas"][0][i] if fetched["metadatas"] else {}
            expanded.append({
                "content": fetched["documents"][0][i],
                "relevance": relevance,
                "artifact_id": artifact_id,
                "filename": meta["filename"],
                "domain": domain,
                "chunk_index": metadata.get("chunk_index", 0),
                "collection": config.collection_name(domain),
                "chunk_id": chunk_id,
                "graph_source": True,
                "graph_expansion_mode": "local_graphrag",
                "shared_entity_count": shared_count,
            })
            existing_chunk_ids.add(chunk_id)

    if expanded:
        logger.info(
            "GraphRAG local expansion added %d chunk(s) via entity neighbourhoods "
            "(seeds=%d, related=%d)",
            len(expanded), len(seed_ids), len(related_pairs),
        )
    return results + expanded


async def graph_expand_results_via_communities(
    results: list[dict[str, Any]],
    query: str,
    chroma_client: Any | None = None,
    neo4j_driver: Any | None = None,
) -> list[dict[str, Any]]:
    """GraphRAG global-mode expansion (Phase 4b.4).

    Selects communities whose member entities overlap with the seed
    artifacts' entities, then surfaces each matched community's
    LLM-generated summary alongside one representative chunk per
    community. Designed for thematic queries that the
    :func:`core.agents.query_router.route` heuristic dispatched to
    global mode.

    No-op when:
      - the community layer is empty (Leiden has not run yet), or
      - none of the seeds carry any MENTIONS edges, or
      - chroma or neo4j is unreachable.

    The summary content rides as a synthetic ``content`` field on the
    returned dicts so downstream rerank/fuse handles it identically
    to a chunk; ``graph_expansion_mode="global_graphrag"`` tags the
    origin so observability can trace the path.
    """
    if not results or neo4j_driver is None or chroma_client is None:
        return results

    seed_ids = list({r["artifact_id"] for r in results if r.get("artifact_id")})
    if not seed_ids:
        return results

    cypher = """
    MATCH (a:Artifact)-[:MENTIONS]->(:Entity)-[:IN_COMMUNITY]->(c:Community)
    WHERE a.id IN $seeds AND c.summary IS NOT NULL AND c.level = 0
    WITH c, count(DISTINCT a) AS seed_overlap
    OPTIONAL MATCH (c)<-[:IN_COMMUNITY]-(top:Entity)
    WITH c, seed_overlap, collect(top)[..3] AS top_entities
    RETURN c.id AS community_id, c.summary AS summary, c.level AS level,
           seed_overlap, [t IN top_entities | t.canonical_id] AS top_entity_ids
    ORDER BY seed_overlap DESC
    LIMIT 5
    """
    try:
        records, _, _ = await asyncio.to_thread(
            neo4j_driver.execute_query, cypher, {"seeds": seed_ids},
        )
    except Exception as e:
        log_swallowed_error('core.agents.query_agent', e)
        logger.warning("global-mode community fetch failed: %s", e)
        return results

    if not records:
        return results

    existing_chunk_ids = {r.get("chunk_id") for r in results}
    expanded: list[dict[str, Any]] = []
    for record in records:
        community_id = record["community_id"]
        summary = record["summary"]
        seed_overlap = int(record["seed_overlap"])
        if not summary:
            continue
        synthetic_chunk_id = f"community:{community_id}"
        if synthetic_chunk_id in existing_chunk_ids:
            continue
        expanded.append({
            "content": summary,
            "relevance": round(0.4 + 0.05 * min(seed_overlap, 5), 4),
            "artifact_id": synthetic_chunk_id,
            "filename": f"community:{community_id}",
            "domain": "graph",
            "chunk_index": 0,
            "collection": "graph_communities",
            "chunk_id": synthetic_chunk_id,
            "graph_source": True,
            "graph_expansion_mode": "global_graphrag",
            "community_id": community_id,
            "community_level": int(record["level"]),
            "seed_overlap": seed_overlap,
            "top_entities": list(record["top_entity_ids"]),
        })
        existing_chunk_ids.add(synthetic_chunk_id)

    if expanded:
        logger.info(
            "GraphRAG global expansion added %d community summar(ies) (seeds=%d)",
            len(expanded), len(seed_ids),
        )
    return results + expanded


# ---------------------------------------------------------------------------
# Reranking
# ---------------------------------------------------------------------------

async def rerank_results(
    results: list[dict[str, Any]],
    query: str,
    use_reranking: bool = True,
) -> list[dict[str, Any]]:
    """Rerank results using the configured strategy.

    Dispatches to cross-encoder (fast local ONNX) or LLM (via
    ``core.utils.internal_llm``) based on ``config.RERANK_MODE``. When the
    ONNX cross-encoder fails to load, results are returned in their original
    order and each is tagged with ``reranker_status = 'onnx_failed_no_fallback'``
    — see :func:`_rerank_cross_encoder` for the rationale.
    """
    if not use_reranking or len(results) == 0:
        return sorted(results, key=lambda x: x["relevance"], reverse=True)

    results = sorted(results, key=lambda x: x["relevance"], reverse=True)

    mode = config.RERANK_MODE

    # When RERANK_PREFER_LOCAL is true and the local cross-encoder is
    # available, always use it regardless of RERANK_MODE — faster and free.
    if getattr(config, "RERANK_PREFER_LOCAL", False) and mode == "llm":
        try:
            from core.retrieval.reranker import _session
            if _session is not None:
                logger.debug("RERANK_PREFER_LOCAL: overriding llm → cross_encoder")
                return await _rerank_cross_encoder(results, query)
        except (ImportError, AttributeError):
            pass  # Fall through to configured mode

    if mode == "cross_encoder":
        return await _rerank_cross_encoder(results, query)
    if mode == "llm":
        return await _rerank_llm(results, query)
    # mode == "none" or unknown — preserve vector order. Absence of a
    # reranker_status field means "no degradation"; only failure paths tag.
    return results


async def _rerank_cross_encoder(
    results: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """Rerank via cross-encoder. Workstream E Phase E.6.4 routes through
    the GPU sidecar when auto-detection picks ``fastembed-sidecar`` and
    the sidecar is reachable; otherwise falls through to local ONNX
    (~50 ms for 15 candidates on CPU).

    On failure, returns results in their input order (already sorted by
    relevance upstream) and tags each with ``reranker_status =
    'onnx_failed_no_fallback'`` so the caller can surface the degraded state.
    The former LLM fallback routed through Bifrost; after Bifrost retirement
    (audit C-4/C-9) a single load failure would otherwise crash every
    query, so we prefer an honest no-op over a broken alternative path.
    """
    # ── Quenchforge GPU fast-path (v0.93.8) ───────────────────────────────
    # Opt-in via RERANK_PROVIDER=quenchforge.  Targets Intel Mac + AMD
    # where ONNX runtime + the sidecar both fall back to CPU.
    quenchforge_result = await _maybe_rerank_via_quenchforge(results, query)
    if quenchforge_result is not None:
        return quenchforge_result

    # ── Sidecar fast-path (Phase E.6.4) ───────────────────────────────────
    sidecar_result = await _maybe_rerank_via_sidecar(results, query)
    if sidecar_result is not None:
        return sidecar_result

    # ── Local ONNX fallback (default) ─────────────────────────────────────
    try:
        from core.retrieval.reranker import rerank as ce_rerank

        loop = asyncio.get_running_loop()
        with span("retrieval.rerank", "cross_encoder", k=len(results)):
            return await loop.run_in_executor(None, ce_rerank, query, results)
    except Exception as e:
        log_swallowed_error('core.agents.query_agent', e)
        logger.warning(
            "Cross-encoder reranking failed — returning results in original "
            "order (no LLM fallback after Bifrost retirement): %s",
            e,
        )
        for r in results:
            r["reranker_status"] = "onnx_failed_no_fallback"
        return results


# Quenchforge serves a single rerank slot per machine; firing N concurrent
# rerank calls at it causes 500-storms that trip the circuit breaker (OPT-5).
# Serialize at the client so production never overwhelms the daemon — eval
# harnesses still need their own PACE_S because the semaphore protects the
# daemon, not batch etiquette.
_RERANK_QUENCHFORGE_SEM = asyncio.Semaphore(1)


async def _maybe_rerank_via_quenchforge(
    results: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]] | None:
    """Route the reranker through Quenchforge when ``RERANK_PROVIDER=quenchforge``.

    Same fall-through contract as :func:`_maybe_rerank_via_sidecar` —
    returns ``None`` on any failure (provider not selected, daemon
    unreachable, model not loaded, schema mismatch) so the chain
    continues down to the sidecar and then the local ONNX path.
    """
    try:
        from utils.quenchforge_client import is_rerank_provider_quenchforge
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("core.agents.query_agent.quenchforge_detect", exc)
        return None
    if not is_rerank_provider_quenchforge():
        return None
    from core.utils import inference_health
    try:
        from utils.quenchforge_client import quenchforge_rerank
        documents = [r.get("content", "") for r in results]
        with span("retrieval.rerank", "quenchforge", k=len(results)):
            async with _RERANK_QUENCHFORGE_SEM:
                scores = await quenchforge_rerank(query, documents)
        for r, s in zip(results, scores, strict=False):
            r["relevance"] = float(s)
            r["reranker_status"] = "quenchforge"
        inference_health.record_success("rerank", provider="quenchforge")
        return sorted(results, key=lambda r: r.get("relevance", 0.0), reverse=True)
    except Exception as exc:  # noqa: BLE001 — fall through to sidecar / local ONNX
        log_swallowed_error("core.agents.query_agent.quenchforge_rerank", exc)
        # Configured for quenchforge GPU rerank but it failed — the chain will
        # serve from the sidecar or local ONNX. Record the degradation so
        # /health.inference_routing.rerank reports it instead of advertising a
        # provider that isn't answering.
        inference_health.record_fallback(
            "rerank", configured="quenchforge", served_by="onnx", detail=str(exc),
        )
        return None


async def _maybe_rerank_via_sidecar(
    results: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]] | None:
    """If the auto-detected provider is the sidecar AND it's reachable,
    rerank via HTTP and return scored+sorted results. Returns ``None``
    to signal "fall through to local ONNX" — never raises into the
    query path.
    """
    try:
        from utils.inference_config import get_inference_config
        cfg = get_inference_config()
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("core.agents.query_agent.sidecar_detect", exc)
        return None
    if cfg.provider != "fastembed-sidecar" or not cfg.sidecar_available:
        return None
    try:
        from utils.inference_sidecar_client import sidecar_rerank
        documents = [r.get("content", "") for r in results]
        with span("retrieval.rerank", "sidecar", k=len(results)):
            scores = await sidecar_rerank(query, documents)
        for r, s in zip(results, scores, strict=False):
            r["relevance"] = float(s)
            r["reranker_status"] = "sidecar"
        return sorted(results, key=lambda r: r.get("relevance", 0.0), reverse=True)
    except Exception as exc:  # noqa: BLE001 — fall through to local ONNX
        log_swallowed_error("core.agents.query_agent.sidecar_rerank", exc)
        return None


async def _rerank_llm(
    results: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """Rerank via Bifrost LLM call (legacy path)."""
    candidates = results[:config.QUERY_RERANK_CANDIDATES]
    remainder = results[config.QUERY_RERANK_CANDIDATES:]

    if len(candidates) <= 1:
        for r in results:
            r.setdefault("reranker_status", "llm_too_few_candidates")
        return results

    snippets = []
    for i, r in enumerate(candidates):
        preview = r["content"][:200].replace("\n", " ").strip()
        snippets.append(f"[{i}] ({r['domain']}/{r['filename']}) {preview}")

    prompt = (
        f"Given the query: \"{query}\"\n\n"
        f"Rank these document snippets by relevance to the query. "
        f"Return ONLY a JSON array of indices in order of most to least relevant.\n\n"
        + "\n".join(snippets)
        + f"\n\nRespond with ONLY a JSON array like [2, 0, 5, 1, ...] containing all indices 0-{len(candidates)-1}."
    )

    try:
        from core.utils.internal_llm import call_internal_llm
        content = await call_internal_llm(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=200,
            stage="rerank_llm",
        )
        ranking = parse_llm_json(content)

        if not isinstance(ranking, list):
            raise ValueError("Expected a list of indices")

        valid_indices = set(range(len(candidates)))
        seen: set[int] = set()
        reranked = []
        for idx in ranking:
            if isinstance(idx, int) and idx in valid_indices and idx not in seen:
                seen.add(idx)
                reranked.append(candidates[idx])

        for i, r in enumerate(candidates):
            if i not in seen:
                reranked.append(r)

        for rank_pos, result in enumerate(reranked):
            llm_score = 1.0 - (rank_pos / len(reranked))
            original_score = result["relevance"]
            result["relevance"] = round(
                config.RERANK_LLM_WEIGHT * llm_score
                + config.RERANK_ORIGINAL_WEIGHT * original_score,
                4,
            )

        return reranked + remainder

    except CircuitOpenError:
        logger.warning("Bifrost rerank circuit open, falling back to embedding sort")
        fallback = sorted(results, key=lambda x: x["relevance"], reverse=True)
        for r in fallback:
            r.setdefault("reranker_status", "llm_circuit_open")
        return fallback
    except (httpx.HTTPStatusError, json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("LLM reranking failed, falling back to embedding sort: %s", e)
        fallback = sorted(results, key=lambda x: x["relevance"], reverse=True)
        for r in fallback:
            r.setdefault("reranker_status", "llm_failed")
        return fallback


# ---------------------------------------------------------------------------
# Metadata boost
# ---------------------------------------------------------------------------

def apply_metadata_boost(
    results: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """Boost results whose tags or sub_category match query terms.

    Small additive boost for metadata alignment, capped at
    QUALITY_METADATA_MAX_BOOST to prevent tag-stuffed artifacts
    from dominating.
    """
    if not results:
        return results

    query_terms = {w.lower() for w in _WORD_RE.findall(query) if len(w) > 2}
    query_terms -= _STOPWORDS

    if not query_terms:
        return results

    for r in results:
        boost = 0.0

        # Sub-category match
        sub_cat = r.get("sub_category", "")
        if sub_cat:
            sub_cat_terms = {t.lower() for t in _WORD_RE.findall(sub_cat)}
            if sub_cat_terms & query_terms:
                boost += config.QUALITY_METADATA_SUBCAT_BOOST

        # Tag match
        tags_json = r.get("tags_json", "[]")
        try:
            tags = json.loads(tags_json) if tags_json else []
        except (json.JSONDecodeError, TypeError):
            tags = []
        for tag in tags:
            tag_terms = {t.lower() for t in _WORD_RE.findall(tag)}
            if tag_terms & query_terms:
                boost += config.QUALITY_METADATA_TAG_BOOST

        # Keyword match (lighter — keywords already used by BM25)
        kw_json = r.get("keywords", "[]")
        try:
            kw_list = json.loads(kw_json) if kw_json else []
        except (json.JSONDecodeError, TypeError):
            kw_list = []
        kw_matches = sum(1 for kw in kw_list if kw.lower() in query_terms)
        if kw_matches > 0:
            boost += min(kw_matches * 0.02, 0.06)

        boost = min(boost, config.QUALITY_METADATA_MAX_BOOST)
        if boost > 0:
            r["relevance"] = round(min(1.0, r["relevance"] + boost), 4)
            r["metadata_boost"] = round(boost, 4)

    return results


# ---------------------------------------------------------------------------
# Context alignment boost
# ---------------------------------------------------------------------------

def _apply_active_learning_signals(
    results: list[dict[str, Any]],
    neo4j_driver: Any,
) -> list[dict[str, Any]]:
    """Enrich retrieval results with endorsement_weight + flag_reason, and
    filter out archived (soft-deleted / quarantined) artifacts.

    Reads each result's source artifact (by ``artifact_id``) and:

    * **Drops** the result when the artifact is ``archived`` (soft-deleted or
      quarantined via the content-lifecycle coordinator). The vector where-clause
      cannot see this flag (it lives on the Neo4j node, not Chroma chunk
      metadata), so this post-retrieval join is where the vector arm honors it —
      closing AF-001, the hole where archived artifacts still surfaced as RAG
      evidence. Clearing ``archived`` restores the artifact.
    * **Drops** the result when ``flag_reason`` is non-empty (inaccurate /
      outdated / off_topic / duplicate / spam — see ``pkb_flag`` for the
      taxonomy). The flag clears via ``pkb_flag(reason='')`` and the
      result reappears next query.
    * **Multiplies** the relevance score by
      ``endorsement_weight`` (default 1.0; range [0.1, 10.0]). User
      endorsements rise; user-demoted artifacts sink. This runs BEFORE
      the reranker so the cross-encoder sees boosted-in / demoted-out
      candidates.

    Single batched Cypher round-trip — collects all unique
    ``artifact_id`` values then UNWINDs to read both properties at once.
    Missing artifacts (e.g. chunk metadata points at a deleted node)
    are treated as unflagged + weight=1.0; the result survives.

    Synchronous on purpose — called via ``asyncio.to_thread`` from the
    async pipeline so it doesn't bind the event loop.
    """
    artifact_ids = sorted({
        aid for r in results
        if (aid := r.get("artifact_id")) is not None
    })
    if not artifact_ids:
        return results

    metadata: dict[str, dict[str, Any]] = {}
    with neo4j_driver.session() as session:
        row = session.run(
            """
            UNWIND $ids AS aid
            MATCH (a:Artifact {id: aid})
            RETURN
                a.id AS id,
                coalesce(a.endorsement_weight, 1.0) AS weight,
                coalesce(a.flag_reason, '') AS flag,
                coalesce(a.archived, false) AS archived
            """,
            ids=artifact_ids,
        )
        for r in row:
            metadata[r["id"]] = {
                "weight": float(r["weight"] or 1.0),
                "flag": str(r["flag"] or ""),
                "archived": bool(r["archived"]),
            }

    filtered: list[dict[str, Any]] = []
    for chunk in results:
        aid = chunk.get("artifact_id")
        meta = metadata.get(aid) if aid else None
        if meta and meta.get("archived"):
            # Soft-deleted / quarantined — must NOT surface as RAG evidence on
            # the vector arm (AF-001). ``archived`` is set by the content-
            # lifecycle coordinator's hide_content; clearing the flag restores
            # the artifact. The vector where-clause cannot filter this (the flag
            # lives on the Neo4j node, not Chroma chunk metadata), so this
            # post-retrieval join is the enforcement point — same mechanism the
            # graph/temporal arms already use.
            chunk["_filtered_reason"] = "archived"
            continue
        if meta and meta["flag"]:
            # Flagged-out — don't surface this chunk at all. We tag the
            # filter event on the chunk dict before dropping it so a
            # caller capturing diagnostic results can still see why
            # it disappeared if they're poking at the upstream pre-
            # filter list.
            chunk["_filtered_reason"] = f"flag:{meta['flag']}"
            continue
        if meta and meta["weight"] != 1.0:
            # Store the endorsement weight; do NOT pre-multiply relevance here.
            # The cross-encoder rerank (Step 5) overwrites relevance outright on
            # the quenchforge/sidecar paths (and blends it on the ONNX fallback),
            # so a value multiplied in here is silently washed out — the reason
            # endorse/demote was a no-op under the live GPU config. The weight is
            # re-applied ONCE post-rerank (Step 5.05) so the signal is
            # provider-independent.
            chunk["_endorsement_weight"] = meta["weight"]
        filtered.append(chunk)
    return filtered


def apply_context_alignment_boost(
    results: list[dict[str, Any]],
    conversation_messages: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Boost results whose content aligns with recent conversation context.

    Extracts key terms from conversation messages and computes what proportion
    appear in each result's content. More term overlap = higher boost.
    Applied after metadata boost, before reranking.
    """
    if not results or not conversation_messages:
        return results

    # Extract all meaningful terms from conversation
    context_terms: set = set()
    for msg in conversation_messages:
        if msg.get("role") == "user":
            words = _WORD_RE.findall(msg.get("content", ""))
            for word in words:
                lower = word.lower()
                if len(lower) > 2 and lower not in _STOPWORDS:
                    context_terms.add(lower)

    if not context_terms:
        return results

    boost_weight = config.CONTEXT_BOOST_WEIGHT

    for r in results:
        content_terms = {w.lower() for w in _WORD_RE.findall(r.get("content", "")) if len(w) > 2}
        matches = context_terms & content_terms
        if matches:
            alignment = len(matches) / len(context_terms)
            boost = alignment * boost_weight
            r["relevance"] = round(min(1.0, r["relevance"] + boost), 4)
            r["context_alignment"] = round(alignment, 4)

    return results


async def _nli_gate_scores(pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Run the NLI gate's batch inference OFF the event loop.

    DeBERTa ONNX over up to 15 pairs takes tens of seconds on a contended
    CPU; called in-loop it stalls the heartbeat past the 45s watchdog,
    which force-exits the process mid-request (the 2026-07-08/2026-07-10
    MCP crash root cause). ``asyncio.to_thread`` keeps the loop serving.
    """
    from core.utils import nli as _nli_mod

    return await asyncio.to_thread(_nli_mod.batch_nli_score, pairs)


# ---------------------------------------------------------------------------
# Quality boost
# ---------------------------------------------------------------------------

async def _apply_quality_and_summaries(
    results: list[dict[str, Any]],
    neo4j_driver: Any | None = None,
    graph_store: GraphStore | None = None,
) -> list[dict[str, Any]]:
    """Apply quality score multiplier to relevance + attach artifact summaries.

    Formula: adjusted = relevance * (QUALITY_BOOST_BASE + QUALITY_BOOST_FACTOR * quality_score)
    Default:  adjusted = relevance * (0.8 + 0.4 * quality_score)

    This means quality=1.0 → 1.2x (boost), quality=0.5 → 1.0x (neutral),
    quality=0.0 → 0.8x (penalty). Artifacts with no stored score (never
    curated) default to the neutral 0.5.

    Fetches real per-artifact scores via ``graph_store.get_quality_and_summaries``
    — a genuine single Cypher round-trip for the whole candidate set (see
    ``Neo4jGraphStore.get_quality_and_summaries``), not the
    ``get_artifacts_batch`` N-query fan-out this used to route through.
    Accepts *graph_store* (preferred) or *neo4j_driver* (legacy, ignored in core/).
    """
    if (graph_store is None and neo4j_driver is None) or not results:
        return results

    artifact_ids = list({r["artifact_id"] for r in results if r.get("artifact_id")})
    if not artifact_ids:
        return results

    if graph_store is None:
        logger.debug("graph_store not provided; skipping quality/summary enrichment")
        return results

    try:
        scores, summaries = await graph_store.get_quality_and_summaries(artifact_ids)
    except Exception as e:
        log_swallowed_error('core.agents.query_agent', e)
        logger.warning(f"Quality/summary lookup failed (skipping): {e}")
        return results

    for r in results:
        aid = r.get("artifact_id", "")
        # Quality boost
        quality = scores.get(aid, 0.5)
        multiplier = config.QUALITY_BOOST_BASE + config.QUALITY_BOOST_FACTOR * quality
        r["relevance"] = round(r["relevance"] * multiplier, 4)
        r["quality_score"] = quality
        # Summary enrichment
        s = summaries.get(aid)
        if s:
            r["summary"] = s

    return results


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def assemble_context(
    results: list[dict[str, Any]],
    max_chars: int = 14000,
    max_chunks_per_artifact: int = 0,
) -> tuple[str, list[dict[str, Any]], int]:
    """Build context window from top results, respecting token budget.

    Limits chunks per artifact to promote source diversity.  A value of 0
    for *max_chunks_per_artifact* means use the global config default.
    """
    if max_chunks_per_artifact <= 0:
        max_chunks_per_artifact = config.CONTEXT_MAX_CHUNKS_PER_ARTIFACT

    context_parts: list[str] = []
    included_sources: list[dict[str, Any]] = []
    char_count = 0
    artifact_counts: dict[str, int] = defaultdict(int)

    for result in results:
        # Defensive .get(): surface-injected results (wiki has no filename) and
        # external results may omit optional display fields — assembly must not
        # KeyError at this presentation boundary.
        artifact_id = result.get("artifact_id", "")

        # Skip if this artifact already has enough chunks in context
        if artifact_counts[artifact_id] >= max_chunks_per_artifact:
            continue

        content = result.get("content", "")
        content_len = len(content)

        if char_count + content_len > max_chars:
            continue  # don't break — later smaller chunks may still fit

        context_parts.append(content)
        included_sources.append({
            "content": content[:200],  # Preview only
            "relevance": result.get("relevance", 0.0),
            "artifact_id": artifact_id,
            "filename": result.get("filename", ""),
            "domain": result.get("domain", ""),
            "chunk_index": result.get("chunk_index", 0),
            # RAG Phase 1.1 — preserve provenance onto sources[]. Never
            # clobber an existing source_type (memory/wiki/external set their
            # own); default to "kb" only when the result didn't declare one.
            "source_type": result.get("source_type", "kb"),
            "created_at": result.get("created_at"),
            "pack_id": result.get("pack_id", ""),
        })
        char_count += content_len
        artifact_counts[artifact_id] += 1

    context = "\n\n".join(context_parts)
    return context, included_sources, char_count


# ---------------------------------------------------------------------------
# Main query agent function
# ---------------------------------------------------------------------------

async def agent_query(
    query: str,
    domains: list[str] | None = None,
    top_k: int = 10,
    use_reranking: bool = True,
    conversation_messages: list[dict[str, str]] | None = None,
    chroma_client: Any | None = None,
    redis_client: Any | None = None,
    neo4j_driver: Any | None = None,
    debug_timing: bool = False,
    allowed_domains: list[str] | None = None,
    strict_domains: bool = False,
    model: str | None = None,
    graph_store: GraphStore | None = None,
    skip_cache: bool = False,
    metadata_filter: dict | None = None,
    exclude_packs: bool = False,
    budget_seconds: float | None = None,
    memory_enabled: bool = True,
) -> dict[str, Any]:
    """Budget-gated public entry for multi-domain query.

    Wraps ``_agent_query_impl`` in ``asyncio.wait_for`` with the configured
    wall-clock ceiling (``AGENT_QUERY_BUDGET_SECONDS``, default 20s) so a
    pathologically slow retrieval can never block the event loop past the
    45s watchdog. On timeout, returns a structured "degraded" response
    rather than raising — the caller (frontend or downstream pipeline) can
    surface a helpful message and let the user retry or narrow the query.

    ``budget_seconds`` overrides the configured ceiling per request:
    offline/batch callers (the eval harness, SDK batch jobs) opt into
    patience without touching the interactive default. Bounds are enforced
    at the API boundary (1–120s in the router schema); internal callers
    are trusted.
    """
    budget = (
        budget_seconds
        if budget_seconds is not None
        else getattr(config, "AGENT_QUERY_BUDGET_SECONDS", 20.0)
    )
    try:
        return await asyncio.wait_for(
            _agent_query_impl(
                query=query,
                domains=domains,
                top_k=top_k,
                use_reranking=use_reranking,
                conversation_messages=conversation_messages,
                chroma_client=chroma_client,
                redis_client=redis_client,
                neo4j_driver=neo4j_driver,
                debug_timing=debug_timing,
                allowed_domains=allowed_domains,
                strict_domains=strict_domains,
                model=model,
                graph_store=graph_store,
                skip_cache=skip_cache,
                metadata_filter=metadata_filter,
                exclude_packs=exclude_packs,
                memory_enabled=memory_enabled,
            ),
            timeout=budget,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "agent_query exceeded %.1fs wall-clock budget for query=%r (degraded response returned)",
            budget, query[:80],
        )
        from core.models.query_envelope import QueryEnvelope
        env = QueryEnvelope()
        env.mark_degraded(
            budget_seconds=budget,
            reason=(
                "Retrieval took longer than the configured budget. "
                "This usually means the system is under load or the query "
                "matched many large collections. Try a more specific query "
                "or narrow the domain filter."
            ),
        )
        return env.to_dict()


async def agent_query_full(
    query: str,
    *,
    domains: list[str] | None = None,
    top_k: int = 10,
    use_reranking: bool = True,
    conversation_messages: list[dict[str, str]] | None = None,
    chroma_client: Any | None = None,
    redis_client: Any | None = None,
    neo4j_driver: Any | None = None,
    graph_store: GraphStore | None = None,
    debug_timing: bool = False,
    allowed_domains: list[str] | None = None,
    strict_domains: bool = False,
    model: str | None = None,
    skip_cache: bool = False,
    metadata_filter: dict | None = None,
    exclude_packs: bool = False,
    kb_enabled: bool = True,
    external_augmentation: bool = True,
    response_text: str | None = None,
    enable_self_rag: bool | None = None,
    budget_seconds: float | None = None,
    memory_enabled: bool = True,
) -> dict[str, Any]:
    """Canonical full agentic-retrieval path.

    The single importable entry every surface routes through — REST
    ``/agent/query`` (manual mode), MCP ``pkb_agent_query``, A2A, custom agents —
    so they all get the identical pipeline: core multi-surface retrieval (rerank,
    provenance, ``exclude_packs``, tenant-scope, via :func:`agent_query`) + CRAG
    external augmentation + Self-RAG. Built in Phase 1 to end the
    wrapper-owns-the-stack bypass (audit RPB-1 / STREAM-07).

    REST-only concerns (the KB_POOL gate, query-scope expansion, exact-match
    query cache, header parsing, smart-mode orchestrator, ndcg metric,
    HTTPException mapping) stay in the thin router wrapper.
    """
    from core.agents.crag import augment_external_crag
    from core.agents.self_rag import maybe_self_rag

    threshold = getattr(config, "RETRIEVAL_QUALITY_THRESHOLD", 0.4)

    if not kb_enabled:
        # Conversation-only path: no KB to retrieve from.
        # E1 CR-032: no source_status / low_confidence write-only stamps.
        result: dict[str, Any] = {
            "context": "", "sources": [], "confidence": 0.0,
            "domains_searched": [], "total_results": 0,
            "token_budget_used": 0, "graph_results": 0, "results": [],
            "strategy": "conversation_only",
        }
    else:
        result = await agent_query(
            query=query,
            domains=domains,
            top_k=top_k,
            use_reranking=use_reranking,
            conversation_messages=conversation_messages,
            chroma_client=chroma_client,
            redis_client=redis_client,
            neo4j_driver=neo4j_driver,
            graph_store=graph_store,
            debug_timing=debug_timing,
            allowed_domains=allowed_domains,
            strict_domains=strict_domains,
            model=model,
            skip_cache=skip_cache,
            metadata_filter=metadata_filter,
            exclude_packs=exclude_packs,
            budget_seconds=budget_seconds,
            memory_enabled=memory_enabled,
        )

    # External CRAG may fire on low KB confidence; the low_confidence boolean is
    # no longer stamped on the response (E1 CR-032 — write-only, zero readers).
    if external_augmentation:
        result = await augment_external_crag(result, query, domains, threshold)

    if isinstance(result, dict):
        # Canonical-boundary clamp: confidence is a 0-1 contract for every
        # surface (SDK model enforces le=1 and 500s otherwise); boosted
        # relevances on small corpora can push the impl's average past 1.
        result["confidence"] = min(1.0, max(0.0, float(result.get("confidence", 0.0) or 0.0)))

    # Self-RAG only fires when a generated answer is supplied to validate.
    result = await maybe_self_rag(
        result,
        response_text,
        enable_self_rag,
        chroma_client=chroma_client,
        neo4j_driver=neo4j_driver,
        redis_client=redis_client,
        model=model,
    )
    return result


# ---------------------------------------------------------------------------
# Phase R.3 — HyPE augmentation helper
# ---------------------------------------------------------------------------

def _hype_retrieval_enabled() -> bool:
    """Return True when ``RETRIEVAL_HYPE_ENABLED`` is explicitly set to truthy.

    Default is ``false`` — the flag must be flipped after eval gate is
    cleared (≥ +0.02 NDCG@10 sustained on the full corpus).
    """
    import os as _os
    val = _os.getenv("RETRIEVAL_HYPE_ENABLED", "false").strip().lower()
    return val in ("true", "1", "yes", "on")


async def _augment_with_hype(
    query: str,
    results: list[dict[str, Any]],
    chroma_client: Any | None,
    domains: list[str] | None,
) -> list[dict[str, Any]]:
    """Augment retrieval results with HyPE (Hypothetical Prompt Embeddings).

    Phase R.3.  Issues a parallel query against the HyPE question collections
    (pre-computed at index time), then deduplicates against the content hits
    — parent chunks that appear in both lists take the higher-relevance score.
    Chunks found only via HyPE are appended with ``hype_source=True``.

    Returns ``results`` unchanged when:
      - ``RETRIEVAL_HYPE_ENABLED`` is false (the default).
      - ``chroma_client`` is not provided.
      - The HyPE collections are missing (index was run with flag off).
      - Any exception occurs (non-fatal; logged at DEBUG).

    Parameters
    ----------
    query:
        The (possibly enriched) search query string.
    results:
        Content-embedding hits from ``multi_domain_query``.
    chroma_client:
        ChromaDB client.  ``None`` → skip silently.
    domains:
        Effective domain list.  ``None`` defaults to ``config.DOMAINS``.
    """
    if not _hype_retrieval_enabled() or chroma_client is None:
        return results

    try:
        from core.retrieval.hype_index import hype_collection_name
        from core.retrieval.hype_match import dedup_with_hype_results
        from core.utils.embeddings import get_embedding_function

        _ef = get_embedding_function()
        if _ef is None:
            logger.debug("_augment_with_hype: no embed function available; skipping")
            return results

        # Build the list of base collection names from the effective domains.
        _domains = domains or config.DOMAINS
        collection_names = [config.collection_name(d) for d in _domains]

        # Embed query once.  _ef returns list[list[float]]; take first row.
        _raw_embeddings: list[list[float]] = await asyncio.to_thread(_ef, [query])
        query_embedding: list[float] = list(_raw_embeddings[0])

        # Query each HyPE collection and collect hits.
        hype_hits: list[dict[str, Any]] = []
        for coll_name in collection_names:
            hype_coll_name = hype_collection_name(coll_name)
            try:
                hype_coll = await asyncio.to_thread(
                    chroma_client.get_collection, hype_coll_name
                )
                hype_results = await asyncio.to_thread(
                    hype_coll.query,
                    query_embeddings=[query_embedding],
                    n_results=10,
                    include=["documents", "metadatas", "distances"],
                )
                if hype_results["ids"] and hype_results["ids"][0]:
                    from core.utils.embeddings import l2_distance_to_relevance
                    for i, hype_doc_id in enumerate(hype_results["ids"][0]):
                        distance = hype_results["distances"][0][i] if hype_results["distances"] else 1.0
                        relevance = l2_distance_to_relevance(distance)
                        meta = hype_results["metadatas"][0][i] if hype_results["metadatas"] else {}
                        hype_hits.append({
                            "content": hype_results["documents"][0][i],
                            "relevance": round(relevance, 4),
                            "chunk_id": hype_doc_id,
                            "source_chunk_id": meta.get("source_chunk_id", ""),
                            "artifact_id": meta.get("source_artifact_id", ""),
                            "filename": "",
                            "domain": "",
                            "chunk_index": 0,
                            "collection": hype_coll_name,
                            "ingested_at": "",
                            "sub_category": "",
                            "tags_json": "[]",
                            "keywords": "[]",
                            "memory_type": "",
                            "metadata": meta,
                        })
            except Exception as e:  # noqa: BLE001 — observability boundary
                # HyPE collection missing (flag was off at index time) — not an error.
                log_swallowed_error(
                    "core.agents.query_agent._augment_with_hype.collection_get",
                    e,
                    context={"hype_collection": hype_coll_name},
                )

        if not hype_hits:
            return results

        merged = dedup_with_hype_results(results, hype_hits)
        hype_net_new = len(merged) - len(results)
        if hype_net_new > 0:
            logger.info(
                "HyPE augmentation added %d net-new chunk(s) (hype_hits=%d)",
                hype_net_new, len(hype_hits),
            )
        return merged

    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("core.agents.query_agent.hype_augment", exc)
        return results


def _surface_route_dict(query: str) -> dict[str, Any]:
    """Compute the knowledge-surface route for a query (GA P0.5 A1a).

    Surfaces *which* intent/surface the query maps to (wiki / vector / graph /
    memory) so the chosen route is visible end-to-end (API/UI/eval) instead of
    living only in observability tooling. Behaviour-neutral for now — A1b biases
    retrieval on this; A2 wires the memory surface. Graceful: never breaks the
    query path.
    """
    try:
        from core.retrieval.surface_router import route as _surface_route

        sr = _surface_route(query)
        return {
            "intent": sr.intent,
            "primary": sr.primary,
            "surfaces": list(sr.surfaces),
            "confidence": round(sr.confidence, 4),
            "matched_entity_hint": sr.matched_entity_hint,
        }
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("core.agents.query_agent.surface_route", exc)
        return {}


def _should_skip_graph(
    high_conf_count: int,
    effective_top_k: int,
    surface_route: dict[str, Any],
    biased_enabled: bool,
) -> bool:
    """Decide whether to skip graph expansion (GA P0.5 A1b).

    Default rule: skip when vector already returned enough high-confidence hits
    (saves a Neo4j round-trip). When surface-biased retrieval is enabled, a
    ``relational`` intent ALWAYS consults the graph surface — the early-exit
    would otherwise starve the very queries the graph exists to answer. Pure
    decision function so it's unit-testable without the live stack.
    """
    if biased_enabled and surface_route.get("intent") == "relational":
        return False
    return high_conf_count >= effective_top_k


# GA P0.5 C2 — wiki / compiled-summary surface. Core stays decoupled from the
# app-layer wiki service via a registered fetcher (mirrors set_data_source_registry
# / set_entity_extraction_enqueue); app startup wires it. Unwired → no-op.
_wiki_page_fetcher: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None


def set_wiki_page_fetcher(
    fn: Callable[[str], Awaitable[dict[str, Any] | None]] | None,
) -> None:
    """Register the app-layer compiled-wiki-page fetcher (called from app startup)."""
    global _wiki_page_fetcher
    _wiki_page_fetcher = fn


async def _recall_wiki_surface(entity_hint: str) -> list[dict[str, Any]]:
    """Fetch the compiled wiki page for an entity and adapt it (GA P0.5 C2).

    Wires the wiki surface into the query path for ``compiled_summary`` queries
    ("what is X"). Returns [] when no fetcher is wired (app startup didn't
    register one) or no page exists. Graceful on any fetcher error.
    """
    if _wiki_page_fetcher is None or not entity_hint:
        return []
    try:
        page = await _wiki_page_fetcher(entity_hint)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("core.agents.query_agent.wiki_surface", exc)
        return []
    if not page:
        return []
    return [
        {
            "content": page.get("content", ""),
            "relevance": 1.0,  # the compiled summary is authoritative for this intent
            "domain": "wiki",
            "artifact_id": page.get("slug", entity_hint),
            "filename": page.get("slug", entity_hint),
            "chunk_index": 0,
            "source_type": "wiki",
            # RAG Phase 1.1 — best-effort provenance date (None when the
            # compiled page dict carries no timestamp).
            "created_at": page.get("updated_at") or page.get("created_at"),
            "source_authority": "compiled_wiki",
            "title": page.get("title", ""),
        }
    ]


async def _recall_memory_surface(
    query: str,
    chroma_client: Any,
    neo4j_driver: Any,
    top_k: int,
) -> list[dict[str, Any]]:
    """Recall episodic memories and adapt them to the query-result shape (GA P0.5 A2).

    Wires the dormant memory surface into the query path: for ``personal_context``
    queries (e.g. "what did we decide"), recall scored memories and merge them so
    they participate in dedup/rerank/assembly like any other source. Adapts
    ``recall_memories``' dict shape (text/adjusted_score/memory_id) onto the
    retrieval contract (content/relevance/artifact_id/source_type). Graceful:
    returns [] on any failure so the query path never breaks.
    """
    try:
        from core.agents.memory import recall_memories

        mems = await recall_memories(
            query, chroma_client=chroma_client, neo4j_driver=neo4j_driver, top_k=top_k,
        )
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("core.agents.query_agent.memory_surface", exc)
        return []
    return [
        {
            "content": m.get("text", ""),
            "relevance": m.get("adjusted_score", 0.0),
            "domain": "conversations",
            "artifact_id": m.get("memory_id", ""),
            "filename": m.get("memory_id", ""),
            "chunk_index": 0,
            "source_type": "memory",
            # RAG Phase 1.1 — best-effort provenance date (None when the
            # recalled memory dict carries no creation timestamp).
            "created_at": m.get("created_at"),
            "source_authority": "user_memory",
            "memory_type": m.get("memory_type", "fact"),
        }
        for m in mems
    ]


# Kept only as the "discount" policy's scale factor — see
# config.features.RAG_CONVERSATIONS_POLICY for why "exclude" is now the
# default instead.
_CHAT_TRANSCRIPT_DISCOUNT = 0.35


def _apply_conversations_rag_policy(
    results: list[dict[str, Any]], policy: str,
) -> list[dict[str, Any]]:
    """Apply RAG_CONVERSATIONS_POLICY to conversations-domain RAG evidence (Phase 1.4).

    Scope is deliberately narrow: only raw chat-transcript *artifacts*
    (``domain == "conversations"`` with a filename minted by
    ``app/processor/jobs/feedback_ingest.py``, always prefixed ``"chat_"``)
    are policy-gated here. Those are assistant-authored and can be
    hallucinated, so letting them resurface as future RAG evidence risks
    the retrieval-side sibling of the chat-verification circularity bug.

    Recalled episodic memories — whether merged in via
    ``_recall_memory_surface`` (``source_type == "memory"``) or retrieved
    directly as "memory_*"-filenamed artifacts — are user-confirmed and are
    never dropped or discounted here, regardless of policy: the identifying
    check below only matches the "chat_" filename prefix, which memory
    artifacts never carry.

    - "exclude"  — drop transcript results entirely (default).
    - "discount" — keep them, relevance scaled by ``_CHAT_TRANSCRIPT_DISCOUNT``.
    - "include"  — keep them at full relevance (no penalty).
    """
    kept: list[dict[str, Any]] = []
    for r in results:
        _fn = r.get("filename", "")
        if r.get("domain") == "conversations" and _fn.startswith("chat_"):
            if policy == "exclude":
                continue
            r["source_authority"] = "chat_transcript"
            if policy == "discount":
                r["relevance"] = round(r["relevance"] * _CHAT_TRANSCRIPT_DISCOUNT, 4)
            # "include": no penalty — falls through and is kept as-is.
        elif r.get("domain") == "conversations" and _fn.startswith("memory_"):
            r["source_authority"] = "user_memory"
        kept.append(r)
    return kept


async def _agent_query_impl(
    query: str,
    domains: list[str] | None = None,
    top_k: int = 10,
    use_reranking: bool = True,
    conversation_messages: list[dict[str, str]] | None = None,
    chroma_client: Any | None = None,
    redis_client: Any | None = None,
    neo4j_driver: Any | None = None,
    debug_timing: bool = False,
    allowed_domains: list[str] | None = None,
    strict_domains: bool = False,
    model: str | None = None,
    graph_store: GraphStore | None = None,
    skip_cache: bool = False,
    metadata_filter: dict | None = None,
    exclude_packs: bool = False,
    memory_enabled: bool = True,
) -> dict[str, Any]:
    """Execute multi-domain query with reranking, graph expansion, and context assembly.

    ``memory_enabled`` honors the caller's ``context_sources.memory`` tri-state
    gate: when False the personal-context memory surface is suppressed so a user
    who toggled Memory off never has recalled memories enter their answer
    (CR-016). Defaults True — the historical always-on behavior.
    """
    timer = StepTimer(enabled=debug_timing)
    # Unconditional wall-clock start (StepTimer is a debug timer, off by default,
    # so its "total" is absent in production) — surfaces real retrieval latency
    # in the envelope so the Knowledge Console isn't a permanent 0ms (CR-039).
    _wall_start = time.monotonic()
    # GA P0.5 A1a — compute the surface route once and surface it in every
    # return path so callers/UI/eval can see which surface intent fired.
    _surface_route = _surface_route_dict(query)
    from config.features import (
        ENABLE_ADAPTIVE_RETRIEVAL,
        ENABLE_INTELLIGENT_ASSEMBLY,
        ENABLE_LATE_INTERACTION,
        ENABLE_LLM_QUERY_DECOMPOSITION,
        ENABLE_MMR_DIVERSITY,
        ENABLE_QUERY_DECOMPOSITION,
        ENABLE_SEMANTIC_CACHE,
        ENABLE_SURFACE_BIASED_RETRIEVAL,
        RAG_CONVERSATIONS_POLICY,
    )

    # Semantic cache early-return — check before any retrieval work.
    # Scope is captured from the INCOMING domain filter (the consumer-visible
    # contract) before follow-up prioritization / adaptive gating mutate it.
    _cache_domains = list(domains) if domains else None
    _query_embedding: np.ndarray | None = None
    with timer.step("semantic_cache_lookup"):
        # E1 CR-001 tail: a narrowing directive (document metadata filter or pack
        # exclusion) scopes retrieval to a subset the general semantic cache must
        # neither serve to unfiltered queries nor be populated by. Skipping the
        # lookup also skips the store (below) — it is gated on _query_embedding,
        # which is only set inside this block.
        if (
            ENABLE_SEMANTIC_CACHE and redis_client and not skip_cache
            and metadata_filter is None and not exclude_packs
        ):
            try:
                from core.retrieval.semantic_cache import cache_lookup
                from core.utils.embeddings import get_embedding_function
                _ef = get_embedding_function()
                if _ef is not None:
                    ef = _ef  # bind for mypy — lambda captures the variable, not the value
                    _query_embedding = await asyncio.to_thread(
                        lambda: np.asarray(ef([query])[0])
                    )
                    cached = cache_lookup(
                        _query_embedding, redis_client, domains=_cache_domains,
                        allowed_domains=allowed_domains,
                        memory_enabled=memory_enabled,
                    )
                    if cached is not None:
                        cached["semantic_cache_hit"] = True
                        return cached
            except Exception as e:  # noqa: BLE001 — observability boundary
                log_swallowed_error(
                    "core.agents.query_agent.semantic_cache_lookup", e,
                )

    search_query = query
    if conversation_messages:
        search_query = _enrich_query(
            query, conversation_messages, max_context_messages=config.QUERY_CONTEXT_MESSAGES,
        )
        if search_query != query:
            logger.info(f"Enriched query: {query!r} → {search_query!r}")

    # Step 0: Adaptive retrieval gate — may short-circuit or reduce top_k
    effective_top_k = top_k
    if ENABLE_ADAPTIVE_RETRIEVAL:
        from core.retrieval.retrieval_gate import classify_retrieval_need
        decision = classify_retrieval_need(query)
        if decision.action == "skip":
            logger.info("Retrieval gate: skip (%s)", decision.reason)
            return {
                "context": "",
                "sources": [],
                "confidence": 0.0,
                "domains_searched": domains if domains else config.DOMAINS,
                "total_results": 0,
                "token_budget_used": 0,
                "graph_results": 0,
                "results": [],
                "retrieval_skipped": True,
                "retrieval_reason": decision.reason,
                "surface_route": _surface_route,
            }
        if decision.action == "light":
            effective_top_k = decision.top_k
            logger.info("Retrieval gate: light (top_k=%d, %s)", effective_top_k, decision.reason)

    # When querying from a chat flow (conversation_messages provided) and no
    # explicit domain filter was requested, exclude the "conversations" domain.
    # Feedback-ingested conversation turns would otherwise dominate results,
    # creating circular noise (same pattern as hallucination.py:87-89).
    effective_domains = domains
    if effective_domains is None and conversation_messages:
        _followup_domains = [d for d in config.DOMAINS if d != "conversations"]
        # CH4: keep the most-likely domains first and cap the tail so the
        # all-domain follow-up fan-out stays within the wall-clock budget
        # without losing coherence (least-relevant domains drop, not arbitrary).
        effective_domains = _prioritize_domains(
            search_query, _followup_domains,
            getattr(config, "AGENT_QUERY_FOLLOWUP_MAX_DOMAINS", 0),
        )
        if len(effective_domains) < len(_followup_domains):
            logger.info(
                "Follow-up retrieval: %d → %d domains (most-likely-first cap)",
                len(_followup_domains), len(effective_domains),
            )
        effective_top_k = _followup_retrieval_top_k(effective_top_k, conversation_messages, domains)

    # Consumer domain isolation: restrict to allowed domains if configured
    if allowed_domains is not None:
        if effective_domains is not None:
            effective_domains = [d for d in effective_domains if d in allowed_domains]
        else:
            effective_domains = list(allowed_domains)
        if not effective_domains:
            logger.info("Consumer domain filter removed all requested domains")
            return {
                "context": "",
                "sources": [],
                "confidence": 0.0,
                "domains_searched": [],
                "total_results": 0,
                "token_budget_used": 0,
                "graph_results": 0,
                "results": [],
                "retrieval_skipped": True,
                "retrieval_reason": "consumer_domain_restricted",
                "surface_route": _surface_route,
            }

    # Step 0.5: Query decomposition — may split into parallel sub-queries
    _skip_normal_retrieval = False
    with timer.step("vector_search"):
        if ENABLE_QUERY_DECOMPOSITION:
            # Force LLM decomposition for *implicit* multi-hop analytical queries
            # (count / date-arithmetic / preference) that carry no conjunction
            # trigger — but only when the SLO-gated flag is enabled, so the live
            # query path's latency budget is unchanged by default.
            from core.agents.answer_synthesis import AnswerMode, classify_answer_mode
            from core.retrieval.query_decomposer import decompose_query, needs_decomposition, parallel_retrieve
            _analytical = classify_answer_mode(search_query) is not AnswerMode.EXTRACTIVE
            _force_llm = ENABLE_LLM_QUERY_DECOMPOSITION and _analytical
            if needs_decomposition(search_query) or _force_llm:
                sub_queries = await decompose_query(
                    search_query, use_llm=_force_llm, force_llm=_force_llm,
                )
                if len(sub_queries) > 1:
                    logger.info("Decomposed query into %d sub-queries: %s", len(sub_queries), sub_queries)

                    async def _retrieve_sub(sq: str) -> list[dict[str, Any]]:
                        return await multi_domain_query(
                            query=sq, domains=effective_domains,
                            top_k=effective_top_k, chroma_client=chroma_client,
                            metadata_filter=metadata_filter,
                        )

                    results = await parallel_retrieve(sub_queries, _retrieve_sub)
                    _skip_normal_retrieval = True

        if not _skip_normal_retrieval:
            with span("retrieval.chroma", "multi_domain_query", domains=len(effective_domains or []), top_k=effective_top_k):
                results = await multi_domain_query(
                    query=search_query,
                    domains=effective_domains,
                    top_k=effective_top_k,
                    chroma_client=chroma_client,
                    metadata_filter=metadata_filter,
                )
        breadcrumb(f"vector search complete: {len(results)} results", category="retrieval")

    # GA P0.5 A2 — memory surface. For personal-context queries, recall episodic
    # memories and merge them so they participate in rerank/assembly. Behind the
    # surface-bias flag ENABLE_SURFACE_BIASED_RETRIEVAL (default ON).
    if (
        ENABLE_SURFACE_BIASED_RETRIEVAL
        and memory_enabled
        and _surface_route.get("intent") == "personal_context"
    ):
        with timer.step("memory_surface"):
            _mem = await _recall_memory_surface(
                search_query, chroma_client, neo4j_driver, effective_top_k,
            )
            if _mem:
                results = results + _mem
                breadcrumb(f"memory surface: +{len(_mem)} memories", category="retrieval")

    # GA P0.5 C2 — wiki surface. For "what is X" queries, prepend the compiled
    # wiki/concept page for the matched entity. Behind the surface-bias flag
    # ENABLE_SURFACE_BIASED_RETRIEVAL (default ON) — a no-op until app startup
    # registers a wiki fetcher.
    if ENABLE_SURFACE_BIASED_RETRIEVAL and _surface_route.get("intent") == "compiled_summary":
        _hint = _surface_route.get("matched_entity_hint")
        if _hint:
            with timer.step("wiki_surface"):
                _wiki = await _recall_wiki_surface(_hint)
                if _wiki:
                    results = _wiki + results
                    breadcrumb("wiki surface: +1 compiled page", category="retrieval")

    # CRAG quality gate lives at the router layer (app/routers/agents.py).
    # The router owns the single-source-of-truth decision for firing external
    # sources when KB relevance is below RETRIEVAL_QUALITY_THRESHOLD. Keeping a
    # second gate here would double-fan-out to the same sources and split
    # threshold tuning across two files.

    # Search adjacent domains at reduced weight when specific domains are requested.
    # Skipped when strict_domains=True (consumer isolation — no cross-domain bleed).
    if not strict_domains and domains and set(domains) != set(config.DOMAINS):
        adjacent = _get_adjacent_domains(domains)
        if adjacent:
            cross_results = await multi_domain_query(
                query=search_query,
                domains=list(adjacent.keys()),
                top_k=max(3, top_k // 2),
                chroma_client=chroma_client,
                # E1 CR-057: honor the document/metadata scope on the adjacent-
                # domain bleed too, or a file-scoped answer admits out-of-file
                # chunks from adjacent domains.
                metadata_filter=metadata_filter,
            )
            for r in cross_results:
                r["relevance"] = round(
                    r["relevance"] * adjacent.get(r["domain"], config.CROSS_DOMAIN_DEFAULT_AFFINITY),
                    4,
                )
                r["cross_domain"] = True
            results.extend(cross_results)

    results = deduplicate_results(results)

    # ── Source authority: conversations-domain RAG policy (Phase 1.4) ────
    # See config.features.RAG_CONVERSATIONS_POLICY for the full loop this
    # flag governs, and _apply_conversations_rag_policy's docstring for the
    # exact scope (memory-surface results are never touched here).
    results = _apply_conversations_rag_policy(results, RAG_CONVERSATIONS_POLICY)

    with timer.step("graph_expansion"):
        graph_count_before = len(results)
        # Early-exit: skip graph expansion when vector search already returned
        # enough high-confidence results (saves a Neo4j round-trip).
        _high_conf = [r for r in results if r.get("relevance", 0) > 0.8]
        if _should_skip_graph(
            len(_high_conf), effective_top_k, _surface_route, ENABLE_SURFACE_BIASED_RETRIEVAL,
        ):
            logger.debug(
                "Skipping graph expansion: %d/%d results above 0.8 confidence",
                len(_high_conf), effective_top_k,
            )
        else:
            from config.features import RETRIEVAL_MODE
            with span("graph.expand", "neighbour_lookup", seed_count=len(results)):
                # RETRIEVAL_MODE=auto delegates to the heuristic router
                # which picks local_graphrag or global_graphrag per query.
                effective_mode = RETRIEVAL_MODE
                if effective_mode == "auto":
                    from core.agents.query_router import route as _route
                    effective_mode = _route(query)

                if effective_mode == "global_graphrag" and neo4j_driver is not None:
                    # Workstream E Phase 4b.4 — community-summary expansion.
                    results = await graph_expand_results_via_communities(
                        results=results,
                        query=query,
                        chroma_client=chroma_client,
                        neo4j_driver=neo4j_driver,
                    )
                    if len(results) == graph_count_before:
                        # No matching summaries (community layer empty or
                        # query has no overlap with seed entities) → fall
                        # back to local entity expansion for non-empty graphs.
                        results = await graph_expand_results_via_entities(
                            results=results, query=query,
                            chroma_client=chroma_client, neo4j_driver=neo4j_driver,
                        )
                elif effective_mode == "local_graphrag" and neo4j_driver is not None:
                    # Workstream E Phase 4a.6 — entity-neighborhood expansion.
                    # Falls through to baseline silently when no entities yet.
                    results = await graph_expand_results_via_entities(
                        results=results,
                        query=query,
                        chroma_client=chroma_client,
                        neo4j_driver=neo4j_driver,
                    )

                # Final fall-through to baseline relationship traversal when
                # no graph-expansion mode contributed anything (e.g.
                # pre-Phase-4a.4 backfill state, or all expansions skipped).
                if len(results) == graph_count_before:
                    results = await graph_expand_results(
                        results=results,
                        query=query,
                        chroma_client=chroma_client,
                        neo4j_driver=neo4j_driver,
                        graph_store=graph_store,
                    )
        graph_results_added = len(results) - graph_count_before

    # Phase R.3 — HyPE dual-query augmentation.
    # When RETRIEVAL_HYPE_ENABLED=true, issue a parallel query against the
    # HyPE (Hypothetical Prompt Embeddings) collections and merge results.
    # Default is off — flag must be flipped after eval gate is cleared.
    with timer.step("hype_augment"):
        results = await _augment_with_hype(
            query=query,
            results=results,
            chroma_client=chroma_client,
            domains=effective_domains,
        )

    from core.utils.temporal import is_within_window, parse_temporal_intent, recency_score
    temporal_days = parse_temporal_intent(query)

    if temporal_days is not None:
        results = [
            r for r in results
            if is_within_window(
                r.get("ingested_at", ""),
                temporal_days,
            )
        ]

    for r in results:
        ingested = r.get("ingested_at", "")
        if ingested:
            boost = recency_score(ingested) * config.TEMPORAL_RECENCY_WEIGHT
            r["relevance"] = round(min(1.0, r["relevance"] + boost), 4)

    # Step 4.5: Metadata boost — surface tag/sub_category-aligned results before reranking
    results = apply_metadata_boost(results, query)

    # Step 4.6: Context alignment boost — reward results matching conversation context
    results = apply_context_alignment_boost(results, conversation_messages)

    # Step 4.7: Active-learning signals — apply endorsement_weight + drop flagged.
    # Single-query Neo4j round-trip enriches each chunk with the source
    # artifact's endorsement_weight (default 1.0) and flag_reason. Flagged
    # artifacts are filtered out of the result set; endorsement_weight
    # multiplies relevance so endorsed sources rise + de-emphasised
    # sources sink before the reranker runs.
    if results and neo4j_driver is not None:
        try:
            results = await asyncio.to_thread(
                _apply_active_learning_signals, results, neo4j_driver,
            )
        except Exception as exc:  # noqa: BLE001 — observability boundary
            log_swallowed_error("query_agent.active_learning_signals", exc)

    # Step 4.9: Personal-first pack exclusion (Slice 7.3). When the caller opts
    # out of knowledge packs for this query, drop every pack chunk before
    # rerank/synthesis — applied here (after all retrieval + graph expansion) so
    # no pack chunk from any path reaches the answer. Memory/wiki/external chunks
    # carry no pack_id and are unaffected. (A Chroma where-clause can't express
    # this: non-pack chunks omit the pack_id key entirely, so $ne/$exists can't
    # select them — a post-retrieval drop is the robust path.)
    if exclude_packs and results:
        results = [r for r in results if not r.get("pack_id")]

    # Step 4.95: Scale-aware junk floor — BEFORE rerank, on the retrieval
    # scale QUALITY_MIN_RELEVANCE_THRESHOLD was calibrated for. It used to
    # run post-rerank (old Step 5.7), where it operated on the reranker's
    # replaced scores: cross-encoder sigmoids are ORDINAL (bge-reranker-v2-m3
    # puts a correct top answer near sigmoid(-4)≈0.02), so the absolute
    # floor emptied the envelope for every indirect-evidence query
    # (live-proven 2026-07-14). Scale rules:
    # - weighted_sum: fused vector+keyword relevance — the calibrated scale.
    #   BM25-only candidates are exempt: their relevance is capped at
    #   HYBRID_KEYWORD_WEIGHT (< the floor), so an unexempted floor would
    #   structurally kill the entire keyword-rescue path.
    # - rrf/tri_rrf: rank-native reciprocal scores (max ≈ 1/k) — absolute
    #   floors are meaningless; each arm is already top-k truncated.
    # - metadata_filter: caller scoped to a file — relaxed floor, as before.
    _fusion_mode = getattr(config, "HYBRID_FUSION_MODE", "weighted_sum")
    if _fusion_mode == "weighted_sum":
        _min_rel = (
            _METADATA_SCOPED_MIN_RELEVANCE if metadata_filter
            else config.QUALITY_MIN_RELEVANCE_THRESHOLD
        )
        results = [
            r for r in results
            if r.get("bm25_only") or r["relevance"] >= _min_rel
        ]

    # Step 5: Reranking (includes both direct and graph-sourced results).
    # Rerank legs REPLACE ``relevance`` with the cross-encoder's sigmoid —
    # keep the retrieval-scale score alongside for observability and any
    # scale-sensitive downstream consumer.
    for _r in results:
        _r.setdefault("retrieval_relevance", _r.get("relevance", 0.0))
    with timer.step("reranking"):
        results = await rerank_results(
            results=results,
            query=query,
            use_reranking=use_reranking,
        )

    # Step 5.05: Apply active-learning endorsement AFTER reranking. Step 4.7
    # stores each artifact's endorsement_weight but no longer pre-multiplies it,
    # because the reranker overwrites/blends relevance and washed the signal out.
    # Fold it into the final reranked score here — once, on every provider path —
    # then re-sort so a promoted (weight>1) / demoted (weight<1) artifact moves.
    if any(r.get("_endorsement_weight") for r in results):
        for _r in results:
            _w = _r.get("_endorsement_weight")
            if _w and _w != 1.0:
                _r["relevance"] = round(float(_r.get("relevance") or 0.0) * float(_w), 4)
        results.sort(key=lambda x: x.get("relevance", 0.0), reverse=True)

    # Step 5.1: Late interaction refinement — ColBERT-style MaxSim on top candidates
    with timer.step("late_interaction"):
        if ENABLE_LATE_INTERACTION and results:
            try:
                from core.retrieval.late_interaction import late_interaction_rerank
                from core.utils.embeddings import get_embedding_function
                _ef = get_embedding_function()
                if _ef is not None:
                    results = await asyncio.to_thread(
                        late_interaction_rerank,
                        results=results, query=query, embed_fn=_ef,
                    )
            except Exception as e:
                log_swallowed_error('core.agents.query_agent', e)
                logger.warning("Late interaction scoring failed: %s", e)

    # Step 5.5: Quality boost + summary enrichment — real per-artifact scores
    # via one UNWIND Cypher round-trip (Neo4jGraphStore.get_quality_and_summaries).
    with timer.step("quality_boost"):
        results = await _apply_quality_and_summaries(results, neo4j_driver, graph_store=graph_store)
        results = sorted(results, key=lambda x: x["relevance"], reverse=True)

    # Step 5.6: MMR diversity reordering — reduce redundancy in top results
    with timer.step("mmr_diversity"):
        if ENABLE_MMR_DIVERSITY and len(results) > 1:
            try:
                from core.utils.diversity import mmr_reorder
                results = mmr_reorder(results=results, query=query)
            except Exception as e:
                log_swallowed_error('core.agents.query_agent', e)
                logger.warning("MMR diversity reordering failed: %s", e)

    # Step 5.65: NLI contradiction gate — remove results that contradict the query
    with timer.step("nli_gate"):
        try:
            _nli_pairs = [(r.get("content", "")[:512], query) for r in results[:15]]
            _nli_scores = await _nli_gate_scores(_nli_pairs)
            _nli_filtered = []
            # results[:15] are already relevance-sorted (Step 5.4). Exempt the
            # top-K matches from the contradiction drop: NLI on (doc, query)
            # pairs false-positives on definitional answers, so a noisy
            # contradiction must not override a strong retrieval rank.
            _exempt_top_k = getattr(config, "NLI_GATE_EXEMPT_TOP_K", 3)
            for _idx, (r, nli) in enumerate(zip(results[:15], _nli_scores)):
                _exempt = _idx < _exempt_top_k
                if not _exempt and nli["contradiction"] >= config.NLI_CONTRADICTION_THRESHOLD:
                    logger.debug("NLI gate removed contradictory result: %s", r.get("filename", "")[:40])
                    continue
                if nli["entailment"] >= 0.5:
                    r["relevance"] = round(min(1.0, r["relevance"] + 0.05), 4)
                    r["nli_entailment"] = nli["entailment"]
                _nli_filtered.append(r)
            # Keep any results beyond top 15 (not NLI-checked, low-ranked)
            _nli_filtered.extend(results[15:])
            results = _nli_filtered
        except Exception as nli_exc:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "core.agents.query_agent.nli_gate", nli_exc,
            )

    # (The former Step 5.7 absolute floor moved to Step 4.95, pre-rerank —
    # post-rerank relevance is the cross-encoder's ordinal sigmoid, where an
    # absolute threshold has no calibrated meaning.)

    # Step 6: Assemble context
    with timer.step("context_assembly"):
        # Model-aware context budget — large-context models get more KB context
        try:
            ctx_budget = config.get_context_budget_for_model(model)
        except (AttributeError, TypeError):
            ctx_budget = getattr(config, "QUERY_CONTEXT_MAX_CHARS", 14_000)
        if not isinstance(ctx_budget, (int, float)):
            ctx_budget = 14_000
        if ENABLE_INTELLIGENT_ASSEMBLY and results:
            try:
                from core.retrieval.context_assembler import intelligent_assemble
                with span("retrieval.assembly", "intelligent_assemble", budget=ctx_budget, n_results=len(results)):
                    context, sources, coverage_meta = intelligent_assemble(
                        results=results, query=query, max_chars=ctx_budget,
                    )
                char_count = len(context)
            except Exception as e:
                log_swallowed_error('core.agents.query_agent', e)
                logger.warning("Intelligent assembly failed, falling back: %s", e)
                with span("retrieval.assembly", "token_budget_pack_fallback", budget=ctx_budget, n_results=len(results)):
                    context, sources, char_count = assemble_context(results, max_chars=ctx_budget)
        else:
            with span("retrieval.assembly", "token_budget_pack", budget=ctx_budget, n_results=len(results)):
                context, sources, char_count = assemble_context(results, max_chars=ctx_budget)

    # Step 7: Calculate confidence (average relevance of included sources).
    # Clamp to [0, 1]: relevance values pass through quality boost and
    # small-corpus BM25 blends that can exceed 1, and confidence is a
    # 0-1 contract everywhere downstream (the SDK response model 500'd
    # on confidence=4.79 from a ~350-doc corpus, 2026-07-10).
    confidence = 0.0
    if sources:
        confidence = min(1.0, max(0.0, sum(s["relevance"] for s in sources) / len(sources)))

    # Step 8: Log query (optional)
    if redis_client:
        try:
            log_event(
                redis_client,
                event_type="query",
                artifact_id="",
                domain=",".join(domains) if domains else "all",
                filename="",
                extra={
                    "query": query,
                    "results": len(results),
                    "graph_results": graph_results_added,
                },
            )
        except Exception as e:
            log_swallowed_error('core.agents.query_agent', e)
            logger.warning(f"Failed to log query: {e}")

    # E1 CR-032: envelope no longer carries write-only reranker_status /
    # domains_no_results (zero production readers; only tests pinned them).
    # domains_searched (CR-074) stays as the informational domain signal.
    result_dict: dict[str, Any] = {
        "context": context,
        "sources": sources,
        "confidence": round(confidence, 4),
        # Report the domains actually searched (after conversations-drop,
        # follow-up cap, and consumer allow-listing), not the raw requested set
        # (CR-074).
        "domains_searched": list(effective_domains) if effective_domains else list(config.DOMAINS),
        "total_results": len(results),
        "token_budget_used": char_count,
        "graph_results": graph_results_added,
        "results": results,
        "surface_route": _surface_route,
    }

    timings = timer.result()
    if timings:
        result_dict["_timings"] = timings
    # Real wall-clock the two FE hooks read (was produced by no backend → a
    # permanent 0ms in the Knowledge Console) — CR-039.
    result_dict["execution_time_ms"] = round((time.monotonic() - _wall_start) * 1000)

    # Semantic cache store — persist result for similar future queries
    if ENABLE_SEMANTIC_CACHE and redis_client and _query_embedding is not None:
        try:
            from core.retrieval.semantic_cache import cache_store
            cache_store(
                query, _query_embedding, result_dict, redis_client,
                domains=_cache_domains, allowed_domains=allowed_domains,
                memory_enabled=memory_enabled,
            )
        except Exception as e:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "core.agents.query_agent.semantic_cache_store", e,
            )

    return result_dict
