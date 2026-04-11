# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
AI Companion MCP Server - MCP SSE Transport + Ingestion Pipeline
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import traceback
from contextlib import asynccontextmanager

import sentry_sdk

if os.environ.get("SENTRY_DSN_MCP"):
    sentry_sdk.init(
        dsn=os.environ["SENTRY_DSN_MCP"],
        environment=os.environ.get("SENTRY_ENVIRONMENT", "development"),
        release=os.environ.get("SENTRY_RELEASE"),
        send_default_pii=False,  # Privacy-first: don't send API keys, IPs, or request bodies
        traces_sample_rate=0.1,
        profile_session_sample_rate=1.0,
        profile_lifecycle="trace",
        enable_logs=True,
    )

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import neo4j as graph
from app.deps import close_chroma, close_neo4j, close_redis, get_neo4j
from app.middleware.auth import APIKeyMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.tenant_context import TenantContextMiddleware
from app.routers import (
    a2a,
    agents,
    artifacts,
    automations,
    chat,
    digest,
    health,
    ingestion,
    kb_admin,
    mcp_sse,
    memories,
    models,
    observability,
    ollama_proxy,
    plugins,
    providers,
    query,
    scanner,
    sdk,
    settings,
    setup,
    sync,
    taxonomy,
    upload,
    user_state,
    workflows,
)
from app.scheduler import start_scheduler, stop_scheduler
from config.features import CERID_MULTI_USER

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ai-companion")

# Extension hooks — populated by bootstrap (internal features, plugins, etc.)
_shutdown_hooks: list = []


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

        from app.sync.user_state import read_settings
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


