# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for Custom Smart RAG weight application — Phase I Day 2.

Verifies the weights actually shift the per-source confidence and
per-domain KB relevance scores in the retrieval blending step.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.data_sources.base import DataSource, DataSourceRegistry, DataSourceResult


class _StubSource(DataSource):
    """Minimal DataSource that returns a single pre-baked result."""

    def __init__(self, name: str, base_confidence: float = 0.5):
        self.name = name
        self.description = f"Stub {name}"
        self.enabled = True
        self._confidence = base_confidence

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        return [DataSourceResult(
            title=f"{self.name} hit",
            content="body",
            source_name=self.name,
            confidence=self._confidence,
        )]

    def is_configured(self) -> bool:
        return True


@pytest.fixture
def weighted_mock_redis():
    """Returns a Redis mock pre-loaded with one non-default weight."""
    r = MagicMock()
    storage: dict[str, dict[bytes, bytes]] = {
        "cerid:rag:weights:global": {
            b"gmail": b"1.5",       # boost Gmail
            b"wikipedia": b"0.5",   # demote wiki
        },
    }
    r.hgetall.side_effect = lambda key: storage.get(key, {})
    return r


class TestDataSourceWeightApplication:
    @pytest.mark.asyncio
    async def test_weights_scale_per_source_confidence(self, weighted_mock_redis):
        """Boost gmail, demote wikipedia — confirmed by post-merge confidences."""
        reg = DataSourceRegistry()
        reg.register(_StubSource("gmail", base_confidence=0.5))
        reg.register(_StubSource("wikipedia", base_confidence=0.5))

        with (
            patch("app.deps.get_redis", return_value=weighted_mock_redis),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            merged = await reg.query_all("test", timeout=2.0)

        by_source = {r["source_name"]: r for r in merged}
        # Both sources scored at 0.5 originally. After weights:
        #   gmail:     0.5 × 1.5 = 0.75
        #   wikipedia: 0.5 × 0.5 = 0.25
        assert by_source["gmail"]["confidence"] == pytest.approx(0.75)
        assert by_source["wikipedia"]["confidence"] == pytest.approx(0.25)

    @pytest.mark.asyncio
    async def test_weights_clamped_to_unit_interval(self):
        """Even with a 2.0× multiplier on a 0.8 base, confidence is
        capped at 1.0 (post-blending it lives in [0, 1])."""
        r = MagicMock()
        r.hgetall.side_effect = lambda _: {b"gmail": b"2.0"}
        reg = DataSourceRegistry()
        reg.register(_StubSource("gmail", base_confidence=0.8))

        with (
            patch("app.deps.get_redis", return_value=r),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            merged = await reg.query_all("test", timeout=2.0)

        assert merged[0]["confidence"] == 1.0  # clamped, not 1.6

    @pytest.mark.asyncio
    async def test_no_weight_change_when_feature_off(self, weighted_mock_redis):
        """Feature flag off → weights bypassed even when populated."""
        reg = DataSourceRegistry()
        reg.register(_StubSource("gmail", base_confidence=0.5))

        with (
            patch("app.deps.get_redis", return_value=weighted_mock_redis),
            patch("config.features.is_feature_enabled", return_value=False),
        ):
            merged = await reg.query_all("test", timeout=2.0)

        # Unchanged from base
        assert merged[0]["confidence"] == 0.5

    @pytest.mark.asyncio
    async def test_no_weight_change_when_all_defaults(self):
        """Even with feature on, if every weight is exactly 1.0 we
        skip the multiplication (is_active short-circuits)."""
        r = MagicMock()
        r.hgetall.side_effect = lambda _: {b"gmail": b"1.0"}
        reg = DataSourceRegistry()
        reg.register(_StubSource("gmail", base_confidence=0.5))

        with (
            patch("app.deps.get_redis", return_value=r),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            merged = await reg.query_all("test", timeout=2.0)

        assert merged[0]["confidence"] == 0.5  # unchanged


class TestKBDomainWeightApplication:
    """Per-domain KB weights are applied in query_agent.multi_domain_query
    via the kb:<domain> prefix. Tests the multiplier logic directly."""

    def test_multiplier_round_trip(self):
        from utils.rag_weights import apply_to_result
        result = apply_to_result(
            0.6,
            domain="mail",
            weights={"kb:mail": 1.5},
        )
        assert result == pytest.approx(0.9)

    def test_no_change_for_unweighted_domain(self):
        from utils.rag_weights import apply_to_result
        result = apply_to_result(
            0.6,
            domain="personal",
            weights={"kb:mail": 1.5},  # different domain
        )
        assert result == 0.6

    def test_kb_and_source_compose(self):
        """A DataSource result that's ALSO domain-tagged receives both
        multipliers."""
        from utils.rag_weights import apply_to_result
        result = apply_to_result(
            0.5,
            source_name="gmail",
            domain="mail",
            weights={"gmail": 1.5, "kb:mail": 0.8},
        )
        # 0.5 × 1.5 × 0.8 = 0.6
        assert result == pytest.approx(0.6)
