# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Core settings — chunking, categorization, service URLs, scheduling, and search tuning."""
from __future__ import annotations

import logging as _logging
import os

# ---------------------------------------------------------------------------
# PDF Parsing (memory-safe chunked extraction)
# ---------------------------------------------------------------------------
PDF_MAX_PAGES = int(os.getenv("PDF_MAX_PAGES", "200"))
PDF_MEMORY_LIMIT_MB = int(os.getenv("PDF_MEMORY_LIMIT_MB", "1024"))  # 1GB per PDF
PDF_LITE_THRESHOLD_PAGES = int(os.getenv("PDF_LITE_THRESHOLD_PAGES", "50"))

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
# When false (default), all LLM calls go directly to OpenRouter via llm_client.py.
# When true, Bifrost is used for intent-based auto-routing on user chat.
USE_BIFROST = os.getenv("CERID_USE_BIFROST", "false").lower() == "true"
BIFROST_URL = os.getenv("BIFROST_URL", "http://bifrost:8080/v1")
BIFROST_TIMEOUT = float(os.getenv("BIFROST_TIMEOUT", "20.0"))

# Default model for internal LLM calls (reranking, hallucination, memory extraction)
LLM_INTERNAL_MODEL = CATEGORIZE_MODELS["smart"]

# ---------------------------------------------------------------------------
# Paths
# Cross-platform: os.path.expanduser handles ~ on all platforms (macOS, Linux, Windows).
# Windows users should set explicit paths in .env (e.g., WATCH_FOLDER=C:\Users\Name\cerid-archive)
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
QUERY_CONTEXT_MAX_CHARS = 14_000    # default max chars assembled for LLM context

# Model-aware context char budgets — use larger budgets for large-context models.
# Keys are model family prefixes (matched via startswith on the model ID).
MODEL_CONTEXT_CHAR_BUDGETS: dict[str, int] = {
    "claude": 40_000,       # Claude: 200K context
    "gemini": 40_000,       # Gemini: 1M context
    "gpt-4o": 20_000,       # GPT-4o: 128K context
    "gpt-4o-mini": 14_000,  # GPT-4o-mini: 128K but cheaper, stay conservative
    "llama": 10_000,        # Llama: 32K–128K context
    "qwen": 10_000,         # Qwen: 32K–128K context
    "grok": 32_000,         # Grok: 131K context
}


def get_context_budget_for_model(model: str | None) -> int:
    """Return the context char budget for a given model ID.

    Matches model family by prefix against MODEL_CONTEXT_CHAR_BUDGETS.
    Returns QUERY_CONTEXT_MAX_CHARS as default for unknown models.
    """
    if not model:
        return QUERY_CONTEXT_MAX_CHARS
    model_lower = model.lower().split("/")[-1]  # strip provider prefix
    for prefix, budget in MODEL_CONTEXT_CHAR_BUDGETS.items():
        if model_lower.startswith(prefix):
            return budget
    return QUERY_CONTEXT_MAX_CHARS
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
# Default: Snowflake Arctic Embed M v1.5 (768d, 8192 ctx, client-side ONNX)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Snowflake/snowflake-arctic-embed-m-v1.5")
# Target dimensions (0 = use model's native output).  Matryoshka-capable models
# support truncation (e.g. 768→256 for speed).
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "0"))
EMBEDDING_ONNX_FILENAME = os.getenv("EMBEDDING_ONNX_FILENAME", "onnx/model.onnx")
EMBEDDING_MODEL_CACHE_DIR = os.getenv("EMBEDDING_MODEL_CACHE_DIR", "")

# ---------------------------------------------------------------------------
# Hallucination Detection
# ---------------------------------------------------------------------------
HALLUCINATION_THRESHOLD = float(os.getenv("HALLUCINATION_THRESHOLD", "0.65"))
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

# Expert-tier verification model — high-capability reasoning model for
# users who want maximum verification quality at higher cost.
# Grok 4: $3/$15 per 1M tokens (vs $0.15/$0.60 for GPT-4o-mini pool).
VERIFICATION_EXPERT_MODEL = os.getenv(
    "VERIFICATION_EXPERT_MODEL",
    "openrouter/x-ai/grok-4",
)

