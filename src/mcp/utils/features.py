"""
Feature flag system for Cerid AI open-core licensing.

Controls which features are available based on the deployment tier
(community vs pro). Provides a decorator for gating endpoints and
a utility function for runtime checks.

Usage:
    from utils.features import require_feature, is_feature_enabled

    # As a decorator on FastAPI endpoints:
    @router.post("/agent/ocr")
    @require_feature("ocr_parsing")
    async def ocr_endpoint(...):
        ...

    # As a runtime check:
    if is_feature_enabled("semantic_dedup"):
        results = await semantic_dedup_check(content)
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Callable

import config

logger = logging.getLogger("ai-companion.features")


def is_feature_enabled(feature_name: str) -> bool:
    """
    Check if a feature is enabled in the current deployment tier.

    Args:
        feature_name: Key from config.FEATURE_FLAGS

    Returns:
        True if the feature is enabled, False otherwise.
        Returns False for unknown features (fail-closed for safety).
    """
    if feature_name not in config.FEATURE_FLAGS:
        logger.warning(f"Unknown feature flag: '{feature_name}' — defaulting to disabled")
        return False
    return config.FEATURE_FLAGS[feature_name]


def require_feature(feature_name: str) -> Callable:
    """
    Decorator that gates a FastAPI endpoint behind a feature flag.

    Returns HTTP 403 with a clear message if the feature requires
    a higher tier than currently configured.

    Note: Only supports async endpoint functions (standard for FastAPI).

    Args:
        feature_name: Key from config.FEATURE_FLAGS
    """
    def decorator(func: Callable) -> Callable:
        if not asyncio.iscoroutinefunction(func):
            raise TypeError(
                f"@require_feature can only decorate async functions, "
                f"but '{func.__name__}' is synchronous."
            )

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not is_feature_enabled(feature_name):
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Feature '{feature_name}' requires Cerid AI Pro. "
                        f"Current tier: {config.FEATURE_TIER}. "
                        f"Set CERID_TIER=pro to enable."
                    ),
                )
            return await func(*args, **kwargs)
        return wrapper
    return decorator


def get_feature_status() -> dict:
    """
    Return the status of all feature flags.

    Useful for the settings API and health checks.
    """
    return {
        "tier": config.FEATURE_TIER,
        "features": {
            name: {
                "enabled": enabled,
                "tier_required": "pro" if not enabled else "community",
            }
            for name, enabled in config.FEATURE_FLAGS.items()
        },
    }
