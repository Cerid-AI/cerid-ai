# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BSL-1.1

"""Custom Smart RAG plugin — per-source weight tuning for retrieval orchestration.

Provides the ``_apply_source_config()`` function that applies user-configurable
source weights and toggles to retrieval results.  Pro tier only
(``custom_smart_rag`` feature flag).
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["apply_source_config", "register"]

logger = logging.getLogger(__name__)


def apply_source_config(
    kb_sources: list[dict[str, Any]],
    memory_sources: list[dict[str, Any]],
    external_sources: list[dict[str, Any]],
    source_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply custom_smart source weights and toggles.

    source_config shape:
        {
            "kb_enabled": True,
            "memory_enabled": True,
            "external_enabled": True,
            "kb_weight": 1.0,
            "memory_weight": 1.0,
            "external_weight": 1.0,
            "memory_types": ["empirical", "decision", ...],  # optional filter
        }
    """
    if not source_config.get("kb_enabled", True):
        kb_sources = []
    if not source_config.get("memory_enabled", True):
        memory_sources = []
    if not source_config.get("external_enabled", True):
        external_sources = []

    # Apply weight scaling to relevance scores
    kb_weight = source_config.get("kb_weight", 1.0)
    memory_weight = source_config.get("memory_weight", 1.0)
    external_weight = source_config.get("external_weight", 1.0)

    for s in kb_sources:
        if "relevance" in s:
            s["relevance"] = s["relevance"] * kb_weight
    for s in memory_sources:
        if "relevance" in s:
            s["relevance"] = s["relevance"] * memory_weight
    for s in external_sources:
        if "relevance" in s:
            s["relevance"] = s["relevance"] * external_weight

    # Optional per-memory-type filter
    allowed_types = source_config.get("memory_types")
    if allowed_types:
        memory_sources = [
            m for m in memory_sources if m.get("memory_type") in allowed_types
        ]

    return kb_sources, memory_sources, external_sources


def register() -> None:
    """Register the custom RAG weighting function with the retrieval orchestrator.

    Sets the module-level handler in the core stub so that
    ``retrieval_orchestrator.py`` can delegate to this implementation.
    """
    from app.agents.retrieval_orchestrator import set_custom_rag_handler
    set_custom_rag_handler(apply_source_config)
    logger.info("Custom Smart RAG plugin registered")
