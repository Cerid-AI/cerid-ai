# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Core settings — chunking, categorization, service URLs, scheduling, and search tuning."""
from __future__ import annotations

import logging as _logging
import os

# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
CHUNK_MAX_TOKENS = 512
CHUNK_OVERLAP = 0.2  # 20% overlap between chunks

# ---------------------------------------------------------------------------
# Categorization tiers
#   manual = domain from folder name only, no AI call
#   smart  = free model (Llama) via Bifrost
#   pro    = premium model (Claude Sonnet) via Bifrost
# ---------------------------------------------------------------------------
CATEGORIZE_MODE = os.getenv("CATEGORIZE_MODE", "smart")

CATEGORIZE_MODELS = {
    "smart": "meta-llama/llama-3.1-8b-instruct:free",
    "pro": "anthropic/claude-sonnet-4-5-20250929",
}

# Max chars of document text sent to AI for classification (~400 tokens).
AI_SNIPPET_MAX_CHARS = 1500

# ---------------------------------------------------------------------------
# Bifrost / LLM Gateway
# ---------------------------------------------------------------------------
BIFROST_URL = os.getenv("BIFROST_URL", "http://bifrost:8080/v1")
BIFROST_TIMEOUT = float(os.getenv("BIFROST_TIMEOUT", "30.0"))

# Default model for internal LLM calls (reranking, hallucination, memory extraction)
LLM_INTERNAL_MODEL = CATEGORIZE_MODELS["smart"]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ARCHIVE_PATH = os.getenv("ARCHIVE_PATH", "/archive")       # container-side mount
WATCH_FOLDER = os.getenv("WATCH_FOLDER", os.path.expanduser("~/cerid-archive"))  # host-side

# ---------------------------------------------------------------------------
# Database URLs
# ---------------------------------------------------------------------------
CHROMA_URL = os.getenv("CHROMA_URL", "http://ai-companion-chroma:8000")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://ai-companion-neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://ai-companion-redis:6379")

# ---------------------------------------------------------------------------
# Temporal Awareness
# ---------------------------------------------------------------------------
TEMPORAL_HALF_LIFE_DAYS = 30         # exponential decay half-life for recency boost
TEMPORAL_RECENCY_WEIGHT = 0.1        # max boost from recency (added to relevance)

# ---------------------------------------------------------------------------
# Hybrid Search
# ---------------------------------------------------------------------------
HYBRID_VECTOR_WEIGHT = float(os.getenv("HYBRID_VECTOR_WEIGHT", "0.6"))
HYBRID_KEYWORD_WEIGHT = float(os.getenv("HYBRID_KEYWORD_WEIGHT", "0.4"))
BM25_DATA_DIR = os.path.join(os.getenv("DATA_DIR", "data"), "bm25")
QUERY_CONTEXT_MAX_CHARS = 14_000    # max chars assembled for LLM context
QUERY_RERANK_CANDIDATES = 15        # max candidates sent to LLM reranker
RERANK_LLM_WEIGHT = float(os.getenv("RERANK_LLM_WEIGHT", "0.6"))
RERANK_ORIGINAL_WEIGHT = float(os.getenv("RERANK_ORIGINAL_WEIGHT", "0.4"))

# ---------------------------------------------------------------------------
# Knowledge Graph Traversal
# ---------------------------------------------------------------------------
GRAPH_TRAVERSAL_DEPTH = 2                     # max hops when traversing relationships
GRAPH_MAX_RELATED = 5                         # max related artifacts returned per query
GRAPH_RELATED_SCORE_FACTOR = 0.4              # score multiplier for graph-sourced results (vs direct hits)
GRAPH_MIN_KEYWORD_OVERLAP = 2                 # min shared keywords to create RELATES_TO
GRAPH_RELATIONSHIP_TYPES = [
    "RELATES_TO",       # shared metadata / same directory
    "DEPENDS_ON",       # import / reference detected in content
    "SUPERSEDES",       # re-ingested file replacing an older version
    "REFERENCES",       # explicit filename mention in content
]

# ---------------------------------------------------------------------------
# Embedding Model
# ---------------------------------------------------------------------------
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "384"))

# ---------------------------------------------------------------------------
# Hallucination Detection
# ---------------------------------------------------------------------------
HALLUCINATION_THRESHOLD = float(os.getenv("HALLUCINATION_THRESHOLD", "0.75"))

# ---------------------------------------------------------------------------
# Memory Extraction
# ---------------------------------------------------------------------------
MEMORY_RETENTION_DAYS = int(os.getenv("MEMORY_RETENTION_DAYS", "180"))

# ---------------------------------------------------------------------------
# Scheduled Maintenance — cron expressions
# ---------------------------------------------------------------------------
SCHEDULE_RECTIFY = os.getenv("SCHEDULE_RECTIFY", "0 3 * * *")         # daily 3 AM
SCHEDULE_HEALTH_CHECK = os.getenv("SCHEDULE_HEALTH_CHECK", "0 */6 * * *")  # every 6h
SCHEDULE_STALE_DETECTION = os.getenv("SCHEDULE_STALE_DETECTION", "0 4 * * 0")  # Sunday 4 AM
SCHEDULE_STALE_DAYS = int(os.getenv("SCHEDULE_STALE_DAYS", "90"))

# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------
# List of webhook endpoints. Each entry: {"url": "...", "events": ["ingestion.complete", ...]}
# If "events" is omitted, all events are sent.
# Configure via WEBHOOK_URLS env var (comma-separated URLs for all events).
_webhook_urls = os.getenv("WEBHOOK_URLS", "")
WEBHOOK_ENDPOINTS = [
    {"url": u.strip()} for u in _webhook_urls.split(",") if u.strip()
]

# ---------------------------------------------------------------------------
# Redis keys
# ---------------------------------------------------------------------------
REDIS_INGEST_LOG = "ingest:log"
REDIS_LOG_MAX = 10_000

# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
SYNC_DIR = os.path.expanduser(os.getenv("CERID_SYNC_DIR", "~/Dropbox/cerid-sync"))
MACHINE_ID = os.getenv("CERID_MACHINE_ID", os.uname().nodename.split(".")[0])
SYNC_BACKEND = os.getenv("CERID_SYNC_BACKEND", "local")

# ---------------------------------------------------------------------------
# Startup validation — normalize and warn on unrecognized values
# ---------------------------------------------------------------------------
_config_logger = _logging.getLogger("ai-companion.config")

CATEGORIZE_MODE = CATEGORIZE_MODE.strip().lower()
if CATEGORIZE_MODE not in ("manual", "smart", "pro"):
    _config_logger.warning(
        "Invalid CATEGORIZE_MODE=%r, defaulting to 'smart'", CATEGORIZE_MODE
    )
    CATEGORIZE_MODE = "smart"

if not NEO4J_PASSWORD:
    _config_logger.warning(
        "NEO4J_PASSWORD is empty — Neo4j queries will fail with auth errors. "
        "Check that .env is loaded (env_file in docker-compose.yml)."
    )