# ---------------------------------------------------------------------------
# External (Cross-Model) Verification
# ---------------------------------------------------------------------------
ENABLE_EXTERNAL_VERIFICATION = os.getenv("ENABLE_EXTERNAL_VERIFICATION", "true").lower() == "true"
EXTERNAL_VERIFY_MODEL = os.getenv("EXTERNAL_VERIFY_MODEL", "openrouter/openai/gpt-4o-mini")
EXTERNAL_VERIFY_KB_THRESHOLD = float(os.getenv("EXTERNAL_VERIFY_KB_THRESHOLD", "0.5"))
EXTERNAL_VERIFY_MAX_TOKENS = 250
EXTERNAL_VERIFY_TEMPERATURE = 0.0
EXTERNAL_VERIFY_MAX_CONCURRENT = int(os.getenv("EXTERNAL_VERIFY_MAX_CONCURRENT", "8"))
# Max concurrent claim verifications (KB search + reranking + external LLM).
# Each verification loads BM25 indices and runs ONNX cross-encoder inference,
# which is memory-intensive.  With 10+ claims, unbounded parallelism can OOM
# a 2 GB container.  Default 5 keeps peak memory manageable on most setups.
VERIFY_CLAIM_MAX_CONCURRENT = int(os.getenv("VERIFY_CLAIM_MAX_CONCURRENT", "8"))
# Minimum available container memory (MB) before allowing a new claim verification.
# Uses cgroup v2 files — no-op when running outside a memory-limited container.
VERIFY_MEMORY_FLOOR_MB = int(os.getenv("VERIFY_MEMORY_FLOOR_MB", "512"))
EXTERNAL_VERIFY_RETRY_ATTEMPTS = 3
EXTERNAL_VERIFY_RETRY_BASE_DELAY = 2.0  # seconds — defense-in-depth (1000 RPM models)
VERIFICATION_MIN_RELEVANCE = float(os.getenv("VERIFICATION_MIN_RELEVANCE", "0.35"))

# ---------------------------------------------------------------------------
# Streaming Verification Timeouts
# ---------------------------------------------------------------------------
# Per-claim timeout: max time for any single claim's full verification
# (including KB lookup, external calls, and all fallbacks).
# Reduced from 30s→15s — parallel execution means individual claims
# shouldn't block the pipeline for half a minute.
STREAMING_PER_CLAIM_TIMEOUT = float(os.getenv("STREAMING_PER_CLAIM_TIMEOUT", "15"))
# Extended timeout for expert-tier models (Grok 4 with :online web search)
# and current-event claims that require web search + reasoning
STREAMING_EXPERT_CLAIM_TIMEOUT = float(os.getenv("STREAMING_EXPERT_CLAIM_TIMEOUT", "45"))
# Total deadline for the entire streaming verification loop (all claims).
# Reduced from 120s→90s — with lower per-claim timeouts and parallel
# execution, 90s is sufficient headroom for 10 claims at 8 concurrency.
STREAMING_TOTAL_TIMEOUT = float(os.getenv("STREAMING_TOTAL_TIMEOUT", "90"))
# Fewer LLM retries on 429 during streaming to avoid compounding delays
STREAMING_RETRY_ATTEMPTS = int(os.getenv("STREAMING_RETRY_ATTEMPTS", "1"))

# ---------------------------------------------------------------------------
# Web Search (Phase 42 — agentic web search fallback)
# ---------------------------------------------------------------------------
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
SEARXNG_URL = os.getenv("SEARXNG_URL", "")  # e.g. http://localhost:8080
ENABLE_AUTO_LEARN = os.getenv("ENABLE_AUTO_LEARN", "false").lower() == "true"
WEB_SEARCH_MAX_RESULTS = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5"))
WEB_SEARCH_RATE_LIMIT = int(os.getenv("WEB_SEARCH_RATE_LIMIT", "10"))  # per minute

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

# Memory Conflict Detection & Decay (Phase 44)
MEMORY_CONFLICT_THRESHOLD = float(os.getenv("MEMORY_CONFLICT_THRESHOLD", "0.85"))
MEMORY_HALF_LIFE_DAYS = float(os.getenv("MEMORY_HALF_LIFE_DAYS", "30.0"))
MEMORY_MIN_RECALL_SCORE = float(os.getenv("MEMORY_MIN_RECALL_SCORE", "0.3"))

