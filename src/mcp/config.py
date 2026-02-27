# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Central configuration — domains, extensions, categorization, service URLs, and feature flags."""

import json
import logging as _logging
import os

# ---------------------------------------------------------------------------
# Hierarchical Taxonomy (domains -> sub-categories -> tags)
# ---------------------------------------------------------------------------
TAXONOMY = {
    "coding": {
        "description": "Source code, scripts, technical documentation",
        "icon": "code",
        "sub_categories": ["python", "javascript", "devops", "architecture", "general"],
    },
    "finance": {
        "description": "Financial documents, tax records, budgets",
        "icon": "dollar-sign",
        "sub_categories": ["tax", "investments", "budgets", "receipts", "general"],
    },
    "projects": {
        "description": "Project plans, meeting notes, specifications",
        "icon": "folder",
        "sub_categories": ["active", "archived", "proposals", "general"],
    },
    "personal": {
        "description": "Personal notes, journal entries, health records",
        "icon": "user",
        "sub_categories": ["notes", "health", "travel", "general"],
    },
    "general": {
        "description": "Uncategorized or cross-domain content",
        "icon": "file",
        "sub_categories": ["general"],
    },
    "conversations": {
        "description": "Extracted memories from chat sessions",
        "icon": "message-circle",
        "sub_categories": ["facts", "decisions", "preferences", "action-items", "general"],
    },
}

# User-defined custom domains via env var (JSON object with same shape as TAXONOMY entries)
_custom_domains_raw = os.getenv("CERID_CUSTOM_DOMAINS", "")
if _custom_domains_raw:
    try:
        _custom = json.loads(_custom_domains_raw)
        if isinstance(_custom, dict):
            TAXONOMY.update(_custom)
    except (json.JSONDecodeError, TypeError):
        pass  # silently ignore malformed custom domains

DOMAINS = list(TAXONOMY.keys())
DEFAULT_DOMAIN = "general"
DEFAULT_SUB_CATEGORY = "general"
INBOX_DOMAIN = "inbox"  # files here trigger AI categorization


def collection_name(domain: str) -> str:
    """ChromaDB collection name for a given domain."""
    return f"domain_{domain.replace(' ', '_').lower()}"

# ---------------------------------------------------------------------------
# Supported file extensions (mapped to parser functions in utils/parsers.py)
# ---------------------------------------------------------------------------
SUPPORTED_EXTENSIONS = {
    # Documents
    ".pdf", ".docx", ".xlsx", ".csv", ".tsv",
    # E-books & rich text
    ".epub", ".rtf",
    # Email
    ".eml", ".mbox",
    # Text / markup
    ".txt", ".md", ".rst", ".log",
    ".html", ".htm", ".xml",
    # Code
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".go", ".rs", ".rb", ".cpp", ".c", ".h", ".cs",
    ".sql", ".r", ".swift", ".kt",
    ".sh", ".bash",
    # Config / data
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
}

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
# Cross-Domain Connections
# ---------------------------------------------------------------------------
DOMAIN_AFFINITY = {
    "coding":        {"projects": 0.6},
    "projects":      {"coding": 0.6, "finance": 0.4},
    "finance":       {"projects": 0.4},
    "personal":      {"general": 0.5, "conversations": 0.3},
    "general":       {"personal": 0.5, "conversations": 0.3},
    "conversations": {"personal": 0.3, "general": 0.3},
}
CROSS_DOMAIN_DEFAULT_AFFINITY = 0.2   # weight for domain pairs not in DOMAIN_AFFINITY

# ---------------------------------------------------------------------------
# Hybrid Search
# ---------------------------------------------------------------------------
HYBRID_VECTOR_WEIGHT = 0.6          # weight for vector (cosine) score
HYBRID_KEYWORD_WEIGHT = 0.4         # weight for BM25 keyword score
BM25_DATA_DIR = os.path.join(os.getenv("DATA_DIR", "data"), "bm25")
QUERY_CONTEXT_MAX_CHARS = 14_000    # max chars assembled for LLM context
QUERY_RERANK_CANDIDATES = 15        # max candidates sent to LLM reranker

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
# Hallucination Detection
# ---------------------------------------------------------------------------
ENABLE_HALLUCINATION_CHECK = os.getenv("ENABLE_HALLUCINATION_CHECK", "false").lower() == "true"
HALLUCINATION_THRESHOLD = float(os.getenv("HALLUCINATION_THRESHOLD", "0.75"))

