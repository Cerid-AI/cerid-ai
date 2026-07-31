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
# Network / auth (LAN exposure) — see docs/LAN_REMOTE_ACCESS.md
# ---------------------------------------------------------------------------
# Surfaced here so they appear in the generated .env.example. The values are
# read dynamically at their call sites (app/middleware/auth.py and the CORS
# middleware in app/main.py call os.getenv directly, so changes apply without
# re-import); these mirror the defaults for documentation + drift coverage.
#
# Shared API key. Empty = auth disabled (localhost-only). start-cerid.sh
# hard-requires this in LAN mode and forwards it to the web UI as
# VITE_CERID_API_KEY so the browser/desktop client can send X-API-Key.
CERID_API_KEY = os.getenv("CERID_API_KEY", "")
# Allowed CORS origins (comma-separated). Same-origin access via the nginx /
# Caddy "/api/mcp" proxy needs no change; set this only for cross-origin browser
# access. "*" disables credentialed CORS.
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173,http://localhost:8888")

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

# Phase 5.2 — classifier confidence floor. When ai_categorize returns a
# confidence below this, the domain is forced to DEFAULT_DOMAIN and a
# `needs-review` tag is attached instead of committing a confident-wrong
# domain (which would mis-route the artifact's chunks + skew every graph
# lens). The needs-review queue drives Track B domain corrections.
CATEGORIZE_CONFIDENCE_THRESHOLD = float(
    os.getenv("CATEGORIZE_CONFIDENCE_THRESHOLD", "0.55")
)

# ---------------------------------------------------------------------------
# LLM timeout (seconds)
# ---------------------------------------------------------------------------
# Bifrost was retired 2026-04-17 (audit C-4); the last pipeline callers that
# still hit a gateway URL were migrated to core.utils.llm_client.call_llm in
# the follow-up (2026-04-17). The name BIFROST_TIMEOUT is kept solely for
# binary backwards-compat — it is now a generic LLM call timeout and is read
# by verification and contextual chunking as such. Prefer BIFROST_TIMEOUT
# for any new callers that need a shared LLM timeout knob.
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
# docker-compose.yml uses required-form ${REDIS_PASSWORD:?...} so an empty
# value fails boot. Ship a non-empty dev default so `cp .env.example .env`
# followed by `docker compose up` works out of the box. Rotate in production.
_redis_password = os.getenv("REDIS_PASSWORD", "changeme-redis")
# OpenRouter API key — required unless running Ollama-only. Declared here so
# scripts/gen_env_example.py surfaces it in .env.example (the generator only
# scans this file). Actual reads happen in core/utils/llm_client.py and the
# /providers, /health, /setup routers.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
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

# Hybrid fusion mode (Workstream E Phase 3a wire-in 2026-05-03 — REVERTED).
# RRF was tested as the default against the seeded eval-corpus v1 and
# regressed every IR metric by 0.22-0.30 absolute (recall@10 0.84→0.57,
# MRR 0.90→0.60, NDCG@10 0.85→0.57) plus latency p95 +33%. Likely cause:
# rank-based fusion of CHUNK-level rankings rewards chunks of the same
# artifact over diversity, hurting artifact-level recall@K after the
# chunk→artifact dedup. RRF remains opt-in (set HYBRID_FUSION_MODE=rrf).
# Re-evaluate after Phase 4 (artifact-level rankings before fusion) or
# with a corpus large enough to dilute the chunk-redundancy effect.
# "weighted_sum" is the legacy behaviour and the validated default
# (HYBRID_VECTOR_WEIGHT * vec + HYBRID_KEYWORD_WEIGHT * bm25).
# "rrf" uses Reciprocal Rank Fusion (Cormack/Clarke/Buettcher 2009 — the
# 2026 default in Elastic, OpenSearch, Azure AI Search, neo4j-graphrag).
# "tri_rrf" (Cycle 3.2 / v0.93.3) extends RRF to three ranking lists —
# vector + BM25 + SPLADE++ learned-sparse. Opt-in via this knob; auto-picked
# when the sparse retrieval toggle flips on in the Settings pane.
HYBRID_FUSION_MODE = os.getenv("HYBRID_FUSION_MODE", "weighted_sum")
HYBRID_RRF_K = int(os.getenv("HYBRID_RRF_K", "60"))
HYBRID_RRF_VECTOR_WEIGHT = float(os.getenv("HYBRID_RRF_VECTOR_WEIGHT", "1.0"))
HYBRID_RRF_BM25_WEIGHT = float(os.getenv("HYBRID_RRF_BM25_WEIGHT", "1.0"))
HYBRID_RRF_SPARSE_WEIGHT = float(os.getenv("HYBRID_RRF_SPARSE_WEIGHT", "1.0"))

# SPLADE++ sparse retrieval (C3.2; model swapped to the Apache-2.0
# Qdrant/Splade_PP_en_v1 2026-07-10). RETRIEVAL_SPARSE_ENABLED is read
# directly in core/retrieval/sparse.py (mirror of the HyPE /
# parent-child pattern). The settings below are infrastructure knobs.
SPLADE_MODEL_PATH = os.getenv("SPLADE_MODEL_PATH", "data/models/splade-pp-en-v1")
SPLADE_ONNX_FILENAME = os.getenv("SPLADE_ONNX_FILENAME", "model.onnx")
SPLADE_TOP_K_TERMS = int(os.getenv("SPLADE_TOP_K_TERMS", "256"))
SPARSE_DATA_DIR = os.path.join(os.getenv("DATA_DIR", "data"), "sparse")

# Adaptive recommendation engine (Cycle 3.2). Cron expression for the
# ConfigRecommenderJob that scans the corpus + flag state and writes
# the cerid:recommendations Redis hash consumed by /health. Off-peak
# every 6h by default — the job is read-only against Neo4j so even
# at sub-cron interval it can't deadlock with ingest.
SCHEDULE_CONFIG_RECOMMENDER = os.getenv("SCHEDULE_CONFIG_RECOMMENDER", "0 */6 * * *")

# Graph/memory freshness sweeps (Cerid v1.0 enablement). Each is gated — set the
# env var empty to disable the in-process cron (operators may prefer host cron).
# Cadences are GPU/cost-conscious on a single-GPU host:
#  - Community re-detection (GDS Leiden, cheap) + summaries (LLM, but
#    skip-existing bounds it) — weekly, Sunday 02:00 UTC.
#  - Constellation 3D coords (fallback layout, no LLM) — nightly 03:30 UTC.
#  - Memory archival sweep (safe, no LLM re-abstraction) — weekly, Sunday 05:00.
SCHEDULE_COMMUNITY_REFRESH = os.getenv("SCHEDULE_COMMUNITY_REFRESH", "0 2 * * sun")
# Per-entity embeddings — 15 min before compute_umap_3d so layout picks up fresh vectors.
SCHEDULE_COMPUTE_ENTITY_EMBEDDINGS = os.getenv("SCHEDULE_COMPUTE_ENTITY_EMBEDDINGS", "15 3 * * *")
SCHEDULE_COMPUTE_UMAP_3D = os.getenv("SCHEDULE_COMPUTE_UMAP_3D", "30 3 * * *")
# Entity trust_state derivation — 1 min after compute_umap_3d.
SCHEDULE_COMPUTE_TRUST_STATE = os.getenv("SCHEDULE_COMPUTE_TRUST_STATE", "31 3 * * *")
# Domain backbone derivation — 1 min after compute_trust_state.
# Independent of umap: runs even when SCHEDULE_COMPUTE_UMAP_3D is empty.
SCHEDULE_DERIVE_DOMAINS = os.getenv("SCHEDULE_DERIVE_DOMAINS", "32 3 * * *")
SCHEDULE_MEMORY_CONSOLIDATION = os.getenv("SCHEDULE_MEMORY_CONSOLIDATION", "0 5 * * sun")
# Cap LLM summaries generated per community-refresh run so a first run on a
# large corpus can't issue an unbounded GPU batch. skip-existing already bounds
# steady state; this bounds the cold-start. 0 / unset = no cap.
COMMUNITY_SUMMARY_MAX_PER_RUN = int(os.getenv("COMMUNITY_SUMMARY_MAX_PER_RUN", "200"))

# Semantic kNN edges (SIMILAR_TO) — Task 3.2.
# Runs inside the nightly compute_umap_3d cadence after entity embeddings
# are computed but before layout so force-layout springs include semantic edges.
SEMANTIC_EDGE_ENABLED: bool = os.getenv("SEMANTIC_EDGE_ENABLED", "true").lower() in ("1", "true")
SEMANTIC_EDGE_K: int = int(os.getenv("SEMANTIC_EDGE_K", "10"))
SEMANTIC_EDGE_THRESHOLD: float = float(os.getenv("SEMANTIC_EDGE_THRESHOLD", "0.66"))
# Down-weight factor applied to SIMILAR_TO edge weights in the force layout so
# co-mention co-occurrence structure stays dominant over semantic similarity.
SEMANTIC_EDGE_SPRING_SCALE: float = float(os.getenv("SEMANTIC_EDGE_SPRING_SCALE", "0.6"))
# Cron schedule for the SIMILAR_TO kNN edge materialisation job (runs between
# entity-embeddings at 3:15 and compute_umap_3d at 3:30). Empty string disables.
SCHEDULE_BUILD_SIMILARITY_EDGES = os.getenv("SCHEDULE_BUILD_SIMILARITY_EDGES", "22 3 * * *")

