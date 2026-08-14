# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Health check and collection listing endpoints."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from http import HTTPStatus
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.deps import get_chroma, get_neo4j, get_redis
from core.utils.swallowed import log_swallowed_error
from core.utils.version import get_version
from utils.encryption import CHROMA_ENCRYPTED_FIELDS, get_encryptor


# --- Response models (generated: single-return dict-literal routes) ---
class LivenessProbeResponse(BaseModel):
    status: str


class HealthPingResponse(BaseModel):
    ok: bool



router = APIRouter()
logger = logging.getLogger("ai-companion")


@router.get("/health/ping", include_in_schema=False, response_model=HealthPingResponse)
async def health_ping() -> dict:
    """Lightweight liveness probe — no DB checks, used by Docker healthcheck."""
    return {"ok": True}


# In-memory health cache — avoids blocking I/O on every poll
_health_cache: dict = {}
_health_cache_ts: float = 0.0
_HEALTH_CACHE_TTL = 30.0  # seconds

# F-PERF-04: coalesce concurrent cache-miss builds. Without this, a thundering
# herd of /health requests on cold cache would each spawn its own
# asyncio.to_thread(_build_health_payload) and saturate the default executor's
# thread pool — pushing /health p95 to 5-8s under load. The lock-protected
# in-flight future shares one build across all concurrent waiters.
_health_build_lock: asyncio.Lock | None = None
_health_build_inflight: asyncio.Future | None = None


def _get_health_build_lock() -> asyncio.Lock:
    """Lazily construct the lock so it binds to the running event loop."""
    global _health_build_lock
    if _health_build_lock is None:
        _health_build_lock = asyncio.Lock()
    return _health_build_lock


async def _refresh_health_cache() -> dict:
    """Rebuild the cached health payload off the event loop.

    Always runs the build via asyncio.to_thread so blocking I/O lands
    on the default executor. Updates _health_cache/_health_cache_ts on
    success. Failures are swallowed (with breadcrumb) — the previous
    stale payload remains in cache, which is strictly better than
    returning an empty response.
    """
    global _health_cache, _health_cache_ts
    try:
        payload = await asyncio.to_thread(_build_health_payload)
        _health_cache = payload
        _health_cache_ts = time.monotonic()
        return payload
    except Exception as exc:  # noqa: BLE001 — refresh boundary
        log_swallowed_error("app.routers.health.refresh_health_cache", exc)
        return _health_cache or {"services": {}, "invariants": {}}


def health_check() -> dict:
    """Public — also called by mcp_sse.py execute_tool."""
    status = {"chromadb": "unknown", "redis": "unknown", "neo4j": "unknown"}
    try:
        get_chroma()
        status["chromadb"] = "connected"
    except Exception as exc:
        log_swallowed_error('app.routers.health', exc)
        status["chromadb"] = f"error: {exc}"
    try:
        get_redis()
        status["redis"] = "connected"
    except Exception as exc:
        log_swallowed_error('app.routers.health', exc)
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
        log_swallowed_error('app.routers.health', exc)
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
            models = [m.get("name", "") for m in resp.json().get("models", [])] if resp.status_code == HTTPStatus.OK else []
            ollama_status = {"reachable": True, "models": len(models), "url": ollama_url}
        except Exception as exc:
            log_swallowed_error('app.routers.health', exc)
            ollama_status = {"reachable": False, "url": ollama_url}

    # Embedding-cache stats — read-only, no side effects. Cheap (single
    # locked dict copy). Lets operators verify the cache is doing work
    # rather than degenerating to 0% hit-rate after a config flip.
    embedding_cache_stats: dict[str, Any]
    try:
        from core.utils.embedding_cache import get_embedding_cache
        embedding_cache_stats = dict(get_embedding_cache().stats())
    except Exception as exc:
        log_swallowed_error("app.routers.health.embedding_cache_stats", exc)
        embedding_cache_stats = {"error": "stats_unavailable"}

    # Phase K6.1 — wiki freshness metrics. Cheap aggregation Cypher,
    # so we can include in every health probe. Exposes coverage,
    # p95 staleness, debounce backlog, and unresolved contradictions
    # — the headline numbers the design doc §9 calls out.
    wiki_health: dict[str, Any]
    try:
        wiki_health = _wiki_freshness_snapshot(get_neo4j())
    except Exception as exc:
        log_swallowed_error("app.routers.health.wiki_freshness_snapshot", exc)
        wiki_health = {"error": "snapshot_failed"}

    # §7.1 regression guard — surface the resolved knowledge-pack registry
    # path + pack count so a path-resolution break (the historical "registry
    # serves 0" failure) is visible in every health probe instead of only
    # showing up as an empty /knowledge_packs/registry response downstream.
    knowledge_packs_health = _knowledge_packs_snapshot()

    result: dict = {
        "status": "healthy" if all(v == "connected" for v in status.values()) else "degraded",
        "version": get_version(),
        "services": status,
        "circuit_breakers": {
            "ollama": ollama_cb_state,
            "openrouter": openrouter_cb_state,
        },
        "openrouter_credits_exhausted": credits_exhausted,
        "embedding_cache": embedding_cache_stats,
        "wiki_freshness": wiki_health,
        "knowledge_packs": knowledge_packs_health,
    }
    result["encryption"] = {
        # get_encryptor() reflects whether encryption is actually operational —
        # a malformed CERID_ENCRYPTION_KEY fails FieldEncryptor construction and
        # get_encryptor() returns None, whereas is_encryption_enabled() only
        # checks that the env var is set, which would report a false positive.
        "key_present": get_encryptor() is not None,
        "fields_covered": len(CHROMA_ENCRYPTED_FIELDS),
    }
    if ollama_status is not None:
        result["ollama"] = ollama_status
    return result


