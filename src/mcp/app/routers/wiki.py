# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Entity wiki API endpoints (Phase W.1).

Routes
------
GET /wiki/entities?limit=30
    Paginated list of entity summaries, ordered by recent activity.

GET /wiki/entities/{slug}
    Full WikiEntityPage for a single entity. Returns 404 if not found.

Path collision note: the contradictions router already mounts routes under
``/wiki/contradictions/*``.  Entity pages live at ``/wiki/entities/*`` — no
overlap.  Both routers share the ``prefix="/wiki"`` convention so the OpenAPI
tags render under the same "wiki" group.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.services.wiki_pages import (
    EntitySummary,
    WikiEntityPage,
    get_entity_page,
    list_entities,
)
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.wiki")

router = APIRouter(prefix="/wiki", tags=["wiki"])


# ---------------------------------------------------------------------------
# Response models are the service models — re-exported from here for
# OpenAPI schema generation.
# ---------------------------------------------------------------------------

__all__ = ["router"]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/entities",
    response_model=list[EntitySummary],
    summary="List entity wiki pages",
    description=(
        "Returns up to ``limit`` entity summaries ordered by recent activity "
        "(count of source artifacts mentioning the entity in the last 30 days). "
        "Entities with no recent activity sort last."
    ),
)
async def list_entity_pages(
    limit: int = Query(
        default=30,
        ge=1,
        le=200,
        description="Maximum number of entities to return (1–200, default 30).",
    ),
) -> list[EntitySummary]:
    from app.deps import get_neo4j

    driver = get_neo4j()
    try:
        return await list_entities(driver, limit=limit)
    except Exception as exc:
        log_swallowed_error("wiki.list_entity_pages", exc)
        raise HTTPException(status_code=500, detail="Failed to retrieve entity list") from exc


@router.get(
    "/entities/{slug}",
    response_model=WikiEntityPage,
    summary="Get entity wiki page",
    description=(
        "Returns the full wiki page for the entity identified by ``slug`` "
        "(its ``canonical_id``). Includes summary, related entities, source "
        "artifact citations, contradictions, and confidence band. Returns 404 "
        "if no entity with this slug exists."
    ),
)
async def get_entity_wiki_page(slug: str) -> WikiEntityPage:
    from app.deps import get_neo4j

    driver = get_neo4j()
    try:
        page = await get_entity_page(driver, slug)
    except Exception as exc:
        log_swallowed_error("wiki.get_entity_wiki_page", exc, context={"slug": slug})
        raise HTTPException(status_code=500, detail="Failed to retrieve entity wiki page") from exc

    if page is None:
        raise HTTPException(status_code=404, detail=f"Entity {slug!r} not found")

    return page
