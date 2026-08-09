# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Custom Smart RAG weights REST surface (Phase I Day 1).

  GET    /settings/rag/weights          → current weight map
  PUT    /settings/rag/weights          → bulk-update
  DELETE /settings/rag/weights          → reset all to default
  GET    /settings/rag/weights/sources  → enumerate known sources
                                           for the UI picker

Feature-gated: when ``custom_smart_rag`` is off, all endpoints return
either an empty map (reads) or 403 (writes). The UI surfaces this as
a Pro upgrade CTA.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import utils.rag_weights as weights_module

logger = logging.getLogger("ai-companion.rag_weights_router")

router = APIRouter(prefix="/settings/rag/weights", tags=["rag-weights"])


# ── response shapes ───────────────────────────────────────────────────

class WeightMap(BaseModel):
    weights: dict[str, float]
    user_scope: str  # "global" or "user:<id>"
    feature_enabled: bool


class WeightUpdate(BaseModel):
    weights: dict[str, float] = Field(
        ..., description="Source name → weight (0.0-2.0). Out-of-range silently clamped.",
    )


class SourceInfo(BaseModel):
    name: str
    kind: str  # "data_source" or "kb_domain"
    description: str
    default_enabled: bool
    current_weight: float


class SourcesList(BaseModel):
    sources: list[SourceInfo]
    min_weight: float
    max_weight: float
    default_weight: float
    feature_enabled: bool


# ── helpers ───────────────────────────────────────────────────────────

def _scope() -> str:
    try:
        from config.features import CERID_MULTI_USER
        if not CERID_MULTI_USER:
            return "global"
        from core.context.identity import get_user_id
        uid = get_user_id()
        return f"user:{uid}" if uid else "global"
    except ImportError:
        return "global"


def _feature_on() -> bool:
    try:
        from config.features import is_feature_enabled
        return bool(is_feature_enabled("custom_smart_rag"))
    except ImportError:
        return False


# ── endpoints ─────────────────────────────────────────────────────────

@router.get("", response_model=WeightMap)
async def get_weights_endpoint() -> WeightMap:
    """Return the active weight map. Empty + feature_enabled=False
    when the Pro flag is off — the UI uses this to decide whether to
    show the editor or the upgrade CTA."""
    enabled = _feature_on()
    return WeightMap(
        weights=weights_module.get_weights() if enabled else {},
        user_scope=_scope(),
        feature_enabled=enabled,
    )


@router.put("", response_model=WeightMap)
async def put_weights_endpoint(req: WeightUpdate) -> WeightMap:
    """Bulk-replace the named weights. Pro-gated."""
    if not _feature_on():
        raise HTTPException(
            status_code=403,
            detail="custom_smart_rag is Pro-tier. Upgrade to enable per-source weight tuning.",
        )
    try:
        weights_module.set_weights(req.weights)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to persist weights: {exc}")
    return WeightMap(
        weights=weights_module.get_weights(),
        user_scope=_scope(),
        feature_enabled=True,
    )


@router.delete("", response_model=WeightMap)
async def delete_weights_endpoint() -> WeightMap:
    """Reset all weights to default (1.0). Pro-gated."""
    if not _feature_on():
        raise HTTPException(
            status_code=403,
            detail="custom_smart_rag is Pro-tier.",
        )
    weights_module.reset_weights()
    return WeightMap(
        weights={},
        user_scope=_scope(),
        feature_enabled=True,
    )


@router.get("/sources", response_model=SourcesList)
async def list_sources_endpoint() -> SourcesList:
    """Enumerate every source the user can assign weights to. The UI
    uses this to render the per-source slider list — call once on
    open, then PUT individual weight updates back."""
    enabled = _feature_on()
    if not enabled:
        # Surface the source list even when the feature is off so the
        # UI can render the upgrade CTA over a realistic preview.
        sources_raw = weights_module.known_sources()
    else:
        sources_raw = weights_module.known_sources()
    return SourcesList(
        sources=[SourceInfo(**s) for s in sources_raw],
        min_weight=weights_module.MIN_WEIGHT,
        max_weight=weights_module.MAX_WEIGHT,
        default_weight=weights_module.DEFAULT_WEIGHT,
        feature_enabled=enabled,
    )
