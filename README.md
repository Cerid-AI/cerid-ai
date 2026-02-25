# Cerid AI

**Self-Hosted Personal AI Knowledge Companion**

A privacy-first, local-first workspace that unifies multi-domain knowledge bases (code, finance, projects, personal artifacts) into a context-aware LLM interface with RAG-powered retrieval, file ingestion, and intelligent agents.

[![Status](https://img.shields.io/badge/Status-Phase%206%20Complete-green)]()
[![License](https://img.shields.io/badge/License-Apache%202.0-blue)](LICENSE)

---

## Overview

Cerid AI provides a unified interface for interacting with multiple LLM providers while maintaining complete control over your personal knowledge. All data stays local; only LLM API calls go external.

**Key Capabilities:**

- **React GUI** at port 3000 — streaming chat, knowledge browser, monitoring & audit dashboards
- **Multi-Provider LLM Access** via Bifrost gateway (Claude, GPT, Grok, Gemini, DeepSeek, Llama)
- **5 Intelligent Agents** — Query (LLM reranking), Triage (LangGraph), Rectification, Audit, Maintenance
- **15 MCP Tools** for knowledge base operations from LibreChat chat UI
- **Hybrid BM25+Vector Search** with knowledge graph traversal and cross-domain connections
- **KB Context Injection** — auto-query knowledge base on chat messages, inject as system prompt context
- **File-Based Ingestion Pipeline** with structure-aware parsing (PDF tables as Markdown via pdfplumber, DOCX, XLSX, CSV, 30+ formats)
- **Multi-Domain Query Agent** with parallel retrieval, LLM reranking, and token budget enforcement
- **Local Vector & Graph Storage** (ChromaDB, Neo4j, Redis)
- **Backend Hardening** — API key auth, rate limiting, Redis query caching (5-min TTL)
- **Scheduled Maintenance** via APScheduler with proactive knowledge surfacing
- **Multi-Machine Sync** via Dropbox — JSONL export/import with auto-import on startup
- **GitHub Actions CI/CD** with 59 pytest tests
- **Three-Tier AI Categorization** (manual, smart, pro) via Bifrost
- **Obsidian Vault Integration** — auto-sync vault notes into knowledge base
- **Privacy-First Architecture** — all data local, only LLM API calls go external

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER BROWSER                                │
│  http://localhost:3000  (React GUI — primary)                       │
│  http://localhost:3080  (LibreChat — legacy)                        │
│  http://localhost:8501  (Streamlit Dashboard — legacy admin)        │
└────────────────┬────────────────────────────────────────────────────┘
                 │
    ┌────────────▼────────────┐
    │   React GUI (nginx)     │
    │   Container: cerid-web  │──── /api/bifrost/ ───┐
    │   Port: 3000            │                      │
    └────────────┬────────────┘                      │
                 │ direct API calls                   │
                 ▼                                    ▼
┌─────────────────────────────────┐    ┌─────────────────────────────┐
│    AI Companion MCP Server      │    │    Bifrost Gateway          │
│  Container: ai-companion-mcp    │    │  Container: bifrost         │
│  Port: 8888                     │    │  Port: 8080                 │
│                                 │    │  Routes to OpenRouter       │
│  REST:  /health /collections    │    └──────────┬──────────────────┘
│         /query /ingest          │               │
│         /artifacts              │               ▼
│  Agents: /agent/query           │    ┌──────────────────────────┐
│          /agent/triage          │    │    OpenRouter API         │
│          /agent/rectify         │    │  (Claude, GPT, Gemini,   │
│          /agent/audit           │    │   Grok, DeepSeek, etc.)  │
│          /agent/maintain        │    └──────────────────────────┘
│  SSE:   /mcp/sse /mcp/messages  │
│  Tools: 15 MCP tools (pkb_*)   │
│  Search: Hybrid BM25 + vector   │
│  Middleware: auth, rate-limit    │
│  Scheduler: APScheduler         │
└────────────┬────────────────────┘
             │
   ┌─────────┼─────────┐
   │         │         │
   ▼         ▼         ▼
ChromaDB   Neo4j     Redis
:8001     :7474     :6379
(vectors) (graph)   (cache+audit)

Host Processes (outside Docker):
├── watch_ingest.py   → Monitors ~/cerid-archive/, POSTs to :8888
├── watch_obsidian.py → Monitors Obsidian vault, POSTs to :8888
└── ingest_cli.py     → Batch CLI tool, POSTs to :8888

Supporting Services (LibreChat stack):
├── MongoDB (27017) — LibreChat data storage
├── Meilisearch (7700) — Search indexing
├── PostgreSQL+pgvector (5432) — RAG vector store
└── RAG API (8000) — Document processing
```

---

## Quick Start

### Prerequisites

- Docker & Docker Compose v2+
- OpenRouter API key ([get one here](https://openrouter.ai/keys))
- macOS or Linux
- `age` encryption tool (`brew install age` on macOS)

### 1. Clone & Configure

```bash
git clone git@github.com:sunrunnerfire/cerid-ai.git
cd cerid-ai

# Copy and edit environment file
cp .env.example .env
# Edit .env and add your OPENROUTER_API_KEY and other secrets

# If cloning on a second machine with existing encrypted secrets:
./scripts/env-unlock.sh   # Decrypts .env.age → .env (requires age key)
```

### 2. Create Archive Folders

```bash
mkdir -p ~/cerid-archive/{coding,finance,projects,personal,general,inbox}
```

### 3. Start Services

```bash
# Start all 4 service groups (Infrastructure → Bifrost → MCP → LibreChat)
./scripts/start-cerid.sh

# Validate the environment
./scripts/validate-env.sh          # Full validation (14 checks)
./scripts/validate-env.sh --quick  # Containers only (Docker + health checks)
./scripts/validate-env.sh --fix    # Auto-start missing infrastructure
```

### 4. Verify

```bash
curl -s http://localhost:8888/health | python3 -m json.tool
```

### 5. Access

| Service | URL | Purpose |
|---------|-----|---------|
| **React GUI** | http://localhost:3000 | **Primary UI** — chat, KB, monitoring, audit |
| LibreChat | http://localhost:3080 | Legacy chat interface (MCP tools) |
| Dashboard | http://localhost:8501 | Legacy Streamlit admin UI |
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
| POST | `/agent/query` | Multi-domain query with LLM reranking |
| POST | `/agent/triage` | LangGraph-powered file triage |
| POST | `/agent/triage/batch` | Batch triage with per-file error recovery |
| POST | `/agent/rectify` | Knowledge base health checks + auto-fix |
| POST | `/agent/audit` | Activity, ingestion, cost, query reports |
| POST | `/agent/maintain` | System health, stale detection, cleanup |

**MCP SSE:** `/mcp/sse` (SSE stream) + `/mcp/messages` (JSON-RPC 2.0)

**MCP Tools (12):** `pkb_query`, `pkb_ingest`, `pkb_ingest_file`, `pkb_health`, `pkb_collections`, `pkb_agent_query`, `pkb_artifacts`, `pkb_recategorize`, `pkb_triage`, `pkb_rectify`, `pkb_audit`, `pkb_maintain`

**Authentication (Phase 6D, opt-in):** Set `CERID_API_KEY` env var to enable. Requests require `X-API-Key` header. Exempt: `/health`, `/mcp/*`, `/docs`.
**Rate Limiting:** `/agent/*` (20 req/min), `/ingest*` (10 req/min), `/recategorize*` (10 req/min) per client IP.
**Query Caching:** `/query` and `/agent/query` responses cached in Redis (5-min TTL).

---

## Directory Structure

```
cerid-ai/
├── README.md
├── CLAUDE.md
├── CONTRIBUTING.md
├── LICENSE
├── .env                              # Secrets (root, encrypted as .env.age)
├── .env.age                          # Encrypted secrets (age)
├── .env.example                      # Template
├── pyproject.toml                    # Ruff config
├── artifacts -> ~/Dropbox/AI-Artifacts
├── data -> src/mcp/data
│
├── docs/
│   ├── CERID_AI_PROJECT_REFERENCE.md
│   ├── PHASE4_PLAN.md
│   └── plans/                        # Implementation plans
│
├── scripts/
│   ├── start-cerid.sh                # One-command 4-step startup
│   ├── validate-env.sh               # Pre-flight validation (--quick, --fix)
│   ├── cerid-sync.py                 # Knowledge base sync CLI
│   ├── env-lock.sh                   # Encrypt .env → .env.age
│   └── env-unlock.sh                 # Decrypt .env.age → .env
│
├── tasks/
│   └── todo.md                       # Task tracker
│
├── src/mcp/                          # MCP Server
│   ├── main.py                       # FastAPI entry point
│   ├── config.py                     # Central configuration
│   ├── deps.py                       # Dependency injection (DB singletons)
│   ├── scheduler.py                  # APScheduler maintenance engine
│   ├── cerid_sync_lib.py             # Sync export/import library
│   ├── sync_check.py                 # Auto-import on startup
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── requirements.txt
│   │
│   ├── routers/                      # FastAPI routers (Phase 4A)
│   │   ├── health.py, query.py, ingestion.py
│   │   ├── artifacts.py, agents.py, digest.py
│   │   └── mcp_sse.py
│   │
│   ├── agents/                       # 5 Agent modules
│   │   ├── query_agent.py            # Multi-domain + LLM reranking
│   │   ├── triage.py                 # LangGraph triage
│   │   ├── rectify.py                # KB health checks
│   │   ├── audit.py                  # Usage analytics
│   │   └── maintenance.py            # System health
│   │
│   ├── utils/
│   │   ├── parsers.py, metadata.py, chunker.py
│   │   ├── graph.py, cache.py
│   │   └── bm25.py                   # BM25 keyword search
│   │
│   ├── scripts/
│   │   ├── watch_ingest.py
│   │   ├── watch_obsidian.py         # Obsidian vault watcher
│   │   └── ingest_cli.py
│   │
│   ├── middleware/                    # Auth + rate limiting (Phase 6D)
│   │   ├── auth.py                   # X-API-Key validation (opt-in)
│   │   └── rate_limit.py             # Sliding window rate limiter
│   │
│   └── tests/                        # 59 pytest tests
│
├── src/web/                          # React GUI (Phase 6)
│   ├── package.json                  # React 19, Vite 7, Tailwind v4, shadcn/ui
│   ├── vite.config.ts                # Bundle splitting, Bifrost proxy
│   ├── Dockerfile                    # Multi-stage: Node build → nginx:alpine
│   ├── nginx.conf                    # SPA fallback + Bifrost reverse proxy
│   └── src/
│       ├── App.tsx                   # Lazy-loaded pane routing
│       ├── lib/types.ts, api.ts      # Types, health client, SSE streaming
│       ├── hooks/                    # use-theme, use-chat, use-conversations,
│       │                             # use-kb-context, use-settings
│       ├── contexts/                 # KB injection context provider
│       └── components/
│           ├── layout/               # Sidebar, status bar, split-pane
│           ├── chat/                 # Chat panel, input, bubbles, dashboard
│           ├── kb/                   # Knowledge pane, artifact cards, graph
│           ├── monitoring/           # Health cards, charts, scheduler
│           └── audit/                # Activity, costs, ingestion, queries
│
├── src/gui/                          # Streamlit Dashboard (legacy)
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
│
└── stacks/
    ├── infrastructure/               # Phase 5 — Neo4j, ChromaDB, Redis
    │   ├── docker-compose.yml
    │   └── data/                     # Persistent DB data (.gitignored)
    ├── bifrost/                      # LLM Gateway
    └── librechat/                    # Chat UI
```

---

## Configuration

### Key Files

| File | Purpose |
|------|---------|
| `.env` | All secrets (root, encrypted as `.env.age` with age) |
| `src/mcp/config.py` | Domains, file extensions, AI tiers, DB URLs |
| `stacks/bifrost/data/config.json` | LLM routing, provider config |
| `stacks/librechat/librechat.yaml` | MCP servers, endpoints, model list |
| `scripts/validate-env.sh` | Pre-flight environment validation (14 checks) |
| `scripts/cerid-sync.py` | Knowledge base sync CLI (export/import/status) |

### Secrets Management

```bash
# Decrypt secrets (first time on a new machine, requires age key)
./scripts/env-unlock.sh

# Re-encrypt after editing .env
./scripts/env-lock.sh
```

The age decryption key lives outside the repo at `~/.config/cerid/age-key.txt`.

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
# Start (4-step: Infrastructure → Bifrost → MCP → LibreChat)
./scripts/start-cerid.sh

# Stop all stacks
cd ~/cerid-ai/stacks/librechat && docker compose down
cd ~/cerid-ai/src/mcp && docker compose down
cd ~/cerid-ai/stacks/bifrost && docker compose down
cd ~/cerid-ai/stacks/infrastructure && docker compose down
```

### Rebuild MCP After Code Changes

```bash
cd ~/cerid-ai/src/mcp && docker compose up -d --build
```

### View Logs

```bash
docker logs ai-companion-mcp --tail 50 -f
docker logs ai-companion-dashboard --tail 50 -f
docker logs LibreChat --tail 50 -f
docker logs bifrost --tail 50 -f
```

### Backup

The preferred backup method is the knowledge base sync CLI:

```bash
# Export local KB to sync directory (JSONL snapshots)
python3 scripts/cerid-sync.py export

# Import from sync directory (non-destructive merge)
python3 scripts/cerid-sync.py import

# Compare local vs sync snapshot
python3 scripts/cerid-sync.py status
```

Manual backup (includes all persistent data):

```bash
tar czf cerid-backup-$(date +%Y%m%d).tar.gz \
  ~/cerid-ai/src/mcp/data \
  ~/cerid-ai/stacks/infrastructure/data \
  ~/cerid-ai/.env \
  ~/cerid-ai/stacks/bifrost/data
```

### Knowledge Base Sync (Multi-Machine)

Sync knowledge bases across machines via Dropbox using JSONL exports:

```bash
python3 scripts/cerid-sync.py export          # dump to ~/Dropbox/cerid-sync/
python3 scripts/cerid-sync.py import          # merge from sync dir
python3 scripts/cerid-sync.py import --force  # overwrite local
python3 scripts/cerid-sync.py status          # compare local vs sync
```

Auto-import on startup: when MCP starts with an empty Neo4j database and a valid manifest exists in the sync directory, it automatically imports all data for zero-config bootstrap.

---

## Service Ports

| Port | Service | Container | Purpose |
|------|---------|-----------|---------|
| 3000 | **React GUI** | cerid-web | **Primary UI** |
| 3080 | LibreChat | LibreChat | Legacy Chat UI |
| 8080 | Bifrost | bifrost | LLM Gateway |
| 8888 | MCP Server | ai-companion-mcp | Knowledge Base API |
| 8501 | Dashboard | ai-companion-dashboard | Legacy Admin UI |
| 8000 | RAG API | rag_api | Document Processing |
| 8001 | ChromaDB | ai-companion-chroma | Vector Store |
| 7474 | Neo4j HTTP | ai-companion-neo4j | Graph DB Browser |
| 7687 | Neo4j Bolt | ai-companion-neo4j | Graph DB Protocol |
| 6379 | Redis | ai-companion-redis | Cache + Audit |
| 5432 | PostgreSQL | vectordb | RAG Vector Store |
| 27017 | MongoDB | chat-mongodb | LibreChat Data |
| 7700 | Meilisearch | chat-meilisearch | Search Index |

---

## Development Roadmap

### Phase 0: Infrastructure ✅
- [x] Docker stacks deployed on `llm-network`
- [x] LibreChat + Bifrost + MCP integration
- [x] MCP SSE transport — tools discoverable from LibreChat UI

### Phase 1: Core Ingestion ✅
- [x] File parsing (PDF, DOCX, XLSX, CSV, HTML, 30+ formats)
- [x] Metadata extraction, three-tier AI categorization
- [x] Token-aware chunking, SHA-256 deduplication
- [x] Folder watcher, CLI batch ingest, Recategorization

### Phase 1.5: Bulk Ingest Hardening ✅
- [x] Concurrent CLI (ThreadPoolExecutor), watcher retry queue
- [x] Atomic dedup (Neo4j UNIQUE CONSTRAINT)
- [x] PDF upgrade: pdfplumber (tables → Markdown)

### Phase 2: Agent Workflows ✅
- [x] Query Agent with LLM reranking (parallel multi-domain retrieval)
- [x] Triage Agent (LangGraph), Rectification, Audit, Maintenance agents
- [x] 15 MCP tools total

### Phase 3: Dashboard & Integrations ✅
- [x] Streamlit admin dashboard (5 panes)
- [x] Obsidian vault watcher

### Phase 4: Optimization & Polish ✅
- [x] **4A:** Modular refactor — split main.py into FastAPI routers
- [x] **4B:** Hybrid BM25+vector search, knowledge graph traversal, cross-domain connections, temporal awareness
- [x] **4C:** Scheduled maintenance (APScheduler), proactive knowledge surfacing, webhooks
- [x] **4D:** 36 tests, GitHub Actions CI, security cleanup, centralized encrypted `.env`

### Phase 5: Multi-Machine Sync ✅
- [x] Infrastructure compose (Neo4j, ChromaDB, Redis in `stacks/infrastructure/`)
- [x] 4-step startup script, environment validation (`validate-env.sh`)
- [x] Knowledge base sync CLI (`cerid-sync.py`) — JSONL export/import via Dropbox
- [x] Auto-import on startup for empty databases

### Phase 6: React GUI + Production Hardening ✅
- [x] **6A:** React 19 scaffold, streaming chat via Bifrost, sidebar nav, conversation history, Docker/nginx
- [x] **6B:** Knowledge context pane — split-pane layout, artifact cards, domain filters, graph preview, KB injection into chat
- [x] **6C:** Monitoring & audit panes — health cards, collection charts, scheduler status, cost breakdown, activity charts
- [x] **6D:** Backend hardening — API key auth, rate limiting, Redis query cache, LLM feedback loop, CORS
- [x] Chat dashboard metrics bar (model costs, token estimate, context window usage)
- [x] Bundle splitting via React.lazy + Vite manualChunks (75% main chunk reduction)

### Phase 7: Intelligence & Automation ✅
- [x] **7A:** Audit intelligence — hallucination detection agent, conversation analytics, enhanced feedback loop
- [x] **7B:** Smart orchestration — client-side model router with cost/complexity scoring, auto-switch recommendations
- [x] **7C:** Proactive knowledge — memory extraction from conversations, smart KB suggestions, memory archival

### Phase 8: Product Foundation (In Progress)
- [ ] **8A:** Open core foundation — plugin architecture, feature flags, OSS cleanup, licensing
- [ ] **8B:** Smart ingestion — OCR, new parsers, semantic dedup
- [ ] **8C:** Hierarchical taxonomy — domains, sub-categories, tags
- [ ] **8D:** Encryption & sync hardening — app-level encryption, transaction safety
- [ ] **8E:** GUI intelligence dashboard — truth panel, live metrics, settings, file upload, onboarding

---

## Host System

- **Hardware:** Mac Pro (16-Core Intel Xeon W, 160 GB RAM)
- **OS:** macOS
- **Docker:** 29.1.5 / Compose v5.0.1
- **Domains:** cerid.ai, cerid.net, getcerid.com

---

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.

---

**Owner:** Justin (@sunrunnerfire)
