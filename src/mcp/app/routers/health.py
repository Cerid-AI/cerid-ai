# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Health check and collection listing endpoints."""
from __future__ import annotations

import logging
import os
import time

from fastapi import APIRouter

from app.deps import get_chroma, get_neo4j, get_redis

router = APIRouter()
logger = logging.getLogger("ai-companion")

# In-memory health cache — avoids blocking I/O on every poll
_health_cache: dict = {}
_health_cache_ts: float = 0.0
_HEALTH_CACHE_TTL = 10.0  # seconds


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
        if driver is None:
            status["neo4j"] = "disabled (lightweight mode)"
        else:
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

    # OpenRouter circuit breaker — covers verification and LLM calls
    try:
        from utils.circuit_breaker import get_breaker as _gb2
        openrouter_cb_state = _gb2("openrouter").state.value
    except (ValueError, ImportError):
        openrouter_cb_state = "unknown"

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
        "version": "0.82.0",
        "services": status,
        "circuit_breakers": {
            "bifrost": bifrost_cb_state,
            "ollama": ollama_cb_state,
            "openrouter": openrouter_cb_state,
        },
        "openrouter_credits_exhausted": credits_exhausted,
    }
    if ollama_status is not None:
        result["ollama"] = ollama_status
    return result


_start_time = time.time()

# Cached OpenRouter auth probe result (refreshed every 30s in degradation_status)
_openrouter_auth_cache: bool | None = None
_openrouter_auth_cache_ts: float = 0.0


def degradation_status() -> dict:
    """Extended health check with degradation tier and uptime."""
    base = health_check()
    try:
        from utils.degradation import DegradationManager
        mgr = DegradationManager()
        tier = mgr.current_tier().value  # .value → lowercase ("full"), not .name ("FULL")
    except Exception:
        tier = "unknown"
    base["degradation_tier"] = tier
    base["uptime_seconds"] = int(time.time() - _start_time)
    base.setdefault("features", {})

    # Pipeline provider routing — tells the frontend which tasks use local models
    import config
    provider = getattr(config, "INTERNAL_LLM_PROVIDER", "openrouter")
    ollama_reachable = base.get("ollama", {}).get("reachable", False)
    is_local = provider == "ollama" and ollama_reachable
    base["pipeline_providers"] = {
        "claim_extraction": provider if is_local else "openrouter",
        "query_decomposition": provider if is_local else "openrouter",
        "topic_extraction": provider if is_local else "openrouter",
        "memory_resolution": provider if is_local else "openrouter",
        "reranking": provider if is_local else "openrouter",
    }
    try:
        base["can_retrieve"] = mgr.can_retrieve()
        base["can_verify"] = mgr.can_verify()
        base["can_generate"] = mgr.can_generate()
    except Exception:
        base["can_retrieve"] = True
        base["can_verify"] = True
        base["can_generate"] = True

    # Inference tier — provider, GPU, latencies
    try:
        from utils.inference_config import inference_health_payload
        base["inference"] = inference_health_payload()
    except Exception:
        pass

    # OpenRouter auth probe — runs on extended health only (15s poll interval)
    # Cached for 30s to avoid hammering the OpenRouter auth endpoint.
    global _openrouter_auth_cache, _openrouter_auth_cache_ts
    now = time.monotonic()
    if now - _openrouter_auth_cache_ts > 30.0:
        try:
            import httpx
            api_key = os.getenv("OPENROUTER_API_KEY", "")
            if api_key:
                resp = httpx.get(
                    "https://openrouter.ai/api/v1/auth/key",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=3,
                )
                _openrouter_auth_cache = resp.status_code == 200
            else:
                _openrouter_auth_cache = None  # no key configured
        except Exception:
            _openrouter_auth_cache = False
        _openrouter_auth_cache_ts = now
    base["openrouter_auth_ok"] = _openrouter_auth_cache

    return base


def list_collections() -> dict:
    """Public — also called by mcp_sse.py execute_tool."""
    chroma = get_chroma()
    collections = chroma.list_collections()
    return {"total": len(collections), "collections": [c.name for c in collections]}


@router.get("/health/live")
def liveness_probe():
    """Kubernetes-style liveness probe — always returns 200."""
    return {"status": "alive"}


@router.get("/health")
def health_check_endpoint():
    global _health_cache, _health_cache_ts
    now = time.monotonic()
    if _health_cache and (now - _health_cache_ts) < _HEALTH_CACHE_TTL:
        return _health_cache
    result = health_check()
    _health_cache = result
    _health_cache_ts = now
    return result


@router.get("/health/status")
def health_status_endpoint():
    """Extended health check with degradation tier and uptime."""
    return degradation_status()


@router.get("/collections")
def list_collections_endpoint():
    return list_collections()


@router.get("/scheduler")
def scheduler_status_endpoint():
    """Return status of all scheduled jobs."""
    from app.scheduler import get_job_status

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
