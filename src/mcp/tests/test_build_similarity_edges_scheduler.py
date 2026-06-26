# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for build_similarity_edges wiring in the nightly scheduler cadence.

Verifies that:
- _run_build_similarity_edges calls build_similarity_edges with the configured
  k and threshold when a driver is available.
- The job is a clean no-op when SEMANTIC_EDGE_ENABLED=False.
- The job is a clean no-op when driver is None.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# Satisfy scheduler module-level stub requirement (mirrors test_scheduler.py).
sys.modules.setdefault("deps", MagicMock())


@pytest.mark.asyncio
async def test_run_build_similarity_edges_calls_build_with_config(monkeypatch):
    """When a driver is available and the flag is enabled, build_similarity_edges
    is called with k=SEMANTIC_EDGE_K and threshold=SEMANTIC_EDGE_THRESHOLD."""
    import config

    fake_driver = MagicMock()
    mock_build = MagicMock(return_value={"edges_created": 3, "entities_with_embeddings": 10, "elapsed_seconds": 0.1})

    monkeypatch.setattr("config.SEMANTIC_EDGE_ENABLED", True, raising=False)

    with patch("app.deps.get_neo4j", return_value=fake_driver):
        with patch("app.db.neo4j.semantic_edges.build_similarity_edges", mock_build):
            from app.scheduler import _run_build_similarity_edges
            await _run_build_similarity_edges()

    mock_build.assert_called_once_with(
        fake_driver,
        k=config.SEMANTIC_EDGE_K,
        threshold=config.SEMANTIC_EDGE_THRESHOLD,
    )


@pytest.mark.asyncio
async def test_run_build_similarity_edges_skips_when_disabled(monkeypatch):
    """When SEMANTIC_EDGE_ENABLED=False the job exits cleanly without calling build."""
    monkeypatch.setattr("config.SEMANTIC_EDGE_ENABLED", False, raising=False)

    mock_build = MagicMock()

    with patch("app.db.neo4j.semantic_edges.build_similarity_edges", mock_build):
        from app.scheduler import _run_build_similarity_edges
        await _run_build_similarity_edges()

    mock_build.assert_not_called()


@pytest.mark.asyncio
async def test_run_build_similarity_edges_skips_when_driver_none(monkeypatch):
    """When no driver is available the job exits cleanly without calling build."""
    monkeypatch.setattr("config.SEMANTIC_EDGE_ENABLED", True, raising=False)

    mock_build = MagicMock()

    with patch("app.deps.get_neo4j", return_value=None):
        with patch("app.db.neo4j.semantic_edges.build_similarity_edges", mock_build):
            from app.scheduler import _run_build_similarity_edges
            await _run_build_similarity_edges()

    mock_build.assert_not_called()
