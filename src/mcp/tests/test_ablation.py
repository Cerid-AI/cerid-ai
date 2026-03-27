# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for ablation study scaffold."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.eval.ablation import (
    _RAG_TOGGLES,
    PRESET_CONFIGS,
    AblationConfig,
    AblationResult,
    results_to_table,
    run_ablation,
)

# ---------------------------------------------------------------------------
# Tests: AblationConfig
# ---------------------------------------------------------------------------

class TestAblationConfig:
    def test_fills_missing_toggles_with_false(self):
        config = AblationConfig(name="partial", toggles={"enable_self_rag": True})
        assert config.toggles["enable_self_rag"] is True
        # All others should be False
        for toggle in _RAG_TOGGLES:
            if toggle != "enable_self_rag":
                assert config.toggles[toggle] is False

    def test_empty_toggles_all_false(self):
        config = AblationConfig(name="empty")
        for toggle in _RAG_TOGGLES:
            assert config.toggles[toggle] is False

    def test_preserves_explicit_values(self):
        toggles = {t: True for t in _RAG_TOGGLES}
        config = AblationConfig(name="all_on", toggles=toggles)
        for toggle in _RAG_TOGGLES:
            assert config.toggles[toggle] is True


# ---------------------------------------------------------------------------
# Tests: PRESET_CONFIGS
# ---------------------------------------------------------------------------

class TestPresetConfigs:
    def test_has_baseline_and_full(self):
        names = [c.name for c in PRESET_CONFIGS]
        assert "baseline" in names
        assert "full" in names

    def test_baseline_all_false(self):
        baseline = next(c for c in PRESET_CONFIGS if c.name == "baseline")
        for toggle in _RAG_TOGGLES:
            assert baseline.toggles[toggle] is False

    def test_full_all_true(self):
        full = next(c for c in PRESET_CONFIGS if c.name == "full")
        for toggle in _RAG_TOGGLES:
            assert full.toggles[toggle] is True

    def test_individual_configs_exist(self):
        names = [c.name for c in PRESET_CONFIGS]
        for toggle in _RAG_TOGGLES:
            expected = f"only_{toggle.removeprefix('enable_')}"
            assert expected in names, f"Missing preset: {expected}"

    def test_individual_config_enables_one(self):
        for toggle in _RAG_TOGGLES:
            name = f"only_{toggle.removeprefix('enable_')}"
            config = next(c for c in PRESET_CONFIGS if c.name == name)
            assert config.toggles[toggle] is True
            for other in _RAG_TOGGLES:
                if other != toggle:
                    assert config.toggles[other] is False


# ---------------------------------------------------------------------------
# Tests: results_to_table
# ---------------------------------------------------------------------------

class TestResultsToTable:
    def test_converts_results_to_dicts(self):
        results = [
            AblationResult(
                config_name="baseline",
                query="test query",
                latency_s=0.5,
                result_count=3,
                answer_snippet="An answer",
                timings={"vector_search": 0.1, "reranking": 0.2},
                ragas_scores={"faithfulness": 0.9},
            ),
        ]
        rows = results_to_table(results)
        assert len(rows) == 1
        row = rows[0]
        assert row["config"] == "baseline"
        assert row["latency_s"] == 0.5
        assert row["t_vector_search"] == 0.1
        assert row["ragas_faithfulness"] == 0.9


# ---------------------------------------------------------------------------
# Tests: run_ablation
# ---------------------------------------------------------------------------

class TestRunAblation:
    @pytest.mark.asyncio
    async def test_runs_queries_across_configs(self):
        mock_result = {
            "answer": "Test answer",
            "artifacts": [{"content": "ctx"}],
            "_timings": {"total": 0.1},
        }
        with (
            patch("utils.features.set_toggle") as mock_set,
            patch("core.agents.query_agent.agent_query", new_callable=AsyncMock, return_value=mock_result),
        ):
            configs = [
                AblationConfig(name="off", toggles={t: False for t in _RAG_TOGGLES}),
                AblationConfig(name="on", toggles={t: True for t in _RAG_TOGGLES}),
            ]
            results = await run_ablation(
                queries=["test query"],
                configs=configs,
                chroma_client=MagicMock(),
                neo4j_driver=MagicMock(),
                redis_client=MagicMock(),
            )

        assert len(results) == 2
        assert results[0].config_name == "off"
        assert results[1].config_name == "on"
        # set_toggle should have been called to apply and restore
        assert mock_set.call_count > 0

    @pytest.mark.asyncio
    async def test_handles_query_failure_gracefully(self):
        with (
            patch("utils.features.set_toggle"),
            patch("agents.query_agent.agent_query", new_callable=AsyncMock, side_effect=RuntimeError("boom")),
        ):
            configs = [AblationConfig(name="test")]
            results = await run_ablation(
                queries=["fail query"],
                configs=configs,
                chroma_client=MagicMock(),
                neo4j_driver=MagicMock(),
                redis_client=MagicMock(),
            )

        assert len(results) == 1
        assert "ERROR" in results[0].answer_snippet