def _knowledge_packs_snapshot() -> dict:
    """§7.1 — knowledge-pack registry health for the health probe.

    Resolves the registry path the same way the serving endpoint does
    (``default_registry_path`` → honours ``CERID_KNOWLEDGE_PACKS_REGISTRY``)
    and reports whether it exists + how many packs it holds. ``count == 0``
    with ``exists == False`` is the signature of the path-resolution
    regression this guard exists to catch.
    """
    try:
        from app.services.knowledge_packs import default_registry_path
        from core.knowledge.packs import load_registry

        path = default_registry_path()
        exists = path.exists()
        count = len(load_registry(path)) if exists else 0
        return {
            "registry_path": str(path),
            "registry_exists": exists,
            "pack_count": count,
            "ok": exists and count > 0,
        }
    except Exception as exc:
        log_swallowed_error("app.routers.health.knowledge_packs_snapshot", exc)
        return {"ok": False, "error": "snapshot_failed"}


def _wiki_freshness_snapshot(driver) -> dict:
    """Phase K6.1 — knowledge architecture freshness metrics.

    Single Cypher query returning:
      * total_entities — denominator for coverage
      * entities_with_summary — numerator for coverage
      * entities_active — entities with mention_count >= 5
      * entities_active_with_summary — coverage among active entities
      * p95_summary_age_hours — staleness for active entities with summaries
      * unresolved_contradictions — :HAS_CONTRADICTION edges to entities
        whose summary_updated_at is older than the latest finding
    """
    if driver is None:
        return {"available": False, "reason": "neo4j_unavailable"}

    try:
        with driver.session() as session:
            # Coverage + active counts
            cov = session.run(
                """
                MATCH (e:Entity)
                WITH count(e) AS total,
                     sum(CASE WHEN e.summary IS NOT NULL THEN 1 ELSE 0 END) AS with_summary,
                     sum(CASE WHEN coalesce(e.mention_count, 0) >= 5 THEN 1 ELSE 0 END) AS active,
                     sum(CASE WHEN coalesce(e.mention_count, 0) >= 5 AND e.summary IS NOT NULL THEN 1 ELSE 0 END) AS active_with_summary
                RETURN total, with_summary, active, active_with_summary
                """
            ).single()
            total = int(cov["total"] or 0) if cov else 0
            with_summary = int(cov["with_summary"] or 0) if cov else 0
            active = int(cov["active"] or 0) if cov else 0
            active_with_summary = int(cov["active_with_summary"] or 0) if cov else 0

            # Unresolved contradictions
            unresolved = session.run(
                """
                MATCH (e:Entity)-[:HAS_CONTRADICTION]->(f:ContradictionFinding)
                WHERE e.summary IS NULL
                   OR e.summary_updated_at IS NULL
                   OR e.summary_updated_at < f.detected_at
                RETURN count(DISTINCT e) AS c
                """
            ).single()
            unresolved_count = int(unresolved["c"] or 0) if unresolved else 0

            # Wiki log activity in the last 24h
            from datetime import datetime, timedelta, timezone

            last_day = (datetime.now(tz=timezone.utc) - timedelta(hours=24)).isoformat()
            activity = session.run(
                "MATCH (k:KnowledgeLog) WHERE k.ts >= $since RETURN count(k) AS c",
                since=last_day,
            ).single()
            log_activity_24h = int(activity["c"] or 0) if activity else 0

        coverage_pct = (
            round(100.0 * with_summary / total, 1) if total else 0.0
        )
        active_coverage_pct = (
            round(100.0 * active_with_summary / active, 1) if active else 0.0
        )

        return {
            "available": True,
            "total_entities": total,
            "entities_with_summary": with_summary,
            "coverage_pct": coverage_pct,
            "active_entities": active,
            "active_entities_with_summary": active_with_summary,
            "active_coverage_pct": active_coverage_pct,
            "unresolved_contradictions": unresolved_count,
            "log_activity_24h": log_activity_24h,
        }
    except Exception as exc:
        log_swallowed_error("app.routers.health.wiki_freshness_query", exc)
        return {"available": False, "reason": "query_failed"}


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
    except Exception as exc:
        log_swallowed_error('app.routers.health', exc)
        tier = "unknown"
    base["degradation_tier"] = tier
    base["uptime_seconds"] = int(time.time() - _start_time)
    base.setdefault("features", {})

    # Pipeline provider routing — tells the frontend which tasks use local models.
    # E1 CR-024: resolve the provider + local-ness through the one authority so a
    # quenchforge deployment (also local, on :11434) is not reported as cloud.
    # This table is a *configuration* signal (which provider is set to serve each
    # stage); daemon liveness is surfaced separately (degradation_tier +
    # inference_routing's inference_health annotation), so it does not gate here.
    from core.routing.provider_state import active_provider, is_local_provider
    provider = active_provider()
    is_local = is_local_provider(provider)
    base["pipeline_providers"] = {
        "claim_extraction": provider if is_local else "openrouter",
        "query_decomposition": provider if is_local else "openrouter",
        "topic_extraction": provider if is_local else "openrouter",
        "memory_resolution": provider if is_local else "openrouter",
        "reranking": provider if is_local else "openrouter",
        "verification_simple": provider if is_local else "openrouter",
        "verification_complex": provider if is_local else "openrouter",
        "chat_generation": provider if is_local else "openrouter",
    }
    try:
        base["can_retrieve"] = mgr.can_retrieve()
        base["can_verify"] = mgr.can_verify()
        base["can_generate"] = mgr.can_generate()
    except Exception as exc:
        log_swallowed_error('app.routers.health', exc)
        base["can_retrieve"] = True
        base["can_verify"] = True
        base["can_generate"] = True

    # Inference tier — provider, GPU, latencies
    try:
        from utils.inference_config import inference_health_payload
        base["inference"] = inference_health_payload()
    except Exception as exc:
        log_swallowed_error("app.routers.health.inference_health_payload", exc)


    # E1 CR-023: surface the local-LLM/rerank fallback degradation the /health
    # inference_routing snapshot records. /health/status previously omitted it, so
    # a GUI polling this endpoint could not show a live serving!=configured
    # fallback (pipeline_providers above is config-intent, not runtime liveness).
    try:
        from core.utils.inference_routing import get_routing_snapshot
        base["inference_routing"] = get_routing_snapshot()
    except Exception as exc:  # noqa: BLE001 — observability augmentation only
        log_swallowed_error("app.routers.health.degradation_inference_routing", exc)
        base.setdefault("inference_routing", {})

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
                if resp.status_code == HTTPStatus.OK:
                    _openrouter_auth_cache = True
                elif resp.status_code == HTTPStatus.UNAUTHORIZED and get_consecutive_auth_failures() == 0:
                    # /auth/key returned 401 but completions are succeeding —
                    # treat as a transient probe false positive.
                    _openrouter_auth_cache = True
                else:
                    _openrouter_auth_cache = resp.status_code == HTTPStatus.OK
            else:
                _openrouter_auth_cache = None  # no key configured
        except Exception as exc:
            log_swallowed_error('app.routers.health', exc)
            # Network error on the probe itself — fall back to completion health.
            try:
                from core.utils.llm_client import get_consecutive_auth_failures
                _openrouter_auth_cache = get_consecutive_auth_failures() == 0
            except Exception as exc:
                log_swallowed_error('app.routers.health', exc)
                _openrouter_auth_cache = False
        _openrouter_auth_cache_ts = now
    base["openrouter_auth_ok"] = _openrouter_auth_cache

    return base


