# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Feature flag system — tier-based gating (community vs pro)."""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Callable

import config

logger = logging.getLogger("ai-companion.features")


def is_feature_enabled(feature_name: str) -> bool:
    """Check if a feature is enabled (fail-closed for unknown features)."""
    if feature_name not in config.FEATURE_FLAGS:
        logger.warning(f"Unknown feature flag: '{feature_name}' — defaulting to disabled")
        return False
    return config.FEATURE_FLAGS[feature_name]


def require_feature(feature_name: str) -> Callable:
    """Decorator that gates a FastAPI endpoint behind a feature flag (async only)."""
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
    """Return the status of all feature flags."""
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