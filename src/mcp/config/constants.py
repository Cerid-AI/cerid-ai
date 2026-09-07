# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Centralized constants — THE source of truth for all magic numbers.

AI agents: import from here. Never hardcode numeric literals in business logic.
Dependencies: none (pure values module).
"""
from __future__ import annotations

# ── Artifact limits ──────────────────────────────────────────────────
MAX_ARTIFACT_LIST = 10_000
MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

# ── Timeouts (seconds) ──────────────────────────────────────────────
HEALTH_CACHE_TTL = 10.0
OLLAMA_READ_TIMEOUT = 120.0
OLLAMA_CONNECT_TIMEOUT = 10.0
VERIFICATION_TIMEOUT = 30.0
QUERY_CACHE_TTL = 300  # 5 minutes
# Per-call store timeouts. Trip raises core.utils.timeouts.StoreTimeoutError so
# the request surfaces a degraded result instead of hanging the event loop.
CHROMA_QUERY_TIMEOUT = 10.0
NEO4J_QUERY_TIMEOUT = 15.0
# Per-source budget for external data sources inside the synchronous
# /agent/query path. Must stay under the 3 s cold-query SLO (R5-1) with
# headroom — a hung source (e.g. DuckDuckGo) otherwise serializes its
# full timeout into first-touch responses until the circuit opens.
EXTERNAL_SOURCE_QUERY_TIMEOUT = 2.0
# Longer per-source budget for the async /query orchestrator path, which is
# off the synchronous 3 s cold-query SLO and can afford more headroom.
EXTERNAL_SOURCE_BROAD_QUERY_TIMEOUT = 5.0
# Per-source budget for authoritative claim verification (hallucination gate).
# Each of these paths applies an outer guard of (inner + 1.0 s).
AUTHORITATIVE_VERIFY_QUERY_TIMEOUT = 4.0
# How long multi_domain_query trusts a cached Chroma collection.count() before
# re-checking. A stale-for-up-to-this-long "empty" reading only delays a
# newly-ingested domain's first hit by one TTL window; the alternative
# (counting every collection on every query) is the cost this cache removes.
COLLECTION_COUNT_CACHE_TTL_S = 60

# ── Budget & rate limits ────────────────────────────────────────────
MONTHLY_BUDGET_USD = 20.0
RATE_LIMIT_WINDOW_SECONDS = 60

# ── Retrieval tuning ────────────────────────────────────────────────
DEFAULT_TOP_K = 10
RETRIEVAL_CACHE_TTL = 1800  # 30 minutes
# External sources carry hardcoded confidence (not semantic similarity), so
# their relevance is discounted before merging with KB results — prevents e.g.
# book-metadata noise from outranking real KB hits. One value, two consumers
# (crag gate + retrieval orchestrator).
EXTERNAL_SOURCE_RELEVANCE_DISCOUNT = 0.6
CHUNK_MAX_TOKENS = 512
CHUNK_OVERLAP_RATIO = 0.2
PARENT_CHILD_RATIO_MIN = 4  # min child:parent token ratio
PARENT_CHILD_RATIO_MAX = 8

# ── Quality scoring v2 ─────────────────────────────────────────────
QUALITY_TIER_EXCELLENT = 0.8
QUALITY_TIER_GOOD = 0.6
QUALITY_TIER_FAIR = 0.4
QUALITY_WEIGHT_RICHNESS = 0.25
QUALITY_WEIGHT_METADATA = 0.20
QUALITY_WEIGHT_FRESHNESS = 0.15
QUALITY_WEIGHT_AUTHORITY = 0.15
QUALITY_WEIGHT_UTILITY = 0.15
QUALITY_WEIGHT_COHERENCE = 0.10
QUALITY_MIN_FLOOR = 0.35
QUALITY_EVERGREEN_DOMAINS = ["coding", "personal", "projects"]
QUALITY_TEMPORAL_HALF_LIFE_DAYS = 7
QUALITY_EVERGREEN_HALF_LIFE_DAYS = 365

# ── Memory ─────────────────────────────────────────────────────────
MEMORY_HALF_LIFE_DAYS = 30.0

# ── Verification ────────────────────────────────────────────────────
MAX_CLAIMS_PER_RESPONSE = 20
CONFIDENCE_FLOOR = 0.3
CONFIDENCE_CEILING = 0.95

# ── Ingestion ───────────────────────────────────────────────────────
AI_SNIPPET_MAX_CHARS = 1500
BM25_MAX_LOADED_DOMAINS = 8   # LRU floor for in-memory BM25 indexes; raised to len(DOMAINS) at runtime
# Debounce window for the deferred BM25 rebuild. add_documents/remove_documents
# no longer re-tokenize the whole domain corpus inline; they mark the index
# dirty and the next eligible search rebuilds. A committed chunk is therefore
# searchable within at most this many seconds of being ingested (exposed at the
# first query after the cooldown). Coalesces an ingest burst into ~one rebuild
# per window instead of one whole-corpus rebuild per document.
BM25_REBUILD_DEBOUNCE_SECONDS = 2.0
# Longest a search will wait for an in-flight rebuild before serving the
# previous (stale) snapshot. Small corpora finish inside the cap, so a
# just-ingested chunk is still visible at the next query; a whole-corpus
# rebuild of a multi-thousand-doc domain exceeds it and runs to completion
# on the worker thread instead of stalling the interactive query path
# (kb-idle-zero: rebuilds serialized in vector_search blew the entire 20s
# retrieval budget — bf-f3 default: background work yields to interactive).
BM25_REBUILD_MAX_INLINE_WAIT_SECONDS = 0.5

# ── Server-side polling hygiene (Task 5) ───────────────────────────────
# /health/status rebuild cadence — decouples the 14.8k/day poll volume from
# the live Neo4j RETURN 1 + breaker reads + quenchforge /api/tags probe cost
# of degradation_status().
HEALTH_STATUS_CACHE_TTL_S = 15
# /observability/trust-score rebuild cadence — the client hook documents the
# score as "computed nightly"; 60s trades no real freshness for removing the
# two Cypher queries + four JSON reads compute_trust_score() ran per poll.
TRUST_SCORE_CACHE_TTL_S = 60
# Cadence for the background refresh of run_invariants() (the divergence
# probe's Chroma gets), decoupled from the ~54s median /health rebuild gap.
INVARIANTS_REFRESH_S = 600

# Hard ceiling for utils.inference_config.probe_local_throughput()'s one
# 64-token completion against the local backend. Bounds a background task,
# never a request path — 90s covers a cold/loaded 7B CPU slot (measured
# 28-64s per call under load) without hanging the recheck loop forever if
# the backend never answers.
LOCAL_THROUGHPUT_PROBE_TIMEOUT_S = 90

__all__ = [
    "MAX_ARTIFACT_LIST",
    "MAX_UPLOAD_SIZE_BYTES",
    "HEALTH_CACHE_TTL",
    "OLLAMA_READ_TIMEOUT",
    "OLLAMA_CONNECT_TIMEOUT",
    "VERIFICATION_TIMEOUT",
    "QUERY_CACHE_TTL",
    "CHROMA_QUERY_TIMEOUT",
    "NEO4J_QUERY_TIMEOUT",
    "MONTHLY_BUDGET_USD",
    "RATE_LIMIT_WINDOW_SECONDS",
    "DEFAULT_TOP_K",
    "RETRIEVAL_CACHE_TTL",
    "CHUNK_MAX_TOKENS",
    "CHUNK_OVERLAP_RATIO",
    "PARENT_CHILD_RATIO_MIN",
    "PARENT_CHILD_RATIO_MAX",
    "QUALITY_TIER_EXCELLENT",
    "QUALITY_TIER_GOOD",
    "QUALITY_TIER_FAIR",
    "MEMORY_HALF_LIFE_DAYS",
    "MAX_CLAIMS_PER_RESPONSE",
    "CONFIDENCE_FLOOR",
    "CONFIDENCE_CEILING",
    "AI_SNIPPET_MAX_CHARS",
    "HEALTH_STATUS_CACHE_TTL_S",
    "TRUST_SCORE_CACHE_TTL_S",
    "INVARIANTS_REFRESH_S",
    "LOCAL_THROUGHPUT_PROBE_TIMEOUT_S",
]
