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
CHUNKING_MODE = os.getenv("CHUNKING_MODE", "semantic")  # "token" or "semantic"

# Contextual chunking — LLM-generated situational summaries prepended to each chunk.
# Uses a lightweight model via Bifrost during ingestion.  Toggle: ENABLE_CONTEXTUAL_CHUNKS.
CONTEXTUAL_CHUNKS_MODEL = os.getenv("CONTEXTUAL_CHUNKS_MODEL", "openrouter/meta-llama/llama-3.3-70b-instruct:free")

# ---------------------------------------------------------------------------
# Categorization tiers
#   manual = domain from folder name only, no AI call
#   smart  = free model (Llama) via Bifrost
#   pro    = premium model (Claude Sonnet) via Bifrost
# ---------------------------------------------------------------------------
CATEGORIZE_MODE = os.getenv("CATEGORIZE_MODE", "smart")

CATEGORIZE_MODELS = {
    "smart": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "pro": "openrouter/anthropic/claude-sonnet-4.6",
}

# Max chars of document text sent to AI for classification (~400 tokens).
AI_SNIPPET_MAX_CHARS = 1500

# ---------------------------------------------------------------------------
# Bifrost / LLM Gateway
# ---------------------------------------------------------------------------
BIFROST_URL = os.getenv("BIFROST_URL", "http://bifrost:8080/v1")
BIFROST_TIMEOUT = float(os.getenv("BIFROST_TIMEOUT", "20.0"))

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
_redis_password = os.getenv("REDIS_PASSWORD", "")
REDIS_URL = os.getenv(
    "REDIS_URL",
    f"redis://:{_redis_password}@ai-companion-redis:6379"
    if _redis_password
    else "redis://ai-companion-redis:6379",
)

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
QUERY_RERANK_CANDIDATES = 15        # max candidates sent to reranker
QUERY_CONTEXT_MESSAGES = 5          # max conversation messages used for query enrichment

# Rerank mode: "cross_encoder" (fast local ONNX), "llm" (Bifrost), "none"
RERANK_MODE = os.getenv("RERANK_MODE", "cross_encoder")

# Cross-encoder model (HuggingFace repo ID)
RERANK_CROSS_ENCODER_MODEL = os.getenv(
    "RERANK_CROSS_ENCODER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2",
)
# ONNX filename within the repo — use quantized variants for faster inference:
#   onnx/model.onnx            (91 MB, float32, any CPU)
#   onnx/model_quint8_avx2.onnx (23 MB, int8, requires AVX2)
RERANK_ONNX_FILENAME = os.getenv("RERANK_ONNX_FILENAME", "onnx/model.onnx")
RERANK_MODEL_CACHE_DIR = os.getenv("RERANK_MODEL_CACHE_DIR", "")

# Score blending weights (cross-encoder or LLM score vs original hybrid score)
RERANK_CE_WEIGHT = float(os.getenv("RERANK_CE_WEIGHT", "0.6"))
RERANK_LLM_WEIGHT = float(os.getenv("RERANK_LLM_WEIGHT", "0.6"))
RERANK_ORIGINAL_WEIGHT = float(os.getenv("RERANK_ORIGINAL_WEIGHT", "0.4"))

# ---------------------------------------------------------------------------
# Knowledge Graph Traversal
# ---------------------------------------------------------------------------
GRAPH_TRAVERSAL_DEPTH = 2                     # max hops when traversing relationships
GRAPH_MAX_RELATED = 5                         # max related artifacts returned per query
GRAPH_RELATED_SCORE_FACTOR = 0.6              # score multiplier for graph-sourced results (vs direct hits)
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
# HuggingFace repo ID.  "all-MiniLM-L6-v2" uses ChromaDB's built-in server-side
# embedding (backward compatible, no migration needed).  Any other model triggers
# client-side ONNX embedding via utils/embeddings.py.
# Recommended upgrade: "Snowflake/snowflake-arctic-embed-m-v1.5" (768d, 8192 ctx)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
# Target dimensions (0 = use model's native output).  Matryoshka-capable models
# support truncation (e.g. 768→256 for speed).
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "0"))
EMBEDDING_ONNX_FILENAME = os.getenv("EMBEDDING_ONNX_FILENAME", "onnx/model.onnx")
EMBEDDING_MODEL_CACHE_DIR = os.getenv("EMBEDDING_MODEL_CACHE_DIR", "")

