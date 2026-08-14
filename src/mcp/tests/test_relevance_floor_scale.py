# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Scale-aware junk floor (2026-07-14).

The absolute relevance floor (QUALITY_MIN_RELEVANCE_THRESHOLD) must run
BEFORE rerank, on the fused-retrieval scale it was calibrated for. Rerank
legs REPLACE ``relevance`` with the cross-encoder's sigmoid, which is
ORDINAL — bge-reranker-v2-m3 puts a correct top answer near
sigmoid(-4) ≈ 0.02 — so the old post-rerank floor emptied the envelope
for every indirect-evidence query (live-proven: cross-domain golden
queries returned zero sources while their expected docs ranked FIRST
among the reranked candidates).

Also covered: BM25-only candidates are exempt from the weighted_sum floor
(their relevance is capped at HYBRID_KEYWORD_WEIGHT, below the floor —
an unexempted floor structurally kills the keyword-rescue path), and
rank-native RRF scores (max ≈ 1/k) skip the absolute floor entirely.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from core.agents.query_agent import agent_query


def _make_result(content: str, relevance: float, **extra) -> dict:
    r = {
        "content": content,
        "relevance": relevance,
        "chunk_id": f"chunk_{content[:8]}",
        "domain": "coding",
        "metadata": {"filename": f"{content[:8]}.md"},
        "artifact_id": f"art_{content[:8]}",
    }
    r.update(extra)
    return r


def _configure(fusion_mode: str = "weighted_sum") -> dict:
    """Config overrides applied to the REAL config module, per setting."""
    return {
        "DOMAINS": ["coding", "general"],
        "DOMAIN_AFFINITY": {},
        "CROSS_DOMAIN_DEFAULT_AFFINITY": 0.2,
        "QUERY_CONTEXT_MAX_CHARS": 14000,
        "QUALITY_BOOST_BASE": 0.8,
        "QUALITY_BOOST_FACTOR": 0.2,
        "QUALITY_METADATA_TAG_BOOST": 0.05,
        "QUALITY_METADATA_SUBCAT_BOOST": 0.08,
        "QUALITY_METADATA_MAX_BOOST": 0.15,
        "QUALITY_MIN_RELEVANCE_THRESHOLD": 0.15,
        "TEMPORAL_HALF_LIFE_DAYS": 30,
        "TEMPORAL_RECENCY_WEIGHT": 0.1,
        "CONTEXT_MAX_CHUNKS_PER_ARTIFACT": 2,
        "QUERY_CONTEXT_MESSAGES": 5,
        "AGENT_QUERY_BUDGET_SECONDS": 30.0,
        "HYBRID_FUSION_MODE": fusion_mode,
    }


_PIPELINE_PATCHES = (
    patch("core.agents.query_agent.log_event"),
    patch("core.agents.query_agent.graph_expand_results"),
    patch("core.agents.query_agent.multi_domain_query"),
    patch("config.features.ENABLE_ADAPTIVE_RETRIEVAL", False),
    patch("config.features.ENABLE_SEMANTIC_CACHE", False),
)


def _run(query_result: list[dict], config_overrides: dict, rerank_side_effect=None):
    """Drive agent_query with mocked retrieval + a controllable reranker."""
    import contextlib

    import config as config_module

    p_log, p_graph, p_mdq, p_adaptive, p_cache = _PIPELINE_PATCHES
    with contextlib.ExitStack() as stack:
        stack.enter_context(p_log)
        mock_graph = stack.enter_context(p_graph)
        mock_mdq = stack.enter_context(p_mdq)
        stack.enter_context(p_adaptive)
        stack.enter_context(p_cache)
        mock_rerank = stack.enter_context(
            patch("core.agents.query_agent.rerank_results")
        )
        stack.enter_context(
            patch("core.utils.temporal.parse_temporal_intent", return_value=None)
        )
        stack.enter_context(
            patch("core.utils.temporal.recency_score", return_value=0.0)
        )
        for name, value in config_overrides.items():
            stack.enter_context(patch.object(config_module, name, value))
        mock_mdq.side_effect = [list(query_result), []]
        # graph_expand_results returns the MERGED list — an identity mock,
        # not return_value=[], which would swallow the retrieval results.
        mock_graph.side_effect = lambda results, *a, **k: results
        if rerank_side_effect is None:
            async def rerank_side_effect(results, query, use_reranking=True):
                return sorted(results, key=lambda r: r["relevance"], reverse=True)
        mock_rerank.side_effect = rerank_side_effect
        response = asyncio.run(
            agent_query("test query", domains=["coding"], chroma_client=MagicMock())
        )
        return response, mock_rerank


class TestScaleAwareFloor:
    def test_ordinal_rerank_scores_survive_post_rerank(self):
        """A doc that passed the retrieval-scale floor must NOT be dropped
        because the reranker's ordinal sigmoid is small (the live defect)."""
        doc = _make_result("relevant document content", relevance=0.4)

        async def _replace_with_ordinal(results, query, use_reranking=True):
            for r in results:
                r["relevance"] = 0.02  # bge sigmoid for a CORRECT indirect answer
                r["reranker_status"] = "quenchforge"
            return results

        response, _ = _run([doc], _configure(), _replace_with_ordinal)
        assert response["total_results"] == 1
        assert response["results"][0]["relevance"] == 0.02

    def test_junk_floored_before_rerank(self):
        """Below-floor fused relevance is dropped BEFORE the reranker sees it."""
        junk = _make_result("junk", relevance=0.05)
        response, mock_rerank = _run([junk], _configure())
        assert response["total_results"] == 0
        passed_to_rerank = mock_rerank.call_args.kwargs["results"]
        assert passed_to_rerank == []

    def test_bm25_only_candidates_exempt_from_floor(self):
        """Keyword-arm-only docs (relevance capped at HYBRID_KEYWORD_WEIGHT,
        below the floor) must reach the reranker for judgment."""
        kw_doc = _make_result("keyword rescue", relevance=0.23, bm25_only=True)
        response, mock_rerank = _run([kw_doc], _configure())
        passed_to_rerank = mock_rerank.call_args.kwargs["results"]
        assert any(r.get("bm25_only") for r in passed_to_rerank)
        assert response["total_results"] == 1

    def test_rrf_mode_skips_absolute_floor(self):
        """RRF fused scores are rank-native (max ~ 1/k) — an absolute floor
        calibrated for weighted_sum would drop every result."""
        rrf_doc = _make_result("rrf ranked", relevance=0.016)
        response, _ = _run([rrf_doc], _configure(fusion_mode="rrf"))
        assert response["total_results"] == 1

    def test_retrieval_relevance_preserved_alongside_rerank_score(self):
        doc = _make_result("keeps provenance", relevance=0.4)

        async def _replace(results, query, use_reranking=True):
            for r in results:
                r["relevance"] = 0.9
            return results

        response, _ = _run([doc], _configure(), _replace)
        out = response["results"][0]
        assert out["relevance"] == 0.9
        assert out["retrieval_relevance"] == 0.4
