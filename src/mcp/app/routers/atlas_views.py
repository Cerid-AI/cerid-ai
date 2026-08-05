# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Atlas saved views — Phase A Day 12.

Per-user list of named Atlas configurations users can pin. A "view" is
a snapshot of {entity, hops, filter, mode, lenses, camera_state, name}
that lets the user jump back to a particular analytic context.

Endpoints:
    GET    /atlas/views          — list views for current user
    POST   /atlas/views          — create a new view
    DELETE /atlas/views/{id}     — delete a view
    PATCH  /atlas/views/{id}     — rename or update a view

Storage:
    Redis hash ``cerid:atlas:views:{user_id}`` mapping view_id → JSON.
    Single-user mode falls back to user_id="default" when no auth
    middleware has populated request.state.user_id.

Tier gating:
    Atlas itself is community-tier; saved views are too. Pro extensions
    (sharing views across users, exporting as PNG, scheduled snapshots)
    layer on later.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.deps import get_redis
from config.features import is_feature_enabled


# --- Response models (generated: single-return dict-literal routes) ---
class AtlasViewsHealthResponse(BaseModel):
    redis_available: Any
    max_views_per_user: Any
    free_tier_max_views: Any
    supported_modes: Any
    pro_unlocked: Any



logger = logging.getLogger("ai-companion.atlas_views")
router = APIRouter(prefix="/atlas/views", tags=["atlas", "views"])

# Hard cap on saved views per user so an automated script can't bloat
# Redis. Tunable via env.
_MAX_VIEWS_PER_USER = int(os.getenv("ATLAS_MAX_VIEWS_PER_USER", "50"))

# Phase M Day 6 — free tier cap, lifted for Pro users.
_FREE_TIER_MAX_VIEWS = int(os.getenv("ATLAS_FREE_TIER_MAX_VIEWS", "3"))

# Phase M Day 6 — saved views generalize across Subjects modes.
# Atlas itself stays the default; the other modes piggyback on the
# same Redis-backed storage so users can pin a constellation focus
# or a Timeline scrub window the same way they pin an Atlas zoom.
_SUPPORTED_VIEW_MODES = {"atlas", "constellation", "timeline", "wiki"}


class AtlasCameraState(BaseModel):
    """Sigma camera state snapshot. Matches sigma's getCamera().getState()."""
    x: float = 0.0
    y: float = 0.0
    ratio: float = 1.0
    angle: float = 0.0


class AtlasViewIn(BaseModel):
    """Payload for POST/PATCH. All fields except `name` and `entity` optional."""
    name: str = Field(..., min_length=1, max_length=80)
    entity: str = Field(..., min_length=1)
    hops: int = Field(2, ge=1, le=3)
    filter: str | None = None
    mode: str = "atlas"
    lenses: list[str] = Field(default_factory=list)
    camera: AtlasCameraState | None = None

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        if v not in _SUPPORTED_VIEW_MODES:
            raise ValueError(
                f"mode must be one of {sorted(_SUPPORTED_VIEW_MODES)}; got {v!r}"
            )
        return v


class AtlasView(AtlasViewIn):
    """Stored view shape."""
    view_id: str
    created_at: str
    updated_at: str


class AtlasViewListResponse(BaseModel):
    views: list[AtlasView]


def _user_key(user_id: str) -> str:
    return f"cerid:atlas:views:{user_id}"


def _resolve_user_id(request: Request) -> str:
    """Extract user id from auth middleware; fall back to default single-user key."""
    uid = getattr(request.state, "user_id", None) if hasattr(request, "state") else None
    return uid or "default"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_pro_unlocked() -> bool:
    """Pro tier unlocks the unlimited-views path.

    Reuses the same `pro_visualization_*` flag family the tour and Phase L
    analytics surface check, so a user with any Pro viz feature on gets
    the full saved-views experience.
    """
    return (
        is_feature_enabled("pro_visualization_tour")
        or is_feature_enabled("pro_visualization_analytics")
        or is_feature_enabled("pro_visualization_timeline")
    )