def list_collections() -> dict:
    """Public — also called by mcp_sse.py execute_tool."""
    chroma = get_chroma()
    collections = chroma.list_collections()
    return {"total": len(collections), "collections": [c.name for c in collections]}


@router.get("/health/live", response_model=LivenessProbeResponse)
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
                "fact_orphans": 0,
                "two_store_residual": 0,
                "vector_visible_archived": 0,
                "collections_empty": [],
                "custom_collections": [],
                "errors": [],
            }
            # Sink-wiring is neo4j-independent — report the real value even here.
            try:
                from core.ingest.sources.ingest_sink import get_source_ingest_fn
                snap["source_ingest_fn_wired"] = get_source_ingest_fn() is not None
            except Exception as exc:
                log_swallowed_error('app.routers.health', exc)
                snap["source_ingest_fn_wired"] = False
            from app.startup.invariants import _probe_chroma, _probe_nli
            try:
                if chroma is not None:
                    snap.update(_probe_chroma(chroma))
            except Exception as exc:
                log_swallowed_error('app.routers.health', exc)
                errs = snap["errors"]
                if isinstance(errs, list):
                    errs.append(f"chroma: {exc}")
            snap.update(_probe_nli())
            snap["healthy_invariants"] = bool(snap.get("nli_model_loaded"))
            snap["mcp"] = _mcp_tool_summary()
            return snap
        snap = run_invariants(chroma, redis_client, neo4j_driver)
        snap["mcp"] = _mcp_tool_summary()
        return snap
    except Exception as exc:
        log_swallowed_error('app.routers.health', exc)
        logger.warning("invariants snapshot failed: %s", exc)
        return {"healthy_invariants": False, "errors": [str(exc)]}