# Webhook-inbox drain: the receiver (POST /sdk/v1/ingest/webhook/{token}) returns
# 202 and rpush'es normalized artifacts onto cerid:webhook_inbox:{source_id};
# this consumer routes them into the KB (without it, payloads were stranded).
# Every 2 min so inbound webhooks land promptly. Empty disables.
SCHEDULE_WEBHOOK_DRAIN = os.getenv("SCHEDULE_WEBHOOK_DRAIN", "*/2 * * * *")
WEBHOOK_DRAIN_MAX_PER_RUN = int(os.getenv("WEBHOOK_DRAIN_MAX_PER_RUN", "200"))

# Connector polling: drives SourceConnector.fetch_since on a cadence for active
# pollable sources (rss, url_watch, …), advancing the sync cursor only after a
# successful ingest (crash-safe resume). Empty disables.
SCHEDULE_SOURCE_POLL = os.getenv("SCHEDULE_SOURCE_POLL", "*/15 * * * *")
SOURCE_POLL_MAX_ARTIFACTS_PER_SOURCE = int(os.getenv("SOURCE_POLL_MAX_ARTIFACTS_PER_SOURCE", "50"))

# IMAP mailbox polling: drives poll_email() on a cadence to ingest new unseen
# mail. Self-skips when no mailbox is configured (legacy /data-sources/email).
# Empty disables.
SCHEDULE_EMAIL_POLL = os.getenv("SCHEDULE_EMAIL_POLL", "*/15 * * * *")

# Contextual retrieval per-tenant monthly USD budget (Workstream E
# Phase 3). Advisory only — no enforcement currently reads this value;
# breaching it does not trip a circuit breaker or disable contextual
# generation. Configurable; default $50 aligns with the
# Anthropic-published cost model (~$1.02/M ingested tokens with
# prompt-cache hits).
CONTEXTUAL_BUDGET_USD_PER_TENANT_PER_MONTH = float(
    os.getenv("CONTEXTUAL_BUDGET_USD_PER_TENANT_PER_MONTH", "50.0"),
)

# CRAG-style retrieval quality gate — if top result relevance is below this
# threshold after initial retrieval, supplement with external sources before
# proceeding to expensive reranking/generation.
RETRIEVAL_QUALITY_THRESHOLD = float(os.getenv("RETRIEVAL_QUALITY_THRESHOLD", "0.4"))

# Staleness window for current/recency-intent queries — when the query asks
# about "current/now/today" and the freshest KB result is older than this,
# fire external regardless of relevance score (Phase 3.2 — answerability over
# relevance for time-scoped queries).
CRAG_STALENESS_WINDOW_DAYS = int(os.getenv("CRAG_STALENESS_WINDOW_DAYS", "7"))

# Staleness window for claim VERIFICATION (Phase 4.2). When a temporal
# (recency / current-event) claim is supported only by KB evidence older
# than this window AND external web verification was inconclusive, the
# verdict is downgraded to ``uncertain`` with reason ``stale_evidence``
# rather than rubber-stamped ``verified`` on stale data. Defaults to the
# CRAG window (same staleness notion, different surface) but is separately
# tunable for operators who want verification stricter/looser than retrieval.
# `or CRAG_STALENESS_WINDOW_DAYS` (not a getenv default) so an *empty* env value
# falls back too — a getenv default only applies when the var is unset. CI seeds
# .env from .env.example, where this lands blank (its default is the non-literal
# str(CRAG_STALENESS_WINDOW_DAYS), which the generator can't resolve), and a bare
# int("") would crash MCP startup.
VERIFICATION_STALENESS_WINDOW_DAYS = int(
    os.getenv("VERIFICATION_STALENESS_WINDOW_DAYS") or CRAG_STALENESS_WINDOW_DAYS
)

# Wall-clock ceiling for a single agent_query() call. Two competing
# constraints pick this value:
#
#   1. Must stay well under the event-loop watchdog's 45s heartbeat
#      threshold so a single slow query can't trip the process kill.
#   2. Must stay SHORT so the app.concurrency.KB_POOL queue drains
#      fast — every /agent/query holds a KB_POOL slot for up to this
#      many seconds, which blocks other KB queries and makes the chat
#      experience feel hung even when /chat/stream itself is fast.
#      (Pre-Task-8 this was _QUERY_SEMAPHORE(2); now path-partitioned
#      so /health and /observability are immune to the queue depth.)
#
# 20s is the post-0.84 sweet spot: the previous 10s ceiling was too tight
# for cold caches + external sources (Wikipedia, DuckDuckGo, per-source
# 5s timeouts) to complete before tripping the budget on the happy path,
# which surfaced as "retrieval budget exceeded" even on healthy queries.
# 20s leaves headroom under the 45s event-loop watchdog and still returns
# a degraded response in time for the user. Increase via env var if
# your hardware is slow enough that this isn't enough budget.
AGENT_QUERY_BUDGET_SECONDS = float(os.getenv("AGENT_QUERY_BUDGET_SECONDS", "20.0"))

# CH4: follow-up (conversation) retrieval fans out across every domain with a
# conversation-enriched query; on CPU inference that all-domain rerank blows the
# budget above. Cap the fan-out to the most-likely domains and trim per-domain
# candidate depth so follow-ups stay within budget while partial retrieval stays
# coherent. Both apply ONLY to follow-ups with no explicit domain filter; set to
# 0 to disable. Fresh (non-conversation) queries still search all domains.
AGENT_QUERY_FOLLOWUP_MAX_DOMAINS = int(os.getenv("AGENT_QUERY_FOLLOWUP_MAX_DOMAINS", "6"))
AGENT_QUERY_FOLLOWUP_TOP_K = int(os.getenv("AGENT_QUERY_FOLLOWUP_TOP_K", "5"))

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
    # Longest matching prefix wins, so a specific family (gpt-4o-mini) is not
    # shadowed by a more general one (gpt-4o) that happens to be earlier in the
    # dict — insertion order must not decide the budget (CR-073).
    for prefix in sorted(MODEL_CONTEXT_CHAR_BUDGETS, key=len, reverse=True):
        if model_lower.startswith(prefix):
            return MODEL_CONTEXT_CHAR_BUDGETS[prefix]
    return QUERY_CONTEXT_MAX_CHARS
QUERY_RERANK_CANDIDATES = int(os.getenv("QUERY_RERANK_CANDIDATES", "15"))  # max candidates sent to reranker (GA P0.5 B2c: eval-tunable; default unchanged)
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
# Max (query, chunk) pair length the IN-PROCESS ONNX cross-encoder tokenizer
# (core.retrieval.reranker) keeps before truncating. Must NOT exceed the
# configured model's positional limit: ms-marco-MiniLM (the default) caps at
# 512, so a parent chunk this large + the query overflows the budget and
# silently drops the chunk's tail from scoring (logged at debug level — see
# reranker._score_pairs). Raising this past a model's real limit makes ONNX
# inference fail on out-of-range position ids, not truncate further.
#
# This setting has NO effect on the RERANK_PROVIDER=quenchforge HTTP path
# (utils.quenchforge_client.quenchforge_rerank) — that wire call carries no
# max_length parameter; truncation there is handled server-side against the
# GGUF model's native context window. QUENCHFORGE_RERANK_MODEL already
# defaults to bge-reranker-v2-m3, which reads a full 512-token parent chunk +
# query without clipping when RERANK_PROVIDER=quenchforge is opted in — that
# is the production route to bge-reranker-v2-m3, not this setting. BAAI's
# upstream repo ships no ONNX export (model.safetensors only, verified via
# the HF hub file listing), so pointing RERANK_CROSS_ENCODER_MODEL at it for
# this in-process path fails at model-download time.
RERANK_MAX_LENGTH = int(os.getenv("RERANK_MAX_LENGTH", "512"))

