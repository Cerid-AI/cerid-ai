# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Settings endpoints — expose server configuration to the GUI."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import config

router = APIRouter()
logger = logging.getLogger("ai-companion.settings")

# Version constant (single source of truth for the API)
_VERSION = "0.8.0"


# ── Pydantic models ──────────────────────────────────────────────────────────

class SettingsUpdateRequest(BaseModel):
    """Subset of settings that can be changed at runtime without restart."""

    categorize_mode: Optional[str] = Field(
        None, description="Categorization tier: manual, smart, or pro"
    )
    enable_feedback_loop: Optional[bool] = Field(
        None, description="Toggle conversation feedback loop"
    )
    enable_hallucination_check: Optional[bool] = Field(
        None, description="Toggle hallucination detection on feedback"
    )
    enable_memory_extraction: Optional[bool] = Field(
        None, description="Toggle memory extraction from conversations"
    )
    hallucination_threshold: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Confidence threshold for hallucination flagging"
    )
    cost_sensitivity: Optional[str] = Field(
        None, description="Cost sensitivity level: low, medium, or high"
    )
    enable_auto_inject: Optional[bool] = Field(
        None, description="Toggle automatic KB context injection for high-confidence results"
    )
    auto_inject_threshold: Optional[float] = Field(
        None, ge=0.5, le=1.0, description="Minimum relevance score for auto-injection"
    )
    enable_model_router: Optional[bool] = Field(
        None, description="Toggle automatic model routing based on query complexity"
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings_endpoint():
    """Return current server settings (read-only view of safe-to-expose config)."""
    return {
        "categorize_mode": config.CATEGORIZE_MODE,
        "chunk_max_tokens": config.CHUNK_MAX_TOKENS,
        "chunk_overlap": config.CHUNK_OVERLAP,
        "enable_encryption": config.ENABLE_ENCRYPTION,
        "enable_feedback_loop": config.ENABLE_FEEDBACK_LOOP,
        "enable_hallucination_check": config.ENABLE_HALLUCINATION_CHECK,
        "enable_memory_extraction": config.ENABLE_MEMORY_EXTRACTION,
        "enable_model_router": config.ENABLE_MODEL_ROUTER,
        "enable_auto_inject": config.ENABLE_AUTO_INJECT,
        "hallucination_threshold": config.HALLUCINATION_THRESHOLD,
        "auto_inject_threshold": config.AUTO_INJECT_THRESHOLD,
        "cost_sensitivity": config.COST_SENSITIVITY,
        "feature_tier": config.FEATURE_TIER,
        "feature_flags": config.FEATURE_FLAGS,
        "domains": config.DOMAINS,
        "taxonomy": config.TAXONOMY,
        "sync_backend": config.SYNC_BACKEND,
        "machine_id": config.MACHINE_ID,
        "version": _VERSION,
        "memory_config": {
            "min_response_length": 100,
            "memory_types": ["fact", "decision", "preference", "action_item"],
            "retention_days": config.MEMORY_RETENTION_DAYS,
            "storage_domain": "conversations",
            "extraction_model": "Llama 3.3 (free tier)",
        },
    }


@router.patch("/settings")
async def update_settings_endpoint(req: SettingsUpdateRequest):
    """Update a subset of settings at runtime.

    Only settings that make sense to change without a restart are accepted.
    """
    updated = {}

    if req.categorize_mode is not None:
        valid_modes = ("manual", "smart", "pro")
        if req.categorize_mode not in valid_modes:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid categorize_mode: '{req.categorize_mode}'. Must be one of {valid_modes}",
            )
        config.CATEGORIZE_MODE = req.categorize_mode
        updated["categorize_mode"] = req.categorize_mode

    if req.enable_feedback_loop is not None:
        config.ENABLE_FEEDBACK_LOOP = req.enable_feedback_loop
        updated["enable_feedback_loop"] = req.enable_feedback_loop

    if req.enable_hallucination_check is not None:
        config.ENABLE_HALLUCINATION_CHECK = req.enable_hallucination_check
        updated["enable_hallucination_check"] = req.enable_hallucination_check

    if req.enable_memory_extraction is not None:
        config.ENABLE_MEMORY_EXTRACTION = req.enable_memory_extraction
        updated["enable_memory_extraction"] = req.enable_memory_extraction

    if req.hallucination_threshold is not None:
        config.HALLUCINATION_THRESHOLD = req.hallucination_threshold
        updated["hallucination_threshold"] = req.hallucination_threshold

    if req.cost_sensitivity is not None:
        valid_levels = ("low", "medium", "high")
        if req.cost_sensitivity not in valid_levels:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid cost_sensitivity: '{req.cost_sensitivity}'. Must be one of {valid_levels}",
            )
        config.COST_SENSITIVITY = req.cost_sensitivity
        updated["cost_sensitivity"] = req.cost_sensitivity

    if req.enable_auto_inject is not None:
        config.ENABLE_AUTO_INJECT = req.enable_auto_inject
        updated["enable_auto_inject"] = req.enable_auto_inject

    if req.auto_inject_threshold is not None:
        config.AUTO_INJECT_THRESHOLD = req.auto_inject_threshold
        updated["auto_inject_threshold"] = req.auto_inject_threshold

    if req.enable_model_router is not None:
        config.ENABLE_MODEL_ROUTER = req.enable_model_router
        updated["enable_model_router"] = req.enable_model_router

    if not updated:
        raise HTTPException(
            status_code=400,
            detail="No valid fields provided. Updatable fields: "
            "categorize_mode, enable_feedback_loop, enable_hallucination_check, "
            "enable_memory_extraction, hallucination_threshold, cost_sensitivity, "
            "enable_auto_inject, auto_inject_threshold, enable_model_router",
        )

    logger.info(f"Settings updated: {updated}")
    return {"status": "success", "updated": updated}