# ---------------------------------------------------------------------------
# Scheduled Maintenance — cron expressions
# ---------------------------------------------------------------------------
SCHEDULE_RECTIFY = os.getenv("SCHEDULE_RECTIFY", "0 3 * * *")         # daily 3 AM
SCHEDULE_HEALTH_CHECK = os.getenv("SCHEDULE_HEALTH_CHECK", "0 */6 * * *")  # every 6h
SCHEDULE_STALE_DETECTION = os.getenv("SCHEDULE_STALE_DETECTION", "0 4 * * 0")  # Sunday 4 AM
SCHEDULE_STALE_DAYS = int(os.getenv("SCHEDULE_STALE_DAYS", "90"))

# ---------------------------------------------------------------------------
# Folder Scanning
# ---------------------------------------------------------------------------
SCAN_PATHS = os.getenv("SCAN_PATHS", ARCHIVE_PATH)  # colon-separated directories to scan
SCAN_MIN_QUALITY = float(os.getenv("SCAN_MIN_QUALITY", "0.4"))  # min quality score (0-1)
SCAN_MAX_FILE_SIZE_MB = int(os.getenv("SCAN_MAX_FILE_SIZE_MB", "50"))
SCAN_EXCLUDE_PATTERNS = [p for p in os.getenv("SCAN_EXCLUDE_PATTERNS", "").split(",") if p]
SCHEDULE_FOLDER_SCAN = os.getenv("SCHEDULE_FOLDER_SCAN", "")  # cron expr, empty=disabled

# ---------------------------------------------------------------------------
# Pipeline Tuning — latency vs quality trade-offs
# ---------------------------------------------------------------------------
# Semantic cache: threshold for embedding similarity match (0.0-1.0)
SEMANTIC_CACHE_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.92"))
SEMANTIC_CACHE_TTL = int(os.getenv("SEMANTIC_CACHE_TTL", "300"))  # 5 minutes

# Query decomposition: max sub-queries to generate
QUERY_DECOMPOSITION_MAX = int(os.getenv("QUERY_DECOMPOSITION_MAX", "3"))

# Reranking: prefer local cross-encoder over LLM for speed
RERANK_PREFER_LOCAL = os.getenv("RERANK_PREFER_LOCAL", "true").lower() == "true"

# Parallel retrieval: max concurrent domain queries
PARALLEL_RETRIEVAL_MAX = int(os.getenv("PARALLEL_RETRIEVAL_MAX", "4"))

# Smart routing: when enabled, "auto" model selection in chat uses the smart
# router to pick the best model based on query complexity and availability.
SMART_ROUTING_ENABLED = os.getenv("SMART_ROUTING_ENABLED", "true").lower() == "true"

# Internal LLM: model to use for pipeline intelligence operations
# (categorization, decomposition, contextual chunks, claim extraction)
# Options: "bifrost" (default, uses Bifrost routing), "ollama" (local), or specific model ID
INTERNAL_LLM_PROVIDER = os.getenv("INTERNAL_LLM_PROVIDER", "bifrost")
INTERNAL_LLM_MODEL = os.getenv("INTERNAL_LLM_MODEL", "")  # empty = provider default

# Default Ollama model for pipeline tasks — lightweight, runs on CPU or GPU
OLLAMA_DEFAULT_MODEL = os.getenv("OLLAMA_DEFAULT_MODEL", "qwen2.5:1.5b")

# User-configurable default model for high-value intelligence tasks
# (verification, expert analysis, complex reasoning)
INTELLIGENCE_MODEL = os.getenv("INTELLIGENCE_MODEL", "")  # empty = auto-select

# ---------------------------------------------------------------------------
# Per-Stage Pipeline Providers
#   Each pipeline stage can independently route to "ollama" or "bifrost".
#   Override per-stage via env vars: PROVIDER_CLAIM_EXTRACTION=bifrost
#   Backward compat: INTERNAL_LLM_PROVIDER=ollama sets ALL stages to ollama.
# ---------------------------------------------------------------------------
_global_provider = os.getenv("INTERNAL_LLM_PROVIDER", "bifrost")

