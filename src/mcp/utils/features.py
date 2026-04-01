# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Feature flag system — re-export bridge.

Canonical implementations now live in ``config.features``.
This module re-exports them for backward compatibility and adds
toggle management utilities that depend on runtime mutation.
"""

from __future__ import annotations

import logging
import sys

import config.features as _features_mod

# ── Re-exports from config.features (canonical location) ─────────────────────
from config.features import (  # noqa: F401
    check_feature,
    check_tier,
    get_feature_status,
    is_feature_enabled,
    is_tier_met,
    require_feature,
)

logger = logging.getLogger("ai-companion.features")


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
