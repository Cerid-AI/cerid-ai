# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Self-RAG Validation Loop — verify claims against KB and refine retrieval.

After initial retrieval + LLM response, this module:
1. Extracts factual claims from the response
2. Checks each claim's coverage in the KB (lightweight vector search)
3. Identifies weak claims (low similarity = retrieval gaps)
4. Uses weak claim text as refined queries for targeted retrieval
5. Merges new results into the original result set
6. Iterates until all claims are covered or max iterations reached

This is a *retrieval enhancement* — it improves the context window for future
interactions without requiring additional LLM calls for verification.
"""

from __future__ import annotations

import logging
from typing import Any

import config

logger = logging.getLogger("ai-companion.self_rag")


async def self_rag_enhance(
    query_result: dict[str, Any],
    response_text: str,
    chroma_client: Any,
    neo4j_driver: Any,
    redis_client: Any,
    model: str | None = None,
) -> dict[str, Any]:
    """Enhance retrieval results by validating claims and filling coverage gaps.

    Parameters
    ----------
    query_result : dict
        Original result from ``agent_query()``.
    response_text : str
        LLM-generated response text to validate.
    chroma_client, neo4j_driver, redis_client :
        Database clients (same as ``agent_query``).
    model : str | None
        Generating model name (for metadata).

    Returns
    -------
    dict
        The original query_result augmented with a ``self_rag`` metadata key
        and potentially enriched ``context``, ``sources``, and ``results``.
    """
    from core.agents.hallucination import extract_claims

    max_iterations = config.SELF_RAG_MAX_ITERATIONS
    weak_threshold = config.SELF_RAG_WEAK_CLAIM_THRESHOLD
    max_refined = config.SELF_RAG_MAX_REFINED_QUERIES

    # Step 1: Extract claims from the LLM response
    claims, extraction_method = await extract_claims(response_text)
    if not claims:
        logger.debug("Self-RAG: no claims extracted (method=%s)", extraction_method)
        return _with_metadata(query_result, "no_claims", 0, 0, 0)

    original_results = list(query_result.get("results", []))
    merged_results = list(original_results)
    all_refined_queries: list[str] = []
    total_additional = 0
    last_assessments: list[dict[str, Any]] = []
    iterations_done = 0

    # Step 2: Iterative refinement loop
    for iteration in range(max_iterations):
        # Check claim coverage against current results
        last_assessments = await _assess_claims(claims, chroma_client, weak_threshold)

        weak_claims = [a for a in last_assessments if not a["covered"]]
        if not weak_claims:
            logger.info(
                "Self-RAG: all %d claims covered after %d iteration(s)",
                len(claims),
                iteration,
            )
            break

        iterations_done = iteration + 1

        # Use weak claim text as refined queries
        refined_queries = [
            a["claim"] for a in weak_claims[:max_refined]
        ]
        all_refined_queries.extend(refined_queries)

        # Targeted retrieval for each weak claim
        additional = await _retrieve_for_claims(
            refined_queries, chroma_client, redis_client, neo4j_driver,
        )
        total_additional += len(additional)

        if not additional:
            logger.info("Self-RAG: no additional results found for weak claims")
            break

        # Merge new results into existing set (dedup)
        merged_results = _merge_results(merged_results, additional)
        logger.info(
            "Self-RAG iteration %d: %d weak claims, %d additional results, %d total",
            iteration + 1,
            len(weak_claims),
            len(additional),
            len(merged_results),
        )

    # Compute final stats from last assessment
    final_weak = sum(1 for a in last_assessments if not a["covered"])

    # Reassemble context if we found additional results
    if total_additional > 0:
        from core.agents.query_agent import assemble_context

        # Sort by relevance before assembly
        merged_results.sort(key=lambda x: x.get("relevance", 0.0), reverse=True)

        context, sources, char_count = assemble_context(
            merged_results, max_chars=config.QUERY_CONTEXT_MAX_CHARS,
        )
        confidence = (
            sum(s["relevance"] for s in sources) / len(sources) if sources else 0.0
        )

        return {
            **query_result,
            "context": context,
            "sources": sources,
            "confidence": round(confidence, 4),
            "total_results": len(merged_results),
            "token_budget_used": char_count,
            "results": merged_results,
            "self_rag": {
                "status": "refined",
                "iterations": iterations_done,
                "claims_total": len(claims),
                "claims_weak": final_weak,
                "refined_queries": all_refined_queries,
                "additional_results_found": total_additional,
                "claim_assessments": last_assessments,
                "extraction_method": extraction_method,
                "model": model,
            },
        }

    # No additional results found — return original with metadata
    status = "all_supported" if final_weak == 0 else "no_additional_results"
    return _with_metadata(
        query_result,
        status,
        iterations_done,
        len(claims),
        final_weak,
        assessments=last_assessments,
        extraction_method=extraction_method,
    )


async def _assess_claims(
    claims: list[str],
    chroma_client: Any,
    threshold: float,
) -> list[dict[str, Any]]:
    """Check how well each claim is covered by the KB (lightweight, no reranking)."""
    from core.agents.query_agent import multi_domain_query

    verification_domains = [d for d in config.DOMAINS if d != "conversations"]
    assessments: list[dict[str, Any]] = []

    for claim in claims:
        try:
            results = await multi_domain_query(
                query=claim,
                domains=verification_domains,
                top_k=3,
                chroma_client=chroma_client,
            )
            max_sim = max((r.get("relevance", 0.0) for r in results), default=0.0)

            # NLI entailment check — replaces pure similarity coverage
            best_nli = {"entailment": 0.0, "contradiction": 0.0, "neutral": 1.0, "label": "neutral"}
            try:
                from core.utils.nli import nli_score
                for r in results[:3]:
                    r_content = r.get("content", "")[:512]
                    if not r_content:
                        continue
                    nli = nli_score(r_content, claim)
                    if nli["entailment"] > best_nli["entailment"]:
                        best_nli = nli
            except Exception:
                logger.debug("Self-RAG: NLI scoring failed for claim %r — using similarity", claim[:50])

            covered = float(best_nli["entailment"]) >= 0.5
            contradicted = float(best_nli["contradiction"]) >= 0.6

            # Fallback: if NLI didn't load, use similarity
            if best_nli["label"] == "neutral" and best_nli["entailment"] == 0.0:
                covered = max_sim >= threshold

            assessments.append({
                "claim": claim,
                "max_similarity": round(max_sim, 4),
                "covered": covered,
                "contradicted": contradicted,
                "nli_entailment": best_nli["entailment"],
                "nli_contradiction": best_nli["contradiction"],
                "top_source": results[0].get("filename", "") if results else "",
            })
        except Exception as e:
            logger.warning("Self-RAG: claim assessment failed for %r: %s", claim[:50], e)
            assessments.append({
                "claim": claim,
                "max_similarity": 0.0,
                "covered": False,
                "contradicted": False,
                "top_source": "",
                "error": str(e),
            })

    return assessments


async def _retrieve_for_claims(
    queries: list[str],
    chroma_client: Any,
    redis_client: Any,
    neo4j_driver: Any,
) -> list[dict[str, Any]]:
    """Run targeted agent_query for each refined query (no reranking for speed)."""
    from core.agents.query_agent import agent_query

    top_k = config.SELF_RAG_REFINED_TOP_K
    additional: list[dict[str, Any]] = []

    for query in queries:
        try:
            result = await agent_query(
                query=query,
                top_k=top_k,
                use_reranking=False,
                chroma_client=chroma_client,
                redis_client=redis_client,
                neo4j_driver=neo4j_driver,
            )
            additional.extend(result.get("results", []))
        except Exception as e:
            logger.warning("Self-RAG: refined query failed for %r: %s", query[:50], e)

    return additional


def _merge_results(
    original: list[dict[str, Any]],
    additional: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge additional results into original set, deduplicating by (artifact_id, chunk_index)."""
    seen: set[tuple[str, int]] = set()
    merged: list[dict[str, Any]] = []

    for r in original:
        key = (r.get("artifact_id", ""), r.get("chunk_index", 0))
        if key not in seen:
            seen.add(key)
            merged.append(r)

    for r in additional:
        key = (r.get("artifact_id", ""), r.get("chunk_index", 0))
        if key not in seen:
            seen.add(key)
            r["self_rag_source"] = True
            merged.append(r)

    return merged


def _with_metadata(
    query_result: dict[str, Any],
    status: str,
    iterations: int,
    claims_total: int,
    claims_weak: int,
    assessments: list[dict[str, Any]] | None = None,
    merged_results: list[dict[str, Any]] | None = None,
    extraction_method: str = "",
) -> dict[str, Any]:
    """Return query_result with self_rag metadata attached."""
    result = dict(query_result)
    if merged_results is not None:
        result["results"] = merged_results
        result["total_results"] = len(merged_results)

    result["self_rag"] = {
        "status": status,
        "iterations": iterations,
        "claims_total": claims_total,
        "claims_weak": claims_weak,
    }
    if assessments:
        result["self_rag"]["claim_assessments"] = assessments
    if extraction_method:
        result["self_rag"]["extraction_method"] = extraction_method

    return result
