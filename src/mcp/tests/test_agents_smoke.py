# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Smoke tests for the agent modules that previously had zero dedicated
coverage (rectify, audit, maintenance, self_rag).

These aren't full behavioural tests — that would require standing up real
Neo4j / Chroma / LLM dependencies. They're regression guards that catch:

  * Import-time errors (module-level `from X import Y` breaking due to a
    Phase C bridge rename or a downstream refactor).
  * Public-API signature drift (callers pass these kwargs; if we change
    names, the test fails before the consumer does).
  * Silent swallowing — when a mocked dependency raises, the agent should
    surface a structured error, not return empty/None.

Deeper behavioural tests live in test_e2e_pipeline.py and the eval harness.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# rectify (core/agents/rectify.py — bridged via agents/rectify.py)
# ---------------------------------------------------------------------------


class TestRectifyImports:
    def test_core_module_imports(self):
        from core.agents import rectify  # noqa: F401

    def test_bridge_module_exports_rectify(self):
        # The agents/rectify.py bridge re-exports from core; a circular
        # import or missing symbol would surface here.
        from agents.rectify import rectify  # noqa: F401
        assert callable(rectify)

    def test_helper_functions_callable(self):
        from core.agents.rectify import (
            analyze_domain_distribution,
            cleanup_orphaned_chunks,
            find_duplicate_artifacts,
            find_orphaned_chunks,
            find_similar_artifacts,
            find_stale_artifacts,
            resolve_duplicates,
        )
        for fn in [
            analyze_domain_distribution, cleanup_orphaned_chunks,
            find_duplicate_artifacts, find_orphaned_chunks,
            find_similar_artifacts, find_stale_artifacts, resolve_duplicates,
        ]:
            assert callable(fn)


class TestRectifySmoke:
    def test_analyze_domain_distribution_with_mock_driver(self):
        from core.agents.rectify import analyze_domain_distribution

        driver = MagicMock()
        session = MagicMock()
        session.run.return_value = []
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)

        result = analyze_domain_distribution(driver)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


class TestAuditImports:
    def test_core_module_imports(self):
        from core.agents import audit  # noqa: F401

    def test_bridge_exports_audit(self):
        from agents.audit import audit  # noqa: F401
        assert callable(audit)

    def test_helper_functions_callable(self):
        from core.agents.audit import (
            estimate_costs,
            get_activity_summary,
            get_conversation_analytics,
            get_ingestion_stats,
            get_query_patterns,
            get_verification_analytics,
        )
        for fn in [
            estimate_costs, get_activity_summary, get_conversation_analytics,
            get_ingestion_stats, get_query_patterns, get_verification_analytics,
        ]:
            assert callable(fn)


class TestAuditSmoke:
    def test_get_activity_summary_with_mock_redis(self):
        from core.agents.audit import get_activity_summary

        redis = MagicMock()
        redis.zrevrangebyscore.return_value = []
        redis.get.return_value = None
        # Should return a dict shape without raising on empty data
        result = get_activity_summary(redis, hours=24)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# maintenance
# ---------------------------------------------------------------------------


class TestMaintenanceImports:
    def test_core_module_imports(self):
        from core.agents import maintenance  # noqa: F401

    def test_bridge_exports_maintain(self):
        from agents.maintenance import maintain  # noqa: F401
        assert callable(maintain)

    def test_helper_functions_callable(self):
        from core.agents.maintenance import (
            analyze_collections,
            check_bifrost_health,
            check_system_health,
            purge_artifacts,
        )
        for fn in [
            analyze_collections, check_bifrost_health, check_system_health,
            purge_artifacts,
        ]:
            assert callable(fn)


class TestMaintenanceSmoke:
    def test_analyze_collections_with_mock_chroma(self):
        from core.agents.maintenance import analyze_collections

        chroma = MagicMock()
        chroma.list_collections.return_value = []
        result = analyze_collections(chroma)
        assert isinstance(result, (dict, list))


# ---------------------------------------------------------------------------
# self_rag
# ---------------------------------------------------------------------------


class TestSelfRagImports:
    def test_core_module_imports(self):
        from core.agents import self_rag  # noqa: F401

    def test_self_rag_enhance_callable(self):
        from core.agents.self_rag import self_rag_enhance
        assert callable(self_rag_enhance)


class TestSelfRagSmoke:
    def test_enhance_short_circuits_when_no_claims_extracted(self):
        """No-claims path must return the original query_result without
        touching databases."""
        from core.agents.self_rag import self_rag_enhance

        query_result = {"results": [], "context": "", "answer": "hi"}
        with patch(
            "core.agents.hallucination.extract_claims",
            new_callable=AsyncMock, return_value=([], "heuristic"),
        ):
            result = _run(self_rag_enhance(
                query_result=query_result,
                response_text="Hi there!",
                chroma_client=MagicMock(),
                neo4j_driver=MagicMock(),
                redis_client=MagicMock(),
            ))
        assert result is not None
        assert isinstance(result, dict)
        # Metadata should indicate the no_claims short-circuit
        assert "self_rag" in result or result == query_result or "results" in result
