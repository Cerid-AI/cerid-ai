# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Constellation tour generator — Phase B Day 7.

Produces an LLM-narrated camera arc through the user's knowledge graph:
a sequence of {camera_xyz, look_at_xyz, duration_ms, narration} steps
that the frontend choreographs via GSAP and (optionally) reads aloud
through the system TTS.

Endpoint:
    POST /graph/tour/generate
        body: { focal_entity?: str, max_stops?: int=6, duration_s?: int=75 }
        returns: TourArc { stops: TourStop[], total_duration_ms, summary }

Pro-gated via ``is_feature_enabled("pro_visualization_tour")``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.deps import get_neo4j
from config.features import is_feature_enabled


# --- Response models (generated: single-return dict-literal routes) ---
class TourHealthResponse(BaseModel):
    pro_visualization_tour_enabled: Any
    max_stops: Any
    default_duration_s: Any



logger = logging.getLogger("ai-companion.graph_tour")
router = APIRouter(prefix="/graph/tour", tags=["graph", "tour"])

_DEFAULT_MAX_STOPS = int(os.getenv("GRAPH_TOUR_MAX_STOPS", "8"))
_DEFAULT_DURATION_S = int(os.getenv("GRAPH_TOUR_DEFAULT_DURATION_S", "75"))


class TourStop(BaseModel):
    """One stop in the tour. Frontend renders camera waypoint + narration."""
    entity_id: str
    entity_name: str
    # 3D camera position (looking at the entity from this point)
    camera: tuple[float, float, float] = (0.0, 0.0, 6.0)
    look_at: tuple[float, float, float] = (0.0, 0.0, 0.0)
    duration_ms: int = 5_000
    narration: str = ""


class TourArc(BaseModel):
    stops: list[TourStop]
    total_duration_ms: int
    summary: str = ""


class TourRequest(BaseModel):
    focal_entity: str | None = None
    max_stops: int = Field(_DEFAULT_MAX_STOPS, ge=2, le=20)
    duration_s: int = Field(_DEFAULT_DURATION_S, ge=15, le=300)
    # Phase M Day 4 — free tier preview path. When true, the
    # endpoint returns a clamped tour (~15s, 3 stops) regardless of
    # the Pro feature flag so community users get a taste without
    # the full feature.
    preview: bool = Field(default=False, description="Return a 15s preview (free tier).")


def _is_pro_enabled() -> bool:
    """Tour mode is a Pro visualization feature."""
    return is_feature_enabled("pro_visualization_tour")


@router.post("/generate", response_model=TourArc)
async def generate_tour(body: TourRequest) -> TourArc:
    """Generate a narrated camera tour through the knowledge graph.

    v1 ships a deterministic top-K-by-mention-count selection with
    templated narration. The LLM-narrated path lands when the chat
    pipeline's structured-output mode is wired through; the response
    shape is stable so the upgrade is drop-in.

    Pro-gated.
    """
    # Phase M Day 4: distinct flows for Pro vs. preview.
    #   - Pro:     full body (up to 20 stops, up to 5 min, full narration)
    #   - Preview: clamped 3 stops + 15s + truncated narration; surfaced
    #              to community users so they can sample the feature
    #              before upgrading.
    is_preview = body.preview and not _is_pro_enabled()
    if not body.preview and not _is_pro_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                "Tour mode requires the Pro tier (pro_visualization_tour "
                "feature). Set ?preview=true for a 15-second free preview."
            ),
        )

    driver = get_neo4j()
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j unavailable")

    # Clamp stops + duration in preview mode regardless of request body
    effective_max_stops = 3 if is_preview else body.max_stops
    effective_duration_s = 15 if is_preview else body.duration_s

    rows = await asyncio.to_thread(_fetch_top_entities, driver, effective_max_stops, body.focal_entity)
    if not rows:
        raise HTTPException(status_code=404, detail="No entities available for a tour")

    # Defensive clamp — the cypher honors LIMIT $max_stops but we
    # re-trim here so over-eager mocks + future query refactors can't
    # break the preview contract.
    rows = rows[:effective_max_stops]

    per_stop_ms = max(2_500, int(effective_duration_s * 1000 / max(1, len(rows))))

    stops: list[TourStop] = []
    for row in rows:
        cam, look = _camera_for_entity(row)
        narration = _templated_narration(row)
        if is_preview:
            # Trim narration in preview to the first sentence so the
            # full LLM-narrated experience stays a Pro reveal.
            first_sentence = narration.split(".")[0]
            narration = first_sentence + "." if first_sentence else narration[:80]
        stops.append(
            TourStop(
                entity_id=row["id"],
                entity_name=row.get("name") or row["id"],
                camera=cam,
                look_at=look,
                duration_ms=per_stop_ms,
                narration=narration,
            )
        )

    summary = _tour_summary(stops)
    if is_preview:
        summary = f"[Preview] {summary} (Upgrade to Pro for the full tour.)"

    return TourArc(
        stops=stops,
        total_duration_ms=per_stop_ms * len(stops),
        summary=summary,
    )