def _mcp_tool_summary(window_minutes: int = 60) -> dict[str, Any]:
    """Roll up MCP tool-call metrics for the /health.invariants.mcp surface.

    Reads the ``mcp_tool_call`` counter and ``mcp_tool_call_duration_ms``
    histogram written by ``app.tools.execute_tool``'s instrumentation
    wrapper (Phase 2.1). Aggregates over a rolling window so callers
    can see "are tools healthy right now?" without running queries
    against the full /observability/metrics surface.

    Returns:
        {
            window_minutes: 60,
            calls: {ok: int, error: int, total: int, error_rate: float},
            latency_ms: {p50, p95, p99, avg, max, count},
            top_tools_by_error: [{tool, error_class, count}],
        }

    Errors are swallowed via ``log_swallowed_error`` so a metrics-
    backend hiccup never sinks /health.
    """
    summary: dict[str, Any] = {
        "window_minutes": window_minutes,
        "calls": {"ok": 0, "error": 0, "total": 0, "error_rate": 0.0},
        "latency_ms": {"p50": None, "p95": None, "p99": None, "avg": None, "max": None, "count": 0},
        "top_tools_by_error": [],
    }
    try:
        from utils.metrics import get_metrics_collector
        collector = get_metrics_collector()

        counter_points = collector.get_metrics("mcp_tool_call", window_minutes)
        ok = sum(1 for p in counter_points if (p.tags or {}).get("outcome") == "ok")
        err = sum(1 for p in counter_points if (p.tags or {}).get("outcome") == "error")
        total = ok + err
        summary["calls"] = {
            "ok": ok,
            "error": err,
            "total": total,
            "error_rate": round(err / total, 3) if total else 0.0,
        }

        # Latency histogram aggregates via the collector's stat helper.
        agg = collector.get_aggregated_metrics(window_minutes=window_minutes)
        lat = agg.get("mcp_tool_call_duration_ms")
        if lat:
            summary["latency_ms"] = {
                "p50": lat.get("p50"),
                "p95": lat.get("p95"),
                "p99": lat.get("p99"),
                "avg": lat.get("avg"),
                "max": lat.get("max"),
                "count": lat.get("count", 0),
            }

        # Top error-by-class. Counter points carry an `error_class` tag
        # when outcome=error.
        if err:
            by_pair: dict[tuple[str, str], int] = {}
            for p in counter_points:
                t = p.tags or {}
                if t.get("outcome") != "error":
                    continue
                k = (t.get("tool", "?"), t.get("error_class", "?"))
                by_pair[k] = by_pair.get(k, 0) + 1
            top = sorted(by_pair.items(), key=lambda x: -x[1])[:5]
            summary["top_tools_by_error"] = [
                {"tool": tool, "error_class": cls, "count": n}
                for (tool, cls), n in top
            ]
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("app.routers.health.mcp_tool_summary", exc)
    return summary


