# Cerid AI

**Self-Hosted Personal AI Knowledge Companion**

A privacy-first, local-first workspace that unifies multi-domain knowledge bases (code, finance, projects, personal artifacts) into a context-aware LLM interface with RAG-powered retrieval, file ingestion, and intelligent agents.

[![Status](https://img.shields.io/badge/Status-Phase%202%20Query%20Agent%20Complete-blue)]()
[![License](https://img.shields.io/badge/License-Private-red)]()

---

## Overview

Cerid AI provides a unified interface for interacting with multiple LLM providers while maintaining complete control over your personal knowledge. All data stays local; only LLM API calls go external.

**Key Capabilities:**

- **Multi-Provider LLM Access** via Bifrost gateway (Claude, GPT, Grok, Gemini, DeepSeek, Llama)
- **File-Based Ingestion Pipeline** with structure-aware document parsing (PDF tables as Markdown via pdfplumber, DOCX, XLSX, CSV, 30+ text formats), metadata extraction, and AI categorization
- **Multi-Domain Query Agent** with intelligent context assembly, deduplication, and cross-collection search
- **RAG-Powered Context Injection** for token-efficient knowledge retrieval (2-4k tokens/query)
- **Local Vector & Graph Storage** (ChromaDB, Neo4j, Redis)
- **MCP SSE Protocol** for tool integration with LibreChat UI
- **Three-Tier AI Categorization** (manual, smart, pro) for flexible ingestion workflows
- **Artifact Tracking & Recategorization** with full audit trail
- **File Deduplication** via SHA-256 content hashing
- **Privacy-First Architecture** — all data local, encrypted storage planned

---

## Architecture

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

---

## Quick Start

### Prerequisites

- Docker & Docker Compose v2+
- OpenRouter API key ([get one here](https://openrouter.ai/keys))
- macOS or Linux

### 1. Clone & Configure

```bash
git clone git@github.com:sunrunnerfire/cerid-ai.git
cd cerid-ai

# Create environment file
cp stacks/librechat/.env.example stacks/librechat/.env
# Edit .env and add your OPENROUTER_API_KEY
```

### 2. Create Archive Folders

```bash
mkdir -p ~/cerid-archive/{coding,finance,projects,personal,general,inbox}
```

### 3. Start Services

```bash
# Create shared network (first time only)
docker network create llm-network

# Start all stacks
./scripts/start-cerid.sh

# Or manually:
cd stacks/bifrost && docker compose up -d
cd ../../src/mcp && docker compose up -d
cd ../../stacks/librechat && docker compose up -d
```

### 4. Verify

```bash
curl -s http://localhost:8888/health | python3 -m json.tool
```

### 5. Access

| Service | URL | Purpose |
|---------|-----|---------|
| LibreChat | http://localhost:3080 | Main chat interface |
| MCP API | http://localhost:8888 | Knowledge base API |
| API Docs | http://localhost:8888/docs | Swagger/OpenAPI docs |
| Bifrost | http://localhost:8080 | LLM gateway dashboard |
| Neo4j Browser | http://localhost:7474 | Graph database UI |

---

## File Ingestion

Cerid AI ingests files from `~/cerid-archive/` into a searchable knowledge base with metadata extraction, optional AI categorization, and full artifact tracking.

### Archive Folder Structure

```
~/cerid-archive/
├── coding/      → domain="coding"   (auto-detected, no AI call)
├── finance/     → domain="finance"  (auto-detected)
├── projects/    → domain="projects" (auto-detected)
├── personal/    → domain="personal" (auto-detected)
├── general/     → domain="general"  (auto-detected)
└── inbox/       → AI categorization triggered (smart or pro tier)
```

### Supported File Types

**Documents:** PDF, DOCX, XLSX, CSV
**Text/Markup:** TXT, MD, RST, LOG, HTML, HTM, XML
**Code:** PY, JS, TS, JSX, TSX, Java, Go, Rust, Ruby, C/C++, C#, SQL, R, Swift, Kotlin, Shell
**Config/Data:** JSON, YAML, YML, TOML, INI, CFG

### Three Ways to Ingest

**1. Folder Watcher** (auto-ingest on file drop):
```bash
python src/mcp/scripts/watch_ingest.py [--mode smart|pro|manual]
```

**2. CLI Batch Ingest** (concurrent, process existing directories):
```bash
python src/mcp/scripts/ingest_cli.py --dir ~/cerid-archive/ [--mode smart] [--domain coding] [--workers 4] [--dry-run]
```

**3. REST API** (programmatic):
```bash
curl -X POST http://localhost:8888/ingest_file \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/archive/coding/script.py", "domain": "coding"}'
```

### AI Categorization Tiers

| Mode | Model | Cost | When Used |
|------|-------|------|-----------|
| `manual` | None | Free | File in a known domain folder |
| `smart` | Llama 3.1 8B (via Bifrost) | Free | Default for inbox/unknown |
| `pro` | Claude Sonnet (via Bifrost) | Paid | Explicit request |

AI calls are token-efficient: only the first ~1,500 characters (~400 tokens) are sent for classification.

### Recategorize Artifacts

```bash
# List artifacts in a domain
curl http://localhost:8888/artifacts?domain=coding

# Move to another domain
curl -X POST http://localhost:8888/recategorize \
  -H "Content-Type: application/json" \
  -d '{"artifact_id": "...", "new_domain": "projects"}'

# View audit trail
curl http://localhost:8888/ingest_log?limit=10
```

---

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Service health check (ChromaDB, Neo4j, Redis) |
| GET | `/collections` | List ChromaDB collections |
| GET | `/artifacts` | List ingested artifacts (filter by domain) |
| GET | `/ingest_log` | Redis audit trail |
| POST | `/query` | Query knowledge base by domain |
| POST | `/ingest` | Ingest raw text content |
| POST | `/ingest_file` | Parse + ingest file with metadata |
| POST | `/recategorize` | Move artifact between domains |

**MCP SSE:** `/mcp/sse` (SSE stream) + `/mcp/messages` (JSON-RPC 2.0)
**MCP Tools:** `pkb_query`, `pkb_ingest`, `pkb_ingest_file`, `pkb_health`, `pkb_collections`

---

## Directory Structure

```
cerid-ai/
├── README.md                            # This file
├── CLAUDE.md                            # Developer guide for AI sessions
├── .gitignore
├── artifacts -> ~/Dropbox/AI-Artifacts  # Symlink to artifacts storage
├── data -> src/mcp/data                 # Symlink to persistent data
│
├── docs/
│   └── CERID_AI_PROJECT_REFERENCE.md    # Detailed technical reference
│
├── scripts/
│   └── start-cerid.sh                   # Stack startup script
│
├── src/mcp/                             # MCP Server (main application)
│   ├── main.py                          # FastAPI server (769 lines)
│   ├── config.py                        # Central configuration
│   ├── utils/
│   │   ├── parsers.py                   # Extensible file parser registry
│   │   ├── metadata.py                  # Metadata extraction + AI categorization
│   │   ├── chunker.py                   # Token-based text chunking
│   │   ├── graph.py                     # Neo4j artifact CRUD
│   │   └── cache.py                     # Redis audit logging
│   ├── scripts/
│   │   ├── watch_ingest.py              # Watchdog folder watcher (host process)
│   │   └── ingest_cli.py                # Batch CLI ingest tool
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── requirements.txt
│
└── stacks/
    ├── bifrost/                          # LLM Gateway
    └── librechat/                        # Chat UI + RAG
```

---

## Configuration

### Key Files

| File | Purpose |
|------|---------|
| `src/mcp/config.py` | Domains, file extensions, AI tiers, DB URLs |
| `stacks/librechat/.env` | API keys (OPENROUTER_API_KEY) |
| `stacks/bifrost/data/config.json` | LLM routing, provider config |
| `stacks/librechat/librechat.yaml` | MCP servers, endpoints, model list |

### Adding a New Domain

1. Edit `src/mcp/config.py` → add to `DOMAINS` list
2. Create folder: `mkdir ~/cerid-archive/<new_domain>`
3. Rebuild: `cd src/mcp && docker compose up -d --build`

### Adding a New File Type

1. Add extension to `SUPPORTED_EXTENSIONS` in `config.py`
2. Register parser function in `utils/parsers.py` with `@register_parser([".ext"])`

---

## Operations

### Start / Stop

```bash
# Start
./scripts/start-cerid.sh

# Stop
cd ~/cerid-ai/stacks/librechat && docker compose down
cd ~/cerid-ai/src/mcp && docker compose down
cd ~/cerid-ai/stacks/bifrost && docker compose down
```

### Rebuild MCP After Code Changes

```bash
cd ~/cerid-ai/src/mcp && docker compose up -d --build
```

### View Logs

```bash
docker logs ai-companion-mcp --tail 50 -f
docker logs LibreChat --tail 50 -f
docker logs bifrost --tail 50 -f
```

### Backup

```bash
tar czf cerid-backup-$(date +%Y%m%d).tar.gz \
  ~/cerid-ai/src/mcp/data \
  ~/cerid-ai/stacks/librechat/.env \
  ~/cerid-ai/stacks/bifrost/data
```

---

## Service Ports

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

---

## Development Roadmap

### Phase 0: Infrastructure ✅

- [x] 10 Docker containers deployed and healthy on `llm-network`
- [x] LibreChat + Bifrost + MCP integration working
- [x] MCP SSE transport — tools discoverable from LibreChat UI
- [x] ChromaDB, Neo4j, Redis connected and operational

### Phase 1: Core Ingestion ✅

- [x] File parsing for PDF, DOCX, XLSX, CSV, HTML, and 30+ text/code formats
- [x] Extensible parser registry with decorator pattern
- [x] HTML tag stripping (script/style/noscript excluded)
- [x] DOCX table extraction alongside paragraph text
- [x] Binary file detection (null byte check)
- [x] Metadata extraction (spaCy NER keywords, token counting, summaries)
- [x] Three-tier AI categorization (manual/smart/pro) via Bifrost
- [x] Token-aware chunking (512 tokens, 20% overlap, batch ChromaDB writes)
- [x] SHA-256 content deduplication via Neo4j
- [x] Neo4j artifact tracking with content_hash index
- [x] Redis audit logging for all ingest/recategorize events
- [x] Recategorization workflow (cross-collection chunk migration)
- [x] REST endpoints + MCP tool for file ingestion
- [x] Folder watcher with file stability detection
- [x] CLI batch ingest with dry-run and domain override
- [x] HTTPException error handling across all endpoints

### Phase 1.5: Bulk Ingest Hardening ✅

- [x] Structure-aware PDF parsing via pdfplumber (tables → Markdown, bbox exclusion)
- [x] Concurrent CLI ingestion (ThreadPoolExecutor, --workers flag)
- [x] Watcher retry queue (30s) and extended stability window (30s)
- [x] Atomic deduplication via Neo4j UNIQUE CONSTRAINT on content_hash
- [x] Query: real relevance scores, source attribution, 14k-char token budget

### Phase 2: Enhanced Search & Agent Workflows (Next)

- [ ] Multi-domain search across collections
- [ ] Query Agent (LangGraph) with cross-domain context assembly
- [ ] Triage Agent wrapping ingestion pipeline
- [ ] Rectification Agent for conflict detection
- [ ] Audit Agent for hallucination checks
- [ ] MCP tool expansion (pkb_search, pkb_artifacts, pkb_recategorize)

### Phase 3: GUI & Advanced Features

- [ ] Streamlit dashboard (ingestion pane, artifact browser, audit pane)
- [ ] Obsidian vault integration
- [ ] Feedback loop for LLM output capture

### Phase 4: Optimization & Production

- [ ] Redis query caching
- [ ] LUKS encryption for data at rest
- [ ] Performance benchmarking
- [ ] Cron-based maintenance automation

---

## Host System

- **Hardware:** Mac Pro (16-Core Intel Xeon W, 160 GB RAM)
- **OS:** macOS
- **Docker:** 29.1.5 / Compose v5.0.1
- **Domains:** cerid.ai, cerid.net, getcerid.com

---

## License

Private repository. All rights reserved.

---

**Owner:** Justin (@sunrunnerfire)
