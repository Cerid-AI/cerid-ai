"""Trading-specific scheduler jobs for cerid-ai.

These jobs run on a cron schedule when CERID_TRADING_ENABLED=true.
They pull data from the trading agent and store insights in the KB.
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


async def run_trading_autoresearch(
    trading_agent_url: str,
    neo4j: Any,
) -> dict[str, Any]:
    """Pull performance summary from trading agent and store in KB.

    GET /aggregate/performance -> analyze -> store as (:TradingInsight) in Neo4j.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{trading_agent_url}/aggregate/performance")
            resp.raise_for_status()
            perf_data: dict[str, Any] = resp.json()

        # Store insight in Neo4j
        await neo4j.execute_write(
            "MERGE (i:TradingInsight {date: date()}) "
            "SET i.sharpe = $sharpe, i.win_rate = $win_rate, "
            "i.total_pnl = $total_pnl, i.updated_at = datetime()",
            sharpe=perf_data.get("sharpe", 0),
            win_rate=perf_data.get("win_rate", 0),
            total_pnl=perf_data.get("total_pnl", 0),
        )

        logger.info("trading_autoresearch_complete", sharpe=perf_data.get("sharpe"))
        return {"status": "ok", "data": perf_data}
    except Exception:
        logger.warning("trading_autoresearch_failed", exc_info=True)
        return {"status": "error"}


async def run_platt_scaling_mirror(
    trading_agent_url: str,
    neo4j: Any,
) -> dict[str, Any]:
    """Mirror Platt calibration params from trading agent to Neo4j.

    Trading agent is authoritative; cerid-ai only mirrors for KB queries.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{trading_agent_url}/sessions")
            resp.raise_for_status()
            sessions: list[dict[str, Any]] = resp.json()

        mirrored = 0
        for session in sessions:
            name = session.get("name", "")
            # Trading agent exposes Platt params via session performance
            perf_resp = None
            async with httpx.AsyncClient(timeout=10.0) as client:
                perf_resp = await client.get(
                    f"{trading_agent_url}/sessions/{name}/performance"
                )
            if perf_resp and perf_resp.status_code == 200:
                perf = perf_resp.json()
                platt = perf.get("platt_params", {})
                if platt.get("A") is not None:
                    await neo4j.execute_write(
                        "MERGE (p:PlattCalibration {session: $session}) "
                        "SET p.A = $a, p.B = $b, p.updated_at = datetime()",
                        session=name, a=platt["A"], b=platt.get("B", 0),
                    )
                    mirrored += 1

        logger.info("platt_mirror_complete", sessions_mirrored=mirrored)
        return {"status": "ok", "mirrored": mirrored}
    except Exception:
        logger.warning("platt_mirror_failed", exc_info=True)
        return {"status": "error", "mirrored": 0}


async def run_longshot_surface_rebuild(
    trading_agent_url: str,
    neo4j: Any,
) -> dict[str, Any]:
    """Rebuild calibration surface from trading agent data.

    Pulls calibration points and stores in Neo4j for longshot queries.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{trading_agent_url}/sdk/v1/trading/longshot-surface",
                json={"asset": "ETH", "date_range": "30d"},
            )
            # This might not exist yet on trading agent side
            if resp.status_code != 200:
                logger.debug("longshot_surface_endpoint_unavailable")
                return {"status": "skipped"}
            data: dict[str, Any] = resp.json()

        points = data.get("calibration_points", [])
        stored = 0
        for point in points:
            await neo4j.execute_write(
                "MERGE (c:CalibrationPoint {market_id: $market_id}) "
                "SET c.implied_prob = $implied_prob, "
                "c.actual_outcome = $actual_outcome, "
                "c.asset = $asset, c.updated_at = datetime()",
                market_id=point.get("market_id", "unknown"),
                implied_prob=point.get("implied_prob", 0),
                actual_outcome=point.get("actual_outcome", 0),
                asset="ETH",
            )
            stored += 1

        logger.info("longshot_surface_rebuild_complete", points_stored=stored)
        return {"status": "ok", "points_stored": stored}
    except Exception:
        logger.warning("longshot_surface_rebuild_failed", exc_info=True)
        return {"status": "error"}