def _load_recommendations(
    redis_client, dismissed_prefix: str, hash_key: str,
) -> list[dict]:
    """Read ``cerid:recommendations`` and filter per-tenant dismissals.

    Cycle 3.2 helper for the /health endpoint. Returns a list of
    ``{id, label, reason, triggered_at, corpus_size, enable_payload}``
    dicts in declaration order (registry order). Callers should treat
    an empty list as "no nudges, all features either off-and-fine or
    already on".

    For now Cerid is single-tenant by default; we read the "default"
    tenant's dismissals set. Multi-tenant installations route
    /health per tenant via middleware (out of scope for v0.93.3).
    """
    if redis_client is None:
        return []
    try:
        raw_hash = redis_client.hgetall(hash_key) or {}
    except Exception as exc:  # noqa: BLE001 — observability augmentation only
        log_swallowed_error("app.routers.health.load_recs.hgetall", exc)
        return []
    if not raw_hash:
        return []

    try:
        dismissed = redis_client.smembers(f"{dismissed_prefix}default") or set()
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("app.routers.health.load_recs.smembers", exc)
        dismissed = set()

    # Normalize bytes → str (redis-py returns bytes by default).
    def _s(v: Any) -> str:
        return v.decode() if isinstance(v, bytes) else str(v)

    dismissed_str = {_s(d) for d in dismissed}

    out: list[dict] = []
    import json as _json
    for key, value in raw_hash.items():
        rec_id = _s(key)
        if rec_id in dismissed_str:
            continue
        try:
            entry = _json.loads(_s(value))
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("app.routers.health.load_recs.json", exc)
            continue
        out.append(entry)
    return out


def _saml_unavailable_reason() -> str:
    """Why ``sso_saml`` cannot serve on this install, or "" when it can."""
    import config.features as _features

    if not _features.CERID_MULTI_USER:
        return (
            "app/routers/saml.py is registered only when CERID_MULTI_USER=true; "
            "SSO issues a session for an IdP-attested user and single-user mode "
            "has none"
        )
    return ""


#: Flags whose in-process implementation is mounted CONDITIONALLY. Each entry
#: answers "why can this not serve right now", and an empty string means it can.
#: Without this, `implementation: "in_process"` is a claim no runtime state can
#: contradict — which is exactly how a paid flag ends up entitled and served by
#: nothing.
_CONDITIONAL_IMPLEMENTATIONS: dict[str, Any] = {
    "sso_saml": _saml_unavailable_reason,
}