PIPELINE_PROVIDERS: dict[str, str] = {
    "claim_extraction": os.getenv("PROVIDER_CLAIM_EXTRACTION", _global_provider),
    "query_decomposition": os.getenv("PROVIDER_QUERY_DECOMPOSITION", _global_provider),
    "topic_extraction": os.getenv("PROVIDER_TOPIC_EXTRACTION", _global_provider),
    "memory_resolution": os.getenv("PROVIDER_MEMORY_RESOLUTION", _global_provider),
    "verification_simple": os.getenv("PROVIDER_VERIFICATION_SIMPLE", _global_provider),
    "verification_complex": os.getenv("PROVIDER_VERIFICATION_COMPLEX", "bifrost"),
    "reranking": os.getenv("PROVIDER_RERANKING", _global_provider),
    "chat_generation": os.getenv("PROVIDER_CHAT_GENERATION", "bifrost"),
}


def get_stage_provider(stage: str) -> str:
    """Return the LLM provider for a given pipeline stage.

    Falls back to 'bifrost' for unknown stages.
    """
    return PIPELINE_PROVIDERS.get(stage, "bifrost")

# ---------------------------------------------------------------------------
# Trading Agent Integration
# ---------------------------------------------------------------------------
CERID_TRADING_ENABLED = os.getenv("CERID_TRADING_ENABLED", "false").lower() in ("true", "1")
TRADING_AGENT_URL = os.getenv("TRADING_AGENT_URL", "http://localhost:8090")
SCHEDULE_TRADING_AUTORESEARCH = os.getenv("SCHEDULE_TRADING_AUTORESEARCH", "0 1 * * *")
SCHEDULE_PLATT_MIRROR = os.getenv("SCHEDULE_PLATT_MIRROR", "0 2 * * *")
SCHEDULE_LONGSHOT_SURFACE = os.getenv("SCHEDULE_LONGSHOT_SURFACE", "30 2 * * *")

