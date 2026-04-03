# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""API endpoints for the community plugin browser (GUI plugin marketplace)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from utils.plugin_registry import plugin_registry_client

logger = logging.getLogger("ai-companion.plugin_registry")

router = APIRouter(prefix="/plugin-registry", tags=["plugin-registry"])


@router.get("")
async def list_community_plugins(q: str = "", type: str = ""):
    """List or search community plugins from the registry.

    Query params:
        q:    free-text search (name, description, tags)
        type: filter by plugin type (tool, connector, parser, agent)
    """
    try:
        results = await plugin_registry_client.search(query=q, plugin_type=type)
        return {"plugins": results, "total": len(results)}
    except Exception as exc:
        logger.warning("Plugin registry search failed: %s", exc)
        return {"plugins": [], "total": 0}


@router.get("/{name}")
async def get_community_plugin(name: str):
    """Get details for a specific community plugin by name."""
    try:
        plugin = await plugin_registry_client.get_plugin(name)
    except Exception as exc:
        logger.warning("Plugin registry lookup failed: %s", exc)
        raise HTTPException(status_code=502, detail="Registry unavailable") from exc

    if plugin is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found in registry")
    return plugin
