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
import threading
import time
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

# ---------------------------------------------------------------------------
# Event-loop watchdog
# Detects a hung asyncio event loop and forces a clean SIGTERM so that
# restart:unless-stopped brings the container back up automatically.
# ---------------------------------------------------------------------------
_WATCHDOG_TIMEOUT_S: float = 45.0
_watchdog_stop = threading.Event()
_heartbeat: list[float] = [0.0]  # mutable container avoids global keyword


async def _heartbeat_task() -> None:
    """Ticks every 5 s while the event loop is alive."""
    while not _watchdog_stop.is_set():
        _heartbeat[0] = time.monotonic()
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            break


def _start_watchdog() -> None:
    """Starts a daemon thread that SIGTERMs the process if the loop goes silent."""
    _watchdog_stop.clear()
    _heartbeat[0] = time.monotonic()

    def _watch() -> None:
        time.sleep(20)  # grace: let the heartbeat task start before first check
        while not _watchdog_stop.is_set():
            time.sleep(10)
            age = time.monotonic() - _heartbeat[0]
            if age > _WATCHDOG_TIMEOUT_S:
                logger.warning(
                    "Event loop watchdog: heartbeat stalled for %.0fs — forcing exit",
                    age,
                )
                # os._exit bypasses signal handlers (which may be overridden) and
                # immediately terminates the process so Docker's restart:unless-stopped
                # can bring the container back up cleanly.
                os._exit(1)

    threading.Thread(target=_watch, name="loop-watchdog", daemon=True).start()


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
    """Log signal receipt with stack trace, then re-raise with default handler.

    Installing a custom handler via signal.signal() overrides uvicorn's asyncio
    handler.  After logging we restore SIG_DFL and re-raise so the process
    terminates normally — otherwise SIGTERM (from `docker stop` or the watchdog)
    would be silently swallowed and the container would hang.
    """
    sig_name = signal.Signals(signum).name
    stack = "".join(traceback.format_stack(frame)) if frame else "no frame"
    logger.critical(
        "SIGNAL RECEIVED: %s (%d) — stack:\n%s", sig_name, signum, stack,
    )
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


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


async def _prewarm_external_sources() -> None:
    """Probe all registered external data sources at startup.

    With failure_threshold=1 on the named datasource-* circuit breakers, a
    single timeout trips the breaker.  If Docker has no egress to external
    services (Wikipedia, DuckDuckGo, etc.), all breakers open here rather than
    on the first user message — eliminating the 5-6s hang per source that
    users would otherwise experience.

    Uses a 3s per-source timeout (shorter than the 5s query-time default) so
    the probe is cheap even in production where sources are reachable.
    """
    try:
        from utils.data_sources import registry
        sources = registry.get_enabled_sources()
        if not sources:
            return
        await asyncio.wait_for(
            registry.query_all("startup connectivity probe", timeout=3.0),
            timeout=4.0,
        )
        logger.info("External data source pre-warm complete (%d sources probed)", len(sources))
    except asyncio.TimeoutError:
        logger.info("External data source pre-warm timed out — circuit breakers now open for unreachable sources")
    except Exception as exc:
        logger.debug("External data source pre-warm error: %s", exc)


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

    # Pre-warm Redis: functional PING beyond TCP connectivity check
    try:
        from app.deps import get_redis as _get_redis
        _get_redis().ping()
        logger.info("Redis PING pre-warm passed")
    except Exception as e:
        logger.warning("Redis PING pre-warm failed (cache may be unavailable): %s", e)

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

    # Pre-warm reranker ONNX model (avoids 2-3s delay on first query).
    # run_in_executor keeps the event loop (and uvicorn) responsive while ONNX
    # loads — without this the loop blocks for several seconds on cold start.
    try:
        from utils.reranker import warmup as reranker_warmup
        await asyncio.get_running_loop().run_in_executor(None, reranker_warmup)
        logger.info("Reranker ONNX model pre-warmed")
    except Exception as e:
        logger.debug("Pre-warm reranker failed (will load on first use): %s", e)

    # Pre-warm embedding model (ONNX inference session)
    try:
        from core.utils.embeddings import get_embedding_function
        ef = get_embedding_function()
        if ef:
            await asyncio.get_running_loop().run_in_executor(None, ef, ["warmup"])
            logger.info("Embedding ONNX model pre-warmed")
    except Exception as e:
        logger.debug("Pre-warm embedding model failed: %s", e)

    # Warm up NLI model — the slow one (~45s on cold start due to model download).
    # Must use run_in_executor: running it directly blocked the event loop for the
    # entire download duration, causing healthcheck timeouts during startup.
    try:
        from core.utils.nli import warmup as nli_warmup
        await asyncio.get_running_loop().run_in_executor(None, nli_warmup)
    except Exception:
        logger.warning("NLI model warmup failed — will load on first verification")

    # Pre-warm external data sources — runs in the background so startup remains
    # fast.  Trips circuit breakers for any source that can't be reached, so the
    # first user message sees an instant skip rather than a per-source timeout.
    asyncio.ensure_future(_prewarm_external_sources())

    # Arm event-loop watchdog. The heartbeat coroutine ticks every 5 s; a daemon
    # thread watches it and sends SIGTERM if the loop goes silent for 45 s.
    # Combined with restart:unless-stopped this gives automatic recovery from
    # hung uvicorn workers without any external monitoring infrastructure.
    asyncio.ensure_future(_heartbeat_task())
    _start_watchdog()
    logger.info("Event loop watchdog armed (%.0fs timeout)", _WATCHDOG_TIMEOUT_S)

    yield

    # Disarm watchdog before shutdown tasks run (avoid spurious SIGTERM during
    # intentional slow-shutdown operations like cache flush).
    _watchdog_stop.set()

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


# -- Internal feature bootstrap -----------------------------------------------
# Below this line: internal-only bootstrap (stripped for public distribution)
try:
    from app.main_internal import bootstrap_internal
    bootstrap_internal(app)
except ImportError:
    pass
