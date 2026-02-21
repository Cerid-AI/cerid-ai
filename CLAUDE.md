# CLAUDE.md - Cerid AI

> **Extends:** `~/Develop/CLAUDE.md` — all global workflow orchestration, core principles, and task
> management rules apply here. This file adds only project-specific context.

---

## Project Overview

Cerid AI is a self-hosted, privacy-first Personal AI Knowledge Companion. It unifies multi-domain knowledge bases (code, finance, projects, artifacts) into a context-aware LLM interface with RAG-powered retrieval and intelligent agents. All data stays local; only LLM API calls go external.

**Status:** Phase 0–2 complete, **Phase 3 (Dashboard) deployed**. All 5 agents operational (Query, Triage, Rectification, Audit, Maintenance); 12 MCP tools; Streamlit dashboard at port 8501.

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
| Dashboard | 8501 | `src/gui/` | Streamlit admin UI |

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
│   │   ├── watch_obsidian.py         # Obsidian vault watcher (host process)
│   │   └── ingest_cli.py             # Batch CLI ingest tool
│   ├── agents/
│   │   ├── query_agent.py            # Multi-domain query with LLM reranking (Phase 2)
│   │   ├── triage.py                 # LangGraph triage agent for intelligent ingestion routing
│   │   ├── rectify.py                # Knowledge base health checks and conflict resolution
│   │   ├── audit.py                  # Operation tracking, cost estimation, usage analytics
│   │   └── maintenance.py            # System health, stale cleanup, collection analysis
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── requirements.txt
├── src/gui/
│   ├── app.py                        # Streamlit dashboard (5 panes)
│   ├── Dockerfile
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

**Agent endpoints (Phase 2):**
- `POST /agent/query` — Multi-domain query with LLM reranking and context assembly
- `POST /agent/triage` — LangGraph-powered file triage (validate → parse → categorize → chunk)
- `POST /agent/triage/batch` — Batch triage with per-file error recovery
- `POST /agent/rectify` — Knowledge base health checks (duplicates, stale, orphans, distribution)
- `POST /agent/audit` — Audit reports (activity, ingestion stats, costs, query patterns)
- `POST /agent/maintain` — Maintenance routines (health, stale detection, collection analysis, orphan cleanup)

**MCP protocol:**
- `GET /mcp/sse` — SSE stream (MCP protocol, JSON-RPC 2.0)
- `POST /mcp/messages?sessionId=X` — JSON-RPC handler

MCP tools (12 total):
- `pkb_query` — Single-domain query
- `pkb_ingest` — Ingest raw text
- `pkb_ingest_file` — Ingest a file with parsing and metadata
- `pkb_health` — Service health check
- `pkb_collections` — List ChromaDB collections
- `pkb_agent_query` — Multi-domain query with LLM reranking
- `pkb_artifacts` — List/filter ingested artifacts
- `pkb_recategorize` — Move artifact between domains
- `pkb_triage` — LangGraph-powered file triage
- `pkb_rectify` — Knowledge base health checks and auto-fix
- `pkb_audit` — Audit reports (activity, ingestion, costs, queries)
- `pkb_maintain` — Maintenance routines (health, stale, collections, orphans)

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

**Obsidian vault watcher (host process):**
```bash
python src/mcp/scripts/watch_obsidian.py --vault ~/Obsidian/MyVault [--domain personal] [--mode smart]
```
Monitors `.md` files only. Uses `/ingest` (text endpoint) since the vault isn't Docker-mounted. Higher debounce (5s) for Obsidian auto-save. Skips `.obsidian/`, `.trash/`, and files <10 bytes.

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

## Phase 2: Agent Workflows

### Query Agent (`agents/query_agent.py`)

Multi-domain search with LLM-powered reranking and intelligent context assembly.

**Features:**
- Parallel retrieval across all 5 ChromaDB collections
- Deduplication by (artifact_id + chunk_index), keeping highest relevance
- LLM reranking via Bifrost (Llama 3.1 free tier) — blends 60% LLM rank + 40% embedding score
- Token budget enforcement (14k character limit)
- Source attribution with confidence scoring

**Usage:**
```bash
curl -X POST http://localhost:8888/agent/query \
  -H "Content-Type: application/json" \
  -d '{"query": "tax deductions", "domains": ["finance", "general"], "top_k": 5}'
```

**Key Functions:**
- `multi_domain_query()` — Parallel ChromaDB queries across domains
- `deduplicate_results()` — Remove duplicate chunks
- `rerank_results()` — LLM-based relevance reranking via Bifrost (falls back to embedding sort)
- `assemble_context()` — Build context within token budget
- `agent_query()` — Main orchestration function

### Triage Agent (`agents/triage.py`)

LangGraph-orchestrated file ingestion pipeline with conditional routing.

**Graph flow:** validate → parse → route_categorization → [categorize?] → extract_metadata → chunk → END

