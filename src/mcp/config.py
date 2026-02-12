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
# Redis keys
# ---------------------------------------------------------------------------
REDIS_INGEST_LOG = "ingest:log"
REDIS_LOG_MAX = 10_000