# ---------------------------------------------------------------------------
# Boardroom Integration
# ---------------------------------------------------------------------------
CERID_BOARDROOM_ENABLED = os.getenv("CERID_BOARDROOM_ENABLED", "false").lower() in ("true", "1")
CERID_BOARDROOM_TIER = os.getenv("CERID_BOARDROOM_TIER", "foundation")

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
# Privacy — Email Header Anonymization
# ---------------------------------------------------------------------------
# When true, From/To/Cc headers in .eml/.mbox files are redacted during
# ingestion to prevent PII leakage into vector/graph stores and LLM prompts.
# Domain is preserved for context (e.g. "[redacted]@example.com").
ANONYMIZE_EMAIL_HEADERS: bool = os.getenv("CERID_ANONYMIZE_EMAIL_HEADERS", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
# "extract_only" = parse text and discard the original file (default)
# "archive"      = copy uploaded files to archive/{domain}/ for Dropbox sync
STORAGE_MODE = os.getenv("CERID_STORAGE_MODE", "extract_only")

# ---------------------------------------------------------------------------
# Sync
# Cross-platform: os.path.expanduser handles ~ on all platforms.
# ---------------------------------------------------------------------------
SYNC_DIR = os.path.expanduser(os.getenv("CERID_SYNC_DIR", "~/Dropbox/cerid-sync"))
MACHINE_ID = os.getenv("CERID_MACHINE_ID", os.uname().nodename.split(".")[0])
SYNC_BACKEND = os.getenv("CERID_SYNC_BACKEND", "local")
SCHEDULE_SYNC_EXPORT = os.getenv("SCHEDULE_SYNC_EXPORT", "")  # cron string, empty = disabled
SYNC_EXPORT_ON_INGEST = os.getenv("SYNC_EXPORT_ON_INGEST", "false").lower() == "true"
SYNC_CONFLICT_STRATEGY = os.getenv("CERID_CONFLICT_STRATEGY", "remote_wins")
TOMBSTONE_TTL_DAYS = int(os.getenv("TOMBSTONE_TTL_DAYS", "90"))
TOMBSTONE_LOG_PATH = os.path.join(os.getenv("DATA_DIR", "data"), "tombstones.jsonl")

# Auto-enable sync encryption when encryption key is available
ENCRYPT_SYNC: bool = os.getenv("CERID_ENCRYPT_SYNC", "").lower() in ("true", "1", "yes") or bool(
    os.getenv("CERID_ENCRYPTION_KEY", "")
)

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

# ---------------------------------------------------------------------------
# Consumer registry (keyed by X-Client-ID header value)
# ---------------------------------------------------------------------------
# Each consumer entry specifies:
#   rate_limits     — path prefix → (max_requests, window_seconds)
#   allowed_domains — list of KB domains the consumer may query (None = all)
#   strict_domains  — when True, disables cross-domain affinity bleed
#
# "gui" is the default for the cerid-ai React GUI (no header sent).
# "_default" is the fallback for unrecognized consumer IDs.
# See docs/INTEGRATION_GUIDE.md for adding new cerid-series consumers.

CONSUMER_REGISTRY: dict[str, dict] = {
    "gui": {
        "rate_limits": {
            "/agent/": (20, 60),
            "/sdk/": (20, 60),
            "/ingest": (10, 60),
            "/recategorize": (10, 60),
        },
        "allowed_domains": None,     # Full access to all domains
        "strict_domains": False,     # Cross-domain affinity enabled
    },
    "trading-agent": {
        "rate_limits": {
            # Worst-case burst: 5 sessions × (3 oracle + 2 memory) = 67.5 calls/min.
            # 80/min gives 12.5/min headroom; client pools (50 oracle + 20 memory)
            # are the actual binding constraints, so this is defense-in-depth.
            "/agent/": (80, 60),
            "/sdk/": (80, 60),
        },
        "allowed_domains": ["trading", "finance"],  # Scoped: no access to personal/coding/etc.
        "strict_domains": True,      # No cross-domain bleed into personal data
    },
    "cli-ingest": {
        "rate_limits": {
            "/ingest": (60, 60),
            "/recategorize": (30, 60),
        },
        "allowed_domains": None,     # Ingest into any domain
        "strict_domains": False,
    },
    "boardroom-agent": {
        "description": "Cerid Boardroom business operations agent",
        "rate_limits": {
            # 4 client-side pools: strategy (40), research (30), analytics (20), ingest (15)
            # Server-side: 40/min foundation → 100/min boardroom (tier-dependent)
            "/agent/": (40, 60),
            "/sdk/": (40, 60),
            "/ingest": (20, 60),
        },
        "allowed_domains": ["strategy", "competitive_intel", "marketing", "advertising",
                            "finance", "operations", "audit", "general"],
        "strict_domains": True,      # No bleed into personal/conversations/coding
    },
    "a2a-agent": {
        "rate_limits": {
            "/a2a/": (30, 60),
            "/agent/": (30, 60),
        },
        "allowed_domains": None,     # A2A peers get full domain access
        "strict_domains": False,
    },
    "cerid-finance": {
        "description": "Cerid Finance personal finance dashboard",
        "rate_limits": {
            "/agent/": (40, 60),     # 40 req/min — dashboard + AI chat
            "/sdk/": (40, 60),
        },
        "allowed_domains": ["finance", "strategy", "general"],
        "strict_domains": True,      # No bleed into personal/trading/coding data
    },
    "folder_scanner": {
        "rate_limits": {
            "/ingest": (60, 60),     # 60 req/min — bulk ingestion
            "/admin/": (30, 60),
        },
        "allowed_domains": None,     # Scanner can write to all domains
        "strict_domains": False,
    },
    "_default": {
        "rate_limits": {
            "/agent/": (10, 60),
            "/sdk/": (10, 60),
            "/ingest": (5, 60),
            "/recategorize": (5, 60),
        },
        "allowed_domains": None,
        "strict_domains": False,
    },
}

# Backward-compatible accessor — rate_limit.py reads this shape unchanged.
CLIENT_RATE_LIMITS: dict[str, dict[str, tuple[int, int]]] = {
    k: v["rate_limits"] for k, v in CONSUMER_REGISTRY.items()
}

if not NEO4J_PASSWORD:
    _config_logger.warning(
        "NEO4J_PASSWORD is empty — Neo4j queries will fail with auth errors. "
        "Check that .env is loaded (env_file in docker-compose.yml)."
    )