# ---------------------------------------------------------------------------
# Hallucination Detection
# ---------------------------------------------------------------------------
HALLUCINATION_THRESHOLD = float(os.getenv("HALLUCINATION_THRESHOLD", "0.75"))
HALLUCINATION_UNVERIFIED_THRESHOLD = float(os.getenv("HALLUCINATION_UNVERIFIED_THRESHOLD", "0.4"))
HALLUCINATION_MIN_RESPONSE_LENGTH = int(os.getenv("HALLUCINATION_MIN_RESPONSE_LENGTH", "50"))
HALLUCINATION_MAX_CLAIMS = int(os.getenv("HALLUCINATION_MAX_CLAIMS", "10"))

# ---------------------------------------------------------------------------
# Verification Pipeline (claim extraction, Q-conversion, cross-model check)
# ---------------------------------------------------------------------------
# Primary model for ALL verification LLM calls — must be non-rate-limited.
# GPT-4o-mini: 1000 RPM, $0.15/$0.60 per 1M tokens.
VERIFICATION_MODEL = os.getenv("VERIFICATION_MODEL", "openrouter/openai/gpt-4o-mini")

# Pool of non-rate-limited models for cross-model diversity selection.
# _pick_verification_model() picks from this pool, preferring a different
# model family than the generator to avoid correlated hallucinations.
VERIFICATION_MODEL_POOL = [
    "openrouter/openai/gpt-4o-mini",       # OpenAI — $0.15/$0.60
    "openrouter/google/gemini-2.5-flash",   # Google — $0.30/$2.50
    "openrouter/x-ai/grok-4.1-fast",       # xAI — $0.20/$0.50, web search capable
]

# Model with live web search for current-event claim verification.
# The `:online` suffix enables OpenRouter's native web search plugin
# which uses xAI's built-in web_search tool for Grok models.
# Grok 4.1 Fast: $0.20/$0.50 per 1M tokens, web search currently free.
VERIFICATION_CURRENT_EVENT_MODEL = os.getenv(
    "VERIFICATION_CURRENT_EVENT_MODEL",
    "openrouter/x-ai/grok-4.1-fast:online",
)

# Stronger model for consistency checking (cross-turn contradiction detection).
# Needs better reasoning than GPT-4o-mini; Gemini 2.5 Flash is 10x cheaper than
# Sonnet but significantly better at nuanced multi-text comparison.
VERIFICATION_CONSISTENCY_MODEL = os.getenv(
    "VERIFICATION_CONSISTENCY_MODEL",
    "openrouter/google/gemini-2.5-flash",
)
# Stronger model for complex factual claims (causal, comparative, multi-hop).
# Falls back to VERIFICATION_MODEL pool for simple factual claims.
VERIFICATION_COMPLEX_MODEL = os.getenv(
    "VERIFICATION_COMPLEX_MODEL",
    "openrouter/google/gemini-2.5-flash",
)