# ---------------------------------------------------------------------------
# NLI Entailment (Natural Language Inference)
# ---------------------------------------------------------------------------
NLI_MODEL = os.getenv("NLI_MODEL", "cross-encoder/nli-deberta-v3-xsmall")
NLI_ONNX_FILENAME = os.getenv("NLI_ONNX_FILENAME", "onnx/model.onnx")
NLI_MODEL_CACHE_DIR = os.getenv("NLI_MODEL_CACHE_DIR", "")
NLI_ENTAILMENT_THRESHOLD = float(os.getenv("NLI_ENTAILMENT_THRESHOLD", "0.7"))
NLI_CONTRADICTION_THRESHOLD = float(os.getenv("NLI_CONTRADICTION_THRESHOLD", "0.6"))
# Inline NLI gating: when true, the MCP answer-synthesis path streams tokens
# through core.agents.hallucination.inline_gate and SUPPRESSES any sentence the
# retrieved evidence contradicts (contradiction >= NLI_CONTRADICTION_THRESHOLD)
# mid-stream, instead of only verifying post-hoc. Default off — opt-in
# capability; the proven post-hoc path (verify_claim / check_hallucinations) is
# unchanged when this is false.
ENABLE_INLINE_NLI_GATING = os.getenv("ENABLE_INLINE_NLI_GATING", "false").lower() == "true"
# The retrieval NLI contradiction gate (query_agent Step 5.65) runs NLI on
# (doc, query) pairs — but a query is a question, not a declarative hypothesis,
# so DeBERTa-MNLI false-positives "contradiction" on definitional answers (it
# dropped a 0.93-relevance "Photosynthesis is…" doc for "what is photosynthesis",
# zeroing recall). The top-K retrieval matches are EXEMPT from the contradiction
# drop: a noisy (doc, question) signal must never override a strong retrieval
# rank. 0 disables the exemption (legacy behaviour). Eval-tunable.
NLI_GATE_EXEMPT_TOP_K = int(os.getenv("NLI_GATE_EXEMPT_TOP_K", "3"))

# Phase 6: decompose multi-fact heuristic claims into atomic sub-claims via
# an LLM call before scoring each independently against the premise. The
# v0.95.5 sliding-window scorer scored at sentence granularity; multi-fact
# sentences hide partial-support failures behind a single entailment label.
FAITHFULNESS_DECOMPOSE_CLAIMS = os.getenv("FAITHFULNESS_DECOMPOSE_CLAIMS", "true").lower() == "true"
FAITHFULNESS_DECOMPOSE_MAX_SUBCLAIMS = int(os.getenv("FAITHFULNESS_DECOMPOSE_MAX_SUBCLAIMS", "6"))
# Atomic sub-claims are shorter and lose surrounding context; deberta-v3-mnli
# under-confidences them at the sentence-tuned 0.7 threshold. A lower bar
# is empirically required to make decomposition a positive lift.
NLI_ATOMIC_ENTAILMENT_THRESHOLD = float(os.getenv("NLI_ATOMIC_ENTAILMENT_THRESHOLD", "0.5"))

# v0.96.0 Phase 5 — opt-in upgrade for faithfulness scoring.
# When enabled, ragas_metrics.faithfulness() calls _extract_claims_llm()
# instead of the regex heuristic. Catches sub-claims in multi-clause
# sentences the regex misses, at the cost of one LLM call per scored
# answer. Disabled by default to keep the RAGAS gate cost-bounded.
FAITHFULNESS_LLM_CLAIM_EXTRACTION = os.getenv(
    "FAITHFULNESS_LLM_CLAIM_EXTRACTION", "false",
).lower() == "true"
FAITHFULNESS_LLM_MAX_CLAIMS = int(os.getenv("FAITHFULNESS_LLM_MAX_CLAIMS", "12"))

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
# Personal-first knowledge-pack ranking (Slice 7.2). Multiplier applied to the
# blended rerank score of chunks carrying a pack_id, AFTER the cross-encoder
# blend — a stable policy knob, not entangled with model scores. <1.0 makes
# personal data win ties; packs still surface when clearly the best answer.
# Operator-tunable at runtime via PATCH /settings (advanced/SERVER scope).
PACK_RELEVANCE_WEIGHT = float(os.getenv("PACK_RELEVANCE_WEIGHT", "0.7"))

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
    "WIKILINKS_TO",     # Obsidian-style [[wikilink]] in markdown body (C2.1)
    "EMBEDS",           # Obsidian-style ![[embed]] / transclusion (C2.1)
    "HAS_ATTACHMENT",   # parent email → child artifact extracted from attachment (C2.4)
    "HAS_FACT",         # (:Entity)-[:HAS_FACT]->(:Fact) — bi-temporal fact layer (m0004/m0006)
    "FACT_OBJECT",      # (:Fact)-[:FACT_OBJECT]->(:Entity) — the fact's object entity, when binary
    "FACT",             # reserved: provenance edge from a source Artifact/Conversation to
                         # its extracted :Fact (m0004's earlier relationship-model exploration,
                         # not yet emitted by any writer — Phase C decides if it's needed)
]

# ---------------------------------------------------------------------------
# Entity Extraction Quality
# ---------------------------------------------------------------------------
# Minimum LLM-reported confidence for an extracted entity to be persisted.
# Entities below this threshold are silently dropped by _normalise_entities
# before any graph write. Raising this reduces single-mention noise;
# lowering it recovers more entities at the cost of graph pollution.
ENTITY_MIN_CONFIDENCE: float = float(os.getenv("ENTITY_MIN_CONFIDENCE", "0.5"))

# Alias-aware entity resolution (Task 2.2).
# ENTITY_RESOLUTION_EMBED: when True, Tier-C embedding comparison is run during
# ingestion (expensive — enable only for the reprocess job or targeted re-indexing).
# ENTITY_RESOLUTION_SIM: cosine similarity threshold for Tier-C merge (0.0–1.0).
ENTITY_RESOLUTION_EMBED: bool = os.getenv("ENTITY_RESOLUTION_EMBED", "false").lower() in ("true", "1", "yes")
ENTITY_RESOLUTION_SIM: float = float(os.getenv("ENTITY_RESOLUTION_SIM", "0.92"))

# Phase 4.2 — embedding-based entity RESOLUTION (real merges, not just
# connectivity). The candidate generator buckets high-similarity entity pairs
# into three bands:
#   sim >= ENTITY_MERGE_AUTO_SIM          → auto-merge (no LLM cost)
#   ENTITY_MERGE_ADJUDICATE_SIM <= sim
#                       < ENTITY_MERGE_AUTO_SIM → LLM adjudication (merge/keep)
#   sim <  ENTITY_MERGE_ADJUDICATE_SIM     → no action (SIMILAR_TO connectivity
#                                            already links them; not a merge)
# The adjudication band is bounded per run so a large graph cannot blow the
# per-run LLM budget: at most ENTITY_MERGE_ADJUDICATION_MAX_PAIRS pairs are
# sent, in batches of ENTITY_MERGE_ADJUDICATION_BATCH per call.
ENTITY_MERGE_AUTO_SIM: float = float(os.getenv("ENTITY_MERGE_AUTO_SIM", "0.94"))
ENTITY_MERGE_ADJUDICATE_SIM: float = float(os.getenv("ENTITY_MERGE_ADJUDICATE_SIM", "0.86"))
ENTITY_MERGE_ADJUDICATION_MAX_PAIRS: int = int(os.getenv("ENTITY_MERGE_ADJUDICATION_MAX_PAIRS", "50"))
ENTITY_MERGE_ADJUDICATION_BATCH: int = int(os.getenv("ENTITY_MERGE_ADJUDICATION_BATCH", "5"))
# Chunk size for the merge/unmerge edge-repoint UNWINDs. A heavily-connected
# entity can carry >10k adjacent edges (~250k SIMILAR_TO at k=5 graph-wide);
# re-pointing them in one UNWIND transaction risks OOM, so every repoint is
# paginated at this batch size (fixes the >10k UNWIND TODO called out by the
# Phase-4 audit before ENTITY_RESOLUTION_EMBED can be flipped on).
ENTITY_MERGE_UNWIND_CHUNK: int = int(os.getenv("ENTITY_MERGE_UNWIND_CHUNK", "5000"))

# Validate the resolution bands are ordered and in range so a misconfigured
# env cannot invert the auto/adjudicate split (which would auto-merge the
# band that was meant for human/LLM review).
assert 0.0 <= ENTITY_MERGE_ADJUDICATE_SIM <= ENTITY_MERGE_AUTO_SIM <= 1.0, (
    "ENTITY_MERGE thresholds must satisfy "
    "0 <= ADJUDICATE_SIM <= AUTO_SIM <= 1: "
    f"adjudicate={ENTITY_MERGE_ADJUDICATE_SIM} auto={ENTITY_MERGE_AUTO_SIM}"
)
assert ENTITY_MERGE_UNWIND_CHUNK > 0, "ENTITY_MERGE_UNWIND_CHUNK must be positive"

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
# support truncation (e.g. 768→256 for speed).  Defaulting to 768 matches the
# native dim of the default Snowflake/arctic-embed-m model and prevents the
# fresh-install dim-mismatch where ChromaDB locks the collection at a
# truncated dim before the embedder reports its native size.
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "768"))
EMBEDDING_ONNX_FILENAME = os.getenv("EMBEDDING_ONNX_FILENAME", "onnx/model.onnx")
EMBEDDING_MODEL_CACHE_DIR = os.getenv("EMBEDDING_MODEL_CACHE_DIR", "")
# ONNX Runtime execution providers (shared by embedding model and cross-encoder
# reranker).  Empty = auto-detect best available; otherwise comma-separated
# list in priority order (e.g. "CUDAExecutionProvider,CPUExecutionProvider").
# CPU is always appended as the universally-available fallback.
ONNX_EXECUTION_PROVIDERS = os.getenv("ONNX_EXECUTION_PROVIDERS", "")

