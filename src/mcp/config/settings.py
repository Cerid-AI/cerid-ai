# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Core settings — chunking, categorization, service URLs, scheduling, and search tuning."""
from __future__ import annotations

import logging as _logging
import os
import re as _re

from config.constants import CHUNK_MAX_TOKENS  # noqa: F401  # re-exported
from utils.model_registry import get_model

# ---------------------------------------------------------------------------
# Sentry (opt-in error monitoring)
# ---------------------------------------------------------------------------
ENABLE_SENTRY = os.getenv("ENABLE_SENTRY", "false").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# PDF Parsing (memory-safe chunked extraction)
# ---------------------------------------------------------------------------
PDF_MAX_PAGES = int(os.getenv("PDF_MAX_PAGES", "200"))
PDF_MEMORY_LIMIT_MB = int(os.getenv("PDF_MEMORY_LIMIT_MB", "1024"))  # 1GB per PDF
PDF_LITE_THRESHOLD_PAGES = int(os.getenv("PDF_LITE_THRESHOLD_PAGES", "50"))

# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

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

# Default model for internal LLM calls (reranking, hallucination, memory extraction).
# Uses gpt-4o-mini by default — cheap ($0.15/$0.60 per 1M tokens), reliable, no rate limits.
# The free Llama model hits 429 rate limits frequently, causing 10s+ retry delays.
# Default sourced from model registry (utils/model_registry.py).
LLM_INTERNAL_MODEL = os.getenv("LLM_INTERNAL_MODEL", "") or get_model("internal", "default")

# ---------------------------------------------------------------------------
# Paths
# Cross-platform: os.path.expanduser handles ~ on all platforms (macOS, Linux, Windows).
# Windows users should set explicit paths in .env (e.g., WATCH_FOLDER=C:\Users\Name\cerid-archive)
# ---------------------------------------------------------------------------
ARCHIVE_PATH = os.getenv("ARCHIVE_PATH", "/archive")       # container-side mount
WATCH_FOLDER = os.getenv("WATCH_FOLDER", os.path.expanduser("~/cerid-archive"))  # host-side

# ---------------------------------------------------------------------------
# Lightweight Mode (8GB machines — skips Neo4j, graph features degrade)
# ---------------------------------------------------------------------------
CERID_LIGHTWEIGHT = os.getenv("CERID_LIGHTWEIGHT", "false").lower() in ("true", "1", "yes")

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
HYBRID_VECTOR_WEIGHT = float(os.getenv("HYBRID_VECTOR_WEIGHT", "0.5"))
HYBRID_KEYWORD_WEIGHT = float(os.getenv("HYBRID_KEYWORD_WEIGHT", "0.5"))
BM25_DATA_DIR = os.path.join(os.getenv("DATA_DIR", "data"), "bm25")

# CRAG-style retrieval quality gate — if top result relevance is below this
# threshold after initial retrieval, supplement with external sources before
# proceeding to expensive reranking/generation.
RETRIEVAL_QUALITY_THRESHOLD = float(os.getenv("RETRIEVAL_QUALITY_THRESHOLD", "0.4"))

# ---------------------------------------------------------------------------
# Storage Monitoring
# ---------------------------------------------------------------------------
STORAGE_WARN_PCT = int(os.getenv("CERID_STORAGE_WARN_PCT", "60"))
STORAGE_CRITICAL_PCT = int(os.getenv("CERID_STORAGE_CRITICAL_PCT", "80"))
STORAGE_LIMIT_MB = int(os.getenv("CERID_STORAGE_LIMIT_MB", "2048"))
INGEST_HISTORY_RETENTION_DAYS = int(os.getenv("CERID_INGEST_HISTORY_DAYS", "7"))

QUERY_CONTEXT_MAX_CHARS = 40_000    # default max chars assembled for LLM context

