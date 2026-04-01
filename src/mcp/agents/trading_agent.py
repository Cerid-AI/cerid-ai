"""Trading-specific agent functions for cerid-ai.

These functions provide KB enrichment for the cerid-trading-agent.
Each function queries Neo4j and/or ChromaDB for trading-relevant
knowledge, then returns structured results.
"""
from __future__ import annotations

from typing import Any

import structlog

from config.taxonomy import collection_name
from errors import ProviderError

logger = structlog.get_logger(__name__)


def _neo4j_query(driver: Any, cypher: str, **params: Any) -> list[dict[str, Any]]:
    """Run a Cypher query using the Neo4j driver's session API."""
    try:
        with driver.session() as session:
            result = session.run(cypher, params)
            return [dict(record) for record in result]
    except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        logger.debug("neo4j_query_failed", exc_info=True)
        return []


async def trading_signal_enrich(
    query: str, signal_data: dict[str, Any], domains: list[str],
    chroma: Any, neo4j: Any, top_k: int = 5,
) -> dict[str, Any]:
    """Enrich a trading signal with KB context."""
    try:
        # Query ChromaDB via collection (not client.query)
        documents: list[str] = []
        sources: list[str] = []
        for domain in domains:
            col_name = collection_name(domain)
            try:
                col = chroma.get_collection(name=col_name)
                results = col.query(
                    query_texts=[query], n_results=top_k,
                )
                docs = results.get("documents", [[]])[0]
                metas = results.get("metadatas", [[]])[0]
                documents.extend(docs)
                sources.extend(m.get("source", "unknown") for m in metas)
            except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
                # Collection may not exist yet — skip
                continue

        asset = signal_data.get("asset", "")
        direction = signal_data.get("direction", "")
        historical = _neo4j_query(
            neo4j,
            "MATCH (t:Trade {asset: $asset, direction: $direction}) "
            "WHERE t.timestamp > datetime() - duration('P30D') "
            "RETURN t.outcome AS outcome, t.pnl AS pnl "
            "ORDER BY t.timestamp DESC LIMIT 10",
            asset=asset, direction=direction,
        )
        answer = "\n".join(documents[:3]) if documents else ""
        confidence = min(1.0, len(documents) / top_k) if documents else 0.0
        return {
            "answer": answer,
            "confidence": confidence,
            "sources": sources,
            "historical_trades": historical,
            "domains_searched": domains,
        }
    except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        logger.warning("trading_signal_enrich_failed", exc_info=True)
        return {
            "answer": "", "confidence": 0.0,
            "sources": [], "historical_trades": [],
        }


async def herd_detect(
    asset: str, sentiment_data: dict[str, Any], neo4j: Any,
) -> dict[str, Any]:
    """Detect herd behavior via correlation graph violations."""
    try:
        correlated = _neo4j_query(
            neo4j,
            "MATCH (a:Asset {symbol: $asset})-[r:CORRELATED_WITH]->(b:Asset) "
            "WHERE r.weight >= 0.8 "
            "RETURN b.symbol AS symbol, a.probability AS prob_a, "
            "b.probability AS prob_b",
            asset=asset,
        )
        violations = []
        for record in correlated:
            prob_sum = (record.get("prob_a") or 0) + (record.get("prob_b") or 0)
            if prob_sum > 1.05:
                violations.append({
                    "correlated_asset": record["symbol"],
                    "prob_sum": prob_sum,
                    "severity": "high" if prob_sum > 1.2 else "medium",
                })
        historical = _neo4j_query(
            neo4j,
            "MATCH (h:HerdEvent {asset: $asset}) "
            "WHERE h.timestamp > datetime() - duration('P30D') "
            "RETURN h.outcome AS outcome, h.magnitude AS magnitude "
            "ORDER BY h.timestamp DESC LIMIT 5",
            asset=asset,
        )
        fgi = sentiment_data.get("fear_greed_index", 50)
        return {
            "violations": violations,
            "historical_matches": historical,
            "sentiment_extreme": abs(fgi - 50) > 30,
        }
    except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        logger.warning("herd_detect_failed", exc_info=True)
        return {
            "violations": [], "historical_matches": [],
            "sentiment_extreme": False,
        }


