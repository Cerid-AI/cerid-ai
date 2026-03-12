# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Feature flag system — tier-based gating and unified toggle management."""

from __future__ import annotations

import asyncio
import functools
import logging
import sys
from collections.abc import Callable
from typing import Any

import config
import config.features as _features_mod

logger = logging.getLogger("ai-companion.features")


# ── Tier-based feature flags (community vs pro) ─────────────────────────────

def is_feature_enabled(feature_name: str) -> bool:
    """Check if a tier-gated feature is enabled (fail-closed for unknown)."""
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


# ── Unified toggle management ───────────────────────────────────────────────

def is_toggle_enabled(name: str) -> bool:
    """Check if a feature toggle is enabled via the unified registry.

    ``name`` should be lowercase with ``enable_`` prefix, e.g. ``"enable_self_rag"``.
    Returns *False* for unknown toggles (fail-closed).
    """
    if name not in _features_mod.FEATURE_TOGGLES:
        logger.warning("Unknown feature toggle: '%s' — defaulting to disabled", name)
        return False
    return _features_mod.FEATURE_TOGGLES[name]


def set_toggle(name: str, value: bool) -> None:
    """Set a feature toggle at runtime, updating all binding surfaces.

    Handles the dual-mutation required by Python's import system:
    1. ``config.features.ENABLE_X`` (module attribute)
    2. ``config.ENABLE_X`` (re-exported via ``config/__init__.py``)
    3. ``FEATURE_TOGGLES[name]`` (unified dict)
    """
    # Canonical module-level attribute name: ENABLE_SELF_RAG etc.
    attr_name = name.upper()
    config_pkg = sys.modules.get("config")

    # Update all three binding surfaces
    _features_mod.FEATURE_TOGGLES[name] = value
    setattr(_features_mod, attr_name, value)
    if config_pkg is not None:
        setattr(config_pkg, attr_name, value)