@router.get("", response_model=AtlasViewListResponse)
async def list_views(request: Request, mode: str | None = None) -> AtlasViewListResponse:
    """List saved views for the current user.

    ``mode`` filter (Phase M Day 6) — when provided, narrows the
    result to that Subjects mode. Unknown modes return an empty list
    rather than 422 so the frontend's per-mode sidebars are robust to
    future mode additions.
    """
    redis = get_redis()
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    user_id = _resolve_user_id(request)
    key = _user_key(user_id)

    try:
        raw = redis.hgetall(key)
    except (OSError, ValueError) as exc:
        logger.exception("atlas_views.list_failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to list views") from exc

    views: list[AtlasView] = []
    for view_id, payload in raw.items():
        try:
            vid = view_id.decode("utf-8") if isinstance(view_id, bytes) else view_id
            data = json.loads(payload if isinstance(payload, str) else payload.decode("utf-8"))
            data["view_id"] = vid
            views.append(AtlasView(**data))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.info("atlas_views.skip_corrupt user=%s vid=%s: %s", user_id, view_id, exc)

    if mode is not None:
        views = [v for v in views if v.mode == mode]

    views.sort(key=lambda v: v.updated_at, reverse=True)
    return AtlasViewListResponse(views=views)


@router.post("", response_model=AtlasView, status_code=201)
async def create_view(body: AtlasViewIn, request: Request) -> AtlasView:
    """Create a new saved view for the current user."""
    redis = get_redis()
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    user_id = _resolve_user_id(request)
    key = _user_key(user_id)

    try:
        existing_count = redis.hlen(key) or 0
    except (OSError, ValueError) as exc:
        logger.exception("atlas_views.create_count_failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to count views") from exc

    if existing_count >= _MAX_VIEWS_PER_USER:
        raise HTTPException(
            status_code=429,
            detail=f"Saved view limit reached ({_MAX_VIEWS_PER_USER}). Delete an existing view first.",
        )

    # Phase M Day 6 — free tier cap (separate from the hard Redis cap).
    # Pro tier (any Pro viz feature) lifts the cap; community caps at 3.
    if not _is_pro_unlocked() and existing_count >= _FREE_TIER_MAX_VIEWS:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Free tier supports up to {_FREE_TIER_MAX_VIEWS} saved views. "
                "Upgrade to Pro for unlimited views, or delete one to make room."
            ),
        )

    view_id = uuid.uuid4().hex[:16]
    now = _now_iso()
    view = AtlasView(
        view_id=view_id,
        created_at=now,
        updated_at=now,
        **body.model_dump(),
    )

    try:
        redis.hset(key, view_id, view.model_dump_json(exclude={"view_id"}))
    except (OSError, ValueError) as exc:
        logger.exception("atlas_views.create_failed user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to save view") from exc

    return view


@router.patch("/{view_id}", response_model=AtlasView)
async def update_view(view_id: str, body: AtlasViewIn, request: Request) -> AtlasView:
    """Update an existing view in place."""
    redis = get_redis()
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    user_id = _resolve_user_id(request)
    key = _user_key(user_id)

    try:
        existing = redis.hget(key, view_id)
    except (OSError, ValueError) as exc:
        logger.exception("atlas_views.update_read_failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to read view") from exc

    if not existing:
        raise HTTPException(status_code=404, detail=f"view '{view_id}' not found")

    try:
        prev_raw = json.loads(existing if isinstance(existing, str) else existing.decode("utf-8"))
    except json.JSONDecodeError as exc:
        # Corrupt row — replace with the new body wholesale rather than
        # 500-ing the user. Surface at INFO so operators can investigate.
        logger.info("atlas_views.update_corrupt_prev vid=%s: %s", view_id, exc)
        prev_raw = {}

    created_at = prev_raw.get("created_at") or _now_iso()
    view = AtlasView(
        view_id=view_id,
        created_at=created_at,
        updated_at=_now_iso(),
        **body.model_dump(),
    )

    try:
        redis.hset(key, view_id, view.model_dump_json(exclude={"view_id"}))
    except (OSError, ValueError) as exc:
        logger.exception("atlas_views.update_failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update view") from exc

    return view


@router.delete("/{view_id}", status_code=204)
async def delete_view(view_id: str, request: Request) -> None:
    """Delete a saved view. Idempotent: missing view returns 204."""
    redis = get_redis()
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    user_id = _resolve_user_id(request)
    key = _user_key(user_id)

    try:
        redis.hdel(key, view_id)
    except (OSError, ValueError) as exc:
        logger.exception("atlas_views.delete_failed user=%s vid=%s: %s", user_id, view_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete view") from exc


@router.get("/health", response_model=AtlasViewsHealthResponse)
async def health() -> dict[str, Any]:
    """Probe — confirms router is mounted and Redis reachable."""
    redis = get_redis()
    return {
        "redis_available": redis is not None,
        "max_views_per_user": _MAX_VIEWS_PER_USER,
        "free_tier_max_views": _FREE_TIER_MAX_VIEWS,
        "supported_modes": sorted(_SUPPORTED_VIEW_MODES),
        "pro_unlocked": _is_pro_unlocked(),
    }
