# CLAUDE.md - Cerid AI

## Project Overview

Cerid AI is a self-hosted, privacy-first Personal AI Knowledge Companion. It unifies multi-domain knowledge bases (code, finance, projects, artifacts) into a context-aware LLM interface with RAG-powered retrieval and intelligent agents. All data stays local; only LLM API calls go external.

**Status:** Phase 0 (Infrastructure) complete. Phase 1 (Core Ingestion) + Phase 1.5 (Bulk Hardening) implemented.

## Architecture

Microservices architecture with Docker Compose orchestration on a shared `llm-network` bridge network. Services communicate by container name.

### Services

| Service | Port | Stack Path | Tech |
|---------|------|------------|------|
| LibreChat (UI) | 3080 | `stacks/librechat/` | Node.js/React |
| MCP Server (API) | 8888 | `src/mcp/` | FastAPI / Python 3.11 |
| Bifrost (LLM Gateway) | 8080 | `stacks/bifrost/` | Semantic intent routing |
| ChromaDB (Vectors) | 8001 | via `src/mcp/docker-compose.yml` | Vector DB |
| Neo4j (Graph) | 7474/7687 | via `src/mcp/docker-compose.yml` | Graph DB |
| Redis (Cache) | 6379 | via `src/mcp/docker-compose.yml` | Cache + audit log |
| MongoDB (Chat) | 27017 | via `stacks/librechat/` | LibreChat persistence |
| PostgreSQL+pgvector | 5432 | via `stacks/librechat/` | RAG vector storage |
| Meilisearch | 7700 | via `stacks/librechat/` | Full-text search |
| RAG API | 8000 | via `stacks/librechat/` | Document processing |

### Key Data Flow

```
User → LibreChat (3080) → Bifrost (8080) → OpenRouter → LLM Provider
                        → MCP Server (8888/SSE) → ChromaDB/Neo4j (RAG context)

File Ingestion:
~/cerid-archive/ → Watcher → POST /ingest_file → Parse → Dedup → Chunk → ChromaDB + Neo4j + Redis
```

Bifrost classifies intent (coding/research/simple/general) and routes to the appropriate model.

## Directory Structure

```
├── README.md                         # Project overview and quick start
├── CLAUDE.md                         # This file — developer guide for AI sessions
├── scripts/start-cerid.sh            # One-command stack startup
├── docs/CERID_AI_PROJECT_REFERENCE.md # Detailed technical reference
├── src/mcp/
│   ├── main.py                       # FastAPI MCP server (REST + SSE + ingestion)
│   ├── config.py                     # Central configuration (domains, tiers, URLs)
│   ├── utils/
│   │   ├── parsers.py                # Extensible file parser registry
│   │   ├── metadata.py               # Metadata extraction + AI categorization
│   │   ├── chunker.py                # Token-based text chunking
│   │   ├── graph.py                  # Neo4j artifact CRUD
│   │   └── cache.py                  # Redis audit logging
│   ├── scripts/
│   │   ├── watch_ingest.py           # Watchdog folder watcher (host process)
│   │   └── ingest_cli.py             # Batch CLI ingest tool
│   ├── agents/triage.py              # (Planned) LangGraph triage agent
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── requirements.txt
├── stacks/
│   ├── bifrost/                      # LLM Gateway
│   └── librechat/                    # Chat UI
├── artifacts/ → ~/Dropbox/AI-Artifacts (symlink)
└── data/ → src/mcp/data (symlink)
```

## Development

### Starting the Stack

```bash
./scripts/start-cerid.sh
# Or manually:
docker network create llm-network  # first time only
cd stacks/bifrost && docker compose up -d
cd stacks/librechat && docker compose up -d
cd src/mcp && docker compose up -d
```

### MCP Server API (src/mcp/main.py)

**Core endpoints:**
- `GET /health` — DB connectivity check
- `GET /collections` — List ChromaDB collections
- `POST /query` — Query knowledge base (domain, top_k)
- `POST /ingest` — Ingest text content

**Ingestion endpoints (Phase 1):**
- `POST /ingest_file` — Ingest a file with parsing, metadata, optional AI categorization
- `POST /recategorize` — Move artifact between domains (moves chunks between collections)
- `GET /artifacts` — List ingested artifacts (filter by domain)
- `GET /ingest_log` — View audit trail from Redis

**MCP protocol:**
- `GET /mcp/sse` — SSE stream (MCP protocol, JSON-RPC 2.0)
- `POST /mcp/messages?sessionId=X` — JSON-RPC handler

MCP tools: `pkb_query`, `pkb_ingest`, `pkb_ingest_file`, `pkb_health`, `pkb_collections`

### Ingestion Pipeline

