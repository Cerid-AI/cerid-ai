# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
AI Companion MCP Server - MCP SSE Transport + Ingestion Pipeline
"""
from __future__ import annotations

import logging
import os
import signal
import traceback
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

# Sentry is OPT-IN only — requires both SENTRY_DSN and ENABLE_SENTRY=true.
# When disabled, no telemetry data leaves the machine.
_sentry_enabled = (
    os.environ.get("ENABLE_SENTRY", "false").lower() in ("true", "1")
    and os.environ.get("SENTRY_DSN", "")
)

if _sentry_enabled:
    import sentry_sdk

    def _sentry_before_send(event, hint):
        """Use CeridError.error_code as Sentry fingerprint for better grouping."""
        exc_info = hint.get("exc_info")
        if exc_info:
            _, exc, _ = exc_info
            if hasattr(exc, "error_code"):
                event["fingerprint"] = [exc.error_code]
        return event

    sentry_sdk.init(
        dsn=os.environ.get("SENTRY_DSN"),
        environment=os.environ.get("SENTRY_ENVIRONMENT", "development"),
        release=os.environ.get("SENTRY_RELEASE"),
        send_default_pii=False,
        traces_sample_rate=0.1,
        profile_session_sample_rate=1.0,
        profile_lifecycle="trace",
        enable_logs=True,
        before_send=_sentry_before_send,
    )

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config.features import CERID_MULTI_USER
from db import neo4j as graph
from deps import close_chroma, close_neo4j, close_redis, get_neo4j
from errors import CeridError, error_response
from middleware.auth import APIKeyMiddleware
from middleware.rate_limit import RateLimitMiddleware
from middleware.request_id import RequestIDMiddleware
from middleware.tenant_context import TenantContextMiddleware
from routers import (
    a2a,
    agent_console,
    agents,
    artifacts,
    automations,
    chat,
    custom_agents,
    data_sources,
    digest,
    dlq,
    health,
    ingestion,
    kb_admin,
    mcp_client,
    mcp_sse,
    memories,
    models,
    observability,
    ollama_proxy,
    plugin_registry,
    plugins,
    providers,
    query,
    scanner,
    sdk,
    sdk_openapi,
    settings,
    setup,
    sync,
    system_monitor,
    taxonomy,
    upload,
    user_state,
    watched_folders,
    webhook_subscriptions,
    widget,
    workflows,
)
from scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ai-companion")


def _hydrate_settings_from_sync() -> None:
    """Apply user settings from the sync directory to runtime config.

    Reads ``user/settings.json`` from :pydata:`config.SYNC_DIR` and applies
    boolean toggles, categorical values, and numeric parameters so that a
    second machine picks up the same configuration automatically.
    """
    import config

    try:
        sync_dir = config.SYNC_DIR
        if not sync_dir:
            return

        from sync.user_state import read_settings
        settings = read_settings(sync_dir)
        if not settings:
            return

        from utils.features import set_toggle

        hydrated = 0

        # ── Boolean toggles ─────────────────────────────────────────────
        _toggle_keys = (
            "enable_feedback_loop",
            "enable_hallucination_check",
            "enable_memory_extraction",
            "enable_auto_inject",
            "enable_model_router",
            "enable_self_rag",
            "enable_contextual_chunks",
            "enable_adaptive_retrieval",
            "enable_query_decomposition",
            "enable_mmr_diversity",
            "enable_intelligent_assembly",
            "enable_late_interaction",
            "enable_semantic_cache",
        )
        for key in _toggle_keys:
            if key in settings and isinstance(settings[key], bool):
                set_toggle(key, settings[key])
                hydrated += 1

        # ── Categorical values ───────────────────────────────────────────
        _categorical = {
            "categorize_mode": ("manual", "smart", "pro"),
            "cost_sensitivity": ("low", "medium", "high"),
            "storage_mode": ("extract_only", "archive"),
        }
        for key, allowed in _categorical.items():
            if key in settings and settings[key] in allowed:
                setattr(config, key.upper(), settings[key])
                hydrated += 1

        # ── Numeric values with range validation ─────────────────────────
        _numeric = {
            "hallucination_threshold": (0.0, 1.0),
            "auto_inject_threshold": (0.5, 1.0),
            "hybrid_vector_weight": (0.0, 1.0),
            "hybrid_keyword_weight": (0.0, 1.0),
            "rerank_llm_weight": (0.0, 1.0),
            "rerank_original_weight": (0.0, 1.0),
        }
        for key, (lo, hi) in _numeric.items():
            if key in settings:
                val = settings[key]
                if isinstance(val, (int, float)) and lo <= val <= hi:
                    setattr(config, key.upper(), float(val))
                    hydrated += 1

        if hydrated:
            logger.info("Hydrated %d settings from sync directory", hydrated)
    except Exception as e:
        logger.warning("Failed to hydrate settings from sync directory: %s", e)


def _signal_handler(signum: int, frame) -> None:
    """Log signal receipt with stack trace for debugging container restarts."""
    sig_name = signal.Signals(signum).name
    stack = "".join(traceback.format_stack(frame)) if frame else "no frame"
    logger.critical(
        "SIGNAL RECEIVED: %s (%d) — stack:\n%s", sig_name, signum, stack,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Install signal handlers AFTER uvicorn startup (uvicorn overwrites module-level handlers)
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    logger.info("Signal handlers installed (SIGTERM/SIGINT)")

    # Startup validation: warn on missing critical env vars
    if not os.getenv("OPENROUTER_API_KEY"):
        logger.warning(
            "OPENROUTER_API_KEY not set — LLM features (categorization, reranking, "
            "verification, memory extraction) will fail"
        )
    if not os.getenv("CERID_API_KEY"):
        logger.warning(
            "security_notice: API key auth disabled — set CERID_API_KEY to require authentication"
        )
    if not os.getenv("REDIS_PASSWORD"):
        logger.warning(
            "security_notice: Redis password empty — set REDIS_PASSWORD for production"
        )

    # Startup: initialize Neo4j schema + run migrations
    import config as _cfg
    if _cfg.CERID_LIGHTWEIGHT:
        logger.info("Lightweight mode enabled — skipping Neo4j schema init (graph features disabled)")
    else:
        try:
            driver = get_neo4j()
            graph.init_schema(driver)
            from db.neo4j.migrations import backfill_updated_at
            backfill_updated_at(driver)
        except Exception as e:
            logger.warning(f"Neo4j schema init failed (will retry on first use): {e}")

    # Ensure default tenant exists (for multi-user mode migration safety)
    if not _cfg.CERID_LIGHTWEIGHT:
        try:
            if _cfg.CERID_MULTI_USER:
                driver = get_neo4j()
                from db.neo4j.users import ensure_default_tenant
                ensure_default_tenant(driver, _cfg.DEFAULT_TENANT_ID)
                logger.info("Multi-user mode enabled — default tenant ensured")
                if not _cfg.CERID_JWT_SECRET:
                    raise RuntimeError(
                        "CERID_JWT_SECRET is required when CERID_MULTI_USER=true. "
                        "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
                    )
        except Exception as e:
            logger.warning(f"Multi-user startup check failed: {e}")

    # Auto-import from sync directory if DB is empty
    try:
        from sync_check import auto_import_if_empty
        auto_import_if_empty()
    except Exception as e:
        logger.warning(f"Sync auto-import check failed: {e}")

    # Hydrate user settings from sync directory (before logging toggle states)
    _hydrate_settings_from_sync()

    # Log feature toggle states
    from config.features import log_feature_toggles
    log_feature_toggles()

    # Load plugins
    try:
        from plugins import load_plugins
        loaded = load_plugins()
        if loaded:
            logger.info(f"Plugins loaded: {', '.join(loaded)}")
    except Exception as e:
        logger.warning(f"Plugin loading failed (server runs without plugins): {e}")

    # Register event bus → webhook bridge (Phase 3 extensibility)
    try:
        from utils.event_bus import event_bus
        from utils.webhooks import fire_event as fire_webhook_event

        async def _webhook_bridge(event):
            try:
                import dataclasses
                payload = dataclasses.asdict(event) if dataclasses.is_dataclass(event) else {}  # type: ignore[arg-type]
                await fire_webhook_event(event.event_type, payload)
            except Exception as _wb_err:
                logger.debug("Webhook bridge delivery failed: %s", _wb_err)

        event_bus.subscribe_all(_webhook_bridge)
        logger.info("Event bus webhook bridge registered")
    except Exception as e:
        logger.debug("Event bus setup skipped: %s", e)

    # Start scheduled maintenance engine
    try:
        start_scheduler()
    except Exception as e:
        logger.warning(f"Scheduler start failed (server runs without it): {e}")

    # Register user-facing automations with scheduler
    try:
        from routers.automations import register_all_automations
        register_all_automations()
    except Exception as e:
        logger.warning(f"Automation registration failed (server runs without it): {e}")

    # Pre-warm connections and models for faster first request
    try:
        from config.taxonomy import DOMAINS, collection_name
        from deps import get_chroma
        chroma = get_chroma()
        _first_domain = DOMAINS[0] if DOMAINS else None
        if _first_domain:
            chroma.get_or_create_collection(name=collection_name(_first_domain))
        logger.info("ChromaDB + embedding model pre-warmed")
    except Exception as e:
        logger.debug("Pre-warm ChromaDB failed (lazy init on first use): %s", e)

    # Pre-warm reranker ONNX model to avoid cold-start penalty on first query
    try:
        from utils.reranker import warmup as warmup_reranker
        warmup_reranker()
    except Exception as e:
        logger.debug("Pre-warm reranker failed (lazy init on first use): %s", e)

    # Pre-warm LLM client pool (direct OpenRouter)
    try:
        from utils.llm_client import _get_client
        await _get_client()
        logger.info("OpenRouter HTTP client pool pre-warmed")
    except Exception as e:
        logger.debug("Pre-warm OpenRouter client failed: %s", e)

    # Bifrost pre-warm only when explicitly enabled
    import config as _startup_config
    if getattr(_startup_config, "USE_BIFROST", False):
        try:
            from utils.bifrost import get_bifrost_client
            await get_bifrost_client()
            logger.info("Bifrost HTTP client pool pre-warmed")
        except Exception as e:
            logger.debug("Pre-warm Bifrost client failed: %s", e)
    else:
        logger.info("Bifrost disabled (CERID_USE_BIFROST=false) — LLM calls route directly to OpenRouter")

    # Ollama model availability check (non-blocking)
    try:
        from utils.ollama_models import startup_model_check
        await startup_model_check()
    except Exception as e:
        logger.debug("Ollama model check skipped: %s", e)

    # Validate model registry against OpenRouter
    try:
        from utils.model_registry import validate_models
        result = await validate_models()
        if result.get("invalid"):
            logger.warning(
                "Model registry has %d invalid/deprecated models: %s",
                len(result["invalid"]),
                result["invalid"],
            )
        else:
            logger.info("Model registry validated: %d models OK", len(result.get("valid", [])))
    except Exception as e:
        logger.debug("Model validation skipped: %s", e)

    # Check Redis for persisted license from Stripe/manual activation
    try:
        from deps import get_redis
        _redis = get_redis()
        tier_val = _redis.get("cerid:license:tier")
        if tier_val:
            tier_str = tier_val.decode("utf-8") if isinstance(tier_val, bytes) else str(tier_val)
            if tier_str in ("pro", "enterprise"):
                from config.features import set_tier
                set_tier(tier_str)
                logger.info("License tier restored from Redis: %s", tier_str)
    except Exception as e:
        logger.debug("License check skipped: %s", e)

    # Connect to external MCP servers (non-blocking per server)
    try:
        from utils.mcp_client import mcp_client_manager
        mcp_client_manager.load_config()
        if mcp_client_manager._configs:
            connected = await mcp_client_manager.connect_all()
            if connected:
                logger.info("MCP client: %d external server(s) connected", len(connected))
    except Exception as e:
        logger.debug("MCP client startup skipped: %s", e)

    # Verification pipeline self-test (lightweight, non-blocking)
    try:
        from agents.hallucination.startup_self_test import run_verification_self_test
        from deps import get_redis
        _redis = get_redis()
        _self_test = await run_verification_self_test(_redis)
        logger.info(
            "Verification self-test: %s (%d claims via %s, %.0fms)",
            _self_test["status"],
            _self_test["claims_found"],
            _self_test["extraction_method"],
            _self_test["duration_ms"],
        )
    except Exception as e:
        logger.warning("Verification self-test failed (non-blocking): %s", e)

    yield

    # Shutdown: stop scheduler, flush caches, close connections, clear MCP sessions
    stop_scheduler()
    try:
        from utils.mcp_client import mcp_client_manager
        await mcp_client_manager.shutdown()
    except Exception as exc:
        logger.warning("MCP client shutdown failed: %s", exc)
    try:
        from utils.llm_client import close_client
        await close_client()
    except Exception as exc:
        logger.warning("LLM client shutdown failed: %s", exc)
    try:
        from utils.bifrost import close_bifrost_client
        await close_bifrost_client()
    except Exception as exc:
        logger.warning("Bifrost client shutdown failed: %s", exc)
    try:
        from routers.chat import close_chat_client
        await close_chat_client()
    except Exception as exc:
        logger.warning("Chat client shutdown failed: %s", exc)
    try:
        from utils.internal_llm import close_ollama_client
        await close_ollama_client()
    except Exception as exc:
        logger.warning("Ollama client shutdown failed: %s", exc)
    # Flush semantic cache HNSW index to Redis before closing Redis
    try:
        from deps import get_redis
        from utils.semantic_cache import flush_cache
        flush_cache(get_redis())
    except Exception as exc:
        logger.warning("Semantic cache flush failed during shutdown: %s", exc)
    close_neo4j()
    close_chroma()
    close_redis()
    mcp_sse.clear_sessions()


app = FastAPI(
    title="AI Companion MCP Server",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(CeridError)
async def cerid_error_handler(request: Request, exc: CeridError) -> JSONResponse:
    """Global handler for all CeridError subclasses — returns structured JSON."""
    status = 500
    if exc.error_code.startswith("PROVIDER_CREDIT_"):
        status = 402
    elif exc.error_code.startswith("PROVIDER_RATE_"):
        status = 429
    elif exc.error_code.startswith("FEATURE_GATE_"):
        status = 403
    elif exc.error_code.startswith("CONFIG_"):
        status = 422
    return JSONResponse(status_code=status, content=error_response(exc))

# Middleware stack (LIFO in Starlette — last added runs first)
# 1. CORS (added first, runs last — wraps response headers)
_DEFAULT_CORS = "http://localhost:3000,http://localhost:5173,http://localhost:8888"
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", _DEFAULT_CORS).split(",") if o.strip()]
_wildcard = _cors_origins == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=not _wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 2. Metrics collection (added second — records latency/throughput, non-blocking)
from middleware.metrics import MetricsMiddleware  # noqa: E402

app.add_middleware(MetricsMiddleware)
# 3. Rate limiting (added third)
app.add_middleware(RateLimitMiddleware)
# 4. API key auth (rejects unauthenticated before rate check)
app.add_middleware(APIKeyMiddleware)
# 5. JWT auth (only active when CERID_MULTI_USER=true — validates Bearer tokens, sets request.state)
if CERID_MULTI_USER:
    from middleware.jwt_auth import JWTAuthMiddleware
    app.add_middleware(JWTAuthMiddleware)
# 6. Tenant context (sets tenant_id/user_id contextvars from request.state for downstream code)
app.add_middleware(TenantContextMiddleware)
# 7. Request ID (added last, runs first — sets X-Request-ID for all subsequent middleware)
app.add_middleware(RequestIDMiddleware)

# Register routers at root (backward compatibility) and /api/v1/ prefix
_api_routers = [
    health.router,
    query.router,
    ingestion.router,
    artifacts.router,
    agents.router,
    automations.router,
    chat.router,
    data_sources.router,
    digest.router,
    taxonomy.router,
    settings.router,
    upload.router,
    memories.router,
    sync.router,
    kb_admin.router,
    dlq.router,
    user_state.router,
    plugins.router,
    scanner.router,
    watched_folders.router,
    workflows.router,
    agent_console.router,
    system_monitor.router,
    plugin_registry.router,
    custom_agents.router,
    webhook_subscriptions.router,
]
for r in _api_routers:
    app.include_router(r)
    app.include_router(r, prefix="/api/v1")

# Setup, provider, and model assignment routers — first-run wizard and BYOK configuration
app.include_router(setup.router)
app.include_router(setup.router, prefix="/api/v1")
app.include_router(providers.router)
app.include_router(providers.router, prefix="/api/v1")
app.include_router(models.router)
app.include_router(models.router, prefix="/api/v1")

# Observability dashboard API (real-time metrics, health score, cost, quality)
app.include_router(observability.router)
app.include_router(observability.router, prefix="/api/v1")

# Ollama local LLM proxy (always registered; endpoints gate on OLLAMA_ENABLED)
app.include_router(ollama_proxy.router)
app.include_router(ollama_proxy.router, prefix="/api/v1")

# SDK router — stable external contract (manages its own /sdk/v1/ prefix)
app.include_router(sdk.router)

# SDK OpenAPI spec — isolated spec for /sdk/v1/ endpoints only
app.include_router(sdk_openapi.router)

# MCP client — external MCP server management endpoints
app.include_router(mcp_client.router)
app.include_router(mcp_client.router, prefix="/api/v1")

# Embeddable chat widget — serves /widget.html, /widget.js, /widget/config
app.include_router(widget.router)

# A2A router — Agent Card at /.well-known/agent.json, tasks at /a2a/* (no prefix)
app.include_router(a2a.router)

# MCP transport stays at root only (not versioned)
app.include_router(mcp_sse.router)

# Auth router (only when multi-user mode is enabled)
if CERID_MULTI_USER:
    from routers import auth as auth_router
    app.include_router(auth_router.router)
    app.include_router(auth_router.router, prefix="/api/v1")


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "AI Companion MCP Server", "version": "1.0.0", "status": "running"}
