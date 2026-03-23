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
    # Circuit breaker states
    try:
        from utils.circuit_breaker import get_breaker
        bifrost_cb_state = get_breaker("bifrost-rerank").state.value
    except (ValueError, ImportError):
        bifrost_cb_state = "unknown"

    try:
        from utils.circuit_breaker import get_breaker as _gb
        ollama_cb_state = _gb("ollama").state.value
    except (ValueError, ImportError):
        ollama_cb_state = "unknown"

    # OpenRouter credit exhaustion flag (set by bifrost.py on 402)
    credits_exhausted = False
    try:
        redis_client = get_redis()
        credits_exhausted = redis_client.get("cerid:openrouter:credits_exhausted") == "1"
    except Exception:
        pass

    # Ollama status (when enabled)
    import os
    ollama_enabled = os.getenv("OLLAMA_ENABLED", "false").lower() in ("true", "1")
    ollama_status: dict | None = None
    if ollama_enabled:
        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        try:
            import httpx
            resp = httpx.get(f"{ollama_url}/api/tags", timeout=1)
            models = [m.get("name", "") for m in resp.json().get("models", [])] if resp.status_code == 200 else []
            ollama_status = {"reachable": True, "models": len(models), "url": ollama_url}
        except Exception:
            ollama_status = {"reachable": False, "url": ollama_url}

    result: dict = {
        "status": "healthy" if all(v == "connected" for v in status.values()) else "degraded",
        "version": "1.0.0",
        "services": status,
        "circuit_breakers": {
            "bifrost": bifrost_cb_state,
            "ollama": ollama_cb_state,
        },
        "openrouter_credits_exhausted": credits_exhausted,
    }
    if ollama_status is not None:
        result["ollama"] = ollama_status
    return result


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
