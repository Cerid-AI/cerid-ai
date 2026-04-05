"""Tests for trading-specific agent functions."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestTradingSignalEnrich:
    @pytest.mark.asyncio
    async def test_returns_enriched_signal(self) -> None:
        from agents.trading_agent import trading_signal_enrich
        mock_chroma = MagicMock()
        mock_chroma.query.return_value = {"documents": [["doc1", "doc2"]], "metadatas": [[{"source": "kb"}]]}
        mock_neo4j = AsyncMock()
        mock_neo4j.execute_read.return_value = []
        result = await trading_signal_enrich(
            query="Analyze ETH long", signal_data={"asset": "ETH", "direction": "long", "confidence": 0.85},
            domains=["finance"], chroma=mock_chroma, neo4j=mock_neo4j, top_k=5,
        )
        assert "answer" in result
        assert "confidence" in result
        assert "sources" in result

    @pytest.mark.asyncio
    async def test_degrades_gracefully_on_error(self) -> None:
        from agents.trading_agent import trading_signal_enrich
        mock_chroma = MagicMock()
        mock_chroma.query.side_effect = RuntimeError("DB down")
        mock_neo4j = AsyncMock()
        result = await trading_signal_enrich(query="test", signal_data={}, domains=[], chroma=mock_chroma, neo4j=mock_neo4j)
        assert result["confidence"] == 0.0


class TestHerdDetect:
    @pytest.mark.asyncio
    async def test_returns_violation_flags(self) -> None:
        from agents.trading_agent import herd_detect
        mock_neo4j = AsyncMock()
        mock_neo4j.execute_read.return_value = [{"symbol": "BTC", "prob_a": 0.7, "prob_b": 0.6}]
        result = await herd_detect(asset="ETH", sentiment_data={"fear_greed_index": 80}, neo4j=mock_neo4j)
        assert "violations" in result
        assert "historical_matches" in result

    @pytest.mark.asyncio
    async def test_no_violations_when_no_correlated_assets(self) -> None:
        from agents.trading_agent import herd_detect
        mock_neo4j = AsyncMock()
        mock_neo4j.execute_read.return_value = []
        result = await herd_detect(asset="ETH", sentiment_data={}, neo4j=mock_neo4j)
        assert result["violations"] == []


class TestKellySize:
    @pytest.mark.asyncio
    async def test_returns_recommended_fraction(self) -> None:
        from agents.trading_agent import kelly_size
        mock_neo4j = AsyncMock()
        mock_neo4j.execute_read.return_value = [{"cv_edge": 0.25, "session": "market-maker"}]
        result = await kelly_size(strategy="market-maker", confidence=0.75, win_loss_ratio=1.5, neo4j=mock_neo4j)
        assert "kelly_fraction" in result
        assert "cv_edge" in result
        assert 0 <= result["kelly_fraction"] <= 1


class TestCascadeConfirm:
    @pytest.mark.asyncio
    async def test_returns_confirmation_score(self) -> None:
        from agents.trading_agent import cascade_confirm
        mock_neo4j = AsyncMock()
        mock_neo4j.execute_read.return_value = [
            {"outcome": "profitable", "venue_count": 3},
            {"outcome": "loss", "venue_count": 2},
        ]
        result = await cascade_confirm(asset="ETH", liquidation_events=[{"venue": "HL", "size_usd": 50000}, {"venue": "dYdX", "size_usd": 30000}], neo4j=mock_neo4j)
        assert "confirmation_score" in result
        assert 0 <= result["confirmation_score"] <= 1


class TestLongshotSurfaceQuery:
    @pytest.mark.asyncio
    async def test_returns_probability_adjustments(self) -> None:
        from agents.trading_agent import longshot_surface_query
        mock_neo4j = AsyncMock()
        mock_neo4j.execute_read.return_value = [{"implied_prob": 0.10, "actual_outcome": 0.15, "market_id": "m1"}]
        result = await longshot_surface_query(asset="ETH", date_range="7d", neo4j=mock_neo4j)
        assert "calibration_points" in result
        assert isinstance(result["calibration_points"], list)

    @pytest.mark.asyncio
    async def test_wildcard_asset_omits_filter(self) -> None:
        """asset='*' must query all CalibrationPoints, not match the literal string '*'."""
        from agents.trading_agent import longshot_surface_query

        call_args: list[tuple] = []

        def fake_query(driver: object, cypher: str, **params: object) -> list[dict]:
            call_args.append((cypher, params))
            return [{"market_id": "m1", "implied_prob": 0.10, "actual_outcome": 0.12, "timestamp": None}]

        import core.agents.trading_agent as _ta
        original = _ta._neo4j_query
        _ta._neo4j_query = fake_query  # type: ignore[assignment]
        try:
            result = await longshot_surface_query(asset="*", date_range="30d", neo4j=object())
        finally:
            _ta._neo4j_query = original

        assert result["count"] == 1
        # The Cypher must NOT contain the asset equality filter
        cypher_used = call_args[0][0]
        assert "{asset: $asset}" not in cypher_used
        assert "asset" not in call_args[0][1]  # no $asset param passed

    @pytest.mark.asyncio
    async def test_all_alias_also_omits_filter(self) -> None:
        """asset='all' is an alias for wildcard and should behave identically to '*'."""
        from agents.trading_agent import longshot_surface_query

        call_args: list[tuple] = []

        def fake_query(driver: object, cypher: str, **params: object) -> list[dict]:
            call_args.append((cypher, params))
            return []

        import core.agents.trading_agent as _ta
        original = _ta._neo4j_query
        _ta._neo4j_query = fake_query  # type: ignore[assignment]
        try:
            await longshot_surface_query(asset="all", date_range="30d", neo4j=object())
        finally:
            _ta._neo4j_query = original

        cypher_used = call_args[0][0]
        assert "{asset: $asset}" not in cypher_used
