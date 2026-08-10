# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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
from http import HTTPStatus

# Must run before any FastAPI import for integration hooks to attach cleanly.
from app.observability.sentry_init import init_sentry

_sentry_enabled = init_sentry()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import neo4j as graph
from app.deps import close_chroma, close_neo4j, close_redis, get_neo4j
from app.middleware.auth import APIKeyMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.tenant_context import TenantContextMiddleware
from app.processor import ProcessorWorker, build_default_registry
from app.processor import router as processor_router_module
from app.routers import (
    a2a,
    agents,
    analytics,
    artifacts,
    atlas_views,
    automations,
    brief_settings,
    briefs,
    chat,
    connectors,
    contradictions,
    digest,
    digests,
    external_apis,
    feedback,
    graph_tour,
    health,
    ingestion,
    kb_admin,
    knowledge_packs,
    mcp_sse,
    meetings,
    memories,
    models,
    oauth,
    observability,
    ollama_proxy,
    plugins,
    pro_automations,
    providers,
    query,
    rag_weights,
    recommendations,
    scanner,
    sdk,
    settings,
    settings_secrets,
    setup,
    sources,
    sync,
    taxonomy,
    updates,
    upload,
    user_state,
    whisper_models,
    wiki,
    workflows,
)
from app.routers import (
    graph as graph_router,
)
from app.routers import (
    license as license_router,
)
from app.scheduler import start_scheduler, stop_scheduler
from config.features import CERID_MULTI_USER
from core.utils.swallowed import log_swallowed_error
from core.utils.version import get_version

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(request_id)s - %(levelname)s - %(message)s",
)
# Attach the request-id filter to every handler so the %(request_id)s
# placeholder always resolves — including for startup / background-task
# logs where no request context exists (will render as "-").
from app.observability.request_id_filter import RequestIdFilter  # noqa: I001
_rid_filter = RequestIdFilter()
for _handler in logging.getLogger().handlers:
    _handler.addFilter(_rid_filter)
