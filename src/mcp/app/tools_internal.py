# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Internal-only trading MCP tools.

This file exists only in cerid-ai-internal. The bootstrap block at
the bottom of tools.py calls get_trading_tools() to extend the MCP_TOOLS
list and uses dispatch_trading_tool() in the dispatch chain.
"""
from __future__ import annotations


def get_trading_tools() -> list[dict]:
    """Return the 5 trading MCP tool definitions."""
    return [
        {
            "name": "pkb_trading_signal",
            "description": "Enrich a trading signal with KB context from ChromaDB and historical trades from Neo4j. Example signal_data: {\"asset\": \"ETH\", \"direction\": \"long\", \"confidence\": 0.82}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query describing the trading signal"},
                    "signal_data": {
                        "type": "object",
                        "description": "Signal metadata (asset, direction, confidence, etc.)",
                        "default": {},
                    },
                    "domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Knowledge domains to search",
                        "default": ["finance", "trading"],
                    },
                    "top_k": {"type": "integer", "description": "Number of KB results", "default": 5},
                },
                "required": ["query"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string"},
                    "confidence": {"type": "number"},
                    "sources": {"type": "array"},
                    "historical_trades": {"type": "array"},
                },
            },
        },
        {
            "name": "pkb_herd_detect",
            "description": "Detect herd behavior by checking correlation graph violations and sentiment extremes. Example sentiment_data: {\"finbert_score\": 0.7, \"fear_greed\": 45}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "asset": {"type": "string", "description": "Asset symbol (e.g. ETH, BTC)"},
                    "sentiment_data": {
                        "type": "object",
                        "description": "Sentiment indicators (fear_greed_index, etc.)",
                        "default": {},
                    },
                },
                "required": ["asset"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "violations": {"type": "array"},
                    "historical_matches": {"type": "array"},
                    "sentiment_extreme": {"type": "boolean"},
                },
            },
        },
        {
            "name": "pkb_kelly_size",
            "description": "Compute Kelly-criterion position size using historical CV_edge from calibration data. Example values: strategy: \"herd-fade\", confidence: 0.75, win_loss_ratio: 1.5",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "strategy": {"type": "string", "description": "Strategy/session name for calibration lookup"},
                    "confidence": {"type": "number", "description": "Win probability (0-1)"},
                    "win_loss_ratio": {"type": "number", "description": "Average win / average loss ratio"},
                },
                "required": ["strategy", "confidence", "win_loss_ratio"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "kelly_fraction": {"type": "number"},
                    "cv_edge": {"type": "number"},
                    "kelly_raw": {"type": "number"},
                    "strategy": {"type": "string"},
                },
            },
        },
        {
            "name": "pkb_cascade_confirm",
            "description": "Confirm a liquidation cascade pattern against historical cascade events. liquidation_events shape: [{\"venue\": \"binance\", \"size_usd\": 500000, \"timestamp\": \"...\"}]",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "asset": {"type": "string", "description": "Asset symbol (e.g. ETH, BTC)"},
                    "liquidation_events": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of liquidation events with venue and size_usd",
                        "default": [],
                    },
                },
                "required": ["asset"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "confirmation_score": {"type": "number"},
                    "historical_cascades": {"type": "integer"},
                    "match_quality": {"type": "string"},
                },
            },
        },
        {
            "name": "pkb_longshot_surface",
            "description": "Query stored calibration surface (implied vs actual probabilities) from Neo4j. date_range format: \"2026-03-01/2026-03-15\" or shorthand \"7d\", \"14d\", \"30d\", \"90d\"",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "asset": {"type": "string", "description": "Asset symbol (e.g. ETH, BTC)"},
                    "date_range": {
                        "type": "string",
                        "description": "Lookback period (7d, 14d, 30d, 90d)",
                        "default": "30d",
                    },
                },
                "required": ["asset"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "calibration_points": {"type": "array"},
                    "count": {"type": "integer"},
                    "asset": {"type": "string"},
                    "date_range": {"type": "string"},
                },
            },
        },
    ]


async def dispatch_trading_tool(name: str, arguments: dict) -> dict | None:
    """Dispatch a trading MCP tool call. Returns None if name doesn't match."""
    from app.deps import get_chroma, get_neo4j

    if name == "pkb_trading_signal":
        from agents.trading_agent import trading_signal_enrich
        return await trading_signal_enrich(
            query=arguments.get("query", ""),
            signal_data=arguments.get("signal_data", {}),
            domains=arguments.get("domains", ["finance", "trading"]),
            chroma=get_chroma(),
            neo4j=get_neo4j(),
            top_k=arguments.get("top_k", 5),
        )
    elif name == "pkb_herd_detect":
        from agents.trading_agent import herd_detect
        return await herd_detect(
            asset=arguments.get("asset", ""),
            sentiment_data=arguments.get("sentiment_data", {}),
            neo4j=get_neo4j(),
        )
    elif name == "pkb_kelly_size":
        from agents.trading_agent import kelly_size
        return await kelly_size(
            strategy=arguments.get("strategy", ""),
            confidence=arguments.get("confidence", 0.5),
            win_loss_ratio=arguments.get("win_loss_ratio", 1.0),
            neo4j=get_neo4j(),
        )
    elif name == "pkb_cascade_confirm":
        from agents.trading_agent import cascade_confirm
        return await cascade_confirm(
            asset=arguments.get("asset", ""),
            liquidation_events=arguments.get("liquidation_events", []),
            neo4j=get_neo4j(),
        )
    elif name == "pkb_longshot_surface":
        from agents.trading_agent import longshot_surface_query
        return await longshot_surface_query(
            asset=arguments.get("asset", ""),
            date_range=arguments.get("date_range", "30d"),
            neo4j=get_neo4j(),
        )
    return None
