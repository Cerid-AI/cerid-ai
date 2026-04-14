# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Memory efficacy measurement — evaluates whether memory recall improves retrieval.

This is an evaluation-only module, not a runtime dependency. It can be invoked
via the eval harness or as a standalone script to quantify the impact of the
memory system on answer quality.

Metrics produced:
- confidence_delta: avg confidence improvement when memories are included
- unique_facts_from_memory: count of results sourced only from memory
- memory_source_rate: fraction of final results that came from memories
- contradiction_rate: fraction of memories that contradict KB results
- staleness_distribution: histogram of memory ages in days
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("ai-companion.memory_efficacy")


async def measure_memory_efficacy(
    test_queries: list[str],
    chroma_client: Any,
    neo4j_driver: Any,
    redis_client: Any = None,
    top_k: int = 10,
) -> dict[str, Any]:
    """Compare retrieval quality with and without memory recall.

    For each test query:
    1. Run orchestrated_query with memory ON  → results_with
    2. Run orchestrated_query with memory OFF → results_without
    3. Compare confidence, source diversity, and result composition

    Returns aggregate metrics across all test queries.
    """
    from agents.retrieval_orchestrator import orchestrated_query

    total_confidence_with = 0.0
    total_confidence_without = 0.0
    total_unique_memory_facts = 0
    total_memory_results = 0
    total_results = 0
    contradiction_count = 0
    memory_ages: list[float] = []
    query_count = 0

    for query in test_queries:
        try:
            # Run WITH memory
            result_with = await orchestrated_query(
                query=query,
                rag_mode="smart",
                top_k=top_k,
                chroma_client=chroma_client,
                redis_client=redis_client,
                neo4j_driver=neo4j_driver,
                context_sources={"kb": True, "memory": True, "external": False},
            )

            # Run WITHOUT memory
            result_without = await orchestrated_query(
                query=query,
                rag_mode="smart",
                top_k=top_k,
                chroma_client=chroma_client,
                redis_client=redis_client,
                neo4j_driver=neo4j_driver,
                context_sources={"kb": True, "memory": False, "external": False},
            )

            conf_with = result_with.get("confidence", 0.0)
            conf_without = result_without.get("confidence", 0.0)
            total_confidence_with += conf_with
            total_confidence_without += conf_without

            # Analyze source breakdown
            breakdown = result_with.get("source_breakdown", {})
            mem_results = breakdown.get("memory", [])
            kb_results = breakdown.get("kb", [])

            total_memory_results += len(mem_results)
            total_results += len(mem_results) + len(kb_results)

            # Count memory-unique facts (not in KB results)
            kb_artifact_ids = {r.get("artifact_id") for r in kb_results if r.get("artifact_id")}
            for mr in mem_results:
                aid = mr.get("artifact_id", mr.get("memory_id", ""))
                if aid and aid not in kb_artifact_ids:
                    total_unique_memory_facts += 1

                # Track memory age
                age = mr.get("age_days", 0.0)
                if age > 0:
                    memory_ages.append(age)

            # Check for contradictions between memory and KB
            if mem_results and kb_results:
                try:
                    from core.utils.nli import nli_score

                    mem_text = mem_results[0].get("content", "")[:512]
                    kb_text = kb_results[0].get("content", "")[:512]
                    if mem_text and kb_text:
                        nli = nli_score(mem_text, kb_text)
                        if float(nli["contradiction"]) >= 0.6:
                            contradiction_count += 1
                except Exception:
                    pass

            query_count += 1

        except Exception as e:
            logger.warning("Efficacy measurement failed for query %r: %s", query[:50], e)

    if query_count == 0:
        return {"error": "No queries completed successfully", "query_count": 0}

    avg_confidence_with = total_confidence_with / query_count
    avg_confidence_without = total_confidence_without / query_count

    # Age distribution buckets
    age_buckets = {"<1d": 0, "1-7d": 0, "7-30d": 0, "30-90d": 0, "90-365d": 0, ">365d": 0}
    for age in memory_ages:
        if age < 1:
            age_buckets["<1d"] += 1
        elif age < 7:
            age_buckets["1-7d"] += 1
        elif age < 30:
            age_buckets["7-30d"] += 1
        elif age < 90:
            age_buckets["30-90d"] += 1
        elif age < 365:
            age_buckets["90-365d"] += 1
        else:
            age_buckets[">365d"] += 1

    return {
        "query_count": query_count,
        "confidence_delta": round(avg_confidence_with - avg_confidence_without, 4),
        "avg_confidence_with_memory": round(avg_confidence_with, 4),
        "avg_confidence_without_memory": round(avg_confidence_without, 4),
        "unique_facts_from_memory": total_unique_memory_facts,
        "memory_source_rate": round(total_memory_results / max(1, total_results), 4),
        "contradiction_rate": round(contradiction_count / max(1, query_count), 4),
        "staleness_distribution": age_buckets,
    }
