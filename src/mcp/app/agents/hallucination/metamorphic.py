# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Metamorphic verification — Pro tier plugin stub.

The full implementation lives in plugins/metamorphic/plugin.py (BSL-1.1).
This stub provides the import interface for the hallucination pipeline.
When the plugin is loaded, it injects the real implementation via
``set_metamorphic_handler()``.  When not loaded, ``metamorphic_score()``
returns a skip sentinel.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "metamorphic_score",
    "set_metamorphic_handler",
]

_metamorphic_fn = None


def set_metamorphic_handler(fn):
    """Called by the plugin's register() to inject the implementation."""
    global _metamorphic_fn
    _metamorphic_fn = fn


async def metamorphic_score(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Delegate to plugin implementation if loaded, otherwise skip.

    Two skip paths:
      - Feature flag disabled (community/enterprise w/o entitlement): skip with
        ``reason="feature_gated"`` so callers can render a clean upgrade CTA
        without sounding broken.
      - Flag enabled but plugin not yet loaded: skip with ``reason=
        "plugin_not_loaded"`` so operators know the install is incomplete.
    """
    from config.features import is_feature_enabled

    if not is_feature_enabled("metamorphic_verification"):
        return {"skipped": True, "reason": "feature_gated",
                "feature": "metamorphic_verification"}
    if _metamorphic_fn is None:
        return {"skipped": True, "reason": "plugin_not_loaded",
                "feature": "metamorphic_verification"}
    return await _metamorphic_fn(*args, **kwargs)