# ---------------------------------------------------------------------------
# Hallucination Detection
# ---------------------------------------------------------------------------
HALLUCINATION_THRESHOLD = float(os.getenv("HALLUCINATION_THRESHOLD", "0.65"))
HALLUCINATION_UNVERIFIED_THRESHOLD = float(os.getenv("HALLUCINATION_UNVERIFIED_THRESHOLD", "0.4"))
HALLUCINATION_MIN_RESPONSE_LENGTH = int(os.getenv("HALLUCINATION_MIN_RESPONSE_LENGTH", "25"))
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
# Catalog-refreshed 2026-05-20: grok-4 was removed from OpenRouter;
# grok-4.20 is the current expert-tier xAI model ($1.25/$2.50 per 1M
# tokens, 2M context window) vs the prior grok-4 at $3/$15 per 1M.
# Note: this default is for non-online (no web search) expert
# verification; smart_router.VERIFICATION_EXPERT branch uses
# grok-4.20:online when web search is desired.
VERIFICATION_EXPERT_MODEL = os.getenv(
    "VERIFICATION_EXPERT_MODEL",
    "openrouter/x-ai/grok-4.20",
)

# Web-search-enabled expert verification model (the `:online` variant) used by
# the smart_router VERIFICATION_EXPERT branch when live search is desired. Kept
# separate from VERIFICATION_EXPERT_MODEL (non-online) so the router has an
# env-overridable, catalog-visible default instead of a hardcoded literal.
VERIFICATION_EXPERT_WEB_MODEL = os.getenv(
    "VERIFICATION_EXPERT_WEB_MODEL",
    "openrouter/x-ai/grok-4.20:online",
)

