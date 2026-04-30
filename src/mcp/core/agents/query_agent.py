# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Multi-domain knowledge base search with LLM reranking."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Any

import httpx
import numpy as np

import config
from config import DOMAINS
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
    return {
        "content": content,
        "relevance": round(relevance, 4),
        "artifact_id": metadata.get("artifact_id", ""),
        "filename": metadata.get("filename", ""),
        "domain": domain,
        "chunk_index": metadata.get("chunk_index", 0),
        "collection": config.collection_name(domain),
        "chunk_id": chunk_id,
        "ingested_at": metadata.get("ingested_at", ""),
        "sub_category": metadata.get("sub_category", ""),
        "tags_json": metadata.get("tags_json", "[]"),
        "keywords": metadata.get("keywords", "[]"),
        "memory_type": metadata.get("memory_type", ""),
    }


# ---------------------------------------------------------------------------
# Cross-domain affinity
# ---------------------------------------------------------------------------

def _get_adjacent_domains(requested: list[str]) -> dict[str, float]:
    """Return non-requested domains with their max affinity score."""
    requested_set = set(requested)
    adjacent: dict[str, float] = {}
    for req in requested:
        explicit = config.DOMAIN_AFFINITY.get(req, {})
        for other in DOMAINS:
            if other in requested_set:
                continue
            weight = explicit.get(other, config.CROSS_DOMAIN_DEFAULT_AFFINITY)
            adjacent[other] = max(adjacent.get(other, 0.0), weight)
    return adjacent


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
        domains = DOMAINS

    invalid_domains = [d for d in domains if d not in DOMAINS]
    if invalid_domains:
        raise ValueError(f"Invalid domains: {invalid_domains}. Valid: {DOMAINS}")

    if chroma_client is None:
        raise ValueError("chroma_client is required")

    # Pre-check which collections actually exist to skip missing domains fast
    try:
        existing_collections = {c.name for c in chroma_client.list_collections()}
    except Exception:
        existing_collections = set()

    async def query_domain(domain: str) -> list[dict[str, Any]]:
        """Query a single domain collection (vector + BM25 hybrid)."""
        col_name = config.collection_name(domain)
        if existing_collections and col_name not in existing_collections:
            return []  # Skip missing collections without HTTP round-trip
        try:
            collection = chroma_client.get_collection(name=col_name)

            query_kwargs: dict[str, Any] = {
                "query_texts": [query],
                "n_results": top_k,
                "include": ["documents", "metadatas", "distances"],
                "where": with_tenant_scope(metadata_filter),
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
            if bm25_mod.is_available():
                bm25_hits = await asyncio.to_thread(bm25_mod.search_bm25, domain, query, top_k)
                if bm25_hits:
                    bm25_map = dict(bm25_hits)

                    # Workstream E Phase 3 wire-in: HYBRID_FUSION_MODE selects
                    # between the legacy weighted-sum blend and Reciprocal
                    # Rank Fusion (Cormack/Clarke/Buettcher 2009; the 2026
                    # default in Elastic, OpenSearch, Azure AI Search,
                    # neo4j-graphrag). Default stays "weighted_sum" so this
                    # commit is a pure plumbing change — flip
                    # HYBRID_FUSION_MODE=rrf to enable.
                    fusion_mode = getattr(config, "HYBRID_FUSION_MODE", "weighted_sum")
                    fused_map: dict[str, float] = {}

                    if fusion_mode == "rrf":
                        from core.retrieval.rrf import rrf_fuse
                        vector_ranking = [
                            (entry["chunk_id"], entry["relevance"])
                            for entry in formatted
                        ]
                        # bm25_hits already sorted descending per search_bm25 contract
                        fused = rrf_fuse(
                            [vector_ranking, bm25_hits],
                            k=config.HYBRID_RRF_K,
                            weights=[
                                config.HYBRID_RRF_VECTOR_WEIGHT,
                                config.HYBRID_RRF_BM25_WEIGHT,
                            ],
                        )
                        fused_map = dict(fused)
                        for entry in formatted:
                            entry["relevance"] = round(
                                fused_map.get(entry["chunk_id"], 0.0), 6,
                            )
                        # Drop entries already represented from bm25_map so
                        # the bm25-only fetch below only fires on net-new
                        # chunk_ids — same shape as the legacy path.
                        for entry in formatted:
                            bm25_map.pop(entry["chunk_id"], None)
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
                            fetched = collection.get(
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
                                # Enforce metadata_filter on BM25-only results too
                                if metadata_filter and not all(
                                    meta.get(k) == v for k, v in metadata_filter.items()
                                ):
                                    continue
                                # In RRF mode the bm25-only chunk's fused
                                # score already accounts for its rank; in
                                # legacy mode use the keyword-weighted bm25.
                                if fusion_mode == "rrf":
                                    rel = round(
                                        fused_map.get(cid, bm25_map[cid]),
                                        6,
                                    )
                                else:
                                    rel = config.HYBRID_KEYWORD_WEIGHT * bm25_map[cid]
                                formatted.append(_format_chroma_result(
                                    content=fetched["documents"][j],
                                    relevance=rel,
                                    chunk_id=cid,
                                    domain=domain,
                                    metadata=meta,
                                ))
                                seen_ids.add(cid)
                        except Exception as e:  # noqa: BLE001 — observability boundary
                            log_swallowed_error(
                                "core.agents.query_agent.bm25_only_fetch", e,
                            )

            return formatted

        except Exception as e:
            logger.warning(f"Error querying domain {domain}: {e}")
            return []

    tasks = [query_domain(domain) for domain in domains]
    domain_results = await asyncio.gather(*tasks)

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

        fetched = await asyncio.to_thread(
            collection.query,
            query_texts=[query],
            n_results=min(3, len(chunk_ids)),
            where=with_tenant_scope({"artifact_id": rel_artifact["id"]}),
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
    # mode == "none" or unknown
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
        logger.warning(
            "Cross-encoder reranking failed — returning results in original "
            "order (no LLM fallback after Bifrost retirement): %s",
            e,
        )
        for r in results:
            r["reranker_status"] = "onnx_failed_no_fallback"
        return results


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
        return sorted(results, key=lambda x: x["relevance"], reverse=True)
    except (httpx.HTTPStatusError, json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("LLM reranking failed, falling back to embedding sort: %s", e)
        return sorted(results, key=lambda x: x["relevance"], reverse=True)


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
            r["relevance"] = round(r["relevance"] + boost, 4)
            r["metadata_boost"] = round(boost, 4)

    return results


# ---------------------------------------------------------------------------
# Context alignment boost
# ---------------------------------------------------------------------------

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
            r["relevance"] = round(r["relevance"] + boost, 4)
            r["context_alignment"] = round(alignment, 4)

    return results


# ---------------------------------------------------------------------------
# Quality boost
# ---------------------------------------------------------------------------

async def apply_quality_boost(
    results: list[dict[str, Any]],
    neo4j_driver: Any | None = None,
    graph_store: GraphStore | None = None,
) -> list[dict[str, Any]]:
    """Apply quality score multiplier to relevance scores.

    Formula: adjusted = relevance * (QUALITY_BOOST_BASE + QUALITY_BOOST_FACTOR * quality_score)
    Default:  adjusted = relevance * (0.8 + 0.2 * quality_score)

    This means quality=1.0 → 1.0x (no change), quality=0.0 → 0.8x (20% penalty).
    Accepts *graph_store* (preferred) or *neo4j_driver* (legacy, ignored in core/).
    """
    if (graph_store is None and neo4j_driver is None) or not results:
        return results

    artifact_ids = list({r["artifact_id"] for r in results if r.get("artifact_id")})
    if not artifact_ids:
        return results

    if graph_store is None:
        logger.debug("graph_store not provided; skipping quality boost")
        return results

    try:
        nodes = await graph_store.get_artifacts_batch(artifact_ids)
        scores = {aid: n.quality_score for aid, n in nodes.items()}
    except Exception as e:
        logger.warning(f"Quality score lookup failed (skipping boost): {e}")
        return results

    for r in results:
        quality = scores.get(r.get("artifact_id", ""), 0.5)
        multiplier = config.QUALITY_BOOST_BASE + config.QUALITY_BOOST_FACTOR * quality
        r["relevance"] = round(r["relevance"] * multiplier, 4)
        r["quality_score"] = quality

    return results


async def _enrich_summaries(
    results: list[dict[str, Any]],
    neo4j_driver: Any | None = None,
    graph_store: GraphStore | None = None,
) -> list[dict[str, Any]]:
    """Attach artifact-level summaries from the knowledge graph to query results."""
    if (graph_store is None and neo4j_driver is None) or not results:
        return results

    artifact_ids = list({r["artifact_id"] for r in results if r.get("artifact_id")})
    if not artifact_ids:
        return results

    if graph_store is None:
        logger.debug("graph_store not provided; skipping summary enrichment")
        return results

    try:
        nodes = await graph_store.get_artifacts_batch(artifact_ids)
        summaries = {aid: n.summary for aid, n in nodes.items() if n.summary}
    except Exception as e:
        logger.warning(f"Summary lookup failed (skipping): {e}")
        return results

    for r in results:
        s = summaries.get(r.get("artifact_id", ""))
        if s:
            r["summary"] = s

    return results


async def _apply_quality_and_summaries(
    results: list[dict[str, Any]],
    neo4j_driver: Any | None = None,
    graph_store: GraphStore | None = None,
) -> list[dict[str, Any]]:
    """Apply quality boost and summary enrichment via the graph store.

    Replaces the previous sequential ``apply_quality_boost`` +
    ``_enrich_summaries`` pattern, halving round-trips.
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
        nodes = await graph_store.get_artifacts_batch(artifact_ids)
        scores = {aid: n.quality_score for aid, n in nodes.items()}
        summaries = {aid: n.summary for aid, n in nodes.items() if n.summary}
    except Exception as e:
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
        artifact_id = result["artifact_id"]

        # Skip if this artifact already has enough chunks in context
        if artifact_counts[artifact_id] >= max_chunks_per_artifact:
            continue

        content = result["content"]
        content_len = len(content)

        if char_count + content_len > max_chars:
            continue  # don't break — later smaller chunks may still fit

        context_parts.append(content)
        included_sources.append({
            "content": content[:200],  # Preview only
            "relevance": result["relevance"],
            "artifact_id": artifact_id,
            "filename": result["filename"],
            "domain": result["domain"],
            "chunk_index": result["chunk_index"],
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
) -> dict[str, Any]:
    """Budget-gated public entry for multi-domain query.

    Wraps ``_agent_query_impl`` in ``asyncio.wait_for`` with the configured
    wall-clock ceiling (``AGENT_QUERY_BUDGET_SECONDS``, default 20s) so a
    pathologically slow retrieval can never block the event loop past the
    45s watchdog. On timeout, returns a structured "degraded" response
    rather than raising — the caller (frontend or downstream pipeline) can
    surface a helpful message and let the user retry or narrow the query.
    """
    budget = getattr(config, "AGENT_QUERY_BUDGET_SECONDS", 20.0)
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
) -> dict[str, Any]:
    """Execute multi-domain query with reranking, graph expansion, and context assembly."""
    timer = StepTimer(enabled=debug_timing)
    from config.features import (
        ENABLE_ADAPTIVE_RETRIEVAL,
        ENABLE_INTELLIGENT_ASSEMBLY,
        ENABLE_LATE_INTERACTION,
        ENABLE_MMR_DIVERSITY,
        ENABLE_QUERY_DECOMPOSITION,
        ENABLE_SEMANTIC_CACHE,
    )

    # Semantic cache early-return — check before any retrieval work
    _query_embedding: np.ndarray | None = None
    with timer.step("semantic_cache_lookup"):
        if ENABLE_SEMANTIC_CACHE and redis_client and not skip_cache:
            try:
                from core.retrieval.semantic_cache import cache_lookup
                from core.utils.embeddings import get_embedding_function
                _ef = get_embedding_function()
                if _ef is not None:
                    ef = _ef  # bind for mypy — lambda captures the variable, not the value
                    _query_embedding = await asyncio.to_thread(
                        lambda: np.asarray(ef([query])[0])
                    )
                    cached = cache_lookup(_query_embedding, redis_client)
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
                "domains_searched": domains if domains else DOMAINS,
                "total_results": 0,
                "token_budget_used": 0,
                "graph_results": 0,
                "results": [],
                "retrieval_skipped": True,
                "retrieval_reason": decision.reason,
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
        effective_domains = [d for d in config.DOMAINS if d != "conversations"]

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
            }

    # Step 0.5: Query decomposition — may split into parallel sub-queries
    _skip_normal_retrieval = False
    with timer.step("vector_search"):
        if ENABLE_QUERY_DECOMPOSITION:
            from core.retrieval.query_decomposer import decompose_query, needs_decomposition, parallel_retrieve
            if needs_decomposition(search_query):
                sub_queries = await decompose_query(search_query)
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

    # CRAG quality gate lives at the router layer (app/routers/agents.py).
    # The router owns the single-source-of-truth decision for firing external
    # sources when KB relevance is below RETRIEVAL_QUALITY_THRESHOLD. Keeping a
    # second gate here would double-fan-out to the same sources and split
    # threshold tuning across two files.

    # Search adjacent domains at reduced weight when specific domains are requested.
    # Skipped when strict_domains=True (consumer isolation — no cross-domain bleed).
    if not strict_domains and domains and set(domains) != set(DOMAINS):
        adjacent = _get_adjacent_domains(domains)
        if adjacent:
            cross_results = await multi_domain_query(
                query=search_query,
                domains=list(adjacent.keys()),
                top_k=max(3, top_k // 2),
                chroma_client=chroma_client,
            )
            for r in cross_results:
                r["relevance"] = round(
                    r["relevance"] * adjacent.get(r["domain"], config.CROSS_DOMAIN_DEFAULT_AFFINITY),
                    4,
                )
                r["cross_domain"] = True
            results.extend(cross_results)

    results = deduplicate_results(results)

    # ── Source authority: demote raw chat transcripts ────────────────────
    # Raw conversations (filename starts with "chat_") are prior LLM
    # responses — NOT authoritative sources.  Using them as evidence
    # creates circular self-verification.  Extracted memories
    # (filename starts with "memory_") are user-confirmed and keep
    # full relevance.
    _CHAT_TRANSCRIPT_DISCOUNT = 0.35
    for r in results:
        if r.get("domain") == "conversations":
            _fn = r.get("filename", "")
            if _fn.startswith("chat_"):
                r["relevance"] = round(r["relevance"] * _CHAT_TRANSCRIPT_DISCOUNT, 4)
                r["source_authority"] = "chat_transcript"
            elif _fn.startswith("memory_"):
                r["source_authority"] = "user_memory"

    with timer.step("graph_expansion"):
        graph_count_before = len(results)
        # Early-exit: skip graph expansion when vector search already returned
        # enough high-confidence results (saves a Neo4j round-trip).
        _high_conf = [r for r in results if r.get("relevance", 0) > 0.8]
        if len(_high_conf) >= effective_top_k:
            logger.debug(
                "Skipping graph expansion: %d/%d results above 0.8 confidence",
                len(_high_conf), effective_top_k,
            )
        else:
            with span("graph.expand", "neighbour_lookup", seed_count=len(results)):
                results = await graph_expand_results(
                    results=results,
                    query=query,
                    chroma_client=chroma_client,
                    neo4j_driver=neo4j_driver,
                    graph_store=graph_store,
                )
        graph_results_added = len(results) - graph_count_before

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
            r["relevance"] = round(r["relevance"] + boost, 4)

    # Step 4.5: Metadata boost — surface tag/sub_category-aligned results before reranking
    results = apply_metadata_boost(results, query)

    # Step 4.6: Context alignment boost — reward results matching conversation context
    results = apply_context_alignment_boost(results, conversation_messages)

    # Step 5: Reranking (includes both direct and graph-sourced results)
    with timer.step("reranking"):
        results = await rerank_results(
            results=results,
            query=query,
            use_reranking=use_reranking,
        )

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
                logger.warning("Late interaction scoring failed: %s", e)

    # Step 5.5: Quality boost + summary enrichment — single Neo4j round-trip
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
                logger.warning("MMR diversity reordering failed: %s", e)

    # Step 5.65: NLI contradiction gate — remove results that contradict the query
    with timer.step("nli_gate"):
        try:
            from core.utils.nli import batch_nli_score
            _nli_pairs = [(r.get("content", "")[:512], query) for r in results[:15]]
            _nli_scores = batch_nli_score(_nli_pairs)
            _nli_filtered = []
            for r, nli in zip(results[:15], _nli_scores):
                if nli["contradiction"] >= config.NLI_CONTRADICTION_THRESHOLD:
                    logger.debug("NLI gate removed contradictory result: %s", r.get("filename", "")[:40])
                    continue
                if nli["entailment"] >= 0.5:
                    r["relevance"] = round(r["relevance"] + 0.05, 4)
                    r["nli_entailment"] = nli["entailment"]
                _nli_filtered.append(r)
            # Keep any results beyond top 15 (not NLI-checked, low-ranked)
            _nli_filtered.extend(results[15:])
            results = _nli_filtered
        except Exception as nli_exc:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "core.agents.query_agent.nli_gate", nli_exc,
            )

    # Step 5.7: Filter low-relevance results below minimum threshold
    # When metadata_filter is set, the caller explicitly scoped to a file —
    # use a relaxed threshold so generic questions still return results.
    _min_rel = 0.05 if metadata_filter else config.QUALITY_MIN_RELEVANCE_THRESHOLD
    results = [r for r in results if r["relevance"] >= _min_rel]

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
                logger.warning("Intelligent assembly failed, falling back: %s", e)
                with span("retrieval.assembly", "token_budget_pack_fallback", budget=ctx_budget, n_results=len(results)):
                    context, sources, char_count = assemble_context(results, max_chars=ctx_budget)
        else:
            with span("retrieval.assembly", "token_budget_pack", budget=ctx_budget, n_results=len(results)):
                context, sources, char_count = assemble_context(results, max_chars=ctx_budget)

    # Step 7: Calculate confidence (average relevance of included sources)
    confidence = 0.0
    if sources:
        confidence = sum(s["relevance"] for s in sources) / len(sources)

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
            logger.warning(f"Failed to log query: {e}")

    result_dict: dict[str, Any] = {
        "context": context,
        "sources": sources,
        "confidence": round(confidence, 4),
        "domains_searched": domains if domains else DOMAINS,
        "total_results": len(results),
        "token_budget_used": char_count,
        "graph_results": graph_results_added,
        "results": results,
    }

    timings = timer.result()
    if timings:
        result_dict["_timings"] = timings

    # Semantic cache store — persist result for similar future queries
    if ENABLE_SEMANTIC_CACHE and redis_client and _query_embedding is not None:
        try:
            from core.retrieval.semantic_cache import cache_store
            cache_store(query, _query_embedding, result_dict, redis_client)
        except Exception as e:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "core.agents.query_agent.semantic_cache_store", e,
            )

    return result_dict
