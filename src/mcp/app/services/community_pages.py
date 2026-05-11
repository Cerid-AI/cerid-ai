# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Community explorer service (Phase R.2).

Assembles community list and detail pages from Neo4j (cached Leiden
summaries written by Phase 4b background jobs).

Layering
--------
* Lives in ``app/services/`` — may import from ``core.*`` and
  ``app/db/neo4j/*``.
* MUST NOT be imported by anything in ``core/`` (import-linter contract).
* Neo4j driver obtained lazily at call time via the caller passing it in
  OR via ``app.deps.get_neo4j`` from the router.
* All sync Neo4j calls are wrapped in ``asyncio.to_thread`` so the sync
  neo4j driver does not block the event loop.

Public API
----------
* ``list_top_communities(driver, *, min_size, limit) -> list[CommunitySummary]``
* ``get_community_page(driver, community_id) -> CommunityFull | None``

Both functions are thin wrappers over :mod:`app.db.neo4j.communities`.
"""
from __future__ import annotations

import asyncio
import logging

from app.db.neo4j.communities import (
    CommunityFull,
    CommunitySummary,
    get_community,
    list_communities,
)
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.community_pages")

# Re-export Pydantic models so routers can import them from here.
__all__ = [
    "CommunitySummary",
    "CommunityFull",
    "list_top_communities",
    "get_community_page",
]


async def list_top_communities(
    driver,
    *,
    min_size: int = 3,
    limit: int = 30,
    level: int = 0,
) -> list[CommunitySummary]:
    """Return up to ``limit`` communities ordered by member_count descending.

    Wraps the synchronous Neo4j adapter call in ``asyncio.to_thread``.

    Args:
        driver: live Neo4j driver (obtained by the caller from app.deps.get_neo4j).
        min_size: minimum member count; filters out tiny communities (default 3).
        limit: max rows returned (default 30, hard-cap 200 in adapter).
        level: Leiden hierarchy depth (default 0 = finest).

    Returns:
        Sorted list of CommunitySummary Pydantic models.
    """
    try:
        rows = await asyncio.to_thread(
            list_communities,
            driver,
            min_size=min_size,
            limit=limit,
            level=level,
        )
    except Exception as exc:
        log_swallowed_error("community_pages.list_top_communities", exc)
        raise
    return rows


async def get_community_page(
    driver,
    community_id: str,
) -> CommunityFull | None:
    """Fetch the full community page for ``community_id``.

    Returns ``None`` when no community with that id exists in Neo4j.

    Args:
        driver: live Neo4j driver.
        community_id: value of Community.id ("{level}:{native_id}").

    Returns:
        CommunityFull | None.
    """
    try:
        result = await asyncio.to_thread(get_community, driver, community_id)
    except Exception as exc:
        log_swallowed_error(
            "community_pages.get_community_page",
            exc,
            context={"community_id": community_id},
        )
        raise
    return result
