# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Feature flags, toggles, and plugin configuration.

This is the canonical location for all tier-gating primitives:
- ``is_feature_enabled()``  — runtime check
- ``require_feature()``     — async endpoint decorator (raises 403)
- ``check_feature()``       — sync helper (raises CeridError)
"""
from __future__ import annotations

import asyncio as _asyncio
import functools as _functools
import logging as _logging
import os
from collections.abc import Callable as _Callable
from typing import Any as _Any

# ---------------------------------------------------------------------------
# Plugin System & Feature Tiers
# ---------------------------------------------------------------------------
# Feature tier: "community" (OSS), "pro" (commercial plugins), or "enterprise"
FEATURE_TIER = os.getenv("CERID_TIER", "community")

# Hierarchical tier levels: enterprise ⊃ pro ⊃ community
_TIER_LEVELS = {"community": 0, "pro": 1, "enterprise": 2}

# Plugin directory (relative to app root or absolute path)
PLUGIN_DIR = os.getenv("CERID_PLUGIN_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugins"))

# Comma-separated list of plugin names to load (empty = auto-discover all)
_enabled_plugins_raw = os.getenv("CERID_ENABLED_PLUGINS", "")
ENABLED_PLUGINS = [p.strip() for p in _enabled_plugins_raw.split(",") if p.strip()] if _enabled_plugins_raw else []

# ---------------------------------------------------------------------------
# Multi-User Auth
# ---------------------------------------------------------------------------
# Multi-user mode is gated through v1.0 by the EXPERIMENTAL escape hatch.
# Two known security gaps must close before this is supported in GA deploys
# (per the 2026-05-24 rc1 beta-test report, findings F2 + F3):
#   * F2 — access + refresh tokens stored in localStorage; an XSS leaks
#     every multi-tenant session. Migrate to httpOnly + same-site=strict.
#   * F3 — get_artifact() Cypher in app/db/neo4j/artifacts.py lacks a
#     tenant_id filter; an authenticated user A can fetch user B's
#     artifacts by guessing UUIDs. Audit every Neo4j read for missing
#     tenant_id parameter and add an import-linter rule that bans
#     reads without one.
# Until both ship, CERID_MULTI_USER=true must be paired with
# CERID_MULTI_USER_EXPERIMENTAL=true so operators see the explicit
# "this is unsupported" gate. Single-user mode (default) is unaffected.
CERID_MULTI_USER = os.getenv("CERID_MULTI_USER", "false").lower() == "true"
CERID_MULTI_USER_EXPERIMENTAL = (
    os.getenv("CERID_MULTI_USER_EXPERIMENTAL", "false").lower() == "true"
)
CERID_JWT_SECRET = os.getenv("CERID_JWT_SECRET", "")
CERID_JWT_ACCESS_TTL = int(os.getenv("CERID_JWT_ACCESS_TTL", "900"))   # 15 min
CERID_JWT_REFRESH_TTL = int(os.getenv("CERID_JWT_REFRESH_TTL", "604800"))  # 7 days
DEFAULT_TENANT_ID = os.getenv("CERID_DEFAULT_TENANT", "default")

# ---------------------------------------------------------------------------
# Webhook Ingest Auth
# ---------------------------------------------------------------------------
CERID_WEBHOOK_SECRET = os.getenv("CERID_WEBHOOK_SECRET", "")

# Feature flags: controls what's available per tier
# Community features are always enabled; pro features require CERID_TIER=pro or enterprise
#
# 2026-05-20 rebalance (Pro Tier Implementation Plan, Phase 1):
#   - Demoted to community: image_understanding (multimodal table-stakes 2026),
#     parent_child_retrieval (RAG quality is plumbing, not a Pro axis),
#     docling_parser (parsing quality is plumbing)
#   - audio_transcription split: plain Whisper transcription is community
#     (audio_transcription_plain + voice_memos_watch); meeting-aware diarized
#     transcription is Pro (meeting_diarization + calendar_stitching +
#     meeting_summary)
#   - Removed: advanced_workflows (no workflow extension story yet)
#   - Added Pro flags: meeting_diarization, calendar_stitching, meeting_summary,
#     daily_digest, inbox_triage, apple_mail_reader, imessage_reader,
#     apple_calendar_eventkit, reminders_eventkit, apple_photos_reader,
#     google_calendar_sync, outlook_calendar_sync
#   - Added community flags: voice_memos_watch, spotlight_integration,
#     share_sheet, shortcuts_actions, quicklook_preview, safari_reading_list,
#     menu_bar_mode, keychain_secrets, tcc_wizard, sparkle_updates,
#     universal_binary, apple_silicon_ml
#
# The granular flags below are the source of truth for runtime gating; the
# FEATURE_BUCKETS map further down groups them for UI presentation and
# marketing copy.

def _pro_level() -> bool:
    return _TIER_LEVELS.get(FEATURE_TIER, 0) >= _TIER_LEVELS["pro"]


def _enterprise_level() -> bool:
    return _TIER_LEVELS.get(FEATURE_TIER, 0) >= _TIER_LEVELS["enterprise"]


FEATURE_FLAGS = {
    # ---- Community features (always enabled) ----
    "ocr_parsing":               True,  # scanned PDF/image text extraction
    "semantic_dedup":            True,  # KB quality
    "image_understanding":       True,  # multimodal BYOK (demoted from Pro 2026-05-20)
    "parent_child_retrieval":    True,  # RAG quality plumbing (demoted 2026-05-20)
    "docling_parser":            True,  # high-fidelity parsing (demoted 2026-05-20)
    "audio_transcription_plain": True,  # Whisper transcription without diarization (community)
    "hierarchical_taxonomy":     True,
    "file_upload_gui":           True,
    "encryption_at_rest":        True,
    "truth_audit":               True,
    "live_metrics":              True,
    "private_mode":              True,  # Level 1 community; deeper levels gated by other checks
    "basic_workflows":           True,

    # ---- Mac-native community features (`mac_native` bucket) ----
    # All True if platform supports them; runtime macOS check at use-site
    "voice_memos_watch":         True,
    "spotlight_integration":     True,
    "share_sheet":               True,
    "shortcuts_actions":         True,
    "quicklook_preview":         True,
    "safari_reading_list":       True,
    "menu_bar_mode":             True,
    "keychain_secrets":          True,
    "tcc_wizard":                True,
    "sparkle_updates":           True,
    "universal_binary":          True,
    "apple_silicon_ml":          True,

    # ---- Pro features (require pro or enterprise tier) ----
    # Pro meeting capture bucket
    "meeting_diarization":       _pro_level(),
    "calendar_stitching":        _pro_level(),
    "meeting_summary":           _pro_level(),
    # Pro intelligence bucket
    "metamorphic_verification":  _pro_level(),
    "custom_smart_rag":          _pro_level(),
    "advanced_analytics":        _pro_level(),
    "daily_digest":              _pro_level(),
    "inbox_triage":              _pro_level(),
    # Pro visualization bucket — graph tour, Atlas saved-views, Constellation
    # analytics/timeline overlays. Gated via is_feature_enabled("pro_visualization_*")
    # in app/routers/graph_tour.py + atlas_views.py. (Were consulted but never
    # registered → failed closed at every tier, so "Take a tour" did nothing.)
    "pro_visualization_tour":      _pro_level(),
    "pro_visualization_analytics": _pro_level(),
    "pro_visualization_timeline":  _pro_level(),
    # Pro cloud connectors bucket
    "gmail_connector":           _pro_level(),
    "outlook_connector":         _pro_level(),
    "google_calendar_sync":      _pro_level(),
    "outlook_calendar_sync":     _pro_level(),
    # Pro Apple connectors bucket (require macOS + FDA at use-site)
    "apple_notes_reader":        _pro_level(),
    "apple_mail_reader":         _pro_level(),
    "imessage_reader":           _pro_level(),
    "apple_calendar_eventkit":   _pro_level(),
    "reminders_eventkit":        _pro_level(),
    "apple_photos_reader":       _pro_level(),
    "spotlight_donation":        _pro_level(),  # Phase G.4 — donate KB artifacts to CoreSpotlight

    # ---- Enterprise features ----
    "multi_user":                CERID_MULTI_USER or _enterprise_level(),
    "sso_saml":                  _enterprise_level(),  # Scaffolded — no impl yet
    "audit_logging":             _enterprise_level(),
    "priority_support":          _enterprise_level(),

    # ---- Back-compat aliases ----
    # `audio_transcription` was a single Pro flag pre-2026-05-20. Existing
    # call sites and tests still reference it; new code should use
    # `audio_transcription_plain` (community) or `meeting_diarization` (Pro).
    # We keep the alias mirroring the *community* plain transcription path so
    # community Whisper still works when this flag is consulted; the Pro
    # uplift consumes the new flags directly.
    "audio_transcription":       True,
    # `calendar_sync` collapsed into google_calendar_sync + outlook_calendar_sync
    "calendar_sync":             _pro_level(),
}


# ---------------------------------------------------------------------------
# FEATURE_BUCKETS — capability groupings for UI + marketing surfaces.
# ---------------------------------------------------------------------------
# Each bucket maps to a list of granular FEATURE_FLAGS keys. A bucket is
# "enabled" when ALL its member flags are enabled (intersection semantics —
# selling Pro means *all* the Pro features in a bucket light up together).
# Phase 1 day 3 of the Pro Tier plan exposes this via /billing/capabilities.

FEATURE_BUCKETS: dict[str, list[str]] = {
    "pro_meeting_capture": [
        "meeting_diarization",
        "calendar_stitching",
        "meeting_summary",
    ],
    "pro_intelligence": [
        "metamorphic_verification",
        "custom_smart_rag",
        "advanced_analytics",
        "daily_digest",
        "inbox_triage",
    ],
    "pro_visualization": [
        "pro_visualization_tour",
        "pro_visualization_analytics",
        "pro_visualization_timeline",
    ],
    "pro_cloud_connectors": [
        "gmail_connector",
        "outlook_connector",
        "google_calendar_sync",
        "outlook_calendar_sync",
    ],
    "pro_apple_connectors": [
        "apple_notes_reader",
        "apple_mail_reader",
        "imessage_reader",
        "apple_calendar_eventkit",
        "reminders_eventkit",
        "apple_photos_reader",
    ],
    "mac_native": [
        "voice_memos_watch",
        "spotlight_integration",
        "share_sheet",
        "shortcuts_actions",
        "quicklook_preview",
        "safari_reading_list",
        "menu_bar_mode",
        "keychain_secrets",
        "tcc_wizard",
        "sparkle_updates",
        "universal_binary",
        "apple_silicon_ml",
    ],
}

# Flags that belong to each tier (used by _get_feature_tier + test_pro_gating_contract)
_PRO_TIER_FLAGS: frozenset[str] = frozenset(
    flag
    for bucket_name, flags in FEATURE_BUCKETS.items()
    if bucket_name.startswith("pro_")
    for flag in flags
) | frozenset({"calendar_sync"})  # back-compat alias

_ENTERPRISE_TIER_FLAGS: frozenset[str] = frozenset(
    {"multi_user", "sso_saml", "audit_logging", "priority_support"}
)


def _refresh_flags() -> None:
    """Recalculate feature flags after a runtime tier change (e.g., license activation)."""
    global FEATURE_FLAGS
    pro = _pro_level()
    ent = _enterprise_level()
    for flag in _PRO_TIER_FLAGS:
        FEATURE_FLAGS[flag] = pro
    for flag in _ENTERPRISE_TIER_FLAGS:
        FEATURE_FLAGS[flag] = ent
    FEATURE_FLAGS["multi_user"] = CERID_MULTI_USER or ent
    # Community flags stay True — they're not tier-gated


# ---------------------------------------------------------------------------
# Feature toggles
# ---------------------------------------------------------------------------
ENABLE_HALLUCINATION_CHECK = os.getenv("ENABLE_HALLUCINATION_CHECK", "true").lower() == "true"
ENABLE_FEEDBACK_LOOP = os.getenv("ENABLE_FEEDBACK_LOOP", "false").lower() == "true"
ENABLE_MEMORY_EXTRACTION = os.getenv("ENABLE_MEMORY_EXTRACTION", "true").lower() == "true"
ENABLE_ENCRYPTION = os.getenv("ENABLE_ENCRYPTION", "false").lower() == "true"
ENABLE_AUTO_INJECT = os.getenv("ENABLE_AUTO_INJECT", "true").lower() == "true"
ENABLE_SELF_RAG = os.getenv("ENABLE_SELF_RAG", "true").lower() == "true"
ENABLE_CONTEXTUAL_CHUNKS = os.getenv("ENABLE_CONTEXTUAL_CHUNKS", "false").lower() == "true"
# Surface-biased retrieval (GA P0.5 A1b/A2/C2) — the surface route
# (core/retrieval/surface_router) biases retrieval: `relational` queries consult
# the graph surface (bypass the high-confidence early-exit), `personal_context`
# queries recall episodic memory (A2), and `compiled_summary` queries prepend the
# compiled wiki page (C2). **Default ON since 2026-06-02** — the memory/wiki-
# augmented production eval cleared the flip: +1.000 recall on the memory/wiki
# strata, 0.000 harm on the regular benchmark (0.842→0.842). See docs/EVAL_BASELINES.md.
ENABLE_SURFACE_BIASED_RETRIEVAL = os.getenv("ENABLE_SURFACE_BIASED_RETRIEVAL", "true").lower() == "true"
ENABLE_MEMORY_RECALL = os.getenv("ENABLE_MEMORY_RECALL", "true").lower() == "true"
# Step-timing instrumentation for the 22-step query pipeline (Workstream E
# Phase 0). When true, every query records per-step elapsed times and emits
# them as a `timings` field on the response. Per-request overrides via the
# `X-Debug-Timing` header still work; this flag controls the default.
ENABLE_STEP_TIMING = os.getenv("ENABLE_STEP_TIMING", "true").lower() == "true"
# CERID_ENCRYPTION_KEY is read directly from env by utils/encryption.py
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# ---------------------------------------------------------------------------
# Advanced RAG Pipeline
# ---------------------------------------------------------------------------
ENABLE_ADAPTIVE_RETRIEVAL = os.getenv("ENABLE_ADAPTIVE_RETRIEVAL", "true").lower() == "true"
ADAPTIVE_RETRIEVAL_LIGHT_TOP_K = int(os.getenv("ADAPTIVE_RETRIEVAL_LIGHT_TOP_K", "3"))

ENABLE_QUERY_DECOMPOSITION = os.getenv("ENABLE_QUERY_DECOMPOSITION", "true").lower() == "true"
QUERY_DECOMPOSITION_MAX_SUBQUERIES = int(os.getenv("QUERY_DECOMPOSITION_MAX_SUBQUERIES", "4"))
# Supersession-at-read: drop memories explicitly marked superseded by a newer
# fact (the write path already sets ``superseded_by``; recall historically
# ignored it and could surface stale values — the knowledge-update failure
# mode). Correctness fix, default ON; reversible via env if a preservation
# gate ever flags it.
ENABLE_MEMORY_SUPERSESSION_FILTER = (
    os.getenv("ENABLE_MEMORY_SUPERSESSION_FILTER", "true").lower() == "true"
)
# LLM-based decomposition for *implicit* multi-hop analytical questions (e.g.
# "how many days between X and Y") that carry no conjunction trigger, so the
# heuristic gate skips them. Adds one LLM call on the analytical-query hot path,
# so it is default-OFF pending the benchmark_slo latency gate; flip on once the
# SLO budget is confirmed (mirrors the eval, which forces it to measure uplift).
ENABLE_LLM_QUERY_DECOMPOSITION = os.getenv("ENABLE_LLM_QUERY_DECOMPOSITION", "false").lower() == "true"

# Self-consistency for the analytical operators (temporal date-math + counting):
# sample N extractions at a non-zero temperature and mode-vote the COMPUTED
# answer. The extraction is the real uncertainty — the Python compute is
# deterministic — so voting over the derived number (not free-form chain-of-
# thought) is the high-signal aggregation (Wang et al. arXiv 2203.11171:
# +12-18% on arithmetic/counting). Gated to analytical phrasings (~27% of items)
# and default-OFF: it multiplies the analytical-extract LLM cost N×, so flip on
# only once the full-500 paired run confirms the uplift (mirrors
# ENABLE_LLM_QUERY_DECOMPOSITION's benchmark-gated rollout). N=1 ⇒ exactly the
# current single temperature-0 call, so the default is a strict no-op.
ENABLE_SELF_CONSISTENCY = os.getenv("ENABLE_SELF_CONSISTENCY", "false").lower() == "true"
SELF_CONSISTENCY_SAMPLES = int(os.getenv("SELF_CONSISTENCY_SAMPLES", "5"))
SELF_CONSISTENCY_TEMPERATURE = float(os.getenv("SELF_CONSISTENCY_TEMPERATURE", "0.7"))

ENABLE_MMR_DIVERSITY = os.getenv("ENABLE_MMR_DIVERSITY", "true").lower() == "true"
MMR_LAMBDA = float(os.getenv("MMR_LAMBDA", "0.7"))

ENABLE_INTELLIGENT_ASSEMBLY = os.getenv("ENABLE_INTELLIGENT_ASSEMBLY", "true").lower() == "true"

ENABLE_LATE_INTERACTION = os.getenv("ENABLE_LATE_INTERACTION", "false").lower() == "true"
LATE_INTERACTION_TOP_N = int(os.getenv("LATE_INTERACTION_TOP_N", "8"))
LATE_INTERACTION_BLEND_WEIGHT = float(os.getenv("LATE_INTERACTION_BLEND_WEIGHT", "0.15"))

ENABLE_SEMANTIC_CACHE = os.getenv("ENABLE_SEMANTIC_CACHE", "true").lower() == "true"
SEMANTIC_CACHE_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.92"))
SEMANTIC_CACHE_TTL = int(os.getenv("SEMANTIC_CACHE_TTL", "600"))  # Canonical location for cache TTL
SEMANTIC_CACHE_MAX_ENTRIES = int(os.getenv("SEMANTIC_CACHE_MAX_ENTRIES", "500"))

# GraphRAG retrieval mode (Workstream E Phase 4a.6)
#   "baseline"        — original step-6 graph_expand_results (relationship
#                       traversal over Domain/SubCategory/Tag).
#   "local_graphrag"  — entity-neighborhood expansion through (:MENTIONS)
#                       edges produced by Phase 4a.3 extraction. Requires
#                       backfill (Phase 4a.4) to have populated the entity
#                       graph; before backfill it falls through to baseline.
#   "auto"            — Phase 4b.3 query-router decides per-query.
RETRIEVAL_MODE = os.getenv("RETRIEVAL_MODE", "baseline").lower()
_VALID_RETRIEVAL_MODES = frozenset(("baseline", "local_graphrag", "auto"))
if RETRIEVAL_MODE not in _VALID_RETRIEVAL_MODES:
    RETRIEVAL_MODE = "baseline"

# Parent-child chunking retrieval
ENABLE_PARENT_CHILD_RETRIEVAL = os.getenv("ENABLE_PARENT_CHILD_RETRIEVAL", "false").lower() in ("true", "1")

# Degradation tiers: circuit-breaker-aware graceful degradation
ENABLE_DEGRADATION_TIERS = os.getenv("ENABLE_DEGRADATION_TIERS", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Smart Orchestration
# ---------------------------------------------------------------------------
# Task 16: ``ENABLE_MODEL_ROUTER`` used to be a separate "client-side hint"
# toggle that diverged from the server-enforced ``SMART_ROUTING_ENABLED``
# (``config/settings.py``).  Two flags naming the same concept drift: prefer
# ``SMART_ROUTING_ENABLED`` — ``ENABLE_MODEL_ROUTER`` is kept as a pure alias
# so any code reading it (GUI ``/settings`` payload, sync hydration) sees the
# same value the server actually enforces.
from config.settings import SMART_ROUTING_ENABLED as ENABLE_MODEL_ROUTER  # noqa: F401,E402

COST_SENSITIVITY = os.getenv("COST_SENSITIVITY", "medium")  # low/medium/high

# ---------------------------------------------------------------------------
# Memory Consolidation
# ---------------------------------------------------------------------------
ENABLE_MEMORY_CONSOLIDATION = os.getenv("ENABLE_MEMORY_CONSOLIDATION", "true").lower() == "true"
ENABLE_CONTEXT_COMPRESSION = os.getenv("ENABLE_CONTEXT_COMPRESSION", "true").lower() == "true"
# Contradiction ledger: when a claim is found to contradict KB evidence during
# verification (kb_nli terminal-contradiction), persist a ContradictionFinding
# (HAS_CONTRADICTION edge) so the Wiki contradiction surface + weekly synthesis
# reflect it. Default on (a documented Wiki primitive; cost is one MERGE per
# detected contradiction on an already-running verification path). Set false to
# disable persistence without touching the verification logic.
ENABLE_CONTRADICTION_LEDGER = os.getenv("ENABLE_CONTRADICTION_LEDGER", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Unified toggle registry — single source of truth for all boolean toggles.
# Module-level ENABLE_* vars above remain for backward compatibility.
# Use ``set_toggle()`` (utils/features.py) for runtime mutations.
# ---------------------------------------------------------------------------
FEATURE_TOGGLES: dict[str, bool] = {
    "enable_hallucination_check": ENABLE_HALLUCINATION_CHECK,
    "enable_feedback_loop": ENABLE_FEEDBACK_LOOP,
    "enable_memory_extraction": ENABLE_MEMORY_EXTRACTION,
    "enable_encryption": ENABLE_ENCRYPTION,
    "enable_auto_inject": ENABLE_AUTO_INJECT,
    "enable_self_rag": ENABLE_SELF_RAG,
    "enable_contextual_chunks": ENABLE_CONTEXTUAL_CHUNKS,
    "enable_memory_recall": ENABLE_MEMORY_RECALL,
    "enable_adaptive_retrieval": ENABLE_ADAPTIVE_RETRIEVAL,
    "enable_query_decomposition": ENABLE_QUERY_DECOMPOSITION,
    "enable_llm_query_decomposition": ENABLE_LLM_QUERY_DECOMPOSITION,
    "enable_self_consistency": ENABLE_SELF_CONSISTENCY,
    "enable_mmr_diversity": ENABLE_MMR_DIVERSITY,
    "enable_intelligent_assembly": ENABLE_INTELLIGENT_ASSEMBLY,
    "enable_late_interaction": ENABLE_LATE_INTERACTION,
    "enable_semantic_cache": ENABLE_SEMANTIC_CACHE,
    "enable_model_router": ENABLE_MODEL_ROUTER,
    "enable_memory_consolidation": ENABLE_MEMORY_CONSOLIDATION,
    "enable_context_compression": ENABLE_CONTEXT_COMPRESSION,
    "enable_parent_child_retrieval": ENABLE_PARENT_CHILD_RETRIEVAL,
    "enable_contradiction_ledger": ENABLE_CONTRADICTION_LEDGER,
}

# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------
_config_logger = _logging.getLogger("ai-companion.config")

COST_SENSITIVITY = COST_SENSITIVITY.strip().lower()
if COST_SENSITIVITY not in ("low", "medium", "high"):
    _config_logger.warning(
        "Invalid COST_SENSITIVITY=%r, defaulting to 'medium'", COST_SENSITIVITY
    )
    COST_SENSITIVITY = "medium"


def log_feature_toggles() -> None:
    """Log all feature toggle states at startup."""
    _config_logger.info("Feature tier: %s", FEATURE_TIER)
    enabled = [k for k, v in FEATURE_TOGGLES.items() if v]
    disabled = [k for k, v in FEATURE_TOGGLES.items() if not v]
    _config_logger.info(
        "Feature toggles — enabled: [%s], disabled: [%s]",
        ", ".join(sorted(enabled)) or "none",
        ", ".join(sorted(disabled)) or "none",
    )


# ---------------------------------------------------------------------------
# Tier-based feature gating (canonical location)
# ---------------------------------------------------------------------------

def is_feature_enabled(feature_name: str) -> bool:
    """Check if a tier-gated feature is enabled (fail-closed for unknown)."""
    if feature_name not in FEATURE_FLAGS:
        _config_logger.warning(
            "Unknown feature flag: '%s' — defaulting to disabled", feature_name
        )
        return False
    return FEATURE_FLAGS[feature_name]


def require_feature(feature_name: str) -> _Callable:
    """Decorator that gates a FastAPI endpoint behind a feature flag (async only).

    Usage::

        @router.post("/endpoint")
        @require_feature("ocr_parsing")
        async def my_endpoint():
            ...
    """
    def decorator(func: _Callable) -> _Callable:
        if not _asyncio.iscoroutinefunction(func):
            raise TypeError(
                f"@require_feature can only decorate async functions, "
                f"but '{func.__name__}' is synchronous."
            )

        @_functools.wraps(func)
        async def wrapper(*args: _Any, **kwargs: _Any) -> _Any:
            if not is_feature_enabled(feature_name):
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Feature '{feature_name}' requires a higher Cerid AI tier. "
                        f"Current tier: {FEATURE_TIER}. "
                        f"Upgrade your tier to enable."
                    ),
                )
            return await func(*args, **kwargs)
        return wrapper
    return decorator


def check_feature(feature_name: str) -> None:
    """Synchronous tier check — raises ``FeatureGateError`` if feature is disabled.

    Use in service functions and sync helpers where ``@require_feature`` cannot
    be applied (it requires async).  Routers should prefer ``@require_feature``.
    """
    if not is_feature_enabled(feature_name):
        from errors import FeatureGateError

        raise FeatureGateError(
            f"Feature '{feature_name}' requires a higher Cerid AI tier. "
            f"Current tier: {FEATURE_TIER}. Upgrade your tier to enable.",
        )


def check_tier(required_tier: str, *, context: str = "") -> None:
    """Synchronous tier comparison — raises ``FeatureGateError`` if unmet.

    Use for dynamic tier checks where the required tier comes from metadata
    (e.g. plugin manifests) rather than a named feature flag.
    """
    if not is_tier_met(required_tier):
        from errors import FeatureGateError

        msg = f"Requires '{required_tier}' tier (current: '{FEATURE_TIER}')."
        if context:
            msg = f"{context} {msg}"
        raise FeatureGateError(msg)


def is_tier_met(required_tier: str) -> bool:
    """Check if the current tier meets the requirement (no exception)."""
    current_level = _TIER_LEVELS.get(FEATURE_TIER, 0)
    required_level = _TIER_LEVELS.get(required_tier, 0)
    return current_level >= required_level


def current_tier() -> str:
    """Live feature tier. Always read through this, never a star-imported copy.

    ``from config.features import *`` binds ``config.FEATURE_TIER`` to a
    *separate* name; rebinding the string here would not follow it. This
    accessor returns the canonical value so readers can't go stale.
    """
    return FEATURE_TIER


def set_tier(new_tier: str) -> str:
    """Runtime tier override — recomputes FEATURE_FLAGS in place.

    This is the single mutation point for the tier. It keeps the
    ``config`` package's star-imported ``FEATURE_TIER`` copy in sync too,
    so legacy ``config.FEATURE_TIER`` readers (plugins, multimodal) never
    drift from the canonical value.

    This is a transient override (lost on restart). For persistent
    changes, set ``CERID_TIER`` in ``.env``.
    """
    global FEATURE_TIER  # noqa: PLW0603
    if new_tier not in _TIER_LEVELS:
        raise ValueError(f"Invalid tier: {new_tier!r} (valid: {list(_TIER_LEVELS)})")
    FEATURE_TIER = new_tier
    _refresh_flags()
    # Sync the star-imported duplicate in the `config` package namespace.
    # config is fully loaded by call time, so this lazy import is safe.
    try:
        import config as _config_pkg
        _config_pkg.FEATURE_TIER = new_tier
    except Exception as exc:  # noqa: BLE001 — best-effort namespace sync
        _config_logger.warning("tier sync to config namespace failed: %s", exc)
    _config_logger.info("Runtime tier override: %s (flags recomputed)", new_tier)
    return new_tier


_COMMUNITY_FLAGS: frozenset[str] = frozenset({
    "ocr_parsing", "semantic_dedup", "image_understanding",
    "parent_child_retrieval", "docling_parser", "audio_transcription_plain",
    "audio_transcription",  # back-compat alias
    "hierarchical_taxonomy", "file_upload_gui", "encryption_at_rest",
    "truth_audit", "live_metrics", "private_mode", "basic_workflows",
    "voice_memos_watch", "spotlight_integration", "share_sheet",
    "shortcuts_actions", "quicklook_preview", "safari_reading_list",
    "menu_bar_mode", "keychain_secrets", "tcc_wizard", "sparkle_updates",
    "universal_binary", "apple_silicon_ml",
})


def _get_feature_tier(feature_name: str) -> str:
    """Determine the minimum tier required for a feature flag.

    Lookup order: explicit community set → enterprise set → pro set → unknown.
    Unknown returns ``"pro"`` (fail-conservative); these surface in
    ``test_pro_gating_contract`` as flags that need a tier assignment.
    """
    if feature_name in _COMMUNITY_FLAGS:
        return "community"
    if feature_name in _ENTERPRISE_TIER_FLAGS:
        return "enterprise"
    if feature_name in _PRO_TIER_FLAGS:
        return "pro"
    return "pro"  # unknown flag — default conservative


def is_bucket_enabled(bucket_name: str) -> bool:
    """Whether ALL features in a capability bucket are enabled.

    Intersection semantics: a bucket is "available" to the customer only when
    every member flag is on. Pro buckets light up together when tier is pro;
    the community ``mac_native`` bucket is always on (platform availability
    is checked at use-site, not here).
    """
    members = FEATURE_BUCKETS.get(bucket_name)
    if not members:
        return False
    return all(FEATURE_FLAGS.get(flag, False) for flag in members)


def get_bucket_status() -> dict[str, dict]:
    """Bucket → {enabled, tier_required, features: {flag: {enabled, tier_required}}}.

    Used by ``/billing/capabilities`` to drive the settings UI rendering and by
    ``scripts/gen_pro_capabilities.py`` to keep ROADMAP.md in sync with
    declared capabilities.
    """
    out: dict[str, dict] = {}
    for bucket, flags in FEATURE_BUCKETS.items():
        is_pro_bucket = bucket.startswith("pro_")
        tier_required = "pro" if is_pro_bucket else "community"
        feature_detail = {
            flag: {
                "enabled": FEATURE_FLAGS.get(flag, False),
                "tier_required": _get_feature_tier(flag),
            }
            for flag in flags
        }
        out[bucket] = {
            "enabled": is_bucket_enabled(bucket),
            "tier_required": tier_required,
            "features": feature_detail,
        }
    return out


def get_feature_status() -> dict:
    """Return the status of all feature flags + bucket rollup."""
    return {
        "tier": FEATURE_TIER,
        "features": {
            name: {
                "enabled": enabled,
                "tier_required": _get_feature_tier(name),
            }
            for name, enabled in FEATURE_FLAGS.items()
        },
        "buckets": get_bucket_status(),
    }
