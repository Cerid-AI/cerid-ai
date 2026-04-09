"""Internal-only settings -- trading, boardroom, billing config.

This file exists only in cerid-ai-internal. The public repo's settings.py
does not reference this file. The bootstrap in main_internal.py calls
extend_settings() at startup.
"""
from __future__ import annotations

import os


def extend_settings() -> None:
    """Add internal-only config to the settings module namespace."""
    import config.settings as _s

    # Trading Agent Integration
    _s.CERID_TRADING_ENABLED = os.getenv("CERID_TRADING_ENABLED", "false").lower() in ("true", "1")
    _s.TRADING_AGENT_URL = os.getenv("TRADING_AGENT_URL", "http://localhost:8090")

    # Boardroom Agent Integration
    _s.CERID_BOARDROOM_ENABLED = os.getenv("CERID_BOARDROOM_ENABLED", "false").lower() in ("true", "1")
    _s.CERID_BOARDROOM_TIER = os.getenv("CERID_BOARDROOM_TIER", "foundation")

    # Extend CONSUMER_REGISTRY with internal-only consumers
    _s.CONSUMER_REGISTRY["trading-agent"] = {
        "rate_limits": {
            "/sdk/": (80, 60),
            "/agent/": (80, 60),
        },
        "allowed_domains": ["trading"],
        "strict_domains": True,
    }
    _s.CONSUMER_REGISTRY["boardroom-agent"] = {
        "rate_limits": {
            "/sdk/": (40, 60),
            "/agent/": (40, 60),
        },
        "allowed_domains": ["strategy", "competitive_intel", "marketing", "advertising", "operations", "audit"],
        "strict_domains": True,
    }

    # Rebuild CLIENT_RATE_LIMITS after extending CONSUMER_REGISTRY
    _s.CLIENT_RATE_LIMITS.update({
        k: v["rate_limits"] for k, v in _s.CONSUMER_REGISTRY.items()
        if k in ("trading-agent", "boardroom-agent")
    })