**File ingestion flow:** Parse file → Dedup check (SHA-256) → Extract metadata → AI categorize (optional) → Chunk → Batch store in ChromaDB + Neo4j + Redis

**Three categorization tiers:**
- `manual` — Domain from folder name only, no AI
- `smart` — Free model (Llama 3.1 via Bifrost) for classification
- `pro` — Premium model (Claude Sonnet via Bifrost)

AI calls are token-efficient: only first ~1500 chars sent for classification. Response format enforced as JSON.

**Supported file types:** PDF (structure-aware via pdfplumber — tables extracted as Markdown), DOCX (with tables), XLSX, CSV, HTML (tag-stripped), 30+ text/code/config formats. Binary files auto-detected and rejected.

**Watcher (host process):**
```bash
python src/mcp/scripts/watch_ingest.py [--mode smart|pro|manual]
```

**CLI batch ingest (concurrent):**
```bash
python src/mcp/scripts/ingest_cli.py --dir ~/cerid-archive/ [--mode smart] [--domain coding] [--workers 4]
```

**Archive folder structure:**
```
~/cerid-archive/
├── coding/      → domain="coding" (manual)
├── finance/     → domain="finance" (manual)
├── projects/    → domain="projects" (manual)
├── personal/    → domain="personal" (manual)
├── general/     → domain="general" (manual)
└── inbox/       → AI categorization triggered
```

### Adding a New Domain

1. Edit `src/mcp/config.py` → add to `DOMAINS` list
2. Create folder: `mkdir ~/cerid-archive/<new_domain>`
3. Rebuild: `cd src/mcp && docker compose up -d --build`

### Recategorizing Artifacts

```bash
# List artifacts in a domain
curl http://localhost:8888/artifacts?domain=coding

# Move to another domain
curl -X POST http://localhost:8888/recategorize \
  -H "Content-Type: application/json" \
  -d '{"artifact_id": "...", "new_domain": "projects"}'
```

### Configuration

- `src/mcp/config.py` — Domains, extensions, categorization tiers, DB URLs
- `stacks/librechat/.env` — API keys (OPENROUTER_API_KEY). Not committed.
- `stacks/bifrost/config.yaml` — Intent classification, model routing, budget
- `stacks/librechat/librechat.yaml` — MCP servers, endpoints, model list

**Key env vars (docker-compose.yml):**
- `CATEGORIZE_MODE=smart` — Default tier (manual/smart/pro)
- `BIFROST_URL=http://bifrost:8080/v1`
- `ARCHIVE_PATH=/archive` — Container-side mount point

### Verification

```bash
curl http://localhost:8888/health
curl http://localhost:8888/collections
curl http://localhost:8888/artifacts
curl http://localhost:8888/ingest_log?limit=10
```

### Extensibility

- **Parsers:** Registry pattern in `utils/parsers.py`. PDF uses pdfplumber (structure-aware). Add Docling later for OCR via `@register_parser`.
- **Domains:** Add to `config.DOMAINS` list. Neo4j nodes auto-created.
- **File types:** Add to `config.SUPPORTED_EXTENSIONS` + register parser.

## Conventions

- Docker services use container-name-based discovery on `llm-network`
- MCP protocol uses SSE transport with session-based message queuing
- Secrets go in `.env` files, never committed
- User files (`~/cerid-archive/`) mounted read-only, never in git repo
- Symlinks used for `artifacts/` and `data/` — don't break them
- ChromaDB metadata values are strings/ints only (lists stored as JSON strings)
- Error responses use `HTTPException` (returns `{"detail": "..."}`)
- Neo4j Cypher: use explicit RETURN clauses, not map projections (breaks with Python string ops)
- Deduplication: SHA-256 of parsed text, atomic via Neo4j UNIQUE CONSTRAINT on `content_hash`
- Batch ChromaDB writes: single `collection.add()` call per ingest, not per-chunk
- PDF parsing: pdfplumber extracts tables as Markdown, non-table text extracted separately to avoid duplication
- Host: Mac Pro (16-Core Xeon W, 160GB RAM), macOS

## Roadmap

- **Phase 1 (Complete):** File ingestion, metadata extraction, AI categorization, deduplication, watcher, CLI, production hardening
- **Phase 1.5 (Complete):** Bulk ingest hardening — concurrent CLI (ThreadPoolExecutor), watcher retry queue, atomic dedup (UNIQUE CONSTRAINT), query improvements (real relevance scores, source attribution, token budget), pdfplumber for structured PDF table extraction
- **Phase 2:** Query Agent, Rectification Agent, Audit Agent, Maintenance Agent
- **Phase 3:** Streamlit dashboard, Obsidian integration
- **Phase 4:** Redis caching optimization, LUKS encryption, production hardening
