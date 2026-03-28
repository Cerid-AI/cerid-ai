# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Centralized constants — THE source of truth for all magic numbers.

AI agents: import from here. Never hardcode numeric literals in business logic.
Dependencies: none (pure values module).
"""
from __future__ import annotations

# ── Artifact limits ──────────────────────────────────────────────────
MAX_ARTIFACT_LIST = 10_000
MAX_ARTIFACTS_PER_DOMAIN = 200
MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

# ── Timeouts (seconds) ──────────────────────────────────────────────
HEALTH_CACHE_TTL = 10.0
A2A_TASK_TTL = 3600
OLLAMA_READ_TIMEOUT = 120.0
OLLAMA_CONNECT_TIMEOUT = 10.0
BIFROST_TIMEOUT = 30.0
VERIFICATION_TIMEOUT = 30.0
QUERY_CACHE_TTL = 300  # 5 minutes

# ── Budget & rate limits ────────────────────────────────────────────
MONTHLY_BUDGET_USD = 20.0
RATE_LIMIT_WINDOW_SECONDS = 60

# ── Retrieval tuning ────────────────────────────────────────────────
DEFAULT_TOP_K = 10
RETRIEVAL_CACHE_TTL = 1800  # 30 minutes
SEMANTIC_CACHE_SIMILARITY_THRESHOLD = 0.92
HYDE_TRIGGER_THRESHOLD = 0.4
CHUNK_MAX_TOKENS = 512
CHUNK_OVERLAP_RATIO = 0.2
PARENT_CHILD_RATIO_MIN = 4  # min child:parent token ratio
PARENT_CHILD_RATIO_MAX = 8

# ── Verification ────────────────────────────────────────────────────
MAX_CLAIMS_PER_RESPONSE = 20
CONFIDENCE_FLOOR = 0.3
CONFIDENCE_CEILING = 0.95

# ── Observability ───────────────────────────────────────────────────
OBSERVABILITY_RETENTION_SECONDS = 10_000
METRICS_HISTORY_LENGTH = 100

# ── Ingestion ───────────────────────────────────────────────────────
AI_SNIPPET_MAX_CHARS = 1500
PDF_DEFAULT_MAX_PAGES = 200
PDF_DEFAULT_MEMORY_LIMIT_MB = 1024
DEDUP_BATCH_SIZE = 100

__all__ = [
    "MAX_ARTIFACT_LIST",
    "MAX_ARTIFACTS_PER_DOMAIN",
    "MAX_UPLOAD_SIZE_BYTES",
    "HEALTH_CACHE_TTL",
    "A2A_TASK_TTL",
    "OLLAMA_READ_TIMEOUT",
    "OLLAMA_CONNECT_TIMEOUT",
    "BIFROST_TIMEOUT",
    "VERIFICATION_TIMEOUT",
    "QUERY_CACHE_TTL",
    "MONTHLY_BUDGET_USD",
    "RATE_LIMIT_WINDOW_SECONDS",
    "DEFAULT_TOP_K",
    "RETRIEVAL_CACHE_TTL",
    "SEMANTIC_CACHE_SIMILARITY_THRESHOLD",
    "HYDE_TRIGGER_THRESHOLD",
    "CHUNK_MAX_TOKENS",
    "CHUNK_OVERLAP_RATIO",
    "PARENT_CHILD_RATIO_MIN",
    "PARENT_CHILD_RATIO_MAX",
    "MAX_CLAIMS_PER_RESPONSE",
    "CONFIDENCE_FLOOR",
    "CONFIDENCE_CEILING",
    "OBSERVABILITY_RETENTION_SECONDS",
    "METRICS_HISTORY_LENGTH",
    "AI_SNIPPET_MAX_CHARS",
    "PDF_DEFAULT_MAX_PAGES",
    "PDF_DEFAULT_MEMORY_LIMIT_MB",
    "DEDUP_BATCH_SIZE",
]
