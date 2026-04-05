# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Health check and collection listing endpoints.

Endpoints:
  /health        — Full health check (cached, backward compat)
  /health/live   — Liveness probe (always 200 unless process crashed)
  /health/ready  — Readiness probe (checks all critical dependencies)
  /health/status — Detailed degradation report with circuit breaker states

Dependencies: deps.py (service connections), utils/degradation.py (tier system)
Error types: none (health endpoints never raise)
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from deps import get_chroma, get_neo4j, get_redis
from errors import CeridError

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
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        status["chromadb"] = f"error: {exc}"
    try:
        get_redis()
        status["redis"] = "connected"
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
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
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
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
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.debug("Redis credits_exhausted check failed: %s", exc)

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
        except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.debug("Ollama health probe failed: %s", exc)
            ollama_status = {"reachable": False, "url": ollama_url}

    result: dict = {
        "status": "healthy" if all(
            v == "connected" or v.startswith("disabled") for v in status.values()
        ) else "degraded",
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

    # Inference provider detection status
    try:
        from utils.inference_config import inference_health_payload
        result["inference"] = inference_health_payload()
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.debug("Inference status unavailable: %s", exc)

    return result


def list_collections() -> dict:
    """Public — also called by mcp_sse.py execute_tool."""
    chroma = get_chroma()
    collections = chroma.list_collections()
    return {"total": len(collections), "collections": [c.name for c in collections]}


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


@router.get("/health/live")
def liveness_probe():
    """Liveness probe — is the process running? Always 200.

    Used by Docker healthcheck and orchestrator liveness gates.
    No dependency checks — fast and reliable.
    """
    return {"status": "alive", "version": "1.0.0"}


@router.get("/health/ready")
def readiness_probe():
    """Readiness probe — are all critical dependencies reachable?

    Returns 200 if all critical services (Neo4j, ChromaDB, Redis) are up.
    Returns 503 if any critical dependency is unreachable.
    Used by orchestrator readiness gates to hold traffic until ready.
    """
    result = health_check()
    services = result.get("services", {})
    all_ready = all(v == "connected" for v in services.values())
    status_code = 200 if all_ready else 503
    return JSONResponse(
        status_code=status_code,
        content={"ready": all_ready, "services": services},
    )


@router.get("/health/status")
def degradation_status():
    """Detailed degradation report with circuit breaker states and tier info.

    Returns current degradation tier, per-service status, circuit breaker
    states, uptime, version, and feature tier information.
    """
    result = health_check()
    # Add degradation tier from DegradationManager
    try:
        from utils.degradation import degradation
        tier_info = degradation.status_report()
        result["degradation_tier"] = tier_info.get("tier", "unknown")
        result["can_retrieve"] = tier_info.get("can_retrieve", True)
        result["can_verify"] = tier_info.get("can_verify", True)
        result["can_generate"] = tier_info.get("can_generate", True)
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.warning("Degradation status unavailable: %s", exc)
        result["degradation_tier"] = "unknown"
    # Add feature tier
    try:
        from config.features import FEATURE_TIER
        result["feature_tier"] = FEATURE_TIER
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.debug("Feature tier unavailable: %s", exc)
    # Add pipeline provider config
    try:
        from config.settings import PIPELINE_PROVIDERS
        result["pipeline_providers"] = PIPELINE_PROVIDERS
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.debug("Pipeline providers unavailable: %s", exc)
    # Add internal LLM model info for status bar
    try:
        import config as _cfg
        result["internal_llm_provider"] = _cfg.INTERNAL_LLM_PROVIDER
        result["internal_llm_model"] = _cfg.INTERNAL_LLM_MODEL or _cfg.OLLAMA_DEFAULT_MODEL
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.debug("Suppressed error: %s", exc)
    # Add verification pipeline status
    try:
        from agents.hallucination.startup_self_test import (
            get_failure_counts_sync,
            get_self_test_status_sync,
        )
        _redis = get_redis()
        result["verification_pipeline"] = {
            "self_test": get_self_test_status_sync(_redis),
            "consecutive_failures": get_failure_counts_sync(_redis),
        }
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.debug("Verification pipeline status unavailable: %s", exc)
    return result


@router.get("/collections")
def list_collections_endpoint():
    return list_collections()


@router.get("/scheduler")
def scheduler_status_endpoint():
    """Return status of all scheduled jobs."""
    from scheduler import get_job_status

    return get_job_status()