# Model-aware context char budgets — use larger budgets for large-context models.
# Keys are model family prefixes (matched via startswith on the model ID).
MODEL_CONTEXT_CHAR_BUDGETS: dict[str, int] = {
    "claude": 120_000,      # Claude: 1M context — use more of it
    "gemini": 120_000,      # Gemini: 1M context
    "gpt-4o": 40_000,       # GPT-4o: 128K context
    "gpt-4o-mini": 20_000,  # GPT-4o-mini: 128K but cheaper
    "llama": 16_000,        # Llama: 32K–128K context
    "grok": 60_000,         # Grok: 2M context — generous budget
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

# ---------------------------------------------------------------------------
# RAG Mode — controls automatic knowledge injection behavior
#   smart  = classify intent, inject for factual/code/analytical, skip for creative/conversational
#   always = always inject full KB context regardless of intent
#   manual = only inject when user explicitly requests it
# ---------------------------------------------------------------------------
RAG_MODE = os.getenv("RAG_MODE", "smart")

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

# ---------------------------------------------------------------------------
# NLI Entailment (Natural Language Inference)
# ---------------------------------------------------------------------------
NLI_MODEL = os.getenv("NLI_MODEL", "cross-encoder/nli-deberta-v3-xsmall")
NLI_ONNX_FILENAME = os.getenv("NLI_ONNX_FILENAME", "onnx/model.onnx")
NLI_MODEL_CACHE_DIR = os.getenv("NLI_MODEL_CACHE_DIR", "")
NLI_ENTAILMENT_THRESHOLD = float(os.getenv("NLI_ENTAILMENT_THRESHOLD", "0.7"))
NLI_CONTRADICTION_THRESHOLD = float(os.getenv("NLI_CONTRADICTION_THRESHOLD", "0.6"))

# ---------------------------------------------------------------------------
# Verified Memory Promotion
# ---------------------------------------------------------------------------
# Automatically promote high-confidence verified claims to empirical memories.
ENABLE_VERIFIED_MEMORY_PROMOTION = os.getenv("ENABLE_VERIFIED_MEMORY_PROMOTION", "true").lower() == "true"
VERIFIED_MEMORY_MIN_CONFIDENCE = float(os.getenv("VERIFIED_MEMORY_MIN_CONFIDENCE", "0.8"))
VERIFIED_MEMORY_MIN_NLI = float(os.getenv("VERIFIED_MEMORY_MIN_NLI", "0.7"))
# NLI guard for memory consolidation — prevents semantic drift during merges.
MEMORY_CONSOLIDATION_NLI_GUARD = float(os.getenv("MEMORY_CONSOLIDATION_NLI_GUARD", "0.7"))

# ---------------------------------------------------------------------------
# Graph-Guided Verification & Authoritative Expert Verification
# ---------------------------------------------------------------------------
# Confidence boost when source artifacts have graph connections to verified artifacts.
GRAPH_VERIFICATION_BOOST = float(os.getenv("GRAPH_VERIFICATION_BOOST", "0.05"))
# Use authoritative external data sources (not just LLM) for expert verification.
EXPERT_VERIFY_USE_AUTHORITATIVE_SOURCES = os.getenv("EXPERT_VERIFY_USE_AUTHORITATIVE_SOURCES", "true").lower() == "true"
# Max authoritative sources queried per expert verification call.
EXPERT_VERIFY_MAX_SOURCES = int(os.getenv("EXPERT_VERIFY_MAX_SOURCES", "3"))

# Score blending weights (cross-encoder or LLM score vs original hybrid score)
RERANK_CE_WEIGHT = float(os.getenv("RERANK_CE_WEIGHT", "0.4"))
RERANK_LLM_WEIGHT = float(os.getenv("RERANK_LLM_WEIGHT", "0.4"))
RERANK_ORIGINAL_WEIGHT = float(os.getenv("RERANK_ORIGINAL_WEIGHT", "0.6"))

# ---------------------------------------------------------------------------
# Knowledge Graph Traversal
# ---------------------------------------------------------------------------
GRAPH_TRAVERSAL_DEPTH = 2                     # max hops when traversing relationships
GRAPH_MAX_RELATED = 5                         # max related artifacts returned per query
GRAPH_RELATED_SCORE_FACTOR = 0.6              # score multiplier for graph-sourced results (vs direct hits)
GRAPH_MIN_KEYWORD_OVERLAP = 2                 # min shared keywords to create RELATES_TO

# ---------------------------------------------------------------------------
# Graph RAG — entity-aware retrieval using Neo4j
# ---------------------------------------------------------------------------
GRAPH_RAG_WEIGHT = float(os.getenv("GRAPH_RAG_WEIGHT", "0.3"))   # blend weight (0.0–1.0)
GRAPH_RAG_MAX_HOPS = int(os.getenv("GRAPH_RAG_MAX_HOPS", "2"))   # traversal depth
GRAPH_RAG_MAX_RESULTS = int(os.getenv("GRAPH_RAG_MAX_RESULTS", "10"))  # max graph results
GRAPH_RELATIONSHIP_TYPES = [
    "RELATES_TO",       # shared metadata / same directory
    "DEPENDS_ON",       # import / reference detected in content
    "SUPERSEDES",       # re-ingested file replacing an older version
    "REFERENCES",       # explicit filename mention in content
]

# Validate relationship type names are safe for Cypher injection
for _rt in GRAPH_RELATIONSHIP_TYPES:
    assert _re.fullmatch(r"[A-Z_]+", _rt), f"Invalid GRAPH_RELATIONSHIP_TYPE: {_rt!r} — must match ^[A-Z_]+$"

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
# Defaults sourced from model registry (utils/model_registry.py).
VERIFICATION_MODEL = os.getenv("VERIFICATION_MODEL", "") or get_model("verification", "default")

# Pool of non-rate-limited models for cross-model diversity selection.
# _pick_verification_model() picks from this pool, preferring a different
# model family than the generator to avoid correlated hallucinations.
from utils.model_registry import ACTIVE_MODELS as _ACTIVE_MODELS  # noqa: E402

VERIFICATION_MODEL_POOL = _ACTIVE_MODELS["verification"]["pool"]

# Model with live web search for current-event claim verification.
# The `:online` suffix enables OpenRouter's native web search plugin
# which uses xAI's built-in web_search tool for Grok models.
VERIFICATION_CURRENT_EVENT_MODEL = os.getenv(
    "VERIFICATION_CURRENT_EVENT_MODEL", "",
) or get_model("verification", "web_search")

# Stronger model for consistency checking (cross-turn contradiction detection).
# Needs better reasoning than GPT-4o-mini; Gemini 2.5 Flash is 10x cheaper than
# Sonnet but significantly better at nuanced multi-text comparison.
VERIFICATION_CONSISTENCY_MODEL = os.getenv(
    "VERIFICATION_CONSISTENCY_MODEL", "",
) or get_model("verification", "consistency")

# Stronger model for complex factual claims (causal, comparative, multi-hop).
# Falls back to VERIFICATION_MODEL pool for simple factual claims.
VERIFICATION_COMPLEX_MODEL = os.getenv(
    "VERIFICATION_COMPLEX_MODEL", "",
) or get_model("verification", "complex")

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
EXTERNAL_VERIFY_MODEL = os.getenv("EXTERNAL_VERIFY_MODEL", "") or get_model("verification", "default")
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
# Per-claim verification timeout (KB lookup + optional LLM verification).
STREAMING_PER_CLAIM_TIMEOUT = float(os.getenv("STREAMING_PER_CLAIM_TIMEOUT", "15"))
# Extended timeout for expert-tier models (Grok 4 with :online web search)
STREAMING_EXPERT_CLAIM_TIMEOUT = float(os.getenv("STREAMING_EXPERT_CLAIM_TIMEOUT", "30"))
# Total deadline for the entire streaming verification loop (all claims).
STREAMING_TOTAL_TIMEOUT = float(os.getenv("STREAMING_TOTAL_TIMEOUT", "90"))
# Fewer LLM retries on 429 during streaming to avoid compounding delays
STREAMING_RETRY_ATTEMPTS = int(os.getenv("STREAMING_RETRY_ATTEMPTS", "1"))

# ---------------------------------------------------------------------------
# Web Search — agentic web search fallback
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
AUTO_INJECT_THRESHOLD = float(os.getenv("AUTO_INJECT_THRESHOLD", "0.15"))
AUTO_INJECT_MAX = int(os.getenv("AUTO_INJECT_MAX", "3"))

# ---------------------------------------------------------------------------
# Context Budget
# ---------------------------------------------------------------------------
CONTEXT_MAX_CHUNKS_PER_ARTIFACT = 5  # max chunks from same artifact in assembled context

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
QUALITY_MIN_RELEVANCE_THRESHOLD = float(os.getenv("QUALITY_MIN_RELEVANCE_THRESHOLD", "0.35"))

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

# Memory Conflict Detection & Decay
MEMORY_CONFLICT_THRESHOLD = float(os.getenv("MEMORY_CONFLICT_THRESHOLD", "0.85"))
MEMORY_HALF_LIFE_DAYS = float(os.getenv("MEMORY_HALF_LIFE_DAYS", "30.0"))  # legacy fallback
MEMORY_MIN_RECALL_SCORE = float(os.getenv("MEMORY_MIN_RECALL_SCORE", "0.3"))
MEMORY_MIN_RECALL_BY_TYPE: dict[str, float] = {
    "empirical": 0.4,
    "decision": 0.4,
    "preference": 0.4,
    "project_context": 0.45,
    "temporal": 0.5,
    "conversational": 0.55,
}

# Memory Salience — per-type stability and scoring
# Stability = base half-life in days for decay. Higher = slower fade.
# "empirical" uses float("inf") — permanent facts never decay.
MEMORY_TYPE_STABILITY: dict[str, float] = {
    "empirical": float("inf"),       # "Python has a GIL" — no decay
    "decision": 90.0,                # "Chose Postgres over Mongo" — slow power-law
    "preference": 60.0,              # "User prefers Rust" — moderate power-law
    "project_context": 14.0,         # "Working on feature X" — fast exponential
    "temporal": 0.0,                 # "Meeting on Tuesday" — event-based step function
    "conversational": 3.0,           # Casual chat, small talk — very fast exponential
}
# Power-law decay types get long-tail preservation; exponential types fade fast.
MEMORY_POWER_LAW_TYPES = {"empirical", "decision", "preference"}
MEMORY_EXPONENTIAL_TYPES = {"project_context", "temporal", "conversational"}

# Source authority weights — how much to trust different memory sources.
SOURCE_AUTHORITY_WEIGHTS: dict[str, float] = {
    "user_stated": 1.0,
    "user_document": 0.9,
    "llm_extracted": 0.7,
    "agent_inferred": 0.5,
    "web_search": 0.4,
}
# Default source authority for memories without an explicit source type.
DEFAULT_SOURCE_AUTHORITY = 0.7

# All valid memory types (6 types).
MEMORY_TYPES = {"empirical", "decision", "preference", "project_context", "temporal", "conversational"}

# Mapping from legacy types to current types (for migration).
MEMORY_TYPE_MIGRATION: dict[str, str] = {
    "fact": "empirical",
    "action_item": "project_context",
    # "decision" and "preference" remain unchanged
}

# Max access log entries stored per memory node (for recency-weighted counting).
MEMORY_ACCESS_LOG_MAX = 50

# ---------------------------------------------------------------------------
# Retrieval Orchestration (RAG modes)
# ---------------------------------------------------------------------------
RAG_ORCHESTRATION_MODE = os.getenv("RAG_ORCHESTRATION_MODE", "manual")  # manual|smart|custom_smart
MEMORY_RECALL_TOP_K = int(os.getenv("MEMORY_RECALL_TOP_K", "5"))
MEMORY_RECALL_MIN_SCORE = float(os.getenv("MEMORY_RECALL_MIN_SCORE", "0.4"))
MEMORY_RECALL_TIMEOUT_MS = int(os.getenv("MEMORY_RECALL_TIMEOUT_MS", "200"))

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
SCHEDULE_WATCHED_RESCAN = os.getenv("SCHEDULE_WATCHED_RESCAN", "")  # cron expr, e.g. "0 */6 * * *"=every 6h, empty=disabled
SCHEDULE_MODEL_CATALOG = os.getenv("SCHEDULE_MODEL_CATALOG", "")  # cron expr, e.g. "0 6 * * *"=daily 6 AM, empty=disabled
ENABLE_AI_TRIAGE = os.getenv("ENABLE_AI_TRIAGE", "").lower() in ("true", "1", "yes")  # Ollama content triage scoring

# ---------------------------------------------------------------------------
# RSS/Atom Feed Polling
# ---------------------------------------------------------------------------
CERID_RSS_POLL_INTERVAL = int(os.getenv("CERID_RSS_POLL_INTERVAL", "1800"))  # seconds, default 30 min

# ---------------------------------------------------------------------------
# Pipeline Tuning — latency vs quality trade-offs
# ---------------------------------------------------------------------------
# Semantic cache: threshold for embedding similarity match (0.0-1.0)
SEMANTIC_CACHE_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.92"))
# NOTE: SEMANTIC_CACHE_TTL is defined in config/features.py (canonical location, 600s).

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
# Options: "openrouter" (default, direct calls), "ollama" (local, the special case), or specific model ID
INTERNAL_LLM_PROVIDER = os.getenv("INTERNAL_LLM_PROVIDER", "openrouter")
INTERNAL_LLM_MODEL = os.getenv("INTERNAL_LLM_MODEL", "")  # empty = provider default

# Default Ollama model for pipeline tasks — lightweight, runs on CPU or GPU
OLLAMA_DEFAULT_MODEL = os.getenv("OLLAMA_DEFAULT_MODEL", "llama3.2:3b")

# User-configurable default model for high-value intelligence tasks
# (verification, expert analysis, complex reasoning)
INTELLIGENCE_MODEL = os.getenv("INTELLIGENCE_MODEL", "")  # empty = auto-select

# ---------------------------------------------------------------------------
# Per-Stage Pipeline Providers
#   Each pipeline stage can independently route to "ollama" or "bifrost".
#   Override per-stage via env vars: PROVIDER_CLAIM_EXTRACTION=bifrost
#   Backward compat: INTERNAL_LLM_PROVIDER=ollama sets ALL stages to ollama.
# ---------------------------------------------------------------------------
_global_provider = os.getenv("INTERNAL_LLM_PROVIDER", "openrouter")

# ---------------------------------------------------------------------------
# Inference Detection
#   INFERENCE_MODE controls how embedding/reranking models are loaded.
#   "auto" (default) = detect best provider at startup.
#   Manual: "onnx-cpu", "onnx-gpu", "ollama", "fastembed-sidecar"
# ---------------------------------------------------------------------------
INFERENCE_MODE = os.getenv("INFERENCE_MODE", "auto")
CERID_SIDECAR_PORT = int(os.getenv("CERID_SIDECAR_PORT", "8889"))
CERID_SIDECAR_URL = os.getenv("CERID_SIDECAR_URL", f"http://localhost:{CERID_SIDECAR_PORT}")
INFERENCE_RECHECK_INTERVAL = int(os.getenv("INFERENCE_RECHECK_INTERVAL", "300"))

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
# Email IMAP Poller
# ---------------------------------------------------------------------------
CERID_EMAIL_IMAP_HOST = os.getenv("CERID_EMAIL_IMAP_HOST", "")
CERID_EMAIL_IMAP_PORT = int(os.getenv("CERID_EMAIL_IMAP_PORT", "993"))
CERID_EMAIL_IMAP_USER = os.getenv("CERID_EMAIL_IMAP_USER", "")
CERID_EMAIL_IMAP_PASSWORD = os.getenv("CERID_EMAIL_IMAP_PASSWORD", "")
CERID_EMAIL_FOLDER = os.getenv("CERID_EMAIL_FOLDER", "INBOX")
CERID_EMAIL_POLL_INTERVAL = int(os.getenv("CERID_EMAIL_POLL_INTERVAL", "15"))  # minutes

# Trading/boardroom config — stubs (overridden at runtime when enabled)
CERID_TRADING_ENABLED: bool = False
TRADING_AGENT_URL: str = ""
CERID_BOARDROOM_ENABLED: bool = False
CERID_BOARDROOM_TIER: str = "foundation"

# ---------------------------------------------------------------------------
# RSS/Atom Feed Poller
# ---------------------------------------------------------------------------
CERID_RSS_POLL_INTERVAL = int(os.getenv("CERID_RSS_POLL_INTERVAL", "30"))  # minutes

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
# Private Mode (Ephemeral Sessions)
#   Level 1: no history saves, no memory extraction
#   Level 2: also skip KB context injection (pure LLM)
#   Level 3: also force local-only models (Ollama)
#   Level 4: also clear Redis query cache on session end
# ---------------------------------------------------------------------------
PRIVATE_MODE_ENABLED: bool = os.getenv("CERID_PRIVATE_MODE", "false").lower() == "true"
PRIVATE_MODE_LEVEL: int = int(os.getenv("CERID_PRIVATE_MODE_LEVEL", "1"))

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
    "cli-ingest": {
        "rate_limits": {
            "/ingest": (60, 60),
            "/recategorize": (30, 60),
        },
        "allowed_domains": None,     # Ingest into any domain
        "strict_domains": False,
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
        "allowed_domains": ["finance", "general"],
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
    "webhook": {
        "rate_limits": {
            "/ingest": (60, 60),     # 60 req/min — external webhook sources
        },
        "allowed_domains": None,     # Webhooks can target any domain
        "strict_domains": False,
    },
    "_default": {
        "rate_limits": {
            "/agent/": (30, 60),
            "/sdk/": (30, 60),
            "/ingest": (10, 60),
            "/recategorize": (10, 60),
        },
        "allowed_domains": None,
        "strict_domains": False,
    },
}

# Backward-compatible accessor — rate_limit.py reads this shape unchanged.
CLIENT_RATE_LIMITS: dict[str, dict[str, tuple[int, int]]] = {
    k: v["rate_limits"] for k, v in CONSUMER_REGISTRY.items()
}

# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------
ALERT_CHECK_INTERVAL_S: int = 60
ALERT_MAX_PER_METRIC: int = 5
ALERT_WEBHOOK_TIMEOUT_S: int = 10
ALERT_EVENTS_MAX: int = 1000  # Max stored alert events

# ---------------------------------------------------------------------------
# Eval Harness
# ---------------------------------------------------------------------------
EVAL_RAGAS_MODEL: str = os.getenv("CERID_EVAL_RAGAS_MODEL", "")
EVAL_LEADERBOARD_MAX: int = 50
EVAL_DEFAULT_BENCHMARK: str = "beir_subset.jsonl"

# ---------------------------------------------------------------------------
# Enterprise Features
# ---------------------------------------------------------------------------
CERID_ENTERPRISE = os.getenv("CERID_ENTERPRISE", "false").lower() in ("1", "true")
ABAC_POLICY_KEY = "cerid:enterprise:abac_policy"
SSO_PROVIDER = os.getenv("CERID_SSO_PROVIDER", "")  # saml | oidc
SSO_METADATA_URL = os.getenv("CERID_SSO_METADATA_URL", "")
CLASSIFICATION_ENABLED = os.getenv("CERID_CLASSIFICATION", "false").lower() in ("1", "true")
AUDIT_STREAM_KEY = "cerid:audit:stream"
AUDIT_RETENTION_DAYS = int(os.getenv("CERID_AUDIT_RETENTION_DAYS", "365"))

# ---------------------------------------------------------------------------
# WebSocket Sync
# ---------------------------------------------------------------------------
WS_SYNC_ENABLED = os.getenv("CERID_WS_SYNC", "false").lower() in ("1", "true")
WS_HEARTBEAT_INTERVAL_S = 30
WS_PRESENCE_TIMEOUT_S = 90
WS_MAX_CONNECTIONS = 50
SYNC_CRDT_ENABLED = True

if not NEO4J_PASSWORD:
    _config_logger.warning(
        "NEO4J_PASSWORD is empty — Neo4j queries will fail with auth errors. "
        "Check that .env is loaded (env_file in docker-compose.yml)."
    )