def _pro_feature_health() -> dict:
    """Per-Pro-flag liveness: entitled, implemented, and why not if not.

    ``degraded`` is the actionable bit — a flag the current tier entitles whose
    backing plugin failed to load. Desktop-implemented features (Apple
    connectors, Spotlight donation) have no backend plugin by design, so they
    are reported ``implementation: "desktop"`` rather than counted as broken.
    """
    import config.features as features
    from plugins import get_failed_plugins, get_loaded_plugins

    loaded = get_loaded_plugins()
    failed = get_failed_plugins()

    # flag -> the failure that withheld it
    blocked: dict[str, dict] = {}
    for info in failed.values():
        for flag in info.get("feature_flags") or []:
            blocked[flag] = info
    # A failed plugin often does not declare feature_flags; fall back to its
    # own name so a plugin named after its flag is still attributed.
    for key, info in failed.items():
        blocked.setdefault(str(info.get("name") or key), info)

    supplied: set[str] = set()
    for info in loaded.values():
        supplied.update(info.get("feature_flags") or [])
        supplied.add(str(info.get("name", "")))

    out: dict[str, dict] = {}
    degraded: list[str] = []
    for flag in sorted(features.FEATURE_FLAGS):
        if features._get_feature_tier(flag) not in ("pro", "enterprise"):
            continue
        entitled = bool(features.FEATURE_FLAGS.get(flag))
        entry: dict[str, object] = {"entitled": entitled}
        if flag in features.PLANNED_FEATURES:
            entry["implementation"] = "planned"
        elif flag in blocked:
            info = blocked[flag]
            entry["implementation"] = "backend_plugin"
            entry["loaded"] = False
            entry["blocked_reason"] = f"{info.get('error_type')}: {info.get('error')}"
            if entitled:
                degraded.append(flag)
        elif flag in supplied:
            entry["implementation"] = "backend_plugin"
            entry["loaded"] = True
        else:
            # No backend plugin claims it, so it must say where it DOES live.
            # This used to be a catch-all "in_process_or_desktop" that
            # `degraded` never drew from, which made 14 of 27 paid flags
            # structurally unfalsifiable: deleting a broken plugin, or adding a
            # flag nobody implemented, both landed here and read as fine.
            declared = features.NON_PLUGIN_IMPLEMENTATIONS.get(flag)
            entry["implementation"] = declared or "unknown"
            if declared in (None, "unimplemented") and entitled:
                degraded.append(flag)
            if declared == "unimplemented":
                entry["blocked_reason"] = (
                    "declared unimplemented: no plugin, no gate, no call site"
                )
            elif declared is None:
                entry["blocked_reason"] = (
                    "no backend plugin supplies this flag and it is not declared "
                    "in config.features.NON_PLUGIN_IMPLEMENTATIONS"
                )
        # An in_process declaration is a claim about a router that is mounted.
        # `sso_saml` names app/routers/saml.py, which is registered only under
        # CERID_MULTI_USER — so on a single-user Enterprise install the flag was
        # entitled, declared implemented, and served by nothing. That is the
        # same substitution as a residual bucket, one flag wide: a declaration
        # that cannot be false is not a declaration.
        if flag in _CONDITIONAL_IMPLEMENTATIONS:
            reason = _CONDITIONAL_IMPLEMENTATIONS[flag]()
            if reason:
                entry["blocked_reason"] = reason
                entry["loaded"] = False
                if entitled:
                    degraded.append(flag)
        out[flag] = entry

    # The tier the flags above were computed at. Reported so a caller can tell
    # WHICH tier it is looking at: scripts/lint-pro-feature-health.py's
    # --require-tier could only ask "is anything entitled?", so
    # `--require-tier enterprise` passed happily against a Pro stack — a check
    # named after a tier that never compared one.
    return {
        "tier": features.FEATURE_TIER,
        "degraded": sorted(degraded),
        "features": out,
    }


