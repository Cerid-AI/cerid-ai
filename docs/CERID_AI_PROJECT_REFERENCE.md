# Cerid AI - Project Plan & Technical Reference

**Document Version:** 3.1
**Date:** February 11, 2026
**Status:** Phase 1 Complete - Core Ingestion Pipeline Hardened & Production-Ready
**Repository:** https://github.com/sunrunnerfire/cerid-ai (private)
**Owner:** Justin (@sunrunnerfire)

---

## Document Purpose

This document serves as the single source of truth for the Cerid AI project. It provides:
1. Complete project context and vision
2. Current system state and configuration details
3. Technical specifications for all components
4. Detailed implementation reference for completed work
5. Development roadmap with implementation guidance
6. Troubleshooting reference

**For LLM Sessions:** This document contains all context needed to continue development. Start by reading the Executive Summary and Current State sections, then reference specific sections as needed.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current State](#2-current-state)
3. [Architecture](#3-architecture)
4. [Directory Structure](#4-directory-structure)
5. [Component Specifications](#5-component-specifications)
6. [Ingestion Pipeline (Phase 1)](#6-ingestion-pipeline-phase-1)
7. [Configuration Reference](#7-configuration-reference)
8. [Operations Guide](#8-operations-guide)
9. [Troubleshooting](#9-troubleshooting)
10. [Development Roadmap](#10-development-roadmap)
11. [Success Metrics](#11-success-metrics)

---

## 1. Executive Summary

### Project Vision

Cerid AI is a **self-hosted Personal AI Knowledge Companion** — a privacy-first, local-first workspace that unifies multi-domain knowledge bases (coding projects, taxes/finance, home projects, personal artifacts) into an efficient, context-aware LLM interface.

### Key Capabilities

- **Multi-Provider LLM Access** via Bifrost gateway (Claude, GPT, Grok, Gemini, DeepSeek)
- **File-Based Ingestion Pipeline** with document parsing, metadata extraction, and AI categorization
- **RAG-Powered Context Injection** for token-efficient knowledge retrieval (2-4k tokens/query)
- **Local Vector & Graph Storage** (ChromaDB, Neo4j, Redis)
- **MCP SSE Protocol** for tool integration with LibreChat UI
- **Three-Tier AI Categorization** (manual, smart, pro) for flexible ingestion workflows
- **Recategorization Workflow** for moving artifacts between domains with full audit trail
- **Privacy-First Architecture** — all data local, only LLM API calls external

### Core Principles

| Principle | Implementation |
|-----------|----------------|
| Self-Hosted/Local-First | Docker containers, no cloud storage, LUKS encryption |
| Token Efficiency | RAG limits context to 2-4k tokens, AI categorization uses ~400 token snippets |
| Extensibility | Parser registry, configurable domains, three-tier AI categorization |
| Privacy & Security | Encrypted local storage, isolated containers, user data never in git repo |
| Low Maintenance | One-command deploys, folder watcher auto-ingestion |
| Data Isolation | User files mounted read-only, archive data excluded from version control |

### Domains

- **cerid.ai** (primary)
- cerid.net
- getcerid.com

---

## 2. Current State

### Phase Status

| Phase | Focus | Status |
|-------|-------|--------|
| Phase 0 | Infrastructure & Baseline | ✅ Complete |
| Phase 1 | Core Ingestion Pipeline | ✅ Complete |
| Phase 2 | Enhanced Search & Agent Workflows | 🔄 Next |
| Phase 3 | GUI & Advanced Features | Planned |
| Phase 4 | Optimization & Documentation | Planned |

### Phase 0 Deliverables (Complete ✅)

- [x] 10 Docker containers deployed and healthy on `llm-network`
- [x] LibreChat UI accessible (port 3080) with Bifrost model routing
- [x] MCP Server with REST API (port 8888)
- [x] MCP SSE transport working — tools discoverable from LibreChat
- [x] ChromaDB, Neo4j, Redis connected and operational
- [x] Git repo consolidated and pushed
- [x] Healthchecks and monitoring configured
- [x] Documentation (README, CLAUDE.md, this document)

### Phase 1 Deliverables (Complete ✅)

**Core Pipeline:**
- [x] File parsing for PDF, DOCX, XLSX, CSV, HTML, and 30+ text/code formats
- [x] Extensible parser registry (decorator-based, swap-in-place)
- [x] Metadata extraction (keywords via spaCy NER, summary, token count)
- [x] Three-tier AI categorization (manual/smart/pro) via Bifrost gateway
- [x] Token-efficient AI calls (~1500 chars / ~400 tokens per classification)
- [x] Token-aware text chunking (512 tokens, 20% overlap)
- [x] Neo4j artifact tracking with BELONGS_TO domain relationships
- [x] Redis audit logging for all ingest/recategorize events
- [x] Recategorization workflow (moves chunks between ChromaDB collections)
- [x] `/ingest_file`, `/recategorize`, `/artifacts`, `/ingest_log` REST endpoints
- [x] `pkb_ingest_file` MCP tool accessible from LibreChat
- [x] Folder watcher script (host process, watchdog-based)
- [x] CLI batch ingestion script
- [x] Central configuration (`config.py`) for domains, extensions, models
- [x] User data isolation (archive mounted read-only, gitignored)

**Production Hardening (Phase 1.1):**
- [x] SHA-256 content deduplication (Neo4j content_hash + index)
- [x] HTML tag stripping for `.html`/`.htm` files (script/style/noscript excluded)
- [x] DOCX table extraction alongside paragraph text
- [x] Binary file detection (null byte check in first 512 bytes)
- [x] Batch ChromaDB writes (single `collection.add()` call per ingest)
- [x] File stability detection in watcher (polls file size before ingesting)
- [x] Watcher debounce cleanup (prevents unbounded memory growth)
- [x] PDF error handling (corrupted files, image-only detection, per-page resilience)
- [x] CSV encoding fallback (UTF-8 → latin-1), row truncation at 5,000
- [x] XLSX access-after-close fix (sheet names captured before iteration)
- [x] spaCy model cached at module level (loaded once, not per-call)
- [x] Expanded stop words for keyword fallback (~130 words)
- [x] HTTPException error handling across all endpoints (proper `{"detail": "..."}` format)
- [x] AI categorization `response_format: {"type": "json_object"}` for reliable parsing
- [x] SSE keepalive logging downgraded from INFO to DEBUG (reduced log noise)
- [x] spaCy `en_core_web_sm` model downloaded in Dockerfile (NER always active)
- [x] pypdf replaced deprecated pypdf2, version pins on all dependencies

### Verified Functionality

```bash
# All verified working:
curl http://localhost:8888/health                    # All 3 services connected
curl -X POST http://localhost:8888/ingest_file ...   # File parsing + storage
curl http://localhost:8888/artifacts?domain=coding   # Neo4j artifact listing
curl -X POST http://localhost:8888/recategorize ...  # Cross-domain chunk migration
curl http://localhost:8888/ingest_log?limit=10       # Redis audit trail
curl -X POST http://localhost:8888/query ...         # Domain-scoped vector search
curl http://localhost:8888/collections               # Per-domain collections
```

### Component Health

| Component | Status | Notes |
|-----------|--------|-------|
| Docker Infrastructure | ✅ Healthy | All 10 containers running |
| LibreChat UI | ✅ Healthy | Port 3080, MCP tools connected |
| Bifrost Gateway | ✅ Healthy | Port 8080, OpenRouter connected |
| MCP Server | ✅ Healthy | Port 8888, REST + SSE + Ingestion |
| ChromaDB | ✅ Healthy | Per-domain collections operational |
| Neo4j | ✅ Healthy | Schema initialized, artifacts tracked |
| Redis | ✅ Healthy | Audit logging active |
| RAG API | ✅ Healthy | Port 8000, document processing |
| MongoDB | ✅ Healthy | LibreChat data |
| Meilisearch | ✅ Healthy | Search indexing |

### Host System

| Spec | Value |
|------|-------|
| Hardware | Mac Pro (MacPro7,1) - Tower |
| CPU | 16-Core Intel Xeon W @ 3.2 GHz |
| RAM | 160 GB |
| OS | macOS |
| Docker | 29.1.5 |
| Docker Compose | v5.0.1 |
| Location | Fairfax, Virginia |

---

## 3. Architecture

### System Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER BROWSER                                │
│                     http://localhost:3080                            │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────────┐
│                        LibreChat (UI)                                │
│                 Container: LibreChat | Port: 3080                    │
│           Chat Interface + MCP Client + RAG Integration              │
└──────────┬──────────────────────────────────────┬───────────────────┘
           │                                      │
           │ LLM Requests                         │ MCP Tools (SSE)
           ▼                                      ▼
┌──────────────────────────┐    ┌─────────────────────────────────────┐
│    Bifrost Gateway       │    │      AI Companion MCP Server        │
│  Container: bifrost      │◄───│   Container: ai-companion-mcp       │
│  Port: 8080              │    │   Port: 8888                        │
│  Routes to OpenRouter    │    │                                     │
└──────────┬───────────────┘    │   REST:  /health /query /ingest     │
           │                    │          /ingest_file /recategorize  │
           ▼                    │          /artifacts /ingest_log      │
┌──────────────────────────┐    │   SSE:   /mcp/sse /mcp/messages     │
│      OpenRouter API      │    │   Tools: pkb_query, pkb_ingest,     │
│ (Claude, GPT, Gemini,    │    │          pkb_ingest_file, pkb_health│
│  Grok, DeepSeek, etc.)   │    │          pkb_collections            │
└──────────────────────────┘    └──────────┬──────────────────────────┘
                                           │
                                ┌──────────┼──────────┐
                                │          │          │
                                ▼          ▼          ▼
                             ChromaDB    Neo4j      Redis
                             :8001      :7474      :6380
                             (vectors)  (graph)    (audit)

Host Processes (outside Docker):
├── watch_ingest.py  → Monitors ~/cerid-archive/, POSTs to :8888
└── ingest_cli.py    → Batch CLI tool, POSTs to :8888

Supporting Services:
├── MongoDB (chat-mongodb)         - LibreChat data storage (27017)
├── Meilisearch (chat-meilisearch) - Search indexing (7700)
├── VectorDB (vectordb)            - PostgreSQL + pgvector for RAG (5432)
└── RAG API (rag_api)              - Document processing (8000)
```

### Network Configuration

All containers communicate via `llm-network` Docker bridge network (172.18.0.0/16).

**Service Discovery:** Containers reference each other by container name:
- LibreChat → `http://bifrost:8080/v1`
- LibreChat → `http://ai-companion-mcp:8888/mcp/sse`
- LibreChat → `http://rag_api:8000`
- MCP → `http://ai-companion-chroma:8000`
- MCP → `bolt://ai-companion-neo4j:7687`
- MCP → `redis://ai-companion-redis:6379`
- MCP → `http://bifrost:8080/v1` (for AI categorization)

### Data Flow

```
INGESTION (Phase 1 - Implemented):
  File → parse_file() → extract_metadata() → [ai_categorize()] → chunk_text()
    → ChromaDB (domain_* collection)
    → Neo4j (Artifact → BELONGS_TO → Domain)
    → Redis (audit log)

INGESTION ENTRY POINTS:
  Host watcher  → POST /ingest_file → _ingest_file()
  CLI batch     → POST /ingest_file → _ingest_file()
  MCP tool      → pkb_ingest_file   → _ingest_file()
  REST API      → POST /ingest_file → _ingest_file()

RECATEGORIZATION:
  POST /recategorize → fetch chunks from source collection
    → add to destination collection (updated metadata)
    → delete from source collection
    → update Neo4j BELONGS_TO relationship
    → Redis audit log (old_domain → new_domain)

QUERY (Phase 0 - Basic):
  POST /query → ChromaDB similarity search → return context + sources

CHAT (Phase 0 - Working):
  User → LibreChat → Bifrost → OpenRouter → LLM Response
  User → LibreChat → RAG API → VectorDB → Context → LLM
```

---

## 4. Directory Structure

```
~/cerid-ai/                              # Main repository
├── README.md                            # Project overview
├── CLAUDE.md                            # Developer guide for AI sessions
├── .gitignore                           # Excludes user data, binaries
├── librechat.yaml                       # LibreChat configuration (root copy)
├── artifacts -> ~/Dropbox/AI-Artifacts  # Symlink to artifacts storage
├── data -> src/mcp/data                 # Symlink to persistent data
│
├── docs/
│   └── CERID_AI_PROJECT_REFERENCE.md    # This document
│
├── scripts/
│   └── start-cerid.sh                   # Stack startup script
│
├── src/
│   └── mcp/                             # MCP Server (main application)
│       ├── main.py                      # FastAPI server (~769 lines)
│       ├── config.py                    # Central configuration (83 lines)
│       ├── requirements.txt             # Python dependencies (19 packages, version-pinned)
│       ├── Dockerfile                   # Container build (includes spaCy model)
│       ├── docker-compose.yml           # MCP + volumes + env
│       ├── docker-compose.override.yml
│       │
│       ├── utils/                       # Utility modules
│       │   ├── __init__.py
│       │   ├── parsers.py              # Extensible file parser registry (290 lines)
│       │   ├── metadata.py             # Metadata extraction + AI categorization (210 lines)
│       │   ├── chunker.py              # Token-based text chunking (56 lines)
│       │   ├── graph.py                # Neo4j artifact CRUD operations (186 lines)
│       │   └── cache.py               # Redis audit logging (57 lines)
│       │
│       ├── scripts/                     # Host-side scripts
│       │   ├── watch_ingest.py         # Watchdog folder watcher (223 lines)
│       │   └── ingest_cli.py           # Batch CLI ingestion tool (171 lines)
│       │
│       ├── agents/                      # Agent modules (Phase 2+)
│       │   └── triage.py               # Placeholder
│       │
│       └── data/                        # Persistent storage (gitignored)
│           ├── chroma/                  # Vector embeddings
│           ├── neo4j/                   # Graph database
│           ├── neo4j-logs/              # Neo4j logs
│           ├── redis/                   # Cache data
│           └── uploads/                 # Uploaded files
│
└── stacks/
    ├── bifrost/                         # LLM Gateway
    │   ├── docker-compose.yml
    │   ├── docker-compose.override.yml
    │   └── data/config.json             # Bifrost configuration
    │
    └── librechat/                       # Chat UI + RAG
        ├── docker-compose.yml
        ├── docker-compose.override.yml
        ├── librechat.yaml               # LibreChat configuration
        ├── .env                         # Environment variables
        ├── uploads/                     # User uploads
        ├── images/                      # Generated images
        └── logs/                        # Application logs

~/cerid-archive/                         # User knowledge archive (HOST, mounted read-only)
├── coding/                              # → domain="coding", mode="manual"
├── finance/                             # → domain="finance", mode="manual"
├── projects/                            # → domain="projects", mode="manual"
├── personal/                            # → domain="personal", mode="manual"
├── general/                             # → domain="general", mode="manual"
└── inbox/                               # → domain="" (triggers AI categorization)
```

### Data Isolation

User files and database volumes never enter the git repo:

| Data | Location | Git Status |
|------|----------|------------|
| User archive | `~/cerid-archive/` | Gitignored, mounted as `/archive:ro` |
| ChromaDB data | `src/mcp/data/chroma/` | Gitignored |
| Neo4j data | `src/mcp/data/neo4j/` | Gitignored |
| Redis data | `src/mcp/data/redis/` | Gitignored |
| Document binaries | `*.pdf, *.docx, *.xlsx, *.csv` | Gitignored globally |

---

## 5. Component Specifications

### 5.1 LibreChat

**Purpose:** Web-based chat interface with RAG and MCP support

| Property | Value |
|----------|-------|
| Image | `ghcr.io/danny-avila/librechat-dev:latest` |
| Container | `LibreChat` |
| Port | 3080 |
| Config | `stacks/librechat/librechat.yaml` |

**Key Features:**
- Multi-model selection via Bifrost (Claude, GPT, Grok, Gemini, DeepSeek)
- RAG document upload and query
- MCP client for tools (SSE connection to ai-companion-mcp:8888)
- Conversation history in MongoDB

### 5.2 Bifrost Gateway

**Purpose:** LLM request router to OpenRouter

| Property | Value |
|----------|-------|
| Image | `maximhq/bifrost:latest` |
| Container | `bifrost` |
| Port | 8080 |
| Config | `stacks/bifrost/data/config.json` |

**Endpoints:**
- Dashboard: http://localhost:8080
- Health: http://localhost:8080/health
- Providers: http://localhost:8080/api/providers
- Chat: http://localhost:8080/v1/chat/completions

**Key Learnings:**
- Uses `config.json` (NOT YAML)
- Field is `logs_store` (plural)
- Delete `config.db*` to force config reload
- Base URL: `https://openrouter.ai/api` (Bifrost appends `/v1`)

### 5.3 MCP Server (AI Companion)

**Purpose:** Personal Knowledge Base with REST API, MCP SSE tools, and ingestion pipeline

| Property | Value |
|----------|-------|
| Image | `mcp-mcp-server` (custom build) |
| Container | `ai-companion-mcp` |
| Port | 8888 |
| Source | `src/mcp/main.py` (~769 lines) |
| Version | 1.0.0 |

**REST Endpoints:**

| Method | Endpoint | Description | Status |
|--------|----------|-------------|--------|
| GET | `/` | Service info | ✅ |
| GET | `/health` | Health check (ChromaDB, Neo4j, Redis) | ✅ |
| GET | `/collections` | List ChromaDB collections | ✅ |
| GET | `/stats` | Database statistics | ✅ |
| POST | `/query` | Query knowledge base by domain | ✅ |
| POST | `/ingest` | Ingest raw text content | ✅ |
| POST | `/ingest_file` | Parse + ingest file with metadata | ✅ Phase 1 |
| POST | `/recategorize` | Move artifact between domains | ✅ Phase 1 |
| GET | `/artifacts` | List artifacts (filter by domain) | ✅ Phase 1 |
| GET | `/ingest_log` | Redis audit trail | ✅ Phase 1 |

**MCP SSE Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/mcp/sse` | SSE stream for LibreChat connection |
| HEAD | `/mcp/sse` | SSE probe |
| POST | `/mcp/sse` | SSE probe |
| POST | `/mcp/messages` | JSON-RPC 2.0 for MCP tool calls |

**MCP Tools:**

| Tool | Description | Input |
|------|-------------|-------|
| `pkb_query` | Query knowledge base | `query`, `domain`, `top_k` |
| `pkb_ingest` | Ingest raw text | `content`, `domain` |
| `pkb_ingest_file` | Ingest file with parsing | `file_path`, `domain`, `categorize_mode` |
| `pkb_health` | Check service health | (none) |
| `pkb_collections` | List collections | (none) |

### 5.4 Storage Services

#### ChromaDB (Vector Store)

| Property | Value |
|----------|-------|
| Image | `chromadb/chroma:latest` |
| Container | `ai-companion-chroma` |
| Port | 8001 (internal 8000) |
| Data | `src/mcp/data/chroma/` |

**Collections:** Named `domain_{name}` (e.g., `domain_coding`, `domain_finance`). Created automatically on first use.

**Metadata Constraint:** ChromaDB does not support list values in metadata. Lists (keywords, chunk_ids) are stored as JSON-serialized strings.

#### Neo4j (Graph Database)

| Property | Value |
|----------|-------|
| Image | `neo4j:latest` |
| Container | `ai-companion-neo4j` |
| Ports | 7474 (HTTP), 7687 (Bolt) |
| Data | `src/mcp/data/neo4j/` |
| Credentials | neo4j / REDACTED_PASSWORD |

**Schema (auto-initialized on startup):**
```cypher
CREATE CONSTRAINT artifact_id IF NOT EXISTS FOR (a:Artifact) REQUIRE a.id IS UNIQUE;
CREATE CONSTRAINT domain_name IF NOT EXISTS FOR (d:Domain) REQUIRE d.name IS UNIQUE;
CREATE INDEX artifact_content_hash IF NOT EXISTS FOR (a:Artifact) ON (a.content_hash);
-- Domain nodes seeded from config.DOMAINS list
```

**Node Types:**
- `Artifact` — id, filename, domain, keywords (JSON), summary, chunk_count, chunk_ids (JSON), content_hash (SHA-256), ingested_at, recategorized_at
- `Domain` — name (coding, finance, projects, personal, general)

**Relationship Types:**
- `(Artifact)-[:BELONGS_TO]->(Domain)` — domain membership

#### Redis (Cache + Audit)

| Property | Value |
|----------|-------|
| Image | `redis:alpine` |
| Container | `ai-companion-redis` |
| Port | 6380 (internal 6379) |
| Data | `src/mcp/data/redis/` |

**Audit Log:** Key `ingest:log`, capped at 10,000 entries. Each entry:
```json
{
  "event": "ingest|recategorize",
  "artifact_id": "uuid",
  "domain": "finance",
  "filename": "doc.pdf",
  "timestamp": "2026-02-10T12:00:00.000000",
  "old_domain": "coding"  // recategorize only
}
```

#### PostgreSQL/pgvector (RAG Vector Store)

| Property | Value |
|----------|-------|
| Image | `pgvector/pgvector:0.8.0-pg15-trixie` |
| Container | `vectordb` |
| Port | 5432 (internal) |
| Credentials | myuser / mypassword |

#### MongoDB (LibreChat Data)

| Property | Value |
|----------|-------|
| Image | `mongo:8.0.17` |
| Container | `chat-mongodb` |
| Port | 27017 (internal) |
| Database | `LibreChat` (case-sensitive!) |

#### Meilisearch (Search Index)

| Property | Value |
|----------|-------|
| Image | `getmeili/meilisearch:v1.12.3` |
| Container | `chat-meilisearch` |
| Port | 7700 (internal) |

### 5.5 RAG API

| Property | Value |
|----------|-------|
| Image | `ghcr.io/danny-avila/librechat-rag-api-dev-lite:latest` |
| Container | `rag_api` |
| Port | 8000 |

Uses OpenRouter API key for embeddings (text-embedding-3-small)

---

## 6. Ingestion Pipeline (Phase 1)

### 6.1 Overview

The ingestion pipeline processes files from the user's archive into the knowledge base. It supports multiple entry points (watcher, CLI, REST, MCP tool) and routes through a shared processing path.

```
Entry Points:
  watch_ingest.py  ──┐
  ingest_cli.py    ──┤
  POST /ingest_file ─┤──→ _ingest_file()
  pkb_ingest_file  ──┘
                        │
                        ▼
              ┌─────────────────┐
              │  parse_file()   │  parsers.py — extension-based dispatch
              │  (PDF/DOCX/etc) │
              └────────┬────────┘
                       ▼
              ┌─────────────────┐
              │extract_metadata()│  metadata.py — local keyword/summary extraction
              │  (spaCy NER)    │
              └────────┬────────┘
                       ▼
              ┌─────────────────┐
              │ ai_categorize() │  metadata.py — optional, via Bifrost
              │ (smart or pro)  │  Sends ~1500 char snippet (~400 tokens)
              └────────┬────────┘
                       ▼
              ┌─────────────────┐
              │  chunk_text()   │  chunker.py — 512 tokens, 20% overlap
              │  (tiktoken)     │
              └────────┬────────┘
                       ▼
              ┌─────────────────────────────────┐
              │  Store:                          │
              │  • ChromaDB (domain_* collection)│
              │  • Neo4j (Artifact node)         │
              │  • Redis (audit log)             │
              └─────────────────────────────────┘
```

### 6.2 Parser Registry (`utils/parsers.py`)

Extensible file parser using a decorator-based registry pattern. Adding a new parser requires only a new function with `@register_parser([".ext"])`.

**Supported Formats:**

| Extension | Parser | Library | Features |
|-----------|--------|---------|----------|
| `.pdf` | `parse_pdf` | pypdf | Per-page extraction, image-only detection, corruption handling |
| `.docx` | `parse_docx` | python-docx | Paragraphs + table extraction |
| `.xlsx` | `parse_xlsx` | openpyxl | Multi-sheet, access-after-close safe |
| `.csv` | `parse_csv` | pandas | UTF-8/latin-1 fallback, 5k row cap |
| `.html .htm` | `parse_html` | stdlib HTMLParser | Tag stripping, script/style/noscript excluded |
| `.txt .md .rst .log .xml` | `parse_text` | built-in | Binary detection (null byte check) |
| `.py .js .ts .jsx .tsx` + 15 more | `parse_text` | built-in | All code formats, binary detection |
| `.json .yaml .yml .toml .ini .cfg` | `parse_text` | built-in | Config/data formats, binary detection |

All parsers cap output at 2MB (`_MAX_TEXT_CHARS`). Text files are checked for binary content (null bytes) before reading.

**Adding a New Parser:**
```python
from utils.parsers import register_parser

@register_parser([".rtf"])
def parse_rtf(file_path: str) -> dict:
    # Parse RTF, return {"text": str, "file_type": "rtf", "page_count": None}
    ...
```

### 6.3 Metadata Extraction (`utils/metadata.py`)

**Local Extraction (no API calls):**
- `filename`, `file_type`, `domain`
- `ingested_at` (ISO timestamp)
- `char_count`, `estimated_tokens` (tiktoken cl100k_base)
- `keywords` — spaCy NER entities (top 10), falls back to word frequency if spaCy model not loaded
- `summary` — first 200 characters of text

**AI Categorization (optional, via Bifrost):**

Three tiers configured in `config.py`:

| Mode | Model | Cost | When Used |
|------|-------|------|-----------|
| `manual` | None | Free | File in a known domain folder |
| `smart` | `meta-llama/llama-3.1-8b-instruct:free` | Free | Default for inbox/unknown |
| `pro` | `anthropic/claude-sonnet-4-5-20250929` | Paid | Explicit request for premium |

Token efficiency: Only sends first 1,500 characters (~400 tokens) of document text to the AI. Returns suggested domain, keywords, and summary.

### 6.4 Text Chunking (`utils/chunker.py`)

- Uses `tiktoken` cl100k_base encoding for accurate token counting
- Default: 512 tokens per chunk, 20% overlap
- Configurable via `config.CHUNK_MAX_TOKENS` and `config.CHUNK_OVERLAP`
- Each chunk stored as a separate ChromaDB document with `chunk_index` metadata

### 6.5 Neo4j Artifact Tracking (`utils/graph.py`)

All Neo4j operations are isolated in `graph.py`. Main.py never runs Cypher directly.

**Functions:**
- `init_schema(driver)` — constraints, content_hash index, seed Domain nodes (called on startup)
- `create_artifact(...)` — creates Artifact node + BELONGS_TO relationship (includes content_hash)
- `get_artifact(driver, artifact_id)` — fetch single artifact
- `list_artifacts(driver, domain, limit)` — list with optional domain filter
- `recategorize_artifact(driver, artifact_id, new_domain)` — move BELONGS_TO to new Domain

**Important:** Neo4j Cypher map projections (`a {.*, key: val}`) cannot be used with Python string `.replace()` for query templating. Use explicit RETURN clauses instead.

### 6.6 Redis Audit Logging (`utils/cache.py`)

- `log_event(redis_client, event_type, artifact_id, domain, filename, extra)` — append to audit log
- `get_log(redis_client, limit)` — read recent entries
- Log key: `ingest:log`, capped at 10,000 entries
- Events: `ingest`, `recategorize`

### 6.7 Recategorization Workflow

Recategorization is a multi-step operation because ChromaDB collections are per-domain:

1. Fetch Artifact from Neo4j → get chunk_ids and old_domain
2. Fetch all chunks from source ChromaDB collection (`domain_{old_domain}`)
3. Add chunks to destination collection (`domain_{new_domain}`) with updated metadata
4. Delete chunks from source collection
5. Update Neo4j: delete old BELONGS_TO, create new BELONGS_TO
6. Log recategorize event to Redis with old_domain and new_domain

### 6.8 Folder Watcher (`scripts/watch_ingest.py`)

Runs on the **host** (not in Docker). Monitors `~/cerid-archive/` for new files.

**Domain Detection:**
- `~/cerid-archive/coding/file.py` → domain="coding", mode="manual"
- `~/cerid-archive/inbox/file.pdf` → domain="" (triggers AI categorization)
- Unknown subfolder → domain="" (triggers AI categorization)

**Path Translation:** Host path `~/cerid-archive/finance/tax.pdf` → Container path `/archive/finance/tax.pdf`

**Features:**
- File stability detection (polls file size 3 times at 1s intervals before ingesting)
- Debounce (2s) with automatic cleanup of stale entries (>60s)
- Handles `on_created`, `on_modified`, and `on_moved` events
- Colored terminal logging (green=success, yellow=warn, red=error)
- Skips hidden files and unsupported extensions

**Usage:**
```bash
python src/mcp/scripts/watch_ingest.py [--mode smart|pro|manual] [--folder ~/cerid-archive]
```

### 6.9 CLI Batch Ingest (`scripts/ingest_cli.py`)

Batch ingestion tool for processing existing files.

**Features:**
- Recursive directory walking
- `--domain` flag to force domain for all files
- `--mode` flag for categorization tier
- `--dry-run` to preview without ingesting
- Skips `legacy-*` directories and hidden files
- Summary report with domain breakdown

**Usage:**
```bash
python src/mcp/scripts/ingest_cli.py --dir ~/cerid-archive/ [--mode smart] [--domain coding] [--dry-run]
```

### 6.10 Central Configuration (`config.py`)

Single source of truth for all configurable values. No hardcoded domains, extensions, or URLs elsewhere.

**Adding a New Domain:**
1. Add to `DOMAINS` list in `config.py`
2. Create folder: `mkdir ~/cerid-archive/new_domain`
3. Neo4j Domain node auto-created on next startup via `init_schema()`
4. ChromaDB collection auto-created on first ingest

**Adding a New File Type:**
1. Add extension to `SUPPORTED_EXTENSIONS` in `config.py`
2. Register parser function in `utils/parsers.py` with `@register_parser([".ext"])`

**Key Configuration Values:**

| Setting | Default | Env Override |
|---------|---------|-------------|
| DOMAINS | coding, finance, projects, personal, general | — |
| DEFAULT_DOMAIN | general | — |
| CATEGORIZE_MODE | smart | `CATEGORIZE_MODE` |
| CHUNK_MAX_TOKENS | 512 | — |
| CHUNK_OVERLAP | 0.2 | — |
| AI_SNIPPET_MAX_CHARS | 1500 | — |
| ARCHIVE_PATH | /archive | `ARCHIVE_PATH` |
| WATCH_FOLDER | ~/cerid-archive | `WATCH_FOLDER` |
| BIFROST_URL | http://bifrost:8080/v1 | `BIFROST_URL` |

---

## 7. Configuration Reference

### 7.1 Environment Variables

**stacks/librechat/.env:**
```bash
OPENROUTER_API_KEY=sk-or-v1-xxxxx    # Required - OpenRouter API key
OPENAI_API_KEY=sk-or-v1-xxxxx        # Same key - used by RAG API for embeddings
```

### 7.2 LibreChat Configuration

**stacks/librechat/librechat.yaml:**
```yaml
version: "1.2.1"

mcpServers:
  ai-companion:
    type: sse
    url: "http://ai-companion-mcp:8888/mcp/sse"

endpoints:
  custom:
    - name: "Bifrost Gateway"
      apiKey: "not-needed"
      baseURL: "http://bifrost:8080/v1"
      models:
        default:
          # Claude, GPT, Grok, Gemini, DeepSeek models via OpenRouter
```

### 7.3 Bifrost Configuration

**stacks/bifrost/data/config.json:**
```json
{
  "providers": [
    {
      "name": "openrouter",
      "base_url": "https://openrouter.ai/api",
      "api_keys": [{"name": "openrouter-key", "value": {"from_env": "OPENROUTER_API_KEY"}}],
      "custom_provider_config": {"base_provider_type": "openai"}
    }
  ],
  "client": {"enable_logging": true},
  "logs_store": {"type": "sqlite", "path": "/app/data/logs.db"}
}
```

### 7.4 MCP Docker Compose

**src/mcp/docker-compose.yml:**
```yaml
services:
  mcp-server:
    container_name: ai-companion-mcp
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8888:8888"
    volumes:
      - ./data:/app/data
      - ./main.py:/app/main.py
      - .:/app
      - ~/cerid-archive:/archive:ro       # Read-only archive mount
    environment:
      - CHROMA_URL=http://ai-companion-chroma:8000
      - NEO4J_URI=bolt://ai-companion-neo4j:7687
      - NEO4J_USER=neo4j
      - NEO4J_PASSWORD=REDACTED_PASSWORD
      - REDIS_URL=redis://ai-companion-redis:6379
      - PORT=8888
      - CATEGORIZE_MODE=smart              # Default AI tier
      - BIFROST_URL=http://bifrost:8080/v1 # AI categorization gateway
      - ARCHIVE_PATH=/archive              # Container-side mount point
    networks:
      - llm-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8888/health"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s
```

### 7.5 MCP Dependencies

**src/mcp/requirements.txt:**
```
# Core framework
fastapi>=0.100
uvicorn[standard]
pydantic>=2.0

# HTTP client
httpx>=0.24
httpx-sse

# Databases
chromadb>=0.4
neo4j>=5.0
redis>=4.0

# AI / NLP
spacy>=3.5
tiktoken
sentence-transformers
langgraph
langchain-core

# File parsing
pypdf>=3.0
python-docx
openpyxl>=3.1
pandas>=2.0

# Utilities
python-dotenv
python-multipart
numpy<2
```

---

## 8. Operations Guide

### 8.1 Start All Services

```bash
# Using start script (recommended)
~/cerid-ai/scripts/start-cerid.sh

# Or manually:
docker network create llm-network 2>/dev/null || true
cd ~/cerid-ai/stacks/bifrost && docker compose up -d
cd ~/cerid-ai/src/mcp && docker compose up -d
cd ~/cerid-ai/stacks/librechat && docker compose up -d
```

### 8.2 Stop All Services

```bash
cd ~/cerid-ai/stacks/librechat && docker compose down
cd ~/cerid-ai/src/mcp && docker compose down
cd ~/cerid-ai/stacks/bifrost && docker compose down
```

### 8.3 Check Status

```bash
# All containers
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Health checks
curl -s http://localhost:8888/health | jq
curl -s http://localhost:3080 | head -3
curl -s http://localhost:8080/health
```

### 8.4 View Logs

```bash
docker logs LibreChat --tail 50 -f
docker logs ai-companion-mcp --tail 50 -f
docker logs bifrost --tail 50 -f
```

### 8.5 Rebuild MCP After Code Changes

```bash
cd ~/cerid-ai/src/mcp && docker compose up -d --build
```

### 8.6 Start Folder Watcher

```bash
python src/mcp/scripts/watch_ingest.py --mode smart
```

### 8.7 Batch Ingest Files

```bash
# Dry run first
python src/mcp/scripts/ingest_cli.py --dir ~/cerid-archive/ --dry-run

# Then ingest
python src/mcp/scripts/ingest_cli.py --dir ~/cerid-archive/ --mode smart
```

### 8.8 API Usage

**Ingest a file:**
```bash
curl -X POST http://localhost:8888/ingest_file \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/archive/coding/script.py", "domain": "coding"}'
```

**Query knowledge base:**
```bash
curl -X POST http://localhost:8888/query \
  -H "Content-Type: application/json" \
  -d '{"query": "search terms", "domain": "coding", "top_k": 3}'
```

**List artifacts:**
```bash
curl http://localhost:8888/artifacts?domain=finance&limit=50
```

**Recategorize an artifact:**
```bash
curl -X POST http://localhost:8888/recategorize \
  -H "Content-Type: application/json" \
  -d '{"artifact_id": "uuid-here", "new_domain": "projects"}'
```

**View audit trail:**
```bash
curl http://localhost:8888/ingest_log?limit=10
```

### 8.9 Connectivity Tests

```bash
# LibreChat → MCP
docker exec LibreChat wget -q -O - http://ai-companion-mcp:8888/health

# LibreChat → Bifrost
docker exec LibreChat wget -q -O - http://bifrost:8080/api/providers | head -20

# MCP → ChromaDB
curl -s http://localhost:8888/collections

# Direct LLM test via Bifrost
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -d '{"model": "openrouter/anthropic/claude-sonnet-4", "messages": [{"role": "user", "content": "Hello!"}]}'
```

### 8.10 Backup

```bash
tar czf cerid-backup-$(date +%Y%m%d).tar.gz \
  ~/cerid-ai/src/mcp/data \
  ~/cerid-ai/stacks/librechat/.env \
  ~/cerid-ai/stacks/bifrost/data
```

---

## 9. Troubleshooting

### 9.1 Container Shows "Unhealthy"

```bash
curl -s http://localhost:8888/health  # MCP
curl -s http://localhost:3080         # LibreChat
curl -s http://localhost:8000/health  # RAG API
```

Common causes: missing `start_period`, IPv6 `localhost` issue (use `127.0.0.1`), missing curl/wget in container.

### 9.2 Neo4j Cypher Map Projection Errors

**Error:** `Invalid input '{': expected an identifier`

**Cause:** Cypher `a {.*, key: val}` syntax conflicts with Python string operations. Double braces `{{` stay literal when using `.replace()` instead of `.format()`.

**Fix:** Use explicit RETURN clauses instead of map projections:
```python
# BAD (breaks with Python string substitution)
"RETURN a {.*, domain: d.name} AS artifact"

# GOOD (explicit fields)
"RETURN a.id AS id, a.filename AS filename, d.name AS domain_name"
```

### 9.3 ChromaDB Metadata List Values

**Error:** Various type errors when storing lists in metadata

**Cause:** ChromaDB does not support list values in metadata fields.

**Fix:** Serialize lists as JSON strings:
```python
meta["keywords"] = json.dumps(["python", "fastapi"])  # NOT a list
```

### 9.4 async/await Errors in MCP Tool Execution

**Error:** `RuntimeError: cannot use await outside async function` or `coroutine was never awaited`

**Cause:** `_ingest_file()` is async (calls `ai_categorize()` which uses httpx). All callers must be async.

**Fix:** `execute_tool()` and `build_response()` are both async. The SSE message handler uses `await build_response()`.

### 9.5 Bifrost Config Not Loading

```bash
cd ~/cerid-ai/stacks/bifrost/data
rm -f config.db*
cd .. && docker compose restart bifrost
```

### 9.6 MongoDB Case Sensitivity

Ensure `MONGO_URI` uses exact case: `mongodb://chat-mongodb:27017/LibreChat`

### 9.7 VectorDB Database Missing

```bash
docker exec vectordb psql -U myuser -d postgres -c "CREATE DATABASE mydatabase;"
```

### 9.8 RAG API NumPy Error

Pin in requirements.txt: `numpy<2`

### 9.9 spaCy Model Not Found

The `en_core_web_sm` model is downloaded automatically during Docker build (in the Dockerfile). If not available at runtime, the system falls back to simple keyword extraction based on word frequency (no NER). To manually install:
```bash
docker exec ai-companion-mcp python -m spacy download en_core_web_sm
```

---

## 10. Development Roadmap

### Phase Overview

| Phase | Focus | Status |
|-------|-------|--------|
| Phase 0 | Infrastructure & Baseline | ✅ Complete |
| Phase 1 | Core Ingestion Pipeline | ✅ Complete |
| Phase 2 | Enhanced Search & Agent Workflows | 🔄 Next |
| Phase 3 | GUI & Advanced Features | Planned |
| Phase 4 | Optimization & Documentation | Planned |

### Phase 0: Infrastructure (Complete ✅)

- [x] Docker stacks deployed (10 containers on `llm-network`)
- [x] LibreChat + Bifrost + MCP integration
- [x] Network connectivity verified
- [x] Git repo consolidated and pushed
- [x] Healthchecks fixed
- [x] MCP SSE transport implemented
- [x] MCP tools discoverable from LibreChat UI

### Phase 1: Core Ingestion Pipeline (Complete ✅)

**Core Pipeline:**
- [x] Central configuration (`config.py`)
- [x] Extensible file parser registry (PDF, DOCX, XLSX, CSV, HTML, 30+ text/code formats)
- [x] Metadata extraction (spaCy NER, token counting, summaries)
- [x] Three-tier AI categorization (manual/smart/pro)
- [x] Token-aware chunking (tiktoken, 512 tokens, 20% overlap)
- [x] Neo4j artifact tracking (Artifact → BELONGS_TO → Domain)
- [x] Redis audit logging (ingest + recategorize events)
- [x] Recategorization workflow (cross-collection chunk migration)
- [x] REST endpoints (/ingest_file, /recategorize, /artifacts, /ingest_log)
- [x] MCP tool (pkb_ingest_file)
- [x] Folder watcher (host process, watchdog)
- [x] CLI batch ingest tool
- [x] User data isolation (read-only mount, gitignore)

**Production Hardening:**
- [x] SHA-256 content deduplication with Neo4j index
- [x] HTML tag stripping, DOCX table extraction, binary file detection
- [x] Batch ChromaDB writes, file stability detection, debounce cleanup
- [x] Error handling (PDF corruption, CSV encoding, XLSX access-after-close)
- [x] HTTPException responses, AI JSON response format, reduced log noise
- [x] spaCy model in Dockerfile, pypdf version upgrade, dependency version pins

### Phase 2: Enhanced Search & Agent Workflows (Next 🔄)

**Goals:** Context-aware search, query refinement, and foundational agent patterns.

**Planned Tasks:**

1. **Enhanced Query Capabilities**
   - Multi-domain search (query across multiple collections simultaneously)
   - Relevance scoring improvements (beyond default ChromaDB cosine similarity)
   - Query result ranking with confidence scores
   - Context assembly with token budget management (<4k tokens)

2. **Query Agent** (`src/mcp/agents/query.py`)
   - LangGraph-based agent for intelligent query routing
   - ChromaDB similarity search + Neo4j relationship traversal
   - Cross-domain context assembly
   - Ranked suggestions with previews

3. **Triage Agent Enhancement** (`src/mcp/agents/triage.py`)
   - LangGraph graph wrapping the existing ingestion pipeline
   - Conditional routing based on file type and content
   - Batch processing with error recovery
   - Integration with existing parser registry

4. **Rectification Agent** (`src/mcp/agents/rectify.py`)
   - Neo4j queries for outdated or conflicting data
   - Conflict detection between related artifacts
   - Auto-update graph relationships
   - Confidence scoring with AI fallback

5. **Audit Agent** (`src/mcp/agents/audit.py`)
   - Post-response similarity check (hallucination detection)
   - Flag generation with confidence scores
   - RSS monitoring (feedparser) for external events

6. **Maintenance Agent** (`src/mcp/agents/maintenance.py`)
   - Scheduled execution for periodic tasks
   - Git repo sync and diff-indexing
   - Stale artifact detection and cleanup

7. **MCP Tool Expansion**
   - `pkb_search` — multi-domain semantic search
   - `pkb_artifacts` — list/filter artifacts from chat
   - `pkb_recategorize` — recategorize from chat

8. **LibreChat Integration Enhancement**
   - YAML prompts with `{{mcp_context}}` placeholder
   - Parameter presets (factual: temp=0.1)
   - Memory integration via MCP tools

### Phase 3: GUI & Advanced Features

**Goals:** Unified dashboard for oversight and advanced workflows.

**Planned Tasks:**

1. **Streamlit Dashboard** (`src/gui/app.py`)
   - Ingestion Pane (file upload, triage preview)
   - Context Pane (search results, selections)
   - Artifact Browser (list, filter, recategorize)
   - Token/Cost Pane (metrics, usage tracking)
   - Audit Pane (flags, resolve buttons)

2. **Obsidian Integration**
   - File watcher for Obsidian vault
   - Webhook to ingestion pipeline on changes
   - Vault sync configuration

3. **Feedback Loop**
   - LLM output capture
   - Artifact extraction (code blocks, tables)
   - Auto-ingest pipeline for generated content

### Phase 4: Optimization & Documentation

**Goals:** Production-ready system.

**Planned Tasks:**

1. **Performance Optimization**
   - Redis caching for query results and embeddings
   - Benchmark with locust.io (target <200ms query latency)
   - Batch embedding generation

2. **Cron Automation**
   - Weekly maintenance agent runs
   - Daily audit checks
   - RSS feed updates

3. **Security Hardening**
   - LUKS encryption for data directory
   - Docker secrets for API keys
   - Network isolation review

4. **Documentation**
   - API documentation (FastAPI /docs)
   - User guide
   - Deployment guide

---

## 11. Success Metrics

### Functional Metrics

| Metric | Target | Current |
|--------|--------|---------|
| RAG Retrieval Accuracy | 90% | Baseline (Phase 2) |
| Parse Success Rate | 95% | ✅ 100% (tested formats) |
| Ingest Latency | <5s per file | ✅ <2s (text files) |
| Query Latency | <2s | ✅ <1s |

### Usability Metrics

| Metric | Target | Current |
|--------|--------|---------|
| File Ingestion | Single API call | ✅ Implemented |
| Domain Discovery | Auto-detect from folder | ✅ Implemented |
| AI Categorization | Configurable per-request | ✅ 3 tiers |
| Recategorization | Single API call | ✅ Implemented |
| Audit Trail | Complete event log | ✅ Redis log |

### Performance Metrics

| Metric | Target | Current |
|--------|--------|---------|
| Monthly Tokens (AI categorization) | <20k | ~400 per file |
| Memory Usage | <4GB total | Monitoring |
| Container Count | 10 | ✅ 10 |

---

## Appendix A: Port Reference

| Port | Service | Container | Purpose |
|------|---------|-----------|---------|
| 3080 | LibreChat | LibreChat | Chat UI |
| 8080 | Bifrost | bifrost | LLM Gateway |
| 8888 | MCP Server | ai-companion-mcp | Knowledge Base API |
| 8000 | RAG API | rag_api | Document Processing |
| 8001 | ChromaDB | ai-companion-chroma | Vector Store |
| 7474 | Neo4j HTTP | ai-companion-neo4j | Graph DB Browser |
| 7687 | Neo4j Bolt | ai-companion-neo4j | Graph DB Protocol |
| 6380 | Redis | ai-companion-redis | Cache + Audit |
| 5432 | PostgreSQL | vectordb | RAG Vector Store |
| 27017 | MongoDB | chat-mongodb | LibreChat Data |
| 7700 | Meilisearch | chat-meilisearch | Search Index |

## Appendix B: Credentials

| Service | Username | Password |
|---------|----------|----------|
| Neo4j | neo4j | REDACTED_PASSWORD |
| VectorDB | myuser | mypassword |
| LibreChat | (user-created) | (user-created) |

## Appendix C: Key File Paths

| Purpose | Path |
|---------|------|
| Repository Root | `~/cerid-ai/` |
| MCP Server Code | `~/cerid-ai/src/mcp/main.py` |
| Central Config | `~/cerid-ai/src/mcp/config.py` |
| Parser Registry | `~/cerid-ai/src/mcp/utils/parsers.py` |
| Metadata/AI Cat. | `~/cerid-ai/src/mcp/utils/metadata.py` |
| Chunker | `~/cerid-ai/src/mcp/utils/chunker.py` |
| Neo4j Operations | `~/cerid-ai/src/mcp/utils/graph.py` |
| Redis Audit | `~/cerid-ai/src/mcp/utils/cache.py` |
| Folder Watcher | `~/cerid-ai/src/mcp/scripts/watch_ingest.py` |
| CLI Ingest | `~/cerid-ai/src/mcp/scripts/ingest_cli.py` |
| MCP Docker Compose | `~/cerid-ai/src/mcp/docker-compose.yml` |
| LibreChat Config | `~/cerid-ai/stacks/librechat/librechat.yaml` |
| LibreChat Env | `~/cerid-ai/stacks/librechat/.env` |
| Bifrost Config | `~/cerid-ai/stacks/bifrost/data/config.json` |
| Start Script | `~/cerid-ai/scripts/start-cerid.sh` |
| Persistent Data | `~/cerid-ai/src/mcp/data/` |
| User Archive | `~/cerid-archive/` |
| Project Reference | `~/cerid-ai/docs/CERID_AI_PROJECT_REFERENCE.md` |

---

*Document updated: February 11, 2026*
*Phase 1 implementation complete and hardened. Phase 2 planning in progress.*
