"""
Cerid AI - Central Configuration
Single source of truth for domains, extensions, categorization, and service URLs.

To add a new domain:  Add to DOMAINS list + mkdir ~/cerid-archive/<domain>
To add a file type:   Add to SUPPORTED_EXTENSIONS + register a parser in utils/parsers.py
To change AI tier:    Set CATEGORIZE_MODE env var to manual/smart/pro
"""

import os

# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------
DOMAINS = ["coding", "finance", "projects", "personal", "general"]
DEFAULT_DOMAIN = "general"
INBOX_DOMAIN = "inbox"  # files here trigger AI categorization

# ---------------------------------------------------------------------------
# Supported file extensions (mapped to parser functions in utils/parsers.py)
# ---------------------------------------------------------------------------
SUPPORTED_EXTENSIONS = {
    # Documents
    ".pdf", ".docx", ".xlsx", ".csv",
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
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "REDACTED_PASSWORD")
REDIS_URL = os.getenv("REDIS_URL", "redis://ai-companion-redis:6379")

# ---------------------------------------------------------------------------
# Temporal Awareness (Phase 4B.4)
# ---------------------------------------------------------------------------
TEMPORAL_HALF_LIFE_DAYS = 30         # exponential decay half-life for recency boost
TEMPORAL_RECENCY_WEIGHT = 0.1        # max boost from recency (added to relevance)

# ---------------------------------------------------------------------------
# Cross-Domain Connections (Phase 4B.3)
# ---------------------------------------------------------------------------
DOMAIN_AFFINITY = {
    "coding":   {"projects": 0.6},
    "projects": {"coding": 0.6, "finance": 0.4},
    "finance":  {"projects": 0.4},
    "personal": {"general": 0.5},
    "general":  {"personal": 0.5},
}
CROSS_DOMAIN_DEFAULT_AFFINITY = 0.2   # weight for domain pairs not in DOMAIN_AFFINITY

# ---------------------------------------------------------------------------
# Hybrid Search (Phase 4B.1)
# ---------------------------------------------------------------------------
HYBRID_VECTOR_WEIGHT = 0.6          # weight for vector (cosine) score
HYBRID_KEYWORD_WEIGHT = 0.4         # weight for BM25 keyword score
BM25_DATA_DIR = os.path.join(os.getenv("DATA_DIR", "data"), "bm25")

# ---------------------------------------------------------------------------
# Knowledge Graph Traversal (Phase 4B.2)
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
# Scheduled Maintenance (Phase 4C.1) — cron expressions
# ---------------------------------------------------------------------------
SCHEDULE_RECTIFY = os.getenv("SCHEDULE_RECTIFY", "0 3 * * *")         # daily 3 AM
SCHEDULE_HEALTH_CHECK = os.getenv("SCHEDULE_HEALTH_CHECK", "0 */6 * * *")  # every 6h
SCHEDULE_STALE_DETECTION = os.getenv("SCHEDULE_STALE_DETECTION", "0 4 * * 0")  # Sunday 4 AM
SCHEDULE_STALE_DAYS = int(os.getenv("SCHEDULE_STALE_DAYS", "90"))

# ---------------------------------------------------------------------------
# Webhooks (Phase 4C.4)
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