def _build_health_payload() -> dict:
    """Run the blocking health checks + invariants snapshot + augmentations.

    Lives in its own helper so the async ``/health`` endpoint can hand
    this off via ``asyncio.to_thread`` instead of blocking the event
    loop on Neo4j cold-cache queries. See F-PERF-04.

    All trust-score / processor-metrics / memory-consolidation /
    inference-routing / recommendations augmentations also run on the
    worker thread for the same reason — they each touch Redis or Neo4j.
    """
    result = health_check()
    result["invariants"] = _invariants_snapshot()
    # Paid-feature liveness. The invariant worth watching is `entitled and not
    # loaded`: the customer is paying for a feature whose implementation the
    # runtime dropped. Three Pro plugins sat in exactly that state because
    # plugin load failures were logged at ERROR and then discarded.
    # scripts/lint-pro-feature-health.py gates on the `degraded` list.
    try:
        result["pro_features"] = _pro_feature_health()
    except Exception as _exc:  # noqa: BLE001 — observability augmentation only
        log_swallowed_error("app.routers.health.pro_features", _exc)
    # Audit-log write failures. `audit_log.audit()` deliberately does not raise
    # — an unwritable log must not brick a self-hosted install — so this is the
    # surface that keeps "the log stopped recording" from being indistinguishable
    # from "nothing happened worth recording".
    try:
        from core.utils import audit_log as _audit_log

        result["audit_log"] = {
            "write_failures": _audit_log.write_failure_count(),
            "segments": len(_audit_log.segments()),
        }
    except Exception as _exc:  # noqa: BLE001 — observability augmentation only
        log_swallowed_error("app.routers.health.audit_log", _exc)
    # Phase E.5 (v0.92): trust-score summary alongside core invariants.
    # Pure metadata — not part of the healthy/degraded gate. Failures
    # here must never affect the /health response code.
    try:
        from app.services.trust_score import trust_score_24h_summary
        try:
            _ts_driver = get_neo4j()
        except Exception as _exc:  # noqa: BLE001 — observability augmentation only
            log_swallowed_error("app.routers.health.trust_score_24h.get_neo4j", _exc)
            _ts_driver = None
        result["invariants"]["trust_score_24h"] = trust_score_24h_summary(_ts_driver)
    except Exception as _exc:  # noqa: BLE001 — observability augmentation only
        log_swallowed_error("app.routers.health.trust_score_24h", _exc)
    # Phase 3b (v0.92): processor metrics alongside core invariants.
    # Pure metadata — not part of the healthy/degraded gate. Failures
    # must never affect the /health response code.
    try:
        from app.processor.metrics import (
            _sync_cost_usd_7d,
            _sync_jobs_completed_24h,
            _sync_throttled_ticks,
        )
        _proc_redis = None
        try:
            _proc_redis = get_redis()
        except Exception as _exc:  # noqa: BLE001
            log_swallowed_error("app.routers.health.processor_metrics.get_redis", _exc)

        if _proc_redis is not None:
            result["invariants"]["processor_jobs_completed_24h"] = (
                _sync_jobs_completed_24h(_proc_redis)
            )
            result["invariants"]["processor_cost_usd_7d"] = float(
                _sync_cost_usd_7d(_proc_redis)
            )
            result["invariants"]["processor_throttled_ticks"] = (
                _sync_throttled_ticks(_proc_redis, 3600.0)
            )
        else:
            result["invariants"]["processor_jobs_completed_24h"] = 0
            result["invariants"]["processor_cost_usd_7d"] = 0.0
            result["invariants"]["processor_throttled_ticks"] = 0
    except Exception as _exc:  # noqa: BLE001 — observability augmentation only
        log_swallowed_error("app.routers.health.processor_metrics", _exc)
        result["invariants"].setdefault("processor_jobs_completed_24h", 0)
        result["invariants"].setdefault("processor_cost_usd_7d", 0.0)
        result["invariants"].setdefault("processor_throttled_ticks", 0)
    # Phase O.2 (v0.92): memory consolidation failure count alongside core
    # invariants. Pure metadata — not part of the healthy/degraded gate.
    # Failures here must never affect the /health response code.
    try:
        from app.services.memory_metrics import memory_consolidation_failures_24h as _mcf24h

        _mcf_redis = None
        try:
            _mcf_redis = get_redis()
        except Exception as _exc:  # noqa: BLE001
            log_swallowed_error(
                "app.routers.health.memory_consolidation_failures.get_redis", _exc
            )
        if _mcf_redis is not None:
            result["invariants"]["memory_consolidation_failures_last_24h"] = _mcf24h(
                _mcf_redis
            )
        else:
            result["invariants"]["memory_consolidation_failures_last_24h"] = 0
    except Exception as _exc:  # noqa: BLE001 — observability augmentation only
        log_swallowed_error("app.routers.health.memory_consolidation_failures", _exc)
        result["invariants"].setdefault("memory_consolidation_failures_last_24h", 0)
    # v0.93.8 — inference routing snapshot. Pure metadata.
    try:
        from core.utils.inference_routing import get_routing_snapshot
        result["inference_routing"] = get_routing_snapshot()
    except Exception as _exc:  # noqa: BLE001 — observability augmentation only
        log_swallowed_error("app.routers.health.inference_routing", _exc)
        result.setdefault("inference_routing", {})
    # Cycle 3.2 — adaptive feature recommendations. Pure metadata.
    try:
        from app.routers.recommendations import _DISMISSED_SET_PREFIX, _REDIS_HASH_KEY
        _rec_redis = None
        try:
            _rec_redis = get_redis()
        except Exception as _exc:  # noqa: BLE001
            log_swallowed_error(
                "app.routers.health.recommendations.get_redis", _exc,
            )
        result["recommended_features"] = _load_recommendations(
            _rec_redis, _DISMISSED_SET_PREFIX, _REDIS_HASH_KEY,
        )
    except Exception as _exc:  # noqa: BLE001 — observability augmentation only
        log_swallowed_error("app.routers.health.recommendations", _exc)
        result.setdefault("recommended_features", [])
    return result