# ---------------------------------------------------------------------------
# External (Cross-Model) Verification
# ---------------------------------------------------------------------------
ENABLE_EXTERNAL_VERIFICATION = os.getenv("ENABLE_EXTERNAL_VERIFICATION", "true").lower() == "true"
EXTERNAL_VERIFY_MODEL = os.getenv("EXTERNAL_VERIFY_MODEL", "openrouter/openai/gpt-4o-mini")
EXTERNAL_VERIFY_KB_THRESHOLD = float(os.getenv("EXTERNAL_VERIFY_KB_THRESHOLD", "0.5"))
EXTERNAL_VERIFY_MAX_TOKENS = 250
EXTERNAL_VERIFY_TEMPERATURE = 0.0
EXTERNAL_VERIFY_MAX_CONCURRENT = int(os.getenv("EXTERNAL_VERIFY_MAX_CONCURRENT", "5"))
# Max concurrent claim verifications (KB search + reranking + external LLM).
# Each verification loads BM25 indices and runs ONNX cross-encoder inference,
# which is memory-intensive.  With 10+ claims, unbounded parallelism can OOM
# a 2 GB container.  Default 3 keeps peak memory well under 1 GB.
VERIFY_CLAIM_MAX_CONCURRENT = int(os.getenv("VERIFY_CLAIM_MAX_CONCURRENT", "3"))
EXTERNAL_VERIFY_RETRY_ATTEMPTS = 3
EXTERNAL_VERIFY_RETRY_BASE_DELAY = 2.0  # seconds — defense-in-depth (1000 RPM models)
VERIFICATION_MIN_RELEVANCE = float(os.getenv("VERIFICATION_MIN_RELEVANCE", "0.35"))

# ---------------------------------------------------------------------------
# Streaming Verification Timeouts
# ---------------------------------------------------------------------------
# Per-claim timeout: max time for any single claim's full verification
# (including KB lookup, external calls, and all fallbacks)
STREAMING_PER_CLAIM_TIMEOUT = float(os.getenv("STREAMING_PER_CLAIM_TIMEOUT", "45"))
# Total deadline for the entire streaming verification loop (all claims)
STREAMING_TOTAL_TIMEOUT = float(os.getenv("STREAMING_TOTAL_TIMEOUT", "180"))
# Fewer LLM retries on 429 during streaming to avoid compounding delays
STREAMING_RETRY_ATTEMPTS = int(os.getenv("STREAMING_RETRY_ATTEMPTS", "1"))

# ---------------------------------------------------------------------------
# Self-RAG (retrieval-augmented generation validation loop)
# ---------------------------------------------------------------------------
SELF_RAG_MAX_ITERATIONS = int(os.getenv("SELF_RAG_MAX_ITERATIONS", "2"))
SELF_RAG_WEAK_CLAIM_THRESHOLD = float(os.getenv("SELF_RAG_WEAK_CLAIM_THRESHOLD", "0.5"))
SELF_RAG_MAX_REFINED_QUERIES = int(os.getenv("SELF_RAG_MAX_REFINED_QUERIES", "3"))
SELF_RAG_REFINED_TOP_K = int(os.getenv("SELF_RAG_REFINED_TOP_K", "5"))

# ---------------------------------------------------------------------------
# Auto-Injection
# ---------------------------------------------------------------------------
AUTO_INJECT_THRESHOLD = float(os.getenv("AUTO_INJECT_THRESHOLD", "0.82"))
AUTO_INJECT_MAX = int(os.getenv("AUTO_INJECT_MAX", "3"))

# ---------------------------------------------------------------------------
# Context Budget
# ---------------------------------------------------------------------------
CONTEXT_MAX_CHUNKS_PER_ARTIFACT = 2  # max chunks from same artifact in assembled context

# ---------------------------------------------------------------------------
# Quality Scoring
# ---------------------------------------------------------------------------
QUALITY_WEIGHT_SUMMARY = 0.30       # weight for summary quality dimension
QUALITY_WEIGHT_KEYWORDS = 0.25      # weight for keyword quality dimension
QUALITY_WEIGHT_FRESHNESS = 0.20     # weight for freshness dimension
QUALITY_WEIGHT_COMPLETENESS = 0.25  # weight for metadata completeness dimension
QUALITY_SUMMARY_MIN_CHARS = 50      # below this: linear ramp to 0
QUALITY_SUMMARY_MAX_CHARS = 500     # above this: gentle penalty
QUALITY_KEYWORDS_OPTIMAL = 5        # keyword count for max score