**Features:**
- Conditional AI categorization (skips for known domains, triggers for inbox)
- Structured data detection (PDFs with tables, XLSX, CSV flagged as `is_structured`)
- Per-node error handling — failures route to `error_end` without crashing the pipeline
- Batch processing via `triage_batch()` — one failure doesn't stop the batch

**Usage:**
```bash
# Single file triage
curl -X POST http://localhost:8888/agent/triage \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/archive/inbox/report.pdf"}'

# Batch triage
curl -X POST http://localhost:8888/agent/triage/batch \
  -H "Content-Type: application/json" \
  -d '{"files": [{"file_path": "/archive/inbox/a.pdf"}, {"file_path": "/archive/coding/b.py"}]}'
```

### Rectification Agent (`agents/rectify.py`)

Knowledge base health monitoring and conflict resolution.

**Checks:**
- **duplicates** — Artifacts sharing the same content_hash across domains
- **stale** — Artifacts not updated in N days (default: 90)
- **orphans** — ChromaDB chunks without matching Neo4j artifact records
- **distribution** — Per-domain artifact/chunk counts and imbalance detection

**Auto-fix capabilities:**
- Resolve duplicates (keep oldest, remove rest + clean ChromaDB chunks)
- Clean orphaned chunks from ChromaDB

**Usage:**
```bash
# Run all checks (read-only)
curl -X POST http://localhost:8888/agent/rectify \
  -H "Content-Type: application/json" \
  -d '{}'

# Auto-fix duplicates and orphans
curl -X POST http://localhost:8888/agent/rectify \
  -H "Content-Type: application/json" \
  -d '{"auto_fix": true, "stale_days": 60}'
```

### Audit Agent (`agents/audit.py`)

Operation tracking, cost estimation, and usage analytics from the Redis audit trail.

**Reports:**
- **activity** — Event counts, domain breakdown, hourly timeline, recent failures
- **ingestion** — File type distribution, duplicate rate, avg chunks per file
- **costs** — Token usage estimates by tier (smart/pro/rerank), USD cost projections
- **queries** — Most-queried domains, average results per query

**Usage:**
```bash
curl -X POST http://localhost:8888/agent/audit \
  -H "Content-Type: application/json" \
  -d '{"reports": ["activity", "costs"], "hours": 48}'
```

### Maintenance Agent (`agents/maintenance.py`)

Comprehensive system health checks and automated cleanup.

**Actions:**
- **health** — Full connectivity check (ChromaDB, Neo4j, Redis, Bifrost) + data counts
- **stale** — Detect artifacts older than N days with optional auto-purge
- **collections** — Collection size analysis, missing/extra collection detection
- **orphans** — Find and optionally clean orphaned ChromaDB chunks

**Usage:**
```bash
# Read-only health check
curl -X POST http://localhost:8888/agent/maintain \
  -H "Content-Type: application/json" \
  -d '{"actions": ["health", "collections"]}'

# Auto-purge stale + orphans
curl -X POST http://localhost:8888/agent/maintain \
  -H "Content-Type: application/json" \
  -d '{"auto_purge": true, "stale_days": 60}'
```

### Dependencies

LangGraph >=0.2.0, langchain-core, langchain-openai, langchain-community

## Phase 3: Streamlit Dashboard

Admin and monitoring UI at `http://localhost:8501` (container: `ai-companion-dashboard`).

**Panes:**
- **Overview** — System health, domain distribution charts, collection listing
- **Artifacts** — Browse/filter artifacts by domain, recategorize from UI
- **Query** — Interactive multi-domain search with result visualization
- **Audit** — Activity timeline, ingestion stats, cost estimates, query patterns
- **Maintenance** — Health checks, stale detection, collection analysis, orphan cleanup

**Stack:** Streamlit + Plotly + Pandas, communicates with MCP server REST API.

**Start:** `cd src/mcp && docker compose up -d` (dashboard service included via `depends_on: mcp-server`)

## Roadmap

- **Phase 1 (Complete):** File ingestion, metadata extraction, AI categorization, deduplication, watcher, CLI, production hardening
- **Phase 1.5 (Complete):** Bulk ingest hardening — concurrent CLI (ThreadPoolExecutor), watcher retry queue, atomic dedup (UNIQUE CONSTRAINT), query improvements (real relevance scores, source attribution, token budget), pdfplumber for structured PDF table extraction
- **Phase 2 (Complete):** Query Agent + LLM reranking, Triage Agent (LangGraph), Rectification Agent, Audit Agent, Maintenance Agent, MCP tool expansion (12 tools)
- **Phase 3 (Complete):** Streamlit dashboard with 5 panes (Overview, Artifacts, Query, Audit, Maintenance). Obsidian vault watcher for auto-sync into knowledge base.
- **Phase 4:** Redis caching optimization, LUKS encryption, production hardening