# ---------------------------------------------------------------------------
# Cloud egress on a local-provider install
# ---------------------------------------------------------------------------
# Two paths send user content to OpenRouter even when the configured inference
# provider is local: the internal-LLM fallback (when the local backend times
# out or its breaker opens) and external claim verification. Both default ON to
# preserve existing behaviour. Operators who chose local inference *for privacy*
# set this false to keep content on the box — the local failure surfaces as an
# error and verification degrades to KB-only instead of silently egressing.
ALLOW_CLOUD_EGRESS_WHEN_LOCAL = (
    os.getenv("ALLOW_CLOUD_EGRESS_WHEN_LOCAL", "true").lower() == "true"
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
# Extended timeout for expert-tier models (Grok 4 with :online web search)
STREAMING_EXPERT_CLAIM_TIMEOUT = float(os.getenv("STREAMING_EXPERT_CLAIM_TIMEOUT", "30"))
# CH5: cross-model + web claim verification run on OpenRouter (call_llm_raw).
# The old hardcoded 12s cross-model cap was too tight for cloud latency under
# the verify semaphore — claims timed out and were regenerated. More generous
# and env-tunable; STREAMING_TOTAL_TIMEOUT below still backstops total runtime.
STREAMING_CROSS_MODEL_CLAIM_TIMEOUT = float(os.getenv("STREAMING_CROSS_MODEL_CLAIM_TIMEOUT", "18"))
STREAMING_WEB_CLAIM_TIMEOUT = float(os.getenv("STREAMING_WEB_CLAIM_TIMEOUT", "25"))
# Total deadline for the entire streaming verification loop (all claims).
STREAMING_TOTAL_TIMEOUT = float(os.getenv("STREAMING_TOTAL_TIMEOUT", "90"))

# ---------------------------------------------------------------------------
# Web Search — agentic web search fallback
# ---------------------------------------------------------------------------
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
SEARXNG_URL = os.getenv("SEARXNG_URL", "")  # e.g. http://localhost:8080
ENABLE_AUTO_LEARN = os.getenv("ENABLE_AUTO_LEARN", "false").lower() == "true"
WEB_SEARCH_MAX_RESULTS = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5"))
WEB_SEARCH_RATE_LIMIT = int(os.getenv("WEB_SEARCH_RATE_LIMIT", "10"))  # per minute
# When a real web-search provider (Tavily/SearXNG) is configured, also fold it
# into the RAG external-source path (not just the pkb_web_search tool). Escape
# hatch: set false to keep web search out of retrieval. No effect without a
# configured provider — a default install is unchanged either way.
ENABLE_WEB_SEARCH_IN_RAG = os.getenv("ENABLE_WEB_SEARCH_IN_RAG", "true").lower() == "true"

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
# QUALITY_WEIGHT_FRESHNESS lives in config/constants.py — the v2 scorer
# (core/utils/quality.py) imports it from there; this module must not
# shadow it with a divergent value.
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

# Synopsis model options — user-selectable, with cost and throttle info.
# Catalog-refreshed 2026-05-20 against OpenRouter live pricing.
# gemini-2.5-flash → gemini-3.1-flash-lite (newer + cheaper).
# gpt-5-nano removed: reasoning model — max_tokens consumed as reasoning
# budget, yielding 0 output chars (OPT-14). Old non-broken IDs kept as
# legacy entries because operators may have them pinned in their persisted
# Settings doc and a sudden removal would break their synopsis route on
# next regenerate.
SYNOPSIS_MODEL_OPTIONS = {
    "openrouter/meta-llama/llama-3.3-70b-instruct:free": {
        "label": "Llama 3.3 (Free)",
        "input_per_1m": 0.0,
        "output_per_1m": 0.0,
        "rpm": 8,
        "throttle": 8.0,
    },
    "openrouter/openai/gpt-4o-mini": {
        "label": "GPT-4o Mini (legacy)",
        "input_per_1m": 0.15,
        "output_per_1m": 0.60,
        "rpm": 1000,
        "throttle": 0.5,
    },
    "openrouter/google/gemini-3.1-flash-lite": {
        "label": "Gemini 3.1 Flash Lite",
        "input_per_1m": 0.25,
        "output_per_1m": 1.50,
        "rpm": 1000,
        "throttle": 0.5,
    },
    "openrouter/google/gemini-2.5-flash": {
        "label": "Gemini 2.5 Flash (legacy)",
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
# Stability (S) = decay scale in days. Higher = slower fade.
# NOTE on the effective half-life (the age at which decay = 0.5):
#   - exponential types (2^(-t/S)):           half-life = S days exactly.
#   - power-law types ((1 + t/(9S))^-0.5):     half-life = 27·S days (solve
#     (1+t/(9S))^-0.5 = 0.5 → t = 27S). So "decision" S=90 ≈ a 2430-day
#     half-life, not 90. Tune power-law S with the 27× factor in mind.
# "empirical" is a large-but-FINITE stability: verification-promoted facts must
# eventually decay so a single verdict can't self-reinforce forever. As a
# power-law type its effective half-life is 27×S (see note above), so 180 days
# still yields a multi-year half-life for genuine durable facts — finite, not
# permanent. (float("inf") previously made these immortal.)
EMPIRICAL_MEMORY_STABILITY_DAYS = float(
    os.getenv("EMPIRICAL_MEMORY_STABILITY_DAYS", "180")
)
MEMORY_TYPE_STABILITY: dict[str, float] = {
    "empirical": EMPIRICAL_MEMORY_STABILITY_DAYS,  # durable facts — slow finite decay
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
# Memory Recall
# ---------------------------------------------------------------------------
MEMORY_RECALL_TOP_K = int(os.getenv("MEMORY_RECALL_TOP_K", "5"))
MEMORY_RECALL_MIN_SCORE = float(os.getenv("MEMORY_RECALL_MIN_SCORE", "0.4"))
MEMORY_RECALL_TIMEOUT_MS = int(os.getenv("MEMORY_RECALL_TIMEOUT_MS", "200"))

# ---------------------------------------------------------------------------
# Scheduled Maintenance — cron expressions
# ---------------------------------------------------------------------------
SCHEDULE_RECTIFY = os.getenv("SCHEDULE_RECTIFY", "0 3 * * *")         # daily 3 AM
SCHEDULE_HEALTH_CHECK = os.getenv("SCHEDULE_HEALTH_CHECK", "0 */6 * * *")  # every 6h
SCHEDULE_STALE_DETECTION = os.getenv("SCHEDULE_STALE_DETECTION", "0 4 * * sun")  # Sunday 4 AM
SCHEDULE_STALE_DAYS = int(os.getenv("SCHEDULE_STALE_DAYS", "90"))
# AF-030 (CL-8) — background KB quality re-scoring. curate() in audit mode is
# cheap (local scoring + one graph write per artifact, NO LLM calls; synopsis
# generation stays off), but it is never re-run after ingest, so quality scores
# go stale as artifacts accrue edits and relationships. Weekly Sunday 4:30 AM by
# default. Empty string disables the cron; the job is ALSO gated off by default
# behind CERID_CURATOR_CRON_ENABLED (read in app/scheduler.py) so an operator
# opts in explicitly — mirrors the SCHEDULE_BACKFILL_ENRICHMENT convention.
SCHEDULE_CURATOR = os.getenv("SCHEDULE_CURATOR", "30 4 * * sun")  # Sunday 4:30 AM
# AF-032 (CL-8) — entity embedding-merge sweep. Ingest runs only Tiers A+B of
# resolve_canonical (alias-table + string-normalize) to stay lean; the Tier-C
# embedding-based merge is this deliberate maintenance sweep (the same
# `scripts/merge_entity_aliases.py --mode embedding --apply` an operator would
# run by hand). Weekly Sunday 5:30 AM by default (after compute_entity_embeddings
# at 3:15 has produced fresh vectors). DOUBLE-gated: registered only when
# CERID_ENTITY_MERGE_CRON_ENABLED opts in AND the sweep no-ops unless
# ENTITY_RESOLUTION_EMBED is on. Empty string disables the cron.
SCHEDULE_ENTITY_MERGE = os.getenv("SCHEDULE_ENTITY_MERGE", "30 5 * * sun")  # Sunday 5:30 AM
# Phase E (bi-temporal memory plan) — once-per-session summarization scan
# cadence. Default every 15 min; empty string disables the cron. The scan is
# dark behind ENABLE_SESSION_SUMMARIZATION (config/features.py, default OFF), so
# this cron is a no-op until the flag flips. Idle threshold + per-scan cap are
# read at scan time via SESSION_SUMMARY_IDLE_MIN / SESSION_SUMMARY_SCAN_LIMIT.
SCHEDULE_SESSION_SUMMARIES = os.getenv("SCHEDULE_SESSION_SUMMARIES", "*/15 * * * *")
# Phase S4 of the unified GA program — K-program metrics snapshot.
# Default midnight UTC. Empty string disables (operator may prefer a
# host-side cron / launchd plist over the in-process scheduler).
# Six metrics emitted: wiki coverage, p95 staleness, faithfulness,
# chunks-per-answer, memory→entity linkage, contradiction p95 — each
# appended to tasks/<monday>-k-program-metrics.md via --cron.
# Empty by default: `scripts/k_program_metrics.py` is a HOST-side operator tool.
# It resolves its own repo root, puts `<root>/src/mcp` on sys.path, reads the
# repo `.env` and appends to `tasks/` — none of which exist inside the container,
# where `/app` *is* `src/mcp`. Scheduled in-container it failed every midnight.
# Operators run it from a checkout (or a bind-mounted repo) and may set this cron
# there; see docs/RUNBOOK_PRODUCTION.md.
SCHEDULE_K_PROGRAM_METRICS = os.getenv("SCHEDULE_K_PROGRAM_METRICS", "")
# Daily Knowledge Stats snapshot for the Sources pane hero card's
# sparklines. Default midnight UTC. One MERGE per day; idempotent
# across reruns.
SCHEDULE_KNOWLEDGE_STATS_SNAPSHOT = os.getenv(
    "SCHEDULE_KNOWLEDGE_STATS_SNAPSHOT", "0 0 * * *",
)

# Nightly per-source retention enforcement. Walks every (:Source)
# and applies its retention_policy. Default 2 AM UTC.
SCHEDULE_RETENTION_ENFORCE = os.getenv(
    "SCHEDULE_RETENTION_ENFORCE", "0 2 * * *",
)
# Wiki refresh crons (Phase K). Empty string disables (matches sibling SCHEDULE_*).
SCHEDULE_WIKI_STALE_SWEEP = os.getenv("SCHEDULE_WIKI_STALE_SWEEP", "0 3 * * *")
SCHEDULE_WIKI_DRIFT_LINT = os.getenv("SCHEDULE_WIKI_DRIFT_LINT", "0 4 * * sun")
# Hard-delete of quarantine-expired artifacts. Empty string disables.
SCHEDULE_QUARANTINE_PURGE = os.getenv("SCHEDULE_QUARANTINE_PURGE", "0 3 * * *")

# Auto-adopt the latest in-family model per role from the OpenRouter catalog.
# MODEL_AUTO_UPDATE_ENABLED gates the scheduler job; set "false" to keep the
# pinned assignments. Same-family + must-exist-in-catalog bounds the drift;
# model_config.json stays revertible via PUT /models/assignments.
MODEL_AUTO_UPDATE_ENABLED = os.getenv("MODEL_AUTO_UPDATE_ENABLED", "true").lower() in ("true", "1")
SCHEDULE_MODEL_AUTO_UPDATE = os.getenv("SCHEDULE_MODEL_AUTO_UPDATE", "0 6 * * mon")  # Mon 6 AM

# Routing-tiers overlay: a JSON map {original_tier_id: resolved_id} written by
# the weekly model_auto_update job (app/routers/models.py::apply_latest_assignments)
# after the role-assignment pass. The smart_router reads it lazily at lookup time
# to keep the FREE/CHEAP/CAPABLE/RESEARCH/EXPERT tier ids current without editing
# the source tables. Lives next to model_config.json (app/data/) by default;
# env-overridable. core/ reads this path from config — it never imports app/.
# Missing/invalid overlay → tier tables are used as-is (fail soft).
ROUTING_TIERS_OVERLAY_PATH = os.getenv(
    "ROUTING_TIERS_OVERLAY_PATH",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app", "data", "routing_tiers.json",
    ),
)

# ---------------------------------------------------------------------------
# Folder Scanning
# ---------------------------------------------------------------------------
SCAN_PATHS = os.getenv("SCAN_PATHS", ARCHIVE_PATH)  # colon-separated directories to scan
# Legacy-path only: honored by _run_folder_scan()'s SCAN_PATHS fallback
# branch (no watched folders registered). Folders registered via the
# watched-folders store bypass these two filters entirely.
SCAN_MIN_QUALITY = float(os.getenv("SCAN_MIN_QUALITY", "0.4"))  # min quality score (0-1)
SCAN_MAX_FILE_SIZE_MB = int(os.getenv("SCAN_MAX_FILE_SIZE_MB", "50"))
SCAN_EXCLUDE_PATTERNS = [p for p in os.getenv("SCAN_EXCLUDE_PATTERNS", "").split(",") if p]
SCHEDULE_FOLDER_SCAN = os.getenv("SCHEDULE_FOLDER_SCAN", "")  # cron expr, empty=disabled
# Phase J — inbox triage cadence. Default every 15 minutes; empty disables.
# Also gated by CERID_INBOX_TRIAGE_ENABLED so the operator opts in
# explicitly. Set INBOX_TRIAGE_MAX_PER_SOURCE to cap LLM cost.
SCHEDULE_INBOX_TRIAGE = os.getenv("SCHEDULE_INBOX_TRIAGE", "*/15 * * * *")
# Phase K — daily digest cadence. Default 7 AM UTC; empty disables.
# Also gated by CERID_DAILY_DIGEST_ENABLED so operator opts in.
# Per-user timezone resolution tracked for Phase K.2 (currently
# everyone gets server-UTC-7am).
SCHEDULE_DAILY_DIGEST = os.getenv("SCHEDULE_DAILY_DIGEST", "0 7 * * *")
# Phase 5.3 — Track A enrichment backfill. Gated OFF by default: the job
# calls the classifier per artifact, so the operator opts in once after
# Slice 5.1/5.2 land (CERID_BACKFILL_ENRICHMENT_ENABLED=true). Nightly until
# the bare-tag backlog drains, then it self-idles (scan returns 0). Pace +
# batch size cap LLM cost; metadata-only writes (no domain/collection moves).
SCHEDULE_BACKFILL_ENRICHMENT = os.getenv("SCHEDULE_BACKFILL_ENRICHMENT", "0 3 * * *")  # 3 AM UTC
BACKFILL_ENRICHMENT_BATCH = int(os.getenv("BACKFILL_ENRICHMENT_BATCH", "100"))
BACKFILL_ENRICHMENT_PACE_S = float(os.getenv("BACKFILL_ENRICHMENT_PACE_S", "0.5"))
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

# Reranking: prefer local cross-encoder over LLM for speed
RERANK_PREFER_LOCAL = os.getenv("RERANK_PREFER_LOCAL", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Ingestion control plane (Workstream E Phase 0)
# ---------------------------------------------------------------------------
# Concurrent ingestion limit (semaphore). Replaces hardcoded `Semaphore(3)`
# in app/routers/ingestion.py. Increase when scaling ingestion workers; keep
# low on memory-constrained hosts (each in-flight ingest holds parser +
# embedder + Neo4j + ChromaDB connections).
INGEST_CONCURRENCY = int(os.getenv("INGEST_CONCURRENCY", "3"))

# Parent/child chunking tunables (Workstream E Phase 0.5). The actual
# read-points live in utils/chunker.py to avoid a config↔chunker import
# cycle; these are mirrored here for callsites (e.g. the markdown
# header-hierarchy chunker) that read sizing via the ``config`` package.
PARENT_CHUNK_TOKENS = int(os.getenv("PARENT_CHUNK_TOKENS", "512"))
CHILD_CHUNK_TOKENS = int(os.getenv("CHILD_CHUNK_TOKENS", "128"))
CHILD_CHUNK_OVERLAP_PCT = float(os.getenv("CHILD_CHUNK_OVERLAP_PCT", "0.1"))
# RAG C2.6 — feature-flag re-export. The canonical read is in
# ``config/features.py`` (with the rest of the ENABLE_* toggles); this
# duplicate getenv call exists solely so ``scripts/gen_env_example.py``
# (which only walks settings.py) picks the variable up and surfaces it in
# ``.env.example``. Default mirrors the feature flag default (off).
ENABLE_PARENT_CHILD_RETRIEVAL = os.getenv(
    "ENABLE_PARENT_CHILD_RETRIEVAL", "false"
).lower() in ("true", "1")

# Layout-aware parser dispatch (Workstream E Phase 2b — default flipped
# 2026-05-03 after eval validation against seeded eval-corpus v1; headline
# numbers incl. the delta vs the legacy chunker are published in
# docs/EVAL_BASELINES_PUBLIC.md).
# When true, ingest_file + ingest_content (when caller pre-dispatches)
# route supported extensions (.csv, .md, .markdown, .py) through the
# core/ingest/parsers/ + chunker registry — each CSV row, Markdown
# section, and Python function/class becomes its own chunk with
# structural metadata (column_headers, heading_path,
# file:start_line:end_line) stamped on the chunk. NOTE (AF-059): only
# ``heading_path`` is consumed (by markdown_strategy during chunking);
# the rest are stamped for provenance/future use but have no store-side
# reader today — they are preserved, not yet consumed.
# Set ENABLE_LAYOUT_AWARE_PARSING=false to revert to the legacy
# flat-text chunker.
ENABLE_LAYOUT_AWARE_PARSING = os.getenv(
    "ENABLE_LAYOUT_AWARE_PARSING", "true",
).lower() in ("true", "1", "yes")

# Ingestion mode (Workstream E Phase 5a). "sync" (default — no behavior
# change for desktop or existing server deployments) processes ingestion
# inline within the request semaphore. "async" enqueues onto the RQ
# queue for processing by the cerid-ingest-worker (must run separately
# via `python -m app.queue.worker` or as a compose service). The
# /ingest/progress contract is preserved across both modes via a Redis
# hash that both the router and worker write to.
INGEST_QUEUE_MODE = os.getenv("INGEST_QUEUE_MODE", "sync").lower()

# Memory-extract mode (Workstream A interface issue A close-out). "sync"
# (default — preserves the existing response envelope inline) runs the
# full extract → consolidate → store pipeline on the request thread.
# "async" enqueues onto the RQ memory queue, returning 202 + job_id
# immediately and exposing the result via
# ``GET /sdk/v1/memory/extract/jobs/{job_id}``. Worker config: same
# ``python -m app.queue.worker`` process drains both the ingest and
# memory queues — it subscribes to whichever queues have ``*_QUEUE_MODE
# =async`` set. Independent of INGEST_QUEUE_MODE.
MEMORY_QUEUE_MODE = os.getenv("MEMORY_QUEUE_MODE", "sync").lower()

# Embedding model version stamp — written to chunk metadata at ingest time
# so downstream re-embed migrations (Phase 5c) can identify which chunks
# need re-encoding when the embedding model changes. Defaults to the
# EMBEDDING_MODEL string for backward compat with existing un-stamped data.
EMBEDDING_MODEL_VERSION = os.getenv("EMBEDDING_MODEL_VERSION", EMBEDDING_MODEL)

# Per-domain embedding-model version overrides (Workstream E Phase 5c).
# Empty by default — the global EMBEDDING_MODEL_VERSION applies. This is a
# source-level dict, not an env var — there is no EMBEDDING_MODEL_VERSIONS_
# PER_DOMAIN environment variable to set. During a dual-collection
# migration, the operator edits this literal directly for the target
# domain, runs scripts/reembed_collection.py to dual-write, then keeps the
# override post-cutover (requires a code change + redeploy) so query
# routing reads from the versioned collection. See
# docs/EMBEDDING_MIGRATIONS.md for the full playbook.
EMBEDDING_MODEL_VERSIONS_PER_DOMAIN: dict[str, str] = {}


def embedding_version_for_domain(domain: str) -> str:
    """Return the embedding-model version label for a given KB domain.

    Falls back to the global EMBEDDING_MODEL_VERSION when no per-domain
    override is set. Used by the chunk-write path to stamp metadata and
    by the query-routing path (Phase 5c cutover) to pick the correct
    versioned ChromaDB collection.
    """
    return EMBEDDING_MODEL_VERSIONS_PER_DOMAIN.get(domain, EMBEDDING_MODEL_VERSION)

# Managed re-embed job (Phase 4.4 — ReembedChunksJob, the processor-job
# promotion of scripts/reembed_collection.py's in-place re-embed logic).
# Mirrors the BACKFILL_ENRICHMENT_BATCH / _PACE_S knob pair.
REEMBED_JOB_BATCH_SIZE = int(os.getenv("REEMBED_JOB_BATCH_SIZE", "200"))
REEMBED_JOB_PACE_S = float(os.getenv("REEMBED_JOB_PACE_S", "0.0"))

# HyPE backfill job (AF-049 — HypeBackfillJob, indexes existing chunks after
# RETRIEVAL_HYPE_ENABLED flips on; ingest only ever covers new chunks). Each
# indexed chunk is an LLM call, so MAX_CHUNKS caps a single run's spend — a
# capped run logs the cap and re-running skips already-indexed chunks, so
# repeated runs converge. BATCH_SIZE is the Chroma page size; PACE_S throttles
# between per-chunk LLM calls.
HYPE_BACKFILL_BATCH_SIZE = int(os.getenv("HYPE_BACKFILL_BATCH_SIZE", "200"))
HYPE_BACKFILL_MAX_CHUNKS = int(os.getenv("HYPE_BACKFILL_MAX_CHUNKS", "500"))
HYPE_BACKFILL_PACE_S = float(os.getenv("HYPE_BACKFILL_PACE_S", "0.0"))

# Smart routing: when enabled, "auto" model selection in chat uses the smart
# router to pick the best model based on query complexity and availability.
SMART_ROUTING_ENABLED = os.getenv("SMART_ROUTING_ENABLED", "true").lower() == "true"

# Internal LLM: model to use for pipeline intelligence operations
# (categorization, decomposition, contextual chunks, claim extraction)
# Options:
#   "openrouter" (default, direct calls)
#   "ollama"     (local, host-native on most platforms)
#   "quenchforge" (local, Mac+AMD inference service that speaks the Ollama
#                  HTTP protocol identically — github.com/cerid-ai/quenchforge,
#                  Apache-2.0; recommended on Intel Mac + AMD discrete GPU
#                  where stock Ollama falls back to CPU per ollama/ollama#1016)
#   or a specific model ID
INTERNAL_LLM_PROVIDER = os.getenv("INTERNAL_LLM_PROVIDER", "openrouter")
INTERNAL_LLM_MODEL = os.getenv("INTERNAL_LLM_MODEL", "")  # empty = provider default
# Display default surfaced by /providers when INTERNAL_LLM_MODEL is unset
# (Slice 2.2 — model ids live in config, never as call-site literals).
INTERNAL_LLM_MODEL_DEFAULT = os.getenv(
    "INTERNAL_LLM_MODEL_DEFAULT", "meta-llama/llama-3.3-70b-instruct"
)
# JSON-mode fallback model for internal-LLM calls that must return strict JSON.
INTERNAL_LLM_JSON_FALLBACK_MODEL = os.getenv(
    "INTERNAL_LLM_JSON_FALLBACK_MODEL", "openai/gpt-4o-mini"
)
# Chat fallback pool — models tried when the primary chat model hits a
# retryable error. Comma-separated env override; defaults to the v1 chain.
CHAT_FALLBACK_POOL = [
    m.strip()
    for m in os.getenv(
        "CHAT_FALLBACK_POOL",
        "openai/gpt-4o-mini,google/gemini-2.5-flash,x-ai/grok-4.3,anthropic/claude-sonnet-4.6",
    ).split(",")
    if m.strip()
]

# Retry budget + backoff for the `_call_ollama` transient-back-pressure
# loop (5xx / 429 / timeout / ConnectError). Mirrors the embed-side
# retry pattern that shipped in 51d7cc9 — eliminates the 10-15%
# Quenchforge fall-through rate observed during sustained-load ablations.
INTERNAL_LLM_MAX_RETRIES = int(os.getenv("INTERNAL_LLM_MAX_RETRIES", "3"))
INTERNAL_LLM_RETRY_BACKOFF = float(os.getenv("INTERNAL_LLM_RETRY_BACKOFF", "0.5"))

# Per-stage provider override pattern: setting
# PROVIDER_STAGE_<NORMALIZED_STAGE> (e.g.
# `PROVIDER_STAGE_LONGMEMEVAL_SCORE=openrouter`) routes that specific
# call site to a different provider, leaving privacy-sensitive stages
# (`memory_resolution`, `claim_extraction`) on the global default.
# Wildcard env pattern — declared here for .env.example surfacing only;
# the actual lookup happens in `core.utils.internal_llm._resolve_stage_provider`.
# Example below uses the LongMemEval scorer stage:
_PROVIDER_STAGE_EXAMPLE = os.getenv("PROVIDER_STAGE_LONGMEMEVAL_SCORE", "")

# ---------------------------------------------------------------------------
# Embedding cache
#   Bounded in-process LRU keyed on (namespace, sha256(text)). Optional
#   SQLite disk tier when CERID_EMBED_CACHE_PATH is set. Both layers are
#   namespace-isolated so a config flip cannot mix vector spaces.
# ---------------------------------------------------------------------------
CERID_EMBED_CACHE_SIZE = int(os.getenv("CERID_EMBED_CACHE_SIZE", "50000"))
CERID_EMBED_CACHE_PATH = os.getenv("CERID_EMBED_CACHE_PATH", "")

# ---------------------------------------------------------------------------
# LongMemEval runtime knobs
#   Declared here for .env.example surfacing; the eval CLI re-reads
#   them via os.environ.get so an operator can toggle without restarting
#   the MCP server.
# ---------------------------------------------------------------------------
LONGMEMEVAL_INGEST_PARALLEL = int(os.getenv("LONGMEMEVAL_INGEST_PARALLEL", "4"))
LONGMEMEVAL_SCORER = os.getenv("LONGMEMEVAL_SCORER", "llm")

# Default Ollama model for pipeline tasks — lightweight, runs on CPU or GPU
OLLAMA_DEFAULT_MODEL = os.getenv("OLLAMA_DEFAULT_MODEL", "llama3.2:3b")

# Quenchforge URL — local Mac+AMD inference service speaking the Ollama HTTP
# protocol on the same port. Defaults to OLLAMA_URL so a pure provider/URL swap
# is sufficient to migrate. Override explicitly when running both side-by-side
# on different ports.
QUENCHFORGE_URL = os.getenv(
    "QUENCHFORGE_URL",
    os.getenv("OLLAMA_URL", "http://host.docker.internal:11434"),
)

# Model names the Quenchforge client sends in /v1/embeddings + /v1/rerank
# requests (the gateway dispatches to the matching slot by this name). Only
# read when EMBEDDINGS_PROVIDER / RERANK_PROVIDER = quenchforge.
#
# QUENCHFORGE_EMBED_MODEL has NO default ON PURPOSE: it must match the model
# your corpus was embedded with (see EMBEDDING_MODEL above). Matching the output
# dimension is necessary but NOT sufficient — e.g. nomic-embed-text-v1.5 and
# Snowflake/snowflake-arctic-embed-m-v1.5 are both 768-dim but live in different
# vector spaces; querying one against a corpus embedded by the other silently
# collapses retrieval. Switching the embed model requires re-embedding the corpus.
# Leaving it empty makes the client raise + fall back to the ONNX embedder.
QUENCHFORGE_EMBED_MODEL = os.getenv("QUENCHFORGE_EMBED_MODEL", "")
# Rerank is a cross-encoder score (no stored vectors), so a sensible default is
# safe. Must be a reranking model Quenchforge serves.
QUENCHFORGE_RERANK_MODEL = os.getenv("QUENCHFORGE_RERANK_MODEL", "bge-reranker-v2-m3")

# Cached hardware-profile token, populated by scripts/detect-gpu.sh and read
# by the setup wizard / /system-check endpoint. One of:
#   nvidia | amd | amd-mac | metal | cpu | "" (empty = re-detect on next call)
# Not authoritative; the source of truth is a fresh detect-gpu.sh invocation.
CERID_HARDWARE_PROFILE = os.getenv("CERID_HARDWARE_PROFILE", "")

# ---------------------------------------------------------------------------
# Advanced RAG feature flags (per docs/TIERED_INFERENCE_ARCHITECTURE.md and
# the Quenchforge integration plan). All default off pending per-flag rollout
# in PR-4 / PR-5. Per-flag rationale lives in the architecture doc.
# ---------------------------------------------------------------------------
ENABLE_CASCADE_RERANK = os.getenv("ENABLE_CASCADE_RERANK", "false").lower() == "true"
# Original-relevance cut below which a candidate skips the cross-encoder when
# ENABLE_CASCADE_RERANK=true. Tuned at 0.3 from internal eval — most
# hybrid-search systems produce a long tail under 0.3 that the cross-encoder
# is unlikely to promote.
CASCADE_RERANK_PRE_THRESHOLD = float(os.getenv("CASCADE_RERANK_PRE_THRESHOLD", "0.3"))
ENABLE_SENTENCE_WINDOW = os.getenv("ENABLE_SENTENCE_WINDOW", "false").lower() == "true"
# Number of surrounding sentences (±) captured in each chunk's `window`
# metadata when ENABLE_SENTENCE_WINDOW=true. 3 is the LlamaIndex default and
# the value used in the Anthropic contextual-retrieval evals.
SENTENCE_WINDOW_SIZE = int(os.getenv("SENTENCE_WINDOW_SIZE", "3"))
ENABLE_PROMPT_PREFIX_CACHE = os.getenv("ENABLE_PROMPT_PREFIX_CACHE", "false").lower() == "true"
# Ollama / Quenchforge keep_alive value when prefix cache is enabled. Accepts
# any duration string the backend understands: "30s", "5m", "1h", "-1" for
# never-unload, "0" for immediate unload. Default 30m holds the model warm
# through typical session bursts without permanently pinning VRAM.
PROMPT_PREFIX_KEEP_ALIVE = os.getenv("PROMPT_PREFIX_KEEP_ALIVE", "30m")

ENABLE_MODEL_CASCADE = os.getenv("ENABLE_MODEL_CASCADE", "false").lower() == "true"

# E1 CR-029: opt-in escalation of COMPLEX + low-cost-sensitivity queries to the
# EXPERT tier (smart_router.EXPERT_MODELS). Off by default so "low cost
# sensitivity" doesn't silently 10x spend — but the tier is now actually
# reachable (it was maintained, weekly-refreshed, and SDK-advertised with no
# route() branch that could ever select it).
ENABLE_EXPERT_ESCALATION = os.getenv("ENABLE_EXPERT_ESCALATION", "false").lower() == "true"

ENABLE_SPECULATIVE_DECODE = os.getenv("ENABLE_SPECULATIVE_DECODE", "false").lower() == "true"
# Smaller draft model that proposes tokens for the main model to accept/reject
# when speculative decoding is enabled. Empty = let the backend default apply.
INTERNAL_LLM_DRAFT_MODEL = os.getenv("INTERNAL_LLM_DRAFT_MODEL", "")

ENABLE_CONSTRAINED_DECODE = os.getenv("ENABLE_CONSTRAINED_DECODE", "false").lower() == "true"

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

# CERID_PRELOAD_MODELS is a Docker build-arg consumed by src/mcp/Dockerfile
# (stage 2: models). The Python app never reads it at runtime — model
# downloading is governed by the lazy-load paths in core/retrieval/reranker.py
# and core/utils/embeddings.py. The declaration here exists so
# scripts/gen_env_example.py surfaces it in .env.example, where
# docker-compose.yml's build.args block reads it via ${CERID_PRELOAD_MODELS:-true}.
# See docs/MODEL_PRELOAD.md for the trade-off.
_PRELOAD_MODELS_FOR_ENV_EXAMPLE = os.getenv("CERID_PRELOAD_MODELS", "false")

# E1 CR-006: keys MUST match the live call_internal_llm ``stage=`` literals
# (core.utils.internal_llm._resolve_stage_provider does an exact-match lookup) or
# the per-stage provider override is silently inert. Renamed to the real stage
# names; dropped verification_* / chat_generation (verification uses call_llm_raw,
# chat uses call_llm — neither routes through call_internal_llm, so no stage
# exists to override, and they defaulted to the retired 'bifrost').
PIPELINE_PROVIDERS: dict[str, str] = {
    "claim_extraction": os.getenv("PROVIDER_CLAIM_EXTRACTION", _global_provider),
    "query_decompose": os.getenv("PROVIDER_QUERY_DECOMPOSE", _global_provider),
    "topic_extraction": os.getenv("PROVIDER_TOPIC_EXTRACTION", _global_provider),
    "memory_conflict_resolve": os.getenv("PROVIDER_MEMORY_CONFLICT_RESOLVE", _global_provider),
    "rerank_llm": os.getenv("PROVIDER_RERANK_LLM", _global_provider),
}

# ---------------------------------------------------------------------------
# Email IMAP Poller
# ---------------------------------------------------------------------------
CERID_EMAIL_IMAP_HOST = os.getenv("CERID_EMAIL_IMAP_HOST", "")
CERID_EMAIL_IMAP_PORT = int(os.getenv("CERID_EMAIL_IMAP_PORT", "993"))
CERID_EMAIL_IMAP_USER = os.getenv("CERID_EMAIL_IMAP_USER", "")
CERID_EMAIL_IMAP_PASSWORD = os.getenv("CERID_EMAIL_IMAP_PASSWORD", "")
CERID_EMAIL_FOLDER = os.getenv("CERID_EMAIL_FOLDER", "INBOX")
CERID_EMAIL_POLL_INTERVAL = int(os.getenv("CERID_EMAIL_POLL_INTERVAL", "15"))  # minutes

# Trading config — public-safe stubs (overridden at runtime when enabled)
CERID_TRADING_ENABLED: bool = False
TRADING_AGENT_URL: str = ""

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
#   Canonical level ladder — MUST match the live enforcement in
#   app/services/private_mode.py and the toolbar (chat-toolbar.tsx). CR-041
#   reconciled an earlier divergent L3/L4 documentation here.
#   Level 1: skip history saves + memory extraction + verification-report persist
#   Level 2: also skip KB/memory context injection (model isolated)
#   Level 3: also skip audit logging
#   Level 4: full ephemeral (session-wipe on close)
# These knobs declare the BOOT posture; the live level is the Redis key
# cerid:private_mode:global, seeded from these at startup by
# app.services.private_mode.seed_private_mode_from_env (CR-011) and mutable at
# runtime via POST /settings/private-mode.
# ---------------------------------------------------------------------------
PRIVATE_MODE_ENABLED: bool = os.getenv("CERID_PRIVATE_MODE", "false").lower() == "true"
PRIVATE_MODE_LEVEL: int = int(os.getenv("CERID_PRIVATE_MODE_LEVEL", "1"))

# ---------------------------------------------------------------------------
# Sensitive-Domain Retrieval (messages/imessage) — dedicated opt-in
# ---------------------------------------------------------------------------
# Orthogonal to the private-mode isolation ladder above (Task 1.2e): the
# ladder controls how much of a session is persisted/exposed, while this
# controls whether privacy-sensitive KB domains (iMessage) are ever eligible
# to surface in retrieval at all. Defaults OFF — the privacy-safe direction.
# Runtime-mutable via PATCH /settings (see app/routers/settings.py); a
# restart resets to the env default, which is also the safe direction.
SENSITIVE_DOMAIN_RETRIEVAL_ENABLED: bool = os.getenv(
    "SENSITIVE_DOMAIN_RETRIEVAL_ENABLED", "false",
).lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# Privacy — Email Header Anonymization
# ---------------------------------------------------------------------------
# When true, From/To/Cc headers in .eml/.mbox files are redacted during
# ingestion to prevent PII leakage into vector/graph stores and LLM prompts.
# Domain is preserved for context (e.g. "[redacted]@example.com").
ANONYMIZE_EMAIL_HEADERS: bool = os.getenv("CERID_ANONYMIZE_EMAIL_HEADERS", "true").lower() == "true"

# ---------------------------------------------------------------------------
# mbox ingestion — cap on messages parsed per .mbox file
# ---------------------------------------------------------------------------
# Large .mbox archives (10k+ messages) blow up the body-text concatenation
# and overwhelm the chunker. The parser stops extracting after this many
# messages, surfaces the truncation as a structured field, and the ingest
# response forwards the flag so callers (UI) can warn the user.
MBOX_MESSAGE_CAP: int = int(os.getenv("MBOX_MESSAGE_CAP", "100"))

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
# CERID_SYNC_DIR_HOST is consumed by docker-compose.yml to parameterize the
# /sync bind mount's HOST-side path. The Python app never reads it — the
# declaration here exists so scripts/gen_env_example.py surfaces it in
# .env.example (see docs/SANDBOX_TESTING.md for parallel-install workflow).
_SYNC_DIR_HOST_FOR_ENV_EXAMPLE = os.getenv("CERID_SYNC_DIR_HOST", "~/Dropbox/cerid-sync")
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
            "/agent/": (120, 60),
            "/sdk/": (120, 60),
            "/ingest": (10, 60),
            "/recategorize": (10, 60),
            # Audit C-11: state-mutating setup + polling admin/observability surfaces
            # were previously unthrottled. Bound them to prevent abuse / tight loops.
            "/setup/": (20, 60),
            "/admin/": (20, 60),
            # OPEN-14: the Diagnostics → Status pane polls several read-only
            # /observability/ endpoints across 4 time windows with periodic
            # refetch; 30/min self-throttled into a spurious "LLM 429". These
            # are cheap idempotent reads — give the dashboard headroom.
            "/observability/": (120, 60),
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
    "trading-agent": {
        "description": "Cerid Trading Agent — autonomous crypto trading",
        "rate_limits": {
            "/sdk/": (80, 60),       # 80 req/min — 5 concurrent sessions burst
            "/agent/": (80, 60),
        },
        "allowed_domains": ["trading"],
        "strict_domains": True,      # No bleed into personal/finance/coding data
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
            "/agent/": (120, 60),
            "/sdk/": (120, 60),
            "/ingest": (10, 60),
            "/recategorize": (10, 60),
            "/setup/": (20, 60),
            "/admin/": (20, 60),
            "/observability/": (120, 60),  # OPEN-14: read-only dashboard polls
            # Auth endpoints (only mounted when CERID_MULTI_USER=true). Tight
            # 5-per-60s budget stops brute-force credential guessing before
            # the password-equality work runs. /refresh shares the bucket so
            # a leaked refresh token can't be replayed at high rate either.
            "/auth/": (5, 60),
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
# Enterprise Features (public-safe stubs only; forbidden wiring lives below
# the "-- Internal settings" hook marker so it's stripped from the public repo)
# ---------------------------------------------------------------------------
CERID_ENTERPRISE = os.getenv("CERID_ENTERPRISE", "false").lower() in ("1", "true")
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

# ---------------------------------------------------------------------------
# Pro-tier MCP cloud connectors (Phase F)
# ---------------------------------------------------------------------------
# Static bearer token shared between the Cerid backend and the sibling MCP
# servers (google-workspace-mcp, ms365-mcp). Both servers expect this in
# the inbound Authorization header.
CERID_CONNECTORS_BEARER = os.getenv("CERID_CONNECTORS_BEARER", "")
# Streamable-HTTP URLs for the sibling MCP servers. Defaults point at the
# Docker network DNS names that stacks/connectors/docker-compose.yml creates.
GOOGLE_WORKSPACE_MCP_URL = os.getenv(
    "GOOGLE_WORKSPACE_MCP_URL", "http://cerid-google-workspace-mcp:8000/mcp",
)
MS365_MCP_URL = os.getenv("MS365_MCP_URL", "http://cerid-ms365-mcp:3000/mcp")
# OAuth client credentials for Google Workspace MCP single-user mode.
# Operator obtains these from Google Cloud Console → APIs & Services →
# Credentials → "Desktop app" OAuth client. The MCP server owns the OAuth
# flow + refresh-token rotation; Cerid backend never touches them.
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")

# Microsoft / Outlook OAuth. ``MICROSOFT_OAUTH_TENANT`` is ``common``
# for personal MSA accounts or a specific tenant GUID for org-only flows.
MICROSOFT_OAUTH_CLIENT_ID = os.getenv("MICROSOFT_OAUTH_CLIENT_ID", "")
MICROSOFT_OAUTH_CLIENT_SECRET = os.getenv("MICROSOFT_OAUTH_CLIENT_SECRET", "")
MICROSOFT_OAUTH_TENANT = os.getenv("MICROSOFT_OAUTH_TENANT", "common")

# ---------------------------------------------------------------------------
# Background Processor — mode contract (docs/BACKGROUND_JOBS.md §9)
# ---------------------------------------------------------------------------
PROCESSOR_MODE = os.getenv("PROCESSOR_MODE", "local")  # local | hybrid | disabled
PROCESSOR_API_THRESHOLD_TOKENS = int(os.getenv("PROCESSOR_API_THRESHOLD_TOKENS", "4000"))
PROCESSOR_MONTHLY_CAP_USD = float(os.getenv("PROCESSOR_MONTHLY_CAP_USD", "5"))
PROCESSOR_API_CAP_FALLBACK = os.getenv("PROCESSOR_API_CAP_FALLBACK", "local")  # local | hold
WORKER_LOAD_CEILING = os.getenv("WORKER_LOAD_CEILING", "auto")  # auto | <float>

if not NEO4J_PASSWORD:
    _config_logger.warning(
        "NEO4J_PASSWORD is empty — Neo4j queries will fail with auth errors. "
        "Check that .env is loaded (env_file in docker-compose.yml)."
    )