@router.get("/health")
async def health_check_endpoint():
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
    global _health_cache, _health_cache_ts, _health_build_inflight
    now = time.monotonic()
    cache_age = now - _health_cache_ts if _health_cache else float("inf")
    if _health_cache and cache_age < _HEALTH_CACHE_TTL:
        # Hot path: cache fresh — return immediately, no I/O.
        result = _health_cache
    elif _health_cache:
        # F-PERF-04: stale-while-revalidate. Serve the stale payload
        # immediately and kick off a background refresh. This keeps
        # /health p95 latency at sub-millisecond regardless of how
        # cold the underlying infra probes are. The refresh runs via
        # asyncio.to_thread so it never blocks the event loop, and
        # the in-flight Future is shared so concurrent stale serves
        # don't each spawn a refresh.
        lock = _get_health_build_lock()
        async with lock:
            now = time.monotonic()
            if _health_cache and (now - _health_cache_ts) < _HEALTH_CACHE_TTL:
                pass  # someone else refreshed while we queued
            elif _health_build_inflight is None or _health_build_inflight.done():
                _health_build_inflight = asyncio.ensure_future(
                    _refresh_health_cache()
                )
        result = _health_cache
    else:
        # Empty cache — block on the first build. With the lifespan
        # pre-warm wired, this branch only fires if the pre-warm
        # itself failed at boot.
        future_to_await: asyncio.Future | None = None
        lock = _get_health_build_lock()
        async with lock:
            if _health_cache:
                pass
            elif _health_build_inflight is None or _health_build_inflight.done():
                _health_build_inflight = asyncio.ensure_future(
                    _refresh_health_cache()
                )
            future_to_await = _health_build_inflight
        if future_to_await is not None and not _health_cache:
            await future_to_await
        result = _health_cache or {"services": {}, "invariants": {}}

    # A service is "ok" when connected OR intentionally disabled (lightweight neo4j).
    def _ok(v: str) -> bool:
        return v == "connected" or v.startswith("disabled")

    services_ok = all(_ok(v) for v in result["services"].values())
    invariants_ok = result.get("invariants", {}).get("healthy_invariants", True)
    http_status = 200 if (services_ok and invariants_ok) else 503
    if http_status == HTTPStatus.OK:
        return result
    return JSONResponse(content=result, status_code=503)


@router.get("/health/status")  # response-model-allowed: dynamic response (shape varies)
def health_status_endpoint():
    """Extended health check with degradation tier and uptime."""
    return degradation_status()


@router.get("/collections")  # response-model-allowed: dynamic response (shape varies)
def list_collections_endpoint():
    return list_collections()


@router.get("/scheduler")  # response-model-allowed: dynamic response (shape varies)
def scheduler_status_endpoint():
    """Return status of all scheduled jobs."""
    from app.scheduler import get_job_status

    return get_job_status()


@router.post("/scheduler/jobs/{job_id}/run")
async def scheduler_run_job_endpoint(job_id: str):
    """Manually trigger a scheduled job now ("a refresh gets a refresh").

    Runs the job out-of-band on the app loop and busts the serving caches it
    feeds (e.g. compute_umap_3d → the Constellation projection). Returns
    immediately; the job runs in the background.
    """
    from app.scheduler import trigger_job

    try:
        return trigger_job(job_id)
    except KeyError:
        return JSONResponse(
            status_code=404,
            content={"detail": f"unknown job '{job_id}'"},
        )
    except ValueError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})


@router.get("/plugins", response_model=dict[str, Any])
def plugins_endpoint():
    """Return loaded plugins and feature flag status."""
    from plugins import get_loaded_plugins
    from utils.features import get_feature_status

    return {
        "plugins": get_loaded_plugins(),
        **get_feature_status(),
    }