async def kelly_size(
    strategy: str, confidence: float, win_loss_ratio: float, neo4j: Any,
) -> dict[str, Any]:
    """Query historical CV_edge for Kelly sizing."""
    try:
        records = _neo4j_query(
            neo4j,
            "MATCH (c:Calibration {session: $session}) "
            "RETURN c.cv_edge AS cv_edge, c.updated_at AS updated_at "
            "ORDER BY c.updated_at DESC LIMIT 1",
            session=strategy,
        )
        cv_edge = records[0]["cv_edge"] if records else 0.2
        q = 1 - confidence
        kelly_raw = (
            (confidence * win_loss_ratio - q) / win_loss_ratio
            if win_loss_ratio > 0 else 0.0
        )
        kelly_fraction = max(0.0, kelly_raw * (1 - cv_edge) * 0.5)
        return {
            "kelly_fraction": min(kelly_fraction, 0.25),
            "cv_edge": cv_edge,
            "kelly_raw": kelly_raw,
            "strategy": strategy,
        }
    except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        logger.warning("kelly_size_failed", exc_info=True)
        return {
            "kelly_fraction": 0.0, "cv_edge": 0.2,
            "kelly_raw": 0.0, "strategy": strategy,
        }


async def cascade_confirm(
    asset: str, liquidation_events: list[dict[str, Any]], neo4j: Any,
) -> dict[str, Any]:
    """Confirm cascade pattern against historical data."""
    venue_count = len({e.get("venue") for e in liquidation_events})
    try:
        historical = _neo4j_query(
            neo4j,
            "MATCH (c:CascadeEvent {asset: $asset}) "
            "WHERE c.timestamp > datetime() - duration('P90D') "
            "RETURN c.outcome AS outcome, c.venue_count AS venue_count, "
            "c.total_size_usd AS total_size_usd "
            "ORDER BY c.timestamp DESC LIMIT 20",
            asset=asset,
        )
        if not historical:
            return {
                "confirmation_score": 0.5,
                "historical_cascades": 0,
                "match_quality": "no_history",
            }
        profitable = [
            h for h in historical if h.get("outcome") == "profitable"
        ]
        if not profitable:
            return {
                "confirmation_score": 0.3,
                "historical_cascades": len(historical),
                "match_quality": "no_profitable_history",
            }
        win_rate = len(profitable) / len(historical)
        venue_match = (
            sum(
                1 for h in profitable
                if h.get("venue_count", 0) >= venue_count
            ) / len(profitable)
        )
        confirmation = 0.5 * win_rate + 0.5 * venue_match
        return {
            "confirmation_score": min(1.0, confirmation),
            "historical_cascades": len(historical),
            "profitable_rate": win_rate,
            "venue_match_rate": venue_match,
            "match_quality": "good" if len(historical) > 5 else "limited",
        }
    except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        logger.warning("cascade_confirm_failed", exc_info=True)
        return {
            "confirmation_score": 0.5,
            "historical_cascades": 0,
            "match_quality": "error",
        }


async def longshot_surface_query(
    asset: str, date_range: str, neo4j: Any,
) -> dict[str, Any]:
    """Query stored calibration surface from Neo4j.

    Pass asset="*" or asset="all" to return calibration points for all assets.
    """
    duration_map = {
        "7d": "P7D", "14d": "P14D", "30d": "P30D", "90d": "P90D",
    }
    duration = duration_map.get(date_range, "P30D")
    try:
        if asset in ("*", "all"):
            records = _neo4j_query(
                neo4j,
                "MATCH (c:CalibrationPoint) "
                "WHERE c.timestamp > datetime() - duration($duration) "
                "RETURN c.market_id AS market_id, "
                "c.implied_prob AS implied_prob, "
                "c.actual_outcome AS actual_outcome, "
                "c.timestamp AS timestamp "
                "ORDER BY c.timestamp DESC LIMIT 500",
                duration=duration,
            )
        else:
            records = _neo4j_query(
                neo4j,
                "MATCH (c:CalibrationPoint {asset: $asset}) "
                "WHERE c.timestamp > datetime() - duration($duration) "
                "RETURN c.market_id AS market_id, "
                "c.implied_prob AS implied_prob, "
                "c.actual_outcome AS actual_outcome, "
                "c.timestamp AS timestamp "
                "ORDER BY c.timestamp DESC LIMIT 500",
                asset=asset, duration=duration,
            )
        return {
            "calibration_points": records,
            "count": len(records),
            "asset": asset,
            "date_range": date_range,
        }
    except (ProviderError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        logger.warning("longshot_surface_query_failed", exc_info=True)
        return {
            "calibration_points": [], "count": 0,
            "asset": asset, "date_range": date_range,
        }
