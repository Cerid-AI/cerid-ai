"""Health check and collection listing endpoints."""
from __future__ import annotations

import logging
from typing import Dict

from fastapi import APIRouter

from deps import get_chroma, get_neo4j, get_redis

router = APIRouter()
logger = logging.getLogger("ai-companion")


def health_check() -> Dict:
    """Public — also called by mcp_sse.py execute_tool."""
    status = {"chromadb": "unknown", "redis": "unknown", "neo4j": "unknown"}
    try:
        get_chroma()
        status["chromadb"] = "connected"
    except Exception:
        status["chromadb"] = "error"
    try:
        get_redis()
        status["redis"] = "connected"
    except Exception:
        status["redis"] = "error"
    try:
        get_neo4j()
        status["neo4j"] = "connected"
    except Exception:
        status["neo4j"] = "error"
    return {
        "status": "healthy" if all(v == "connected" for v in status.values()) else "degraded",
        "services": status,
    }


def list_collections() -> Dict:
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


@router.get("/stats")
def stats_endpoint():
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
