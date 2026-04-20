# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Context assembly — combines retrieved chunks into a coherent context window.

Dependencies: config/constants.py. Error types: none.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

import httpx

import config
from core.utils.circuit_breaker import CircuitOpenError
from core.utils.llm_parsing import parse_llm_json
from core.utils.text import STOPWORDS as _STOPWORDS
from core.utils.text import WORD_RE as _WORD_RE
from errors import RetrievalError

logger = logging.getLogger("ai-companion.query_agent")

__all__ = [
    "deduplicate_results",
    "rerank_results",
    "_rerank_cross_encoder",
    "_rerank_llm",
    "apply_metadata_boost",
    "apply_context_alignment_boost",
    "apply_quality_boost",
    "_apply_quality_and_summaries",
    "assemble_context",
]


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate chunks, keeping highest relevance per (artifact_id, chunk_index)."""
    groups = defaultdict(list)
    for result in results:
        key = (result["artifact_id"], result["chunk_index"])
        groups[key].append(result)

    deduplicated = []
    for group in groups.values():
        best = max(group, key=lambda x: x["relevance"])
        deduplicated.append(best)

    return deduplicated


# ---------------------------------------------------------------------------
# Reranking
# ---------------------------------------------------------------------------

async def rerank_results(
    results: list[dict[str, Any]],
    query: str,
    use_reranking: bool = True,
) -> list[dict[str, Any]]:
    """Rerank results using the configured strategy.

    Dispatches to cross-encoder (fast local ONNX) or Bifrost LLM based on
    ``config.RERANK_MODE``.  Falls back to relevance sort on any failure.
    """
    if not use_reranking or len(results) == 0:
        return sorted(results, key=lambda x: x["relevance"], reverse=True)

    results = sorted(results, key=lambda x: x["relevance"], reverse=True)

    mode = config.RERANK_MODE

    # When RERANK_PREFER_LOCAL is true and the local cross-encoder is
    # available, always use it regardless of RERANK_MODE — faster and free.
    if getattr(config, "RERANK_PREFER_LOCAL", False) and mode == "llm":
        try:
            from core.retrieval.reranker import _session  # type: ignore[attr-defined]
            if _session is not None:
                logger.debug("RERANK_PREFER_LOCAL: overriding llm → cross_encoder")
                return await _rerank_cross_encoder(results, query)
        except (ImportError, AttributeError):
            pass  # Fall through to configured mode

    if mode == "cross_encoder":
        return await _rerank_cross_encoder(results, query)
    if mode == "llm":
        return await _rerank_llm(results, query)
    # Fallback: no reranking (mode is "none" or unrecognised)
    return results


async def _rerank_cross_encoder(
    results: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """Rerank via local cross-encoder model (ONNX, ~50 ms for 15 candidates)."""
    try:
        from core.retrieval.reranker import rerank as ce_rerank

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, ce_rerank, query, results)
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning("Cross-encoder reranking failed, falling back to LLM: %s", e)
        return await _rerank_llm(results, query)


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

def apply_quality_boost(
    results: list[dict[str, Any]],
    neo4j_driver: Any | None = None,
) -> list[dict[str, Any]]:
    """Apply quality score multiplier to relevance scores.

    Formula: adjusted = relevance * (QUALITY_BOOST_BASE + QUALITY_BOOST_FACTOR * quality_score)
    Default:  adjusted = relevance * (0.8 + 0.2 * quality_score)

    This means quality=1.0 → 1.0x (no change), quality=0.0 → 0.8x (20% penalty).
    """
    if neo4j_driver is None or not results:
        return results

    artifact_ids = list({r["artifact_id"] for r in results if r.get("artifact_id")})
    if not artifact_ids:
        return results

    try:
        from db.neo4j.artifacts import get_quality_scores
        scores = get_quality_scores(neo4j_driver, artifact_ids)
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning(f"Quality score lookup failed (skipping boost): {e}")
        return results

    for r in results:
        quality = scores.get(r.get("artifact_id", ""), 0.5)
        multiplier = config.QUALITY_BOOST_BASE + config.QUALITY_BOOST_FACTOR * quality
        r["relevance"] = round(r["relevance"] * multiplier, 4)
        r["quality_score"] = quality

    return results


def _enrich_summaries(
    results: list[dict[str, Any]],
    neo4j_driver: Any | None = None,
) -> list[dict[str, Any]]:
    """Attach artifact-level summaries from Neo4j to query results."""
    if neo4j_driver is None or not results:
        return results

    artifact_ids = list({r["artifact_id"] for r in results if r.get("artifact_id")})
    if not artifact_ids:
        return results

    try:
        from db.neo4j.artifacts import get_artifact_summaries
        summaries = get_artifact_summaries(neo4j_driver, artifact_ids)
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning(f"Summary lookup failed (skipping): {e}")
        return results

    for r in results:
        s = summaries.get(r.get("artifact_id", ""))
        if s:
            r["summary"] = s

    return results


def _apply_quality_and_summaries(
    results: list[dict[str, Any]],
    neo4j_driver: Any | None = None,
) -> list[dict[str, Any]]:
    """Apply quality boost and summary enrichment in a single Neo4j query.

    Replaces the previous sequential ``apply_quality_boost`` +
    ``_enrich_summaries`` pattern, halving Neo4j round-trips.
    """
    if neo4j_driver is None or not results:
        return results

    artifact_ids = list({r["artifact_id"] for r in results if r.get("artifact_id")})
    if not artifact_ids:
        return results

    try:
        from db.neo4j.artifacts import get_quality_and_summaries
        scores, summaries = get_quality_and_summaries(neo4j_driver, artifact_ids)
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
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

    from utils.agent_events import emit_agent_event
    emit_agent_event(
        "assembler",
        f"Weaving {len(included_sources)} sources into a coherent answer ({char_count} chars)",
        level="success",
    )

    return context, included_sources, char_count