logger = logging.getLogger("ai-companion")
logger.info("sentry_tracing=%s", "on" if _sentry_enabled else "off")

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
            "auto_inject_threshold": (0.0, 1.0),
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
        log_swallowed_error('app.main', e)
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
            if resp.status_code == HTTPStatus.OK:
                # Reset ALL OpenRouter-dependent breakers — they share the same
                # upstream and may have tripped from startup DNS/auth races.
                for breaker_name in ("openrouter", "bifrost-verify", "bifrost-claims",
                                     "bifrost-synopsis", "bifrost-memory",
                                     "bifrost-compress", "bifrost-decompose"):
                    get_breaker(breaker_name).reset()
                reset_auth_failure_count()
                logger.info(
                    "Startup OpenRouter auth probe succeeded (attempt %d/%d) — all LLM circuits reset to CLOSED",
                    attempt + 1, max_attempts,
                )
                return
            if resp.status_code == HTTPStatus.UNAUTHORIZED:
                logger.warning(
                    "Startup OpenRouter auth probe: API key rejected (401) — aborting probe loop"
                )
                return
            logger.debug(
                "Startup OpenRouter auth probe attempt %d/%d: HTTP %d",
                attempt + 1, max_attempts, resp.status_code,
            )
        except Exception as exc:
            log_swallowed_error(
                "app.main.openrouter_auth_probe",
                exc,
                context={"attempt": attempt + 1, "max_attempts": max_attempts},
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
        from app.data_sources import registry
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
        log_swallowed_error("app.main.prewarm_external_sources", exc)


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
        except Exception as exc:
            log_swallowed_error('app.main', exc)
            return url

    unreachable: list[str] = []

    for attempt in range(1, _INFRA_MAX_RETRIES + 1):
        unreachable = []
        # --- ChromaDB HTTP check ---
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(chroma_url.rstrip("/") + "/api/v2/heartbeat")
            if r.status_code >= HTTPStatus.BAD_REQUEST:
                unreachable.append(f"ChromaDB ({_host(chroma_url)}: HTTP {r.status_code})")
        except Exception as exc:
            log_swallowed_error('app.main', exc)
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
            except Exception as wc_exc:
                log_swallowed_error(
                    "app.main.check_infra_connectivity_neo4j_close",
                    wc_exc,
                )
        except Exception as exc:
            log_swallowed_error('app.main', exc)
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
            except Exception as wc_exc:
                log_swallowed_error(
                    "app.main.check_infra_connectivity_redis_close",
                    wc_exc,
                )
        except Exception as exc:
            log_swallowed_error('app.main', exc)
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


def _warn_if_unlicensed_pro() -> None:
    """Say so, every boot, when paid features are on without a license.

    Deliberately not a failure and not rate-limited: pinning ``CERID_TIER`` to a
    paid tier is supported for air-gapped and enterprise images, so this must
    not block anyone — but it must also never be mistaken for a licensed
    install, by an operator or by whoever reads the logs after them.
    """
    try:
        from app.deps import get_redis
        from app.routers.license import STATE_UNLICENSED_PRO, entitlement_state
        from utils.license import public_key_is_malformed, verification_enabled

        if public_key_is_malformed():
            # Distinct from "empty": the operator meant to enable verification
            # and the value is unusable. Every key is now rejected, so say that
            # rather than reusing the "is empty" text, which sent operators
            # looking for an unset variable that is in fact set.
            logger.error(
                "license_notice: CERID_LICENSE_PUBLIC_KEY is set but could not be "
                "parsed as an Ed25519 public key — ALL license keys will be rejected. "
                "Re-copy the value; it is base64 of the 32-byte raw key."
            )
        elif not verification_enabled():
            logger.warning(
                "license_notice: CERID_LICENSE_PUBLIC_KEY is empty — license signatures "
                "are NOT verified on this server, so any correctly-shaped key unlocks "
                "paid features. Intended for local preview only; unset the variable to "
                "restore verification."
            )

        if entitlement_state(get_redis()) == STATE_UNLICENSED_PRO:
            logger.warning(
                "license_notice: paid-tier features are enabled by CERID_TIER=%s with no "
                "license key and no trial on this server. This is an UNLICENSED copy of "
                "Cerid Pro. Buy a license at https://cerid.ai/pricing, or start the free "
                "14-day trial in Settings → Plan & Billing.",
                os.getenv("CERID_TIER", "community"),
            )
    except Exception as exc:  # noqa: BLE001 — a notice must never affect boot
        log_swallowed_error('app.main.license_notice', exc)


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

    # Re-derive the tier from persisted entitlement. FEATURE_TIER is read from
    # CERID_TIER at import, so without this an activated license or a running
    # trial silently reverts to the baseline on every restart. Skipped when the
    # commercial router is present — that build runs its own reconcile at boot.
    if not _commercial_billing_present():
        try:
            from app.deps import get_redis
            from app.routers.license import reconcile_license_state

            logger.info("License reconcile at startup — tier %r",
                        reconcile_license_state(get_redis()))
        except Exception as exc:  # noqa: BLE001 — never block boot on entitlement
            logger.warning("License reconcile skipped: %s", exc)

    _warn_if_unlicensed_pro()

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

    # Seed the global private-mode level from the boot env (CERID_PRIVATE_MODE /
    # CERID_PRIVATE_MODE_LEVEL) so a hardened install enforces from boot rather
    # than running at level 0 until the GUI toggle (E1 CR-011). No-op unless the
    # env enables private mode and the Redis key is unset.
    try:
        from app.services.private_mode import seed_private_mode_from_env
        seed_private_mode_from_env()
    except Exception as e:
        log_swallowed_error("app.main.seed_private_mode", e)

    # Startup: initialize Neo4j schema + run migrations
    try:
        driver = get_neo4j()
        graph.init_schema(driver)
        from app.db.neo4j.migrations import backfill_updated_at, register_recategorized_at
        backfill_updated_at(driver)
        register_recategorized_at(driver)
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning(f"Neo4j schema init failed (will retry on first use): {e}")

    # Multi-user POLICY gates — deliberately OUTSIDE the try below.
    #
    # These two `raise`s used to sit inside a `try/except Exception` that logged
    # a warning and continued, so both fail-closed guards were decorative: a
    # deployment with CERID_MULTI_USER=true and no experimental acknowledgement,
    # or with no JWT secret, booted anyway with a line in the log. An `except`
    # that exists to tolerate infrastructure hiccups must not also swallow the
    # refusals the code raises on purpose.
    #
    # The JWT check now runs BEFORE ensure_default_tenant rather than after, so
    # a misconfigured boot refuses before it writes anything.
    import config as _cfg
    if _cfg.CERID_MULTI_USER:
        # Experimental gate: F2 (localStorage tokens) + F3 (missing
        # tenant filter on get_artifact Cypher) must close before
        # multi-user is a supported deploy mode. Operators acknowledging
        # the risk set CERID_MULTI_USER_EXPERIMENTAL=true.
        if not _cfg.CERID_MULTI_USER_EXPERIMENTAL:
            raise RuntimeError(
                "CERID_MULTI_USER=true is gated as EXPERIMENTAL through "
                "v1.0 GA. Two known security gaps (F2 localStorage "
                "tokens, F3 missing tenant_id filter on Neo4j artifact "
                "reads — see tasks/2026-05-24-rc1-beta-test-report.md) "
                "must close first. To proceed at your own risk in a "
                "non-production environment, set "
                "CERID_MULTI_USER_EXPERIMENTAL=true alongside "
                "CERID_MULTI_USER=true."
            )
        if not _cfg.CERID_JWT_SECRET:
            raise RuntimeError(
                "CERID_JWT_SECRET is required when CERID_MULTI_USER=true. "
                "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        logger.warning(
            "Multi-user mode is EXPERIMENTAL in this release — "
            "F2 + F3 security gaps remain open. See "
            "tasks/2026-05-24-rc1-beta-test-report.md."
        )

    # Ensure default tenant exists (for multi-user mode migration safety).
    # This one IS best-effort: a Neo4j hiccup here should not stop the boot.
    try:
        if _cfg.CERID_MULTI_USER:
            from app.db.neo4j.users import ensure_default_tenant
            ensure_default_tenant(driver, _cfg.DEFAULT_TENANT_ID)
            logger.info("Multi-user mode enabled — default tenant ensured")
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning(f"Default-tenant ensure failed: {e}")

    # Auto-import from sync directory if DB is empty
    try:
        from sync_check import auto_import_if_empty
        auto_import_if_empty()
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning(f"Sync auto-import check failed: {e}")

    # Hydrate user settings from sync directory (before logging toggle states)
    _hydrate_settings_from_sync()

    # Log feature toggle states
    from config.features import log_feature_toggles
    log_feature_toggles()

    # Wire the DataSourceRegistry into core/authoritative_verify via DI.
    # Keeps core/ free of runtime app.* imports while still letting the
    # hallucination pipeline query external sources.
    try:
        from app.data_sources import registry as _data_source_registry
        from core.agents.hallucination.authoritative_verify import set_data_source_registry
        set_data_source_registry(_data_source_registry)
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning(f"DataSourceRegistry wiring failed (authoritative verify disabled): {e}")

    # Wire the contradiction-ledger sink into core/verification via DI (same
    # pattern as above — core/ cannot import app.services.contradiction_log).
    # When the NLI guard finds a claim contradicting KB evidence, this persists
    # a ContradictionFinding so the Wiki contradiction surface + weekly synthesis
    # light up. Stable content-derived IDs make re-detection idempotent.
    try:
        from app.services.contradiction_log import ContradictionFinding, log_contradiction
        from core.agents.hallucination.contradiction_sink import set_contradiction_sink, stable_id

        async def _contradiction_sink(
            *,
            claim_text: str,
            source_text: str,
            source_artifact_id: str = "",
            severity: str = "medium",
            entity_slug: str | None = None,
            query_ctx_id: str | None = None,
        ) -> None:
            # Anchor the contradiction to the most-prominent entity mentioned by
            # the contradicting source artifact so it surfaces on that entity's
            # wiki page AND fires the contradiction_detected wiki-refresh event
            # (record_contradiction only writes the HAS_CONTRADICTION edge — the
            # signal knowledge-stats counts — when an entity_slug is present).
            if entity_slug is None and source_artifact_id:
                try:
                    from app.deps import get_neo4j
                    _drv = get_neo4j()
                    if _drv is not None:
                        with _drv.session() as _sess:
                            _row = _sess.run(
                                "MATCH (a:Artifact {id: $aid})-[:MENTIONS]->(e:Entity) "
                                "RETURN e.canonical_id AS slug "
                                "ORDER BY coalesce(e.mention_count, 0) DESC LIMIT 1",
                                aid=source_artifact_id,
                            ).single()
                            if _row and _row.get("slug"):
                                entity_slug = _row["slug"]
                except Exception as _exc:  # noqa: BLE001 — anchor lookup is best-effort
                    log_swallowed_error("app.main.contradiction_sink.entity_lookup", _exc)
            finding = ContradictionFinding(
                finding_id=stable_id(claim_text, source_artifact_id),
                claim_a_id=stable_id(claim_text),
                claim_b_id=stable_id(source_artifact_id or source_text),
                claim_a_text=claim_text[:1000],
                claim_b_text=source_text[:1000],
                entity_slug=entity_slug,
                severity=severity,  # type: ignore[arg-type]
                query_ctx_id=query_ctx_id,
                source_artifacts=[source_artifact_id] if source_artifact_id else [],
            )
            await log_contradiction(finding)

        set_contradiction_sink(_contradiction_sink)
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning(f"Contradiction-ledger sink wiring failed (ledger disabled): {e}")

    # Metamorphic scoring (Pro) — same DI seam. The scorer's import interface is
    # app-side, the only sensible caller (the streaming verifier) is core-side,
    # and without this wiring the feature has no caller at all despite shipping
    # a plugin, a TIER_MATRIX row and a frontend type.
    try:
        from app.agents.hallucination.metamorphic import metamorphic_score
        from core.agents.hallucination.metamorphic_sink import set_metamorphic_sink

        set_metamorphic_sink(metamorphic_score)
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning(f"Metamorphic sink wiring failed (scoring disabled): {e}")

    # Wire the connector ingest sink into core/ingest via DI (same pattern) so
    # SourceConnector.fetch_since can persist fetched feed entries via the real
    # ingest_content without a core→app import. Powers the source_poll worker.
    try:
        from app.services.ingestion import ingest_content as _ingest_content
        from core.ingest.sources.ingest_sink import set_source_ingest_fn

        async def _source_ingest_fn(
            content: str, *, domain: str = "general", metadata: dict | None = None,
        ) -> str | None:
            res = await asyncio.to_thread(
                _ingest_content, content=content, domain=domain, metadata=metadata or {},
            )
            return res.get("artifact_id") if isinstance(res, dict) else None

        set_source_ingest_fn(_source_ingest_fn)
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning(f"Source ingest-sink wiring failed (connector polling disabled): {e}")

    # Wire the Phase J/K agent DI seams (inbox triage + daily digest). core/
    # must not import app/, so app injects the registry + graph accessor here at
    # startup. Defined once in app/agents_di.py so startup + tests wire the
    # identical seam (no divergent copies).
    try:
        from app.agents_di import (
            wire_crag_external_di,
            wire_daily_digest_di,
            wire_inbox_triage_di,
        )

        wire_inbox_triage_di()
        wire_daily_digest_di()
        wire_crag_external_di()
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning(f"Agent DI wiring failed (inbox triage / daily digest / CRAG disabled): {e}")

    # Phase K2.1 — wire the entity-extraction enqueue callback into
    # core.agents.memory so freshly-stored memories trigger graph
    # entity upserts (and the K1.3 wiki refresh chain). Keeps core/
    # free of app.* imports via DI, same pattern as authoritative_verify.
    try:
        from app.db.redis.processor_queue import enqueue_job
        from app.processor.jobs.entity_extraction import EntityExtractionJob
        from core.agents.memory import set_entity_extraction_enqueue

        def _enqueue_memory_entity_extraction(artifact_id: str) -> None:
            val = os.environ.get(
                "CERID_MEMORY_ENTITY_EXTRACTION_ENABLED", "true",
            ).strip().lower()
            if val not in ("true", "1", "yes", "on"):
                return
            payload = {"artifact_id": artifact_id, "tenant_id": "default"}
            enqueue_job(EntityExtractionJob(**payload), payload=payload)

        set_entity_extraction_enqueue(_enqueue_memory_entity_extraction)
    except Exception as e:
        log_swallowed_error('app.main', e)

    # Let log_swallowed_error reach Redis without every call site passing a
    # client — only ~25 of 1,000+ sites did, so the counters behind
    # /health.swallowed_errors_last_hour were never written.
    try:
        from app.deps import get_redis as _get_redis_for_swallowed
        from core.utils.swallowed import set_swallowed_redis_sink

        set_swallowed_redis_sink(_get_redis_for_swallowed)
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning(f"Memory→entity extraction wiring failed: {e}")

    # GA P0.5 C2 — wire the compiled-wiki fetcher so surface-biased retrieval can
    # prepend an entity's wiki page for "what is X" (compiled_summary) queries.
    # Same core↛app DI pattern; the fetcher resolves the entity hint to a slug and
    # reads the cached summary from Neo4j. Graceful: returns None on miss so C2 is
    # a no-op when the entity has no page.
    # Extracted to app.startup.surface_wiring so eval harnesses and scripts that
    # drive the answer path outside this process wire the SAME fetcher. Inline
    # here, they silently ran with the wiki surface disabled and measured a
    # degraded path that looked healthy.
    from app.startup.surface_wiring import wire_query_surfaces

    wire_query_surfaces()

    # Load plugins
    try:
        from plugins import load_plugins
        loaded = load_plugins()
        if loaded:
            logger.info(f"Plugins loaded: {', '.join(loaded)}")
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning(f"Plugin loading failed (server runs without plugins): {e}")

    # Start scheduled maintenance engine
    try:
        start_scheduler()
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning(f"Scheduler start failed (server runs without it): {e}")

    # Register user-facing automations with scheduler
    try:
        from app.routers.automations import register_all_automations
        register_all_automations()
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning(f"Automation registration failed (server runs without it): {e}")

    # Start background processor worker
    try:
        from app.db.redis.processor_queue import RedisJobQueue
        from app.deps import get_redis as _proc_get_redis
        from config import settings as _proc_settings
        _proc_redis = _proc_get_redis()
        _proc_queue = RedisJobQueue(_proc_redis)
        _proc_registry = build_default_registry()
        _proc_load_ceiling: float | None = None
        _raw_ceiling = (_proc_settings.WORKER_LOAD_CEILING or "").strip().lower()
        if _raw_ceiling and _raw_ceiling != "auto":
            try:
                _proc_load_ceiling = float(_raw_ceiling)
            except ValueError as exc:
                log_swallowed_error("app.main", exc)
        _proc_worker = ProcessorWorker(
            _proc_queue,
            _proc_registry,
            redis_client=_proc_redis,
            load_ceiling=_proc_load_ceiling,
        )
        await _proc_worker.start()
        app.state.processor_worker = _proc_worker
        app.state.processor_queue = _proc_queue
        logger.info("ProcessorWorker started (%d job types registered)", len(_proc_registry))
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning(f"ProcessorWorker start failed (server runs without it): {e}")

    # Wire brief scheduler (N.1) — enqueues BriefGenerationJob daily and
    # WeeklySynthesisJob weekly through the processor. Best-effort; failure
    # to register doesn't block boot.
    try:
        if hasattr(app.state, "processor_queue"):
            from app.scheduler import get_scheduler
            from app.services.briefs.scheduler import (
                schedule_daily_brief,
                schedule_weekly_synthesis,
            )
            _brief_scheduler = get_scheduler()
            if _brief_scheduler is not None:
                schedule_daily_brief(_brief_scheduler, app.state.processor_queue)
                schedule_weekly_synthesis(_brief_scheduler, app.state.processor_queue)
                logger.info("Brief scheduler wired (daily + weekly enqueue cron live)")
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning(f"Brief scheduler wiring failed (server runs without briefs): {e}")

    # Pre-warm connections and models for faster first request
    try:
        import config
        from app.deps import get_chroma
        from config.taxonomy import collection_name
        chroma = get_chroma()
        # AF-033: live config.DOMAINS (the canonical runtime list rehydration +
        # POST /taxonomy/domain reassign) rather than the import-time
        # config.taxonomy.DOMAINS snapshot, so runtime domains pre-warm too.
        domains = config.DOMAINS
        for domain in domains:
            chroma.get_or_create_collection(name=collection_name(domain))
        # Also pre-warm conversations collection (used by memory recall)
        chroma.get_or_create_collection(name="domain_conversations")
        logger.info("ChromaDB + embedding model pre-warmed (%d domain collections)", len(domains))
    except Exception as e:
        log_swallowed_error("app.main.prewarm_chroma", e)

    # Pre-warm Redis: functional PING beyond TCP connectivity check
    try:
        from app.deps import get_redis as _get_redis
        _get_redis().ping()
        logger.info("Redis PING pre-warm passed")
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning("Redis PING pre-warm failed (cache may be unavailable): %s", e)

    # Phase O.2 (v0.92): bind memory consolidation failure callback.
    # core/ must never import app/, so we wire the DI callback from here
    # after Redis is known to be available.
    try:
        import functools

        import core.agents.memory_consolidation as _mc_mod
        from app.deps import get_redis as _cb_get_redis
        from app.services.memory_metrics import record_consolidation_failure as _rcf

        _cb_redis = _cb_get_redis()
        _mc_mod.consolidation_failure_callback = functools.partial(_rcf, _cb_redis)
        logger.info("Memory consolidation failure callback registered")
    except Exception as e:
        log_swallowed_error("app.main.memory_consolidation_failure_callback_bind", e)
        logger.warning("Memory consolidation failure callback not registered: %s", e)

    # Wire the semantic-cache backend: a dedicated chroma collection
    # ("semantic_query_cache"). The cache module stays layering-correct
    # (no chromadb import in core/) by accepting the collection via
    # set_cache_backend. Skipped when the feature flag is off so we
    # don't materialise the collection on disk unnecessarily.
    try:
        from config.features import ENABLE_SEMANTIC_CACHE
        if ENABLE_SEMANTIC_CACHE:
            from app.deps import get_semantic_cache_collection
            from core.retrieval.semantic_cache import set_cache_backend
            set_cache_backend(get_semantic_cache_collection())
            logger.info("Semantic cache backend registered (chroma collection)")
    except Exception as e:
        log_swallowed_error("app.main.semantic_cache_backend_init", e)

    # Bump the monotonic restart counter so /observability/restarts can
    # tell consumers (trading-agent etc.) when MCP last booted. Best-
    # effort — Redis-down doesn't gate startup. (Workstream A Phase 1.3.)
    from app.routers.observability import increment_restart_counter
    increment_restart_counter()

    # Pre-warm LLM client pool (direct OpenRouter)
    try:
        from core.utils.llm_client import _get_client
        await _get_client()
        logger.info("OpenRouter HTTP client pool pre-warmed")
    except Exception as e:
        log_swallowed_error("app.main.prewarm_openrouter_client", e)

    # Background: probe OpenRouter auth with exponential backoff.
    # Resets the circuit breaker on first success so startup 401s (DNS/auth not
    # yet stabilised) do not block LLM features for the full recovery timeout.
    if os.getenv("OPENROUTER_API_KEY"):
        asyncio.ensure_future(_openrouter_auth_probe_loop())

    # E1 CR-008: project the persisted BYOK direct-provider enablement into the
    # canonical (env) plane so both transports (chat + call_llm) dispatch direct
    # keys after a bare restart — not only after a PUT /providers/config. Without
    # this, the BYOK_DIRECT_PROVIDERS marker is empty on boot and a configured
    # direct key silently reverts to OpenRouter. Best-effort: Redis-down must not
    # gate startup.
    try:
        from app.deps import get_redis
        from core.routing.model_providers import enabled_direct_providers, load_config
        from core.routing.provider_state import project_byok_env
        # Name must not shadow earlier ``import config as _cfg`` in this function
        # (mypy treats that as Module for the whole scope).
        provider_cfg = load_config(get_redis())
        project_byok_env(enabled_direct_providers(provider_cfg))
        # E1 R10 / CR-099: re-project persisted local-backend URLs onto the env
        # plane at boot (BYOK keys already projected above). Without this, a
        # Settings-saved quenchforge/ollama URL works until restart then reverts.
        for _pname, _envk in (("quenchforge", "QUENCHFORGE_URL"), ("ollama", "OLLAMA_URL")):
            _st = provider_cfg.providers.get(_pname)
            if _st is not None and getattr(_st, "url", None):
                os.environ[_envk] = _st.url
    except Exception as e:
        log_swallowed_error("app.main.project_byok_env", e)

    # Bifrost was fully retired (audit C-4 + follow-up 2026-04-17). Chat,
    # smart-router, and the last pipeline callers (topic extraction,
    # contextual chunking, maintenance health probes) all route direct to
    # OpenRouter via core.utils.llm_client.call_llm. No pre-warm required.

    # Pre-warm Ollama client pool (for pipeline tasks). Gate on the env plane:
    # OLLAMA_ENABLED is an env var, never a config-module attribute, so the old
    # getattr(config, "OLLAMA_ENABLED", False) gate was always False and this
    # pre-warm was dead code even with OLLAMA_ENABLED=true (E1 CR-109).
    from core.routing.provider_state import ollama_enabled

    if ollama_enabled():
        try:
            from core.utils.internal_llm import _get_ollama_client
            await _get_ollama_client()
            logger.info("Ollama HTTP client pool pre-warmed")
        except Exception as e:
            log_swallowed_error("app.main.prewarm_ollama_client", e)

    # Pre-warm reranker ONNX model (avoids 2-3s delay on first query).
    # run_in_executor keeps the event loop (and uvicorn) responsive while ONNX
    # loads — without this the loop blocks for several seconds on cold start.
    try:
        from core.retrieval.reranker import warmup as reranker_warmup
        await asyncio.get_running_loop().run_in_executor(None, reranker_warmup)
        logger.info("Reranker ONNX model pre-warmed")
    except Exception as e:
        log_swallowed_error("app.main.prewarm_reranker", e)

    # E1 CR-072: run the model-registry validation its module docstring promises
    # ("auto-validates against OpenRouter on startup"). It was never wired, so the
    # pricing cache stayed empty and deprecated model ids went undetected. Fire it
    # in the background so a slow/offline OpenRouter never delays boot —
    # validate_models degrades gracefully to hardcoded defaults on network failure.
    try:
        from utils.model_registry import validate_models
        asyncio.create_task(validate_models())
    except Exception as e:
        log_swallowed_error("app.main.model_registry_validate", e)

    # Pre-warm embedding model (ONNX inference session)
    try:
        from core.utils.embeddings import get_embedding_function
        ef = get_embedding_function()
        if ef:
            await asyncio.get_running_loop().run_in_executor(None, ef, ["warmup"])
            logger.info("Embedding ONNX model pre-warmed")
    except Exception as e:
        log_swallowed_error("app.main.prewarm_embedding_model", e)

    # Validate collection embedding dimensions against the configured embedder.
    # Dim-locks inside existing Chroma collections are a silent landmine — we
    # surface them at boot with a remediation pointer rather than blowing up
    # on first ingest. Runs after pre-warm so ef.load() has happened.
    try:
        from app.startup import run_startup_dim_check
        await asyncio.get_running_loop().run_in_executor(None, run_startup_dim_check)
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning("Startup dim check errored (non-fatal): %s", e)

    # CL-7/AF-012: restore operator-created :Domain nodes into runtime config so a
    # domain added via POST /taxonomy/domain survives a restart — without this its
    # live consumers (ingest domain-clamp, clear_domain, reembed) reverted to the
    # static domain set and orphaned the custom collection.
    try:
        # get_neo4j is already module-imported (top of file); a local re-import
        # here would shadow it and break the earlier lifespan use (F823).
        from app.startup.domain_rehydration import rehydrate_runtime_domains
        await asyncio.get_running_loop().run_in_executor(
            None, rehydrate_runtime_domains, get_neo4j(),
        )
    except Exception as e:
        log_swallowed_error('app.main', e)
        logger.warning("Domain rehydration errored (non-fatal): %s", e)

    # Warm up NLI model — the slow one (~45s on cold start due to model download).
    # Must use run_in_executor: running it directly blocked the event loop for the
    # entire download duration, causing healthcheck timeouts during startup.
    try:
        from core.utils.nli import warmup as nli_warmup
        await asyncio.get_running_loop().run_in_executor(None, nli_warmup)
    except Exception as exc:
        log_swallowed_error('app.main', exc)
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

    # MCP SSE session reaper — evicts sessions idle longer than
    # _IDLE_TIMEOUT_S (5 minutes by default). Started here so it
    # shares the lifespan; cancelled on shutdown via the stored task
    # ref so the cleanup is deterministic.
    _mcp_reaper_task = asyncio.create_task(mcp_sse._session_reaper())
    app.state.mcp_reaper_task = _mcp_reaper_task

    # Register sibling MCP connectors (Phase F). The pool's circuit
    # breaker handles "server not running yet" gracefully; we register
    # the URL + bearer here so the first plugin call_tool succeeds
    # if the connector stack is up.
    try:
        from config import settings as _conn_settings
        from core.mcp_clients.client_pool import get_pool

        if _conn_settings.CERID_CONNECTORS_BEARER:
            headers = {"Authorization": f"Bearer {_conn_settings.CERID_CONNECTORS_BEARER}"}
            get_pool().register(
                "google_workspace",
                _conn_settings.GOOGLE_WORKSPACE_MCP_URL,
                headers=headers,
            )
            get_pool().register(
                "ms365",
                _conn_settings.MS365_MCP_URL,
                headers=headers,
            )
            logger.info(
                "Registered sibling MCP connectors: google_workspace, ms365",
            )
        else:
            logger.debug(
                "CERID_CONNECTORS_BEARER unset — Pro cloud connectors not registered",
            )
    except Exception as exc:
        log_swallowed_error("app.main.lifespan.register_sibling_mcp", exc)

    # F-PERF-04: pre-warm the /health cache so the first request after
    # boot doesn't pay the ~700ms cold-cache cost while concurrent
    # /agent/query loads compete for the executor's thread pool.
    try:
        import app.routers.health as _health_mod
        from app.routers.health import _build_health_payload
        _health_mod._health_cache = await asyncio.to_thread(_build_health_payload)
        _health_mod._health_cache_ts = time.monotonic()
        logger.info("health cache pre-warmed at startup")
    except Exception as exc:
        log_swallowed_error("app.main.lifespan.health_prewarm", exc)

    yield

    # Cancel SSE reaper before tearing down sessions.
    try:
        app.state.mcp_reaper_task.cancel()
        await app.state.mcp_reaper_task
    except (asyncio.CancelledError, AttributeError):
        pass

    # Disarm watchdog before shutdown tasks run (avoid spurious SIGTERM during
    # intentional slow-shutdown operations like cache flush).
    _watchdog_stop.set()

    # Shutdown: disconnect sibling MCP clients (Phase F).
    try:
        from core.mcp_clients.client_pool import get_pool

        await get_pool().disconnect_all()
    except Exception as exc:
        log_swallowed_error('app.main', exc)
        logger.warning("MCPClientPool disconnect failed: %s", exc)

    # Shutdown: stop background processor worker
    try:
        if hasattr(app.state, "processor_worker"):
            await app.state.processor_worker.stop()
    except Exception as exc:
        log_swallowed_error('app.main', exc)
        logger.warning("ProcessorWorker shutdown failed: %s", exc)

    # Shutdown: stop scheduler, flush caches, close connections, clear MCP sessions
    try:
        stop_scheduler()
    except Exception as exc:
        log_swallowed_error('app.main', exc)
        logger.warning("Scheduler shutdown failed: %s", exc)
    try:
        from core.utils.llm_client import close_client
        await close_client()
    except Exception as exc:
        log_swallowed_error('app.main', exc)
        logger.warning("LLM client shutdown failed: %s", exc)
    try:
        from app.routers.chat import close_chat_client
        await close_chat_client()
    except Exception as exc:
        log_swallowed_error('app.main', exc)
        logger.warning("Chat client shutdown failed: %s", exc)
    try:
        from core.utils.internal_llm import close_ollama_client
        await close_ollama_client()
    except Exception as exc:
        log_swallowed_error('app.main', exc)
        logger.warning("Ollama client shutdown failed: %s", exc)
    try:
        from app.routers.providers import close_openrouter_client
        await close_openrouter_client()
    except Exception as exc:
        log_swallowed_error('app.main', exc)
        logger.warning("OpenRouter credit-probe client shutdown failed: %s", exc)
    # Extension shutdown hooks (registered by bootstrap)
    for _hook in _shutdown_hooks:
        try:
            await _hook()
        except Exception as exc:
            log_swallowed_error('app.main', exc)
            logger.warning("Extension shutdown hook failed: %s", exc)
    # Semantic cache: clear the registered backend handle so any late
    # callers hit the disabled path. flush_cache itself is now a no-op
    # (chromadb persists every upsert immediately) but is retained as a
    # public-API hinge.
    try:
        from app.deps import get_redis
        from core.retrieval.semantic_cache import flush_cache, set_cache_backend
        flush_cache(get_redis())
        set_cache_backend(None)
    except Exception as exc:
        log_swallowed_error("app.main.shutdown_semantic_cache_flush", exc)
    close_neo4j()
    close_chroma()
    close_redis()
    mcp_sse.clear_sessions()


app = FastAPI(
    title="AI Companion MCP Server",
    version=get_version(),
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

# Register routers at root. The legacy `/api/v1/*` dual mount was retired
# (Task 16): it double-registered every route (185 duplicates in openapi.json)
# and served no purpose — callers using /api/v1/* should be redirected to
# root paths by the nginx proxy in a follow-up. `/sdk/v1/*` endpoints live at
# their own prefix intentionally — they are the stable external contract and
# are registered separately below.
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
    knowledge_packs.router,
    user_state.router,
    plugins.router,
    scanner.router,
    workflows.router,
]
for r in _api_routers:
    app.include_router(r)

# Setup, provider, and model assignment routers — first-run wizard and BYOK configuration
app.include_router(setup.router)
app.include_router(settings_secrets.router)
# R4-1 security invariant: redact 'input' from all FastAPI 422 validation error
# responses so that a mis-typed API key is never echoed back to the caller.
settings_secrets.register_redacted_validation_handler(app)

# Render any uncaught CeridError as structured JSON instead of a bare 500
# (audit Cluster 4 / CEG-1/CEG-2). Defined in app/error_handlers.py so startup +
# tests register the identical handler.
from app.error_handlers import register_cerid_error_handler

register_cerid_error_handler(app)
app.include_router(providers.router)
app.include_router(models.router)

# Observability dashboard API (real-time metrics, health score, cost, quality)
app.include_router(observability.router)

# Background processor control API (queue status, pause/resume, recent jobs)
app.include_router(processor_router_module)

# Wiki API — entity pages and contradiction ledger (Phase W)
app.include_router(contradictions.router)
app.include_router(wiki.router)

# Graph visualization API — Atlas / Constellation / Timeline data
# (Cerid v1.0 Phase A — 2026-05-21 systemic plan).
# Aliased to graph_router because `graph` is already the alias for
# app.db.neo4j on line 26.
app.include_router(graph_router.router)

# Atlas saved views — per-user named graph configurations (Phase A Day 12).
app.include_router(atlas_views.router)

# Constellation tour mode — LLM-narrated camera arc (Phase B Day 7).
app.include_router(graph_tour.router)

# Offline license activation + self-serve trial. The commercial build ships a
# Stripe-backed router owning the same Redis keys; mounting both would let two
# writers race on cerid:license:status, so this one stands down when that one
# is present (that build registers its own router later during bootstrap).
def _commercial_billing_present() -> bool:
    """True when the internal Stripe router is importable in this build."""
    import importlib.util

    try:
        return importlib.util.find_spec("routers.billing") is not None
    except (ImportError, AttributeError, ValueError):
        # No `routers` package at all (the public tree strips it wholesale) —
        # find_spec raises rather than returning None when the PARENT is missing.
        return False


if not _commercial_billing_present():
    app.include_router(license_router.router)

# Whisper model download manager — Phase E Day 3.
app.include_router(whisper_models.router)

# Meeting capture orchestration — Phase E Day 4.
app.include_router(meetings.router)

# Cloud connector OAuth + status surface (Phase F.2 cleanup).
app.include_router(connectors.router)

# Source-management surface backing the F1/F2/F3 wizard flow.
# Side-effect-imports the connector package so register_connector
# calls run at process boot.
import core.ingest.sources.connectors  # noqa: F401, E402

app.include_router(sources.router)

# Phase 3 (B3.2 / B3.3) — Pro connector OAuth entry + callback.
app.include_router(oauth.router)

# Custom Smart RAG weights surface (Phase I).
app.include_router(rag_weights.router)

# Daily digest surface (Phase K).
app.include_router(digests.router)

# Pro-tier feature automation runtime overrides (UX consolidation).
app.include_router(pro_automations.router)

# Advanced analytics — Phase L (heatmap + sankey + quality timeline).
app.include_router(analytics.router)

# Brief scheduler settings (RAG C3.4) — vault-write toggle for daily +
# weekly synthesis jobs.  Lives under /briefs/* so the scheduler is the
# obvious consumer; routes mounted here, not on the user-state router.
app.include_router(brief_settings.router)

# Briefs read API (Task 2.1a) — GET /briefs + GET /briefs/{brief_id}.
# Included after brief_settings so the static /briefs/settings route is
# matched before this router's dynamic /briefs/{brief_id}.
app.include_router(briefs.router)

# Adaptive recommendations dismiss/clear (C3.2). The read-side surfaces
# via /health.recommended_features; this router only owns the write
# side for per-tenant dismissals.
app.include_router(recommendations.router)

# External public-API adapter management (Phase API.1 + API.2)
app.include_router(external_apis.router)

# App-version update check (ST10)
app.include_router(updates.router)

# Ollama local LLM proxy (always registered; endpoints gate on OLLAMA_ENABLED)
app.include_router(ollama_proxy.router)

# SDK router — stable external contract (manages its own /sdk/v1/ prefix)
app.include_router(sdk.router)

# Per-claim user feedback (Phase R.1) — additive endpoint under /sdk/v1/
app.include_router(feedback.router)

# A2A router — Agent Card at /.well-known/agent.json, tasks at /a2a/* (no prefix)
app.include_router(a2a.router)

# MCP transport stays at root only (not versioned)
app.include_router(mcp_sse.router)

# Auth router (only when multi-user mode is enabled)
if CERID_MULTI_USER:
    from app.routers import auth as auth_router
    app.include_router(auth_router.router)

# Former bridge-layer routers (Sprint F.2 of the 2026-04-19 consolidation
# program moved them from src/mcp/routers/ to src/mcp/app/routers/).
# src/mcp/routers/ now holds only billing.py, which stays there because
# .sync-manifest.yaml strips it for the public distribution.
from app.routers import (  # noqa: E402,I001
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

_legacy_routers = [
    data_sources.router,
    watched_folders.router,
    system_monitor.router,
    dlq.router,
    webhook_subscriptions.router,
    agent_console.router,
    agent_console.activity_router,
    custom_agents.router,
    mcp_client.router,
    plugin_registry.router,
    widget.router,
]
for r in _legacy_routers:
    app.include_router(r)

# SDK OpenAPI spec (serves at /sdk/v1/openapi.json — no versioned prefix needed)
app.include_router(sdk_openapi.router)


@app.get("/")
def root():
    return {"service": "AI Companion MCP Server", "version": get_version(), "status": "running"}