async def _openrouter_auth_probe_loop() -> None:
    """Probe OpenRouter auth on startup with exponential backoff.

    Runs as a background task (non-blocking).  On first successful auth it
    resets the openrouter circuit breaker and the consecutive-401 counter so
    that transient startup failures (DNS not yet resolved, auth service slow)
    do not leave LLM features permanently broken until the next restart.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return

    import httpx as _httpx

    from core.utils.circuit_breaker import exponential_backoff_with_jitter, get_breaker
    from core.utils.llm_client import reset_auth_failure_count

    max_attempts = 8
    for attempt in range(max_attempts):
        try:
            async with _httpx.AsyncClient(timeout=5.0) as probe_client:
                resp = await probe_client.get(
                    "https://openrouter.ai/api/v1/auth/key",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            if resp.status_code == 200:
                get_breaker("openrouter").reset()
                reset_auth_failure_count()
                logger.info(
                    "Startup OpenRouter auth probe succeeded (attempt %d/%d) — circuit reset to CLOSED",
                    attempt + 1, max_attempts,
                )
                return
            if resp.status_code == 401:
                logger.warning(
                    "Startup OpenRouter auth probe: API key rejected (401) — aborting probe loop"
                )
                return
            logger.debug(
                "Startup OpenRouter auth probe attempt %d/%d: HTTP %d",
                attempt + 1, max_attempts, resp.status_code,
            )
        except Exception as exc:
            logger.debug(
                "Startup OpenRouter auth probe attempt %d/%d failed: %s",
                attempt + 1, max_attempts, exc,
            )

        delay = exponential_backoff_with_jitter(attempt, base_delay=2.0, max_delay=30.0)
        await asyncio.sleep(delay)

    logger.warning(
        "Startup OpenRouter auth probe exhausted %d attempts — openrouter circuit may remain open",
        max_attempts,
    )


async def _check_infra_connectivity() -> None:
    """Verify reachability of Neo4j, ChromaDB, and Redis at startup.

    Retries up to _INFRA_MAX_RETRIES times with a short sleep between attempts.
    On persistent failure logs a clear diagnostic pointing at the most likely
    cause (Docker network split) and continues in degraded mode so the process
    doesn't prevent the health endpoint from being polled.
    """
    import httpx

    _INFRA_MAX_RETRIES = 5
    _INFRA_RETRY_DELAY = 3.0  # seconds between attempts

    chroma_url = os.getenv("CHROMA_URL", "http://ai-companion-chroma:8000")
    neo4j_bolt = os.getenv("NEO4J_URI", "bolt://ai-companion-neo4j:7687")
    redis_url = os.getenv("REDIS_URL", "redis://ai-companion-redis:6379")

    # Extract hostnames for the diagnostic message
    def _host(url: str) -> str:
        try:
            from urllib.parse import urlparse
            return urlparse(url).hostname or url
        except Exception:
            return url

    targets = {
        "ChromaDB": (chroma_url.rstrip("/") + "/api/v1/heartbeat", "http"),
        "Neo4j": (neo4j_bolt, "bolt"),
        "Redis": (redis_url, "redis"),
    }

    unreachable: list[str] = []

    for attempt in range(1, _INFRA_MAX_RETRIES + 1):
        unreachable = []
        # --- ChromaDB HTTP check ---
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(chroma_url.rstrip("/") + "/api/v1/heartbeat")
            if r.status_code >= 400:
                unreachable.append(f"ChromaDB ({_host(chroma_url)}: HTTP {r.status_code})")
        except Exception as exc:
            unreachable.append(f"ChromaDB ({_host(chroma_url)}: {type(exc).__name__})")

        # --- Neo4j Bolt TCP check (lightweight — just opens a socket) ---
        try:
            from urllib.parse import urlparse
            parsed = urlparse(neo4j_bolt)
            host = parsed.hostname or "ai-companion-neo4j"
            port = parsed.port or 7687
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=3.0
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        except Exception as exc:
            unreachable.append(f"Neo4j ({host}:{port}: {type(exc).__name__})")

        # --- Redis TCP check ---
        try:
            from urllib.parse import urlparse as _up
            rp = _up(redis_url)
            rhost = rp.hostname or "ai-companion-redis"
            rport = rp.port or 6379
            _, rwriter = await asyncio.wait_for(
                asyncio.open_connection(rhost, rport), timeout=3.0
            )
            rwriter.close()
            try:
                await rwriter.wait_closed()
            except Exception:
                pass
        except Exception as exc:
            unreachable.append(f"Redis ({rhost}:{rport}: {type(exc).__name__})")

        if not unreachable:
            logger.info("Startup infra connectivity check passed (attempt %d/%d)", attempt, _INFRA_MAX_RETRIES)
            return

        logger.warning(
            "Startup infra connectivity check attempt %d/%d — unreachable: %s",
            attempt, _INFRA_MAX_RETRIES, ", ".join(unreachable),
        )
        if attempt < _INFRA_MAX_RETRIES:
            await asyncio.sleep(_INFRA_RETRY_DELAY)

    logger.error(
        "INFRA CONNECTIVITY FAILURE — could not reach: %s after %d attempts. "
        "Most likely cause: Docker network split. "
        "All compose files must use 'llm-network: external: true'. "
        "If you started containers with mixed compose files, run: "
        "docker compose down && docker network rm cerid-ai_llm-network && "
        "./scripts/start-cerid.sh",
        ", ".join(unreachable),
        _INFRA_MAX_RETRIES,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Install signal handlers AFTER uvicorn startup (uvicorn overwrites module-level handlers)
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    logger.info("Signal handlers installed (SIGTERM/SIGINT)")

    # Startup infra connectivity check — detects Docker network splits early.
    # Warns and continues in degraded mode on persistent failure rather than
    # crashing, so the /health endpoint remains reachable for diagnosis.
    await _check_infra_connectivity()

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
    try:
        driver = get_neo4j()
        graph.init_schema(driver)
        from app.db.neo4j.migrations import backfill_updated_at, register_recategorized_at
        backfill_updated_at(driver)
        register_recategorized_at(driver)
    except Exception as e:
        logger.warning(f"Neo4j schema init failed (will retry on first use): {e}")

    # Ensure default tenant exists (for multi-user mode migration safety)
    try:
        import config as _cfg
        if _cfg.CERID_MULTI_USER:
            from app.db.neo4j.users import ensure_default_tenant
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

    # Start scheduled maintenance engine
    try:
        start_scheduler()
    except Exception as e:
        logger.warning(f"Scheduler start failed (server runs without it): {e}")

    # Register user-facing automations with scheduler
    try:
        from app.routers.automations import register_all_automations
        register_all_automations()
    except Exception as e:
        logger.warning(f"Automation registration failed (server runs without it): {e}")

    # Pre-warm connections and models for faster first request
    try:
        from app.deps import get_chroma
        from config.taxonomy import DOMAINS, collection_name
        chroma = get_chroma()
        for domain in DOMAINS:
            chroma.get_or_create_collection(name=collection_name(domain))
        # Also pre-warm conversations collection (used by memory recall)
        chroma.get_or_create_collection(name="domain_conversations")
        logger.info("ChromaDB + embedding model pre-warmed (%d domain collections)", len(DOMAINS))
    except Exception as e:
        logger.debug("Pre-warm ChromaDB failed (lazy init on first use): %s", e)

    # Pre-warm LLM client pool (direct OpenRouter)
    try:
        from core.utils.llm_client import _get_client
        await _get_client()
        logger.info("OpenRouter HTTP client pool pre-warmed")
    except Exception as e:
        logger.debug("Pre-warm OpenRouter client failed: %s", e)

    # Background: probe OpenRouter auth with exponential backoff.
    # Resets the circuit breaker on first success so startup 401s (DNS/auth not
    # yet stabilised) do not block LLM features for the full recovery timeout.
    if os.getenv("OPENROUTER_API_KEY"):
        asyncio.ensure_future(_openrouter_auth_probe_loop())

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

    # Pre-warm Ollama client pool (for pipeline tasks)
    if getattr(_startup_config, "OLLAMA_ENABLED", False):
        try:
            from core.utils.internal_llm import _get_ollama_client
            await _get_ollama_client()
            logger.info("Ollama HTTP client pool pre-warmed")
        except Exception as e:
            logger.debug("Pre-warm Ollama client failed: %s", e)

    # Pre-warm reranker ONNX model (avoids 2-3s delay on first query)
    try:
        from utils.reranker import warmup as reranker_warmup
        reranker_warmup()
        logger.info("Reranker ONNX model pre-warmed")
    except Exception as e:
        logger.debug("Pre-warm reranker failed (will load on first use): %s", e)

    # Pre-warm embedding model (ONNX inference session)
    try:
        from core.utils.embeddings import get_embedding_function
        ef = get_embedding_function()
        if ef:
            ef(["warmup"])  # trigger lazy model load
            logger.info("Embedding ONNX model pre-warmed")
    except Exception as e:
        logger.debug("Pre-warm embedding model failed: %s", e)

    # Warm up NLI model (non-blocking, swallows exceptions)
    try:
        from core.utils.nli import warmup as nli_warmup
        nli_warmup()
    except Exception:
        logger.warning("NLI model warmup failed — will load on first verification")

    yield

    # Shutdown: stop scheduler, flush caches, close connections, clear MCP sessions
    try:
        stop_scheduler()
    except Exception as exc:
        logger.warning("Scheduler shutdown failed: %s", exc)
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
        from app.routers.chat import close_chat_client
        await close_chat_client()
    except Exception as exc:
        logger.warning("Chat client shutdown failed: %s", exc)
    try:
        from utils.internal_llm import close_ollama_client
        await close_ollama_client()
    except Exception as exc:
        logger.warning("Ollama client shutdown failed: %s", exc)
    # Extension shutdown hooks (registered by bootstrap)
    for _hook in _shutdown_hooks:
        try:
            await _hook()
        except Exception as exc:
            logger.warning("Extension shutdown hook failed: %s", exc)
    # Flush semantic cache HNSW index to Redis before closing Redis
    try:
        from app.deps import get_redis
        from utils.semantic_cache import flush_cache
        flush_cache(get_redis())
    except Exception:
        logger.debug("Semantic cache flush skipped during shutdown")
    close_neo4j()
    close_chroma()
    close_redis()
    mcp_sse.clear_sessions()


app = FastAPI(
    title="AI Companion MCP Server",
    version="1.0.0",
    lifespan=lifespan,
)

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
from app.middleware.metrics import MetricsMiddleware  # noqa: E402

app.add_middleware(MetricsMiddleware)
# 3. Rate limiting (added third)
app.add_middleware(RateLimitMiddleware)
# 4. API key auth (rejects unauthenticated before rate check)
app.add_middleware(APIKeyMiddleware)
# 5. JWT auth (only active when CERID_MULTI_USER=true — validates Bearer tokens, sets request.state)
if CERID_MULTI_USER:
    from app.middleware.jwt_auth import JWTAuthMiddleware
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
    digest.router,
    taxonomy.router,
    settings.router,
    upload.router,
    memories.router,
    sync.router,
    kb_admin.router,
    user_state.router,
    plugins.router,
    scanner.router,
    workflows.router,
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

# A2A router — Agent Card at /.well-known/agent.json, tasks at /a2a/* (no prefix)
app.include_router(a2a.router)

# MCP transport stays at root only (not versioned)
app.include_router(mcp_sse.router)

# Auth router (only when multi-user mode is enabled)
if CERID_MULTI_USER:
    from app.routers import auth as auth_router
    app.include_router(auth_router.router)
    app.include_router(auth_router.router, prefix="/api/v1")

# Routers from bridge layer (not yet extracted to app/routers/ — Phase C follow-up)
from routers import (  # noqa: E402,I001
    agent_console,
    custom_agents,
    data_sources,
    dlq,
    mcp_client,
    plugin_registry,
    sdk_openapi,
    system_monitor,
    watched_folders,
    webhook_subscriptions,
    widget,
)

_bridge_routers = [
    data_sources.router,
    watched_folders.router,
    system_monitor.router,
    dlq.router,
    webhook_subscriptions.router,
    agent_console.router,
    custom_agents.router,
    mcp_client.router,
    plugin_registry.router,
    widget.router,
]
for r in _bridge_routers:
    app.include_router(r)
    app.include_router(r, prefix="/api/v1")

# SDK OpenAPI spec (serves at /sdk/v1/openapi.json — no versioned prefix needed)
app.include_router(sdk_openapi.router)


@app.get("/")
def root():
    return {"service": "AI Companion MCP Server", "version": "1.0.0", "status": "running"}


