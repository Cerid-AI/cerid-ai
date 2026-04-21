# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Health check and collection listing endpoints."""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.deps import get_chroma, get_neo4j, get_redis
from core.utils.swallowed import log_swallowed_error
from core.utils.version import get_version

router = APIRouter()
logger = logging.getLogger("ai-companion")


@router.get("/health/ping", include_in_schema=False)
async def health_ping() -> dict:
    """Lightweight liveness probe — no DB checks, used by Docker healthcheck."""
    return {"ok": True}


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
        from core.utils.circuit_breaker import get_breaker as _gb
        ollama_cb_state = _gb("ollama").state.value
    except (ValueError, ImportError):
        ollama_cb_state = "unknown"

    # OpenRouter circuit breaker — covers verification and LLM calls
    try:
        from core.utils.circuit_breaker import get_breaker as _gb2
        openrouter_cb_state = _gb2("openrouter").state.value
    except (ValueError, ImportError):
        openrouter_cb_state = "unknown"

    # OpenRouter credit exhaustion flag (set by llm_client on 402)
    credits_exhausted = False
    try:
        redis_client = get_redis()
        credits_exhausted = redis_client.get("cerid:openrouter:credits_exhausted") == "1"
    except Exception as exc:
        log_swallowed_error("app.routers.health.credits_exhausted_probe", exc)

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
        "version": get_version(),
        "services": status,
        "circuit_breakers": {
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
    except Exception as exc:
        log_swallowed_error("app.routers.health.inference_health_payload", exc)

    # OpenRouter auth probe — runs on extended health only (15s poll interval)
    # Cached for 30s to avoid hammering the OpenRouter auth endpoint.
    #
    # The /auth/key endpoint can return 401 intermittently (rate limiting) even
    # when completions are working fine.  If the probe returns 401 but the
    # completion client's consecutive-failure counter is 0, completions are
    # succeeding and we report auth_ok=True to avoid a false-positive UI error.
    global _openrouter_auth_cache, _openrouter_auth_cache_ts
    now = time.monotonic()
    if now - _openrouter_auth_cache_ts > 30.0:
        try:
            import httpx

            from core.utils.llm_client import get_consecutive_auth_failures
            api_key = os.getenv("OPENROUTER_API_KEY", "")
            if api_key:
                resp = httpx.get(
                    "https://openrouter.ai/api/v1/auth/key",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=3,
                )
                if resp.status_code == 200:
                    _openrouter_auth_cache = True
                elif resp.status_code == 401 and get_consecutive_auth_failures() == 0:
                    # /auth/key returned 401 but completions are succeeding —
                    # treat as a transient probe false positive.
                    _openrouter_auth_cache = True
                else:
                    _openrouter_auth_cache = resp.status_code == 200
            else:
                _openrouter_auth_cache = None  # no key configured
        except Exception:
            # Network error on the probe itself — fall back to completion health.
            try:
                from core.utils.llm_client import get_consecutive_auth_failures
                _openrouter_auth_cache = get_consecutive_auth_failures() == 0
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


def _invariants_snapshot() -> dict:
    """Build the invariants block for /health, swallowing any top-level error.

    Task 14: reports observable data-layer facts beyond "connected".  A
    total failure of the probe module itself is treated as a critical
    invariant violation (503) — if we can't measure health, we can't
    claim it.
    """
    try:
        from app.startup.invariants import run_invariants
        chroma = None
        redis_client = None
        neo4j_driver = None
        try:
            chroma = get_chroma()
        except Exception as exc:
            log_swallowed_error("app.routers.health.invariants_snapshot.get_chroma", exc)
        try:
            redis_client = get_redis()
        except Exception as exc:
            log_swallowed_error("app.routers.health.invariants_snapshot.get_redis", exc)
        try:
            neo4j_driver = get_neo4j()
        except Exception as exc:
            log_swallowed_error("app.routers.health.invariants_snapshot.get_neo4j", exc, redis_client=redis_client)
        if neo4j_driver is None:
            # Lightweight mode — skip the orphan check, NLI still matters.
            snap: dict[str, Any] = {
                "verification_report_orphans": 0,
                "collections_empty": [],
                "errors": [],
            }
            from app.startup.invariants import _probe_chroma, _probe_nli
            try:
                if chroma is not None:
                    snap.update(_probe_chroma(chroma))
            except Exception as exc:
                errs = snap["errors"]
                if isinstance(errs, list):
                    errs.append(f"chroma: {exc}")
            snap.update(_probe_nli())
            snap["healthy_invariants"] = bool(snap.get("nli_model_loaded"))
            return snap
        return run_invariants(chroma, redis_client, neo4j_driver)
    except Exception as exc:
        logger.warning("invariants snapshot failed: %s", exc)
        return {"healthy_invariants": False, "errors": [str(exc)]}


@router.get("/health")
def health_check_endpoint():
    """Return infrastructure health.

    Returns HTTP 200 when all required services are reachable ("healthy") and
    HTTP 503 when any are down ("degraded").  The Docker HEALTHCHECK uses
    ``curl -f`` which fails on non-2xx, so this causes ``docker ps`` to show
    the container as *unhealthy* when a network split isolates MCP from infra.

    The Neo4j "disabled (lightweight mode)" state is treated as healthy — it
    is intentional, not a connectivity failure.

    Task 14: the response additionally carries an ``invariants`` block with
    observable data-layer facts (empty Chroma collections, orphan
    VerificationReports, NLI model load status).  A critical invariant
    violation flips the endpoint to 503 even when transport connections
    are nominally healthy.
    """
    global _health_cache, _health_cache_ts
    now = time.monotonic()
    if _health_cache and (now - _health_cache_ts) < _HEALTH_CACHE_TTL:
        result = _health_cache
    else:
        result = health_check()
        result["invariants"] = _invariants_snapshot()
        _health_cache = result
        _health_cache_ts = now

    # A service is "ok" when connected OR intentionally disabled (lightweight neo4j).
    def _ok(v: str) -> bool:
        return v == "connected" or v.startswith("disabled")

    services_ok = all(_ok(v) for v in result["services"].values())
    invariants_ok = result.get("invariants", {}).get("healthy_invariants", True)
    http_status = 200 if (services_ok and invariants_ok) else 503
    if http_status == 200:
        return result
    return JSONResponse(content=result, status_code=503)


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