@router.get("/health", response_model=TourHealthResponse)
async def tour_health() -> dict[str, Any]:
    """Probe — confirms router mounted + feature-gate state."""
    return {
        "pro_visualization_tour_enabled": _is_pro_enabled(),
        "max_stops": _DEFAULT_MAX_STOPS,
        "default_duration_s": _DEFAULT_DURATION_S,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fetch_top_entities(driver: Any, max_stops: int, focal_entity: str | None) -> list[dict[str, Any]]:
    """Pull top-K entities by mention_count, with their 3D coords if available.

    Falls back to entity name when umap_* coords aren't populated yet
    (compute_umap_3d job hasn't run) — the frontend will skip such stops
    and just narrate them at the origin.
    """
    where = "WHERE e.canonical_id IS NOT NULL"
    cypher = f"""
        MATCH (e:Entity)
        {where}
        RETURN
            e.canonical_id AS id,
            coalesce(e.name, e.canonical_id) AS name,
            coalesce(e.mention_count, 0) AS mention_count,
            e.umap_x AS x,
            e.umap_y AS y,
            e.umap_z AS z
        ORDER BY mention_count DESC
        LIMIT $limit
    """
    try:
        with driver.session() as session:
            result = session.run(cypher, limit=max_stops)
            rows = result.data()
    except (OSError, RuntimeError, ValueError) as exc:
        logger.exception("graph_tour: query failed: %s", exc)
        return []

    # If a focal entity is provided, ensure it leads the list.
    if focal_entity:
        focal_row = next((r for r in rows if r["id"] == focal_entity), None)
        if focal_row:
            rows.remove(focal_row)
            rows.insert(0, focal_row)

    return rows


def _camera_for_entity(row: dict[str, Any]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Pick a camera position that frames the entity nicely.

    The look_at is the entity's UMAP coords (or origin if not yet projected).
    The camera sits at (entity + offset) where offset is a unit-vector
    direction times a sensible viewing distance.
    """
    ex = float(row.get("x") or 0.0)
    ey = float(row.get("y") or 0.0)
    ez = float(row.get("z") or 0.0)

    # Distance scaled by entity scale; clamp to [4, 12] so we don't end
    # up inside the entity or all the way out at the starfield.
    dist = 6.5
    # Direction: simple diagonal so neighbouring entities don't get the
    # same camera angle. Deterministic per entity via id hash.
    h = sum(ord(c) for c in row.get("id", "")) or 1
    angle = (h % 360) * 3.14159265 / 180.0
    import math
    cx = ex + dist * math.cos(angle)
    cy = ey + dist * 0.4 * (1 if h % 3 else -1)
    cz = ez + dist * math.sin(angle) + 2.0

    return ((cx, cy, cz), (ex, ey, ez))


def _templated_narration(row: dict[str, Any]) -> str:
    """v1 templated narration. Replace with LLM-generated when wired."""
    name = row.get("name") or row.get("id", "this entity")
    mention_count = int(row.get("mention_count") or 0)
    if mention_count > 50:
        return f"{name} sits at the centre of {mention_count} mentions — one of your corpus's anchor nodes."
    if mention_count > 10:
        return f"{name}. Mentioned {mention_count} times across your knowledge base."
    return f"{name}. A specialist topic with focused coverage."


def _tour_summary(stops: list[TourStop]) -> str:
    if not stops:
        return ""
    leading = stops[0].entity_name
    rest = len(stops) - 1
    if rest <= 0:
        return f"A short visit to {leading}."
    return f"Starting at {leading}, then {rest} more stop{'s' if rest != 1 else ''} through your knowledge graph."