QUALITY_BOOST_FACTOR = 0.4          # max relevance multiplier from quality
QUALITY_BOOST_BASE = 0.8            # base multiplier (score=0 → 0.8x, score=0.5 → 1.0x, score=1.0 → 1.2x)

QUALITY_METADATA_TAG_BOOST = 0.05   # per-matching-tag relevance boost
QUALITY_METADATA_SUBCAT_BOOST = 0.08  # sub_category match relevance boost
QUALITY_METADATA_MAX_BOOST = 0.15   # cap on total metadata boost
QUALITY_MIN_RELEVANCE_THRESHOLD = float(os.getenv("QUALITY_MIN_RELEVANCE_THRESHOLD", "0.15"))

# ---------------------------------------------------------------------------
# Conversation Context Alignment
# ---------------------------------------------------------------------------
CONTEXT_BOOST_WEIGHT = float(os.getenv("CONTEXT_BOOST_WEIGHT", "0.08"))

# ---------------------------------------------------------------------------
# Synopsis Generation (AI-generated artifact summaries via curator)
# ---------------------------------------------------------------------------
SYNOPSIS_MODEL = CATEGORIZE_MODELS["smart"]   # free Llama model via Bifrost
SYNOPSIS_MAX_INPUT_CHARS = 2000
SYNOPSIS_MAX_TOKENS = 100

# Synopsis model options — user-selectable, with cost and throttle info
SYNOPSIS_MODEL_OPTIONS = {
    "openrouter/meta-llama/llama-3.3-70b-instruct:free": {
        "label": "Llama 3.3 (Free)",
        "input_per_1m": 0.0,
        "output_per_1m": 0.0,
        "rpm": 8,
        "throttle": 8.0,
    },
    "openrouter/openai/gpt-4o-mini": {
        "label": "GPT-4o Mini",
        "input_per_1m": 0.15,
        "output_per_1m": 0.60,
        "rpm": 1000,
        "throttle": 0.5,
    },
    "openrouter/google/gemini-2.5-flash": {
        "label": "Gemini 2.5 Flash",
        "input_per_1m": 0.30,
        "output_per_1m": 2.50,
        "rpm": 1000,
        "throttle": 0.5,
    },
    "openrouter/anthropic/claude-sonnet-4.6": {
        "label": "Claude Sonnet 4.6",
        "input_per_1m": 3.0,
        "output_per_1m": 15.0,
        "rpm": 1000,
        "throttle": 0.5,
    },
}

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
# Storage
# ---------------------------------------------------------------------------
# "extract_only" = parse text and discard the original file (default)
# "archive"      = copy uploaded files to archive/{domain}/ for Dropbox sync
STORAGE_MODE = os.getenv("CERID_STORAGE_MODE", "extract_only")

# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
SYNC_DIR = os.path.expanduser(os.getenv("CERID_SYNC_DIR", "~/Dropbox/cerid-sync"))
MACHINE_ID = os.getenv("CERID_MACHINE_ID", os.uname().nodename.split(".")[0])
SYNC_BACKEND = os.getenv("CERID_SYNC_BACKEND", "local")
SCHEDULE_SYNC_EXPORT = os.getenv("SCHEDULE_SYNC_EXPORT", "")  # cron string, empty = disabled
SYNC_EXPORT_ON_INGEST = os.getenv("SYNC_EXPORT_ON_INGEST", "false").lower() == "true"
SYNC_CONFLICT_STRATEGY = os.getenv("CERID_CONFLICT_STRATEGY", "remote_wins")
TOMBSTONE_TTL_DAYS = int(os.getenv("TOMBSTONE_TTL_DAYS", "90"))
TOMBSTONE_LOG_PATH = os.path.join(os.getenv("DATA_DIR", "data"), "tombstones.jsonl")

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
