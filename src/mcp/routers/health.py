# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Health check and collection listing endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter

from deps import get_chroma, get_neo4j, get_redis

router = APIRouter()
logger = logging.getLogger("ai-companion")


def health_check() -> dict:
    """Public — also called by mcp_sse.py execute_tool."""
    status = {"chromadb": "unknown", "redis": "unknown", "neo4j": "unknown"}
    try:
        get_chroma()
        status["chromadb"] = "connected"
    except Exception as exc:
        status["chromadb"] = f"error: {exc}"
    try:
        get_redis()
        status["redis"] = "connected"
    except Exception as exc:
        status["redis"] = f"error: {exc}"
    try:
        driver = get_neo4j()
        # get_neo4j() validates auth on first connect, but verify on every
        # health check by running a trivial query (catches stale sessions).
        with driver.session() as session:
            session.run("RETURN 1").consume()
        status["neo4j"] = "connected"
    except Exception as exc:
        status["neo4j"] = f"error: {exc}"
    return {
        "status": "healthy" if all(v == "connected" for v in status.values()) else "degraded",
        "version": "1.0.0",
        "services": status,
    }


def list_collections() -> dict:
    """Public — also called by mcp_sse.py execute_tool."""
    chroma = get_chroma()
    collections = chroma.list_collections()
    return {"total": len(collections), "collections": [c.name for c in collections]}


@router.get("/health")
def health_check_endpoint():
    return health_check()


@router.get("/collections")
def list_collections_endpoint():
    return list_collections()


@router.get("/scheduler")
def scheduler_status_endpoint():
    """Return status of all scheduled jobs."""
    from scheduler import get_job_status

    return get_job_status()


@router.get("/plugins")
def plugins_endpoint():
    """Return loaded plugins and feature flag status."""
    from plugins import get_loaded_plugins
    from utils.features import get_feature_status

    return {
        "plugins": get_loaded_plugins(),
        **get_feature_status(),
    }
