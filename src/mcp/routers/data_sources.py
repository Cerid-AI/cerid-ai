# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Data source management — list, enable/disable preloaded and custom sources."""
from __future__ import annotations

import logging

from fastapi import APIRouter

router = APIRouter(tags=["data-sources"])
logger = logging.getLogger("ai-companion.data_sources")


@router.get("/data-sources")
async def list_data_sources():
    """List all registered data sources with their status."""
    from utils.data_sources import registry
    sources = registry.list_sources()
    return {"sources": sources, "total": len(sources)}


@router.post("/data-sources/{name}/enable")
async def enable_source(name: str):
    """Enable a registered data source by name."""
    from utils.data_sources import registry
    for s in registry._sources.values():
        if s.name == name:
            s.enabled = True
            return {"status": "enabled", "name": name}
    return {"error": f"Source '{name}' not found"}


@router.post("/data-sources/{name}/disable")
async def disable_source(name: str):
    """Disable a registered data source by name."""
    from utils.data_sources import registry
    for s in registry._sources.values():
        if s.name == name:
            s.enabled = False
            return {"status": "disabled", "name": name}
    return {"error": f"Source '{name}' not found"}
