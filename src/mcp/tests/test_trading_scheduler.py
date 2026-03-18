"""Tests for trading scheduler jobs."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestTradingAutoresearch:
    @pytest.mark.asyncio
    async def test_stores_performance_in_neo4j(self) -> None:
        import httpx
        import respx

        from agents.trading_scheduler_jobs import run_trading_autoresearch

        with respx.mock:
            respx.get("http://localhost:8090/aggregate/performance").mock(
                return_value=httpx.Response(200, json={"sharpe": 1.5, "win_rate": 0.65, "total_pnl": 50.0})
            )
            mock_neo4j = AsyncMock()
            result = await run_trading_autoresearch("http://localhost:8090", mock_neo4j)
            assert result["status"] == "ok"
            mock_neo4j.execute_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_api_failure(self) -> None:
        import httpx
        import respx

        from agents.trading_scheduler_jobs import run_trading_autoresearch

        with respx.mock:
            respx.get("http://localhost:8090/aggregate/performance").mock(
                return_value=httpx.Response(500)
            )
            mock_neo4j = AsyncMock()
            result = await run_trading_autoresearch("http://localhost:8090", mock_neo4j)
            assert result["status"] == "error"


class TestPlattScalingMirror:
    @pytest.mark.asyncio
    async def test_mirrors_platt_params(self) -> None:
        import httpx
        import respx

        from agents.trading_scheduler_jobs import run_platt_scaling_mirror

        with respx.mock:
            respx.get("http://localhost:8090/sessions").mock(
                return_value=httpx.Response(200, json=[{"name": "market-maker"}])
            )
            respx.get("http://localhost:8090/sessions/market-maker/performance").mock(
                return_value=httpx.Response(200, json={"platt_params": {"A": -2.5, "B": 0.3}})
            )
            mock_neo4j = AsyncMock()
            result = await run_platt_scaling_mirror("http://localhost:8090", mock_neo4j)
            assert result["status"] == "ok"
            assert result["mirrored"] == 1


class TestLongshotSurfaceRebuild:
    @pytest.mark.asyncio
    async def test_stores_calibration_points(self) -> None:
        import httpx
        import respx

        from agents.trading_scheduler_jobs import run_longshot_surface_rebuild

        with respx.mock:
            respx.post("http://localhost:8090/sdk/v1/trading/longshot-surface").mock(
                return_value=httpx.Response(200, json={
                    "calibration_points": [
                        {"market_id": "m1", "implied_prob": 0.1, "actual_outcome": 0.15},
                    ],
                    "count": 1,
                })
            )
            mock_neo4j = AsyncMock()
            result = await run_longshot_surface_rebuild("http://localhost:8090", mock_neo4j)
            assert result["status"] == "ok"
            assert result["points_stored"] == 1