# ---------------------------------------------------------------------------
# Feedback Loop
# ---------------------------------------------------------------------------
ENABLE_FEEDBACK_LOOP = os.getenv("ENABLE_FEEDBACK_LOOP", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Smart Orchestration
# NOTE: ENABLE_MODEL_ROUTER and MONTHLY_BUDGET are client-side hints only.
# They are exposed to the GUI via GET /settings but never enforced server-side.
# ---------------------------------------------------------------------------
ENABLE_MODEL_ROUTER = os.getenv("ENABLE_MODEL_ROUTER", "false").lower() == "true"
COST_SENSITIVITY = os.getenv("COST_SENSITIVITY", "medium")  # low/medium/high
MONTHLY_BUDGET = float(os.getenv("MONTHLY_BUDGET", "0"))  # USD, 0 = unlimited

# ---------------------------------------------------------------------------
# Memory Extraction
# ---------------------------------------------------------------------------
ENABLE_MEMORY_EXTRACTION = os.getenv("ENABLE_MEMORY_EXTRACTION", "false").lower() == "true"
MEMORY_RETENTION_DAYS = int(os.getenv("MEMORY_RETENTION_DAYS", "180"))

# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
SYNC_DIR = os.path.expanduser(os.getenv("CERID_SYNC_DIR", "~/Dropbox/cerid-sync"))
MACHINE_ID = os.getenv("CERID_MACHINE_ID", os.uname().nodename.split(".")[0])
SYNC_BACKEND = os.getenv("CERID_SYNC_BACKEND", "local")

# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------
ENABLE_ENCRYPTION = os.getenv("ENABLE_ENCRYPTION", "false").lower() == "true"
# CERID_ENCRYPTION_KEY is read directly from env by utils/encryption.py
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# ---------------------------------------------------------------------------
# Plugin System & Feature Tiers
# ---------------------------------------------------------------------------
# Feature tier: "community" (OSS) or "pro" (commercial plugins enabled)
FEATURE_TIER = os.getenv("CERID_TIER", "community")

# Plugin directory (relative to app root or absolute path)
PLUGIN_DIR = os.getenv("CERID_PLUGIN_DIR", os.path.join(os.path.dirname(__file__), "plugins"))

# Comma-separated list of plugin names to load (empty = auto-discover all)
_enabled_plugins_raw = os.getenv("CERID_ENABLED_PLUGINS", "")
ENABLED_PLUGINS = [p.strip() for p in _enabled_plugins_raw.split(",") if p.strip()] if _enabled_plugins_raw else []

# Feature flags: controls what's available per tier
# Community features are always enabled; pro features require CERID_TIER=pro
FEATURE_FLAGS = {
    # Pro-only features (disabled in community tier)
    "ocr_parsing":         FEATURE_TIER == "pro",
    "audio_transcription": FEATURE_TIER == "pro",
    "image_understanding": FEATURE_TIER == "pro",
    "semantic_dedup":      FEATURE_TIER == "pro",
    "advanced_analytics":  FEATURE_TIER == "pro",
    "multi_user":          FEATURE_TIER == "pro",
    # Community features (always enabled)
    "hierarchical_taxonomy": True,
    "file_upload_gui":       True,
    "encryption_at_rest":    True,
    "truth_audit":           True,
    "live_metrics":          True,
}

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

COST_SENSITIVITY = COST_SENSITIVITY.strip().lower()
if COST_SENSITIVITY not in ("low", "medium", "high"):
    _config_logger.warning(
        "Invalid COST_SENSITIVITY=%r, defaulting to 'medium'", COST_SENSITIVITY
    )
    COST_SENSITIVITY = "medium"

if not NEO4J_PASSWORD:
    _config_logger.warning(
        "NEO4J_PASSWORD is empty — Neo4j queries will fail with auth errors. "
        "Check that .env is loaded (env_file in docker-compose.yml)."
    )