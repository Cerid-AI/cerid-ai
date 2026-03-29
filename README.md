# Cerid AI

**Self-Hosted Personal AI Knowledge Companion**

A privacy-first, local-first workspace that unifies multi-domain knowledge bases (code, finance, projects, personal artifacts) into a context-aware LLM interface with RAG-powered retrieval, file ingestion, and intelligent agents.

[![Status](https://img.shields.io/badge/Status-Phase%2051%20Complete-green)]()
[![License](https://img.shields.io/badge/License-Apache%202.0-blue)](LICENSE)

---

## Overview

Cerid AI provides a unified interface for interacting with multiple LLM providers while maintaining complete control over your personal knowledge. All data stays local; only LLM API calls go external.

**Key Capabilities:**

- **React GUI** at port 3000 — streaming chat, knowledge browser, monitoring & audit dashboards
- **Multi-Provider LLM Access** via Bifrost gateway (Claude, GPT, Grok, Gemini, Llama, Mistral)
- **10 Intelligent Agents** — Query (LLM reranking), Triage (LangGraph), Rectification, Audit, Maintenance, Hallucination Detection, Memory Extraction, Curation, Self-RAG, Decomposer
- **Trading Agent Integration** — 5 MCP tools + SDK endpoints for signal enrichment, herd detection, Kelly sizing, cascade confirmation, and longshot calibration (opt-in via `CERID_TRADING_ENABLED`)
- **26 MCP Tools** for knowledge base, trading, web search, memory, and multi-modal operations via MCP protocol
- **A2A Protocol** — Agent-to-Agent communication for remote agent discovery and task invocation
- **Plugin System** — Extensible via manifest-based plugins (multi-modal KB, visual workflow builder)
- **Observability Dashboard** — 8 Redis time-series metrics, health score grading, SVG sparklines
- **Local LLM via Ollama** — Air-gapped deployment with local model routing
- **Visual Workflow Builder** — DAG-based workflow engine with drag-and-drop SVG canvas
- **Electron Desktop App** — Native macOS + Windows app with Docker lifecycle management
- **Hallucination Detection** — claim extraction + KB verification with per-message truth audit + metamorphic verification (Pro tier)
- **Graceful Degradation** — 5-tier automatic service degradation (FULL → REDUCED → MINIMAL → CACHE_ONLY → OFFLINE)
- **Exception Hierarchy** — typed `CeridError` subclasses with `@handle_errors` decorator, zero silent failures
- **Per-Stage Ollama Routing** — 8 pipeline stages independently routable to local or cloud LLM
- **Memory Extraction** — facts, decisions, preferences extracted from conversations and stored as KB artifacts
- **Smart Model Router** — complexity scoring, cost sensitivity, auto-switch recommendations
- **Smart Model Switching** — cost estimation for model switches, summarize-and-switch, "start fresh" option, color-coded context usage gauge
- **Hybrid BM25+Vector Search** with knowledge graph traversal and cross-domain connections
- **KB Context Injection** — auto-query knowledge base on chat messages, inject as system prompt context
- **File-Based Ingestion Pipeline** with structure-aware parsing (PDF tables as Markdown via pdfplumber, DOCX, XLSX, CSV, 30+ formats)
- **Multi-Domain Query Agent** with parallel retrieval, LLM reranking, and token budget enforcement
- **Local Vector & Graph Storage** (ChromaDB, Neo4j, Redis)
- **Backend Hardening** — API key auth, rate limiting, Redis query caching (5-min TTL)
- **Scheduled Maintenance** via APScheduler with proactive knowledge surfacing
- **Multi-Machine Sync** via Dropbox — JSONL export/import with auto-import on startup
- **Source Attribution** — collapsible source references with relevance scores on chat responses
- **Model Context Breaks** — provider-colored model badges, switch dividers between model changes
- **Smart Auto-RAG** — query intent classification (factual/code/analytical/creative/conversational) with automatic retrieval strategy selection
- **External Data Sources** — pluggable framework for Wikipedia, Wolfram Alpha, Exchange Rates with enable/disable management
- **Dynamic Model Registry** — OpenRouter auto-validation of 20+ models at startup, runtime model management
- **Verification Activity Console** — real-time monospace pipeline log showing each verification stage
- **GitHub Actions CI/CD** with 2,311+ tests (1805+ pytest + 506+ frontend)
- **Three-Tier AI Categorization** (manual, smart, pro) via Bifrost
- **Obsidian Vault Integration** — auto-sync vault notes into knowledge base
- **Reproducible Builds** — pip-compile lock files with hashes, pinned Docker images, Dependabot
- **Accessibility** — ARIA labels, keyboard navigation, screen reader support across 14 components
- **Privacy-First Architecture** — all data local, only LLM API calls go external

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER BROWSER                                │
│  http://localhost:3000  (React GUI — primary)                       │
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
│          /agent/audit           │    │   Grok, Llama, etc.)     │
│          /agent/maintain        │    └──────────────────────────┘
│  SSE:   /mcp/sse /mcp/messages  │
│  Tools: 26 MCP tools (pkb_*)   │
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
git clone git@github.com:Cerid-AI/cerid-ai.git
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
# Start all 4 service groups (Infrastructure → Bifrost → MCP → React GUI)
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
| MCP API | http://localhost:8888 | Knowledge base API |
| API Docs | http://localhost:8888/docs | Swagger/OpenAPI docs |
| Bifrost | http://localhost:8080 | LLM gateway dashboard |
| Neo4j Browser | http://localhost:7474 | Graph database UI |

---

## Second Machine Setup

To set up Cerid AI on an additional machine with existing encrypted secrets and Dropbox sync:

### Prerequisites

- Docker & Docker Compose v2+
- `age` encryption tool (`brew install age`)
- Dropbox installed and syncing (for knowledge base sync)
- Access to the [dotfiles repo](https://github.com/sunrunnerfire/dotfiles) (contains the age decryption key)

### Steps

```bash
# 1. Install age key from dotfiles
git clone git@github.com:sunrunnerfire/dotfiles.git ~/dotfiles
cd ~/dotfiles && bash install.sh    # installs age key to ~/.config/cerid/age-key.txt

# 2. Install age
brew install age                    # macOS (use apt on Linux)

# 3. Clone the repo
git clone git@github.com:Cerid-AI/cerid-ai.git ~/cerid-ai
cd ~/cerid-ai

# 4. Decrypt secrets
./scripts/env-unlock.sh             # .env.age → .env (requires age key from step 1)

# 5. Set up archive directory (choose one)
# Option A: Dropbox sync (recommended for multi-machine)
ln -s ~/Dropbox/cerid-archive ~/cerid-archive
# Option B: Standalone (no sync)
mkdir -p ~/cerid-archive/{coding,finance,projects,personal,general,inbox}

# 6. Start services (first run builds all images — takes a few minutes)
./scripts/start-cerid.sh

# 7. Validate
./scripts/validate-env.sh
```

On first startup with an empty Neo4j database, the MCP server auto-imports knowledge base data from `~/Dropbox/cerid-sync/` if a valid manifest exists there.

### Rebuilding After Code Changes

After pulling new code, rebuild containers that use local Dockerfiles:

```bash
./scripts/start-cerid.sh --build    # rebuilds MCP, React GUI
```

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
| POST | `/upload` | Upload file for ingestion |
| POST | `/recategorize` | Move artifact between domains |
| POST | `/agent/query` | Multi-domain query with LLM reranking |
| POST | `/agent/triage` | LangGraph-powered file triage |
| POST | `/agent/triage/batch` | Batch triage with per-file error recovery |
| POST | `/agent/rectify` | Knowledge base health checks + auto-fix |
| POST | `/agent/audit` | Activity, ingestion, cost, query reports |
| POST | `/agent/maintain` | System health, stale detection, cleanup |
| POST | `/agent/hallucination` | Check LLM response for hallucinations against KB |
| GET | `/agent/hallucination/{id}` | Retrieve stored hallucination report |
| POST | `/agent/memory/extract` | Extract and store memories from conversation |
| POST | `/agent/memory/archive` | Archive old conversation memories |
| POST | `/agent/curate` | Score artifact quality (4 dimensions) |
| POST | `/chat/stream` | Direct-to-OpenRouter chat proxy (SSE) |
| GET | `/memories` | List extracted memories |
| GET | `/taxonomy` | Hierarchical taxonomy tree (domains, sub-categories, tags) |
| GET | `/settings` | Server settings and feature flags |
| PATCH | `/settings` | Update server settings |
| POST | `/sync/export` | Export knowledge base (incremental) |
| POST | `/sync/import` | Import knowledge base (with conflict resolution) |
| GET | `/sync/status` | Compare local vs sync snapshot |
| GET | `/archive/files` | List archived files by domain |

**MCP SSE:** `/mcp/sse` (SSE stream) + `/mcp/messages` (JSON-RPC 2.0)

**MCP Tools (18):** `pkb_query`, `pkb_ingest`, `pkb_ingest_file`, `pkb_health`, `pkb_collections`, `pkb_agent_query`, `pkb_artifacts`, `pkb_recategorize`, `pkb_triage`, `pkb_rectify`, `pkb_audit`, `pkb_maintain`, `pkb_curate`, `pkb_digest`, `pkb_scheduler_status`, `pkb_check_hallucinations`, `pkb_memory_extract`, `pkb_memory_archive`

**Authentication (Phase 6D, opt-in):** Set `CERID_API_KEY` env var to enable. Requests require `X-API-Key` header. Exempt: `/health`, `/mcp/*`, `/docs`.
**Rate Limiting:** `/agent/*` (20 req/min), `/ingest*` (10 req/min), `/recategorize*` (10 req/min) per client IP.
**Query Caching:** `/query` and `/agent/query` responses cached in Redis (5-min TTL).

---

## Directory Structure

```
cerid-ai/
├── README.md
├── CLAUDE.md                          # AI developer guide
├── CONTRIBUTING.md
├── LICENSE                            # Apache-2.0
├── NOTICE
├── Makefile                           # lock-python, install-hooks, deps-check
├── pyproject.toml                     # Ruff + pytest config
├── .env.age                           # Encrypted secrets (age)
├── .env.example                       # Template
├── artifacts -> ~/Dropbox/AI-Artifacts
├── data -> src/mcp/data
│
├── .github/
│   ├── workflows/ci.yml              # 9-job CI (lint, typecheck, test, security, lock-sync, frontend, docker, frontend-marketing, frontend-desktop)
│   └── dependabot.yml                # Weekly grouped PRs (pip, npm, actions, docker)
│
├── docs/
│   ├── CERID_AI_PROJECT_REFERENCE.md  # Detailed technical reference
│   ├── DEPENDENCY_COUPLING.md         # Cross-service version constraints
│   ├── ISSUES.md                      # Issue tracker (1 open)
│   ├── OPERATIONS.md                  # API keys, secrets, rate limits, CI
│   ├── PHASE4_PLAN.md
│   └── plans/                         # Implementation plans (6 docs)
│
├── scripts/
│   ├── start-cerid.sh                 # One-command 4-step startup
│   ├── validate-env.sh                # Pre-flight validation (--quick, --fix)
│   ├── cerid-sync.py                  # Knowledge base sync CLI
│   ├── env-lock.sh                    # Encrypt .env → .env.age
│   ├── env-unlock.sh                  # Decrypt .env.age → .env
│   └── hooks/pre-commit               # Lock file sync guard
│
├── tasks/
│   └── todo.md                        # Task tracker
│
├── src/mcp/                           # MCP Server (FastAPI + Python 3.11)
│   ├── main.py                        # FastAPI entry point (114 lines — routes via routers/)
│   ├── config/                        # Configuration package (split from config.py)
│   │   ├── settings.py                # URLs, timeouts, env vars
│   │   ├── taxonomy.py                # TAXONOMY dict, domains, sub-categories
│   │   └── features.py                # Feature flags, tier constants
│   ├── deps.py                        # DB singletons, retry wrappers, auth validation
│   ├── scheduler.py                   # APScheduler maintenance engine
│   ├── tools.py                       # MCP tool registry + dispatcher (26 tools)
│   ├── sync_check.py                  # Auto-import on startup
│   ├── Dockerfile                     # python:3.11.14-slim, non-root user
│   ├── docker-compose.yml             # MCP server service
│   ├── requirements.txt               # Human-editable dependency ranges
│   ├── requirements.lock              # pip-compile with hashes (reproducible)
│   ├── requirements-dev.txt           # Test + dev dependencies
│   ├── requirements-dev.lock          # Dev lock file with hashes
│   │
│   ├── routers/                       # FastAPI routers (13 modules)
│   │   ├── health.py, query.py, ingestion.py, artifacts.py
│   │   ├── agents.py, chat.py, digest.py, mcp_sse.py
│   │   ├── taxonomy.py, settings.py, upload.py
│   │   ├── sync.py, memories.py
│   │   └── __init__.py
│   │
│   ├── services/                      # Service layer
│   │   └── ingestion.py               # Core ingest pipeline (extracted from router)
│   │
│   ├── agents/                        # 9 Agent modules
│   │   ├── query_agent.py             # Multi-domain + LLM reranking
│   │   ├── triage.py                  # LangGraph triage pipeline
│   │   ├── rectify.py                 # KB health checks + auto-fix
│   │   ├── audit.py                   # Usage analytics + conversation costs
│   │   ├── maintenance.py             # System health + cleanup
│   │   ├── hallucination.py           # Claim extraction + KB verification
│   │   ├── memory.py                  # Memory extraction + archival
│   │   ├── curator.py                 # Artifact quality scoring + synopsis
│   │   └── self_rag.py                # Self-RAG validation loop
│   │
│   ├── db/                            # Database layer
│   │   └── neo4j/                     # Neo4j CRUD package
│   │       ├── schema.py              # Constraints, indexes, seed data
│   │       ├── artifacts.py           # Artifact CRUD (6 functions)
│   │       ├── relationships.py       # Relationship discovery (5 functions)
│   │       └── taxonomy.py            # Domain/tag management (5 functions)
│   │
│   ├── parsers/                       # File parser package
│   │   ├── registry.py                # Parser registry + parse_file()
│   │   ├── pdf.py, office.py          # PDF, DOCX, XLSX parsers
│   │   ├── structured.py              # CSV, HTML, plain text parsers
│   │   ├── email.py                   # EML, MBOX parsers
│   │   └── ebook.py                   # EPUB, RTF parsers
│   │
│   ├── sync/                          # KB sync package
│   │   ├── export.py                  # Export Neo4j/Chroma/BM25/Redis
│   │   ├── import_.py                 # Import with merge/overwrite
│   │   ├── manifest.py                # Manifest read/write
│   │   ├── status.py                  # Local vs sync comparison
│   │   └── _helpers.py                # Constants + utility functions
│   │
│   ├── plugins/                       # Plugin system (manifest-based, feature tiers)
│   │   └── ocr/                       # OCR parser plugin (pro tier)
│   │
│   ├── utils/                         # Utility modules (shims + standalone)
│   │   ├── parsers.py                 # Re-export shim → parsers/
│   │   ├── graph.py                   # Re-export shim → db/neo4j/
│   │   ├── metadata.py, chunker.py, cache.py, query_cache.py
│   │   ├── bm25.py, dedup.py, encryption.py
│   │   ├── features.py, temporal.py, time.py
│   │   ├── llm_parsing.py, sync_backend.py, webhooks.py
│   │   └── __init__.py
│   │
│   ├── scripts/
│   │   ├── watch_ingest.py            # Folder watcher (host process)
│   │   ├── watch_obsidian.py          # Obsidian vault watcher
│   │   └── ingest_cli.py             # Batch CLI ingest tool
│   │
│   ├── middleware/                     # Auth + rate limiting + request tracing
│   │   ├── auth.py                    # X-API-Key validation (opt-in)
│   │   ├── rate_limit.py              # Sliding window rate limiter + headers
│   │   └── request_id.py             # X-Request-ID middleware
│   │
│   └── tests/                         # 1646+ pytest tests
│
├── src/web/                           # React GUI (Phase 6+)
│   ├── .nvmrc                         # Node version source of truth (22)
│   ├── package.json                   # React 19, Vite 7, Tailwind v4, shadcn/ui
│   ├── vite.config.ts                 # Bundle splitting, Bifrost proxy
│   ├── Dockerfile                     # Multi-stage: node:22 build → nginx:1.27
│   ├── nginx.conf                     # SPA fallback + Bifrost reverse proxy
│   └── src/
│       ├── App.tsx                    # Lazy-loaded pane routing
│       ├── lib/                       # types.ts, api.ts, model-router.ts, utils.ts
│       ├── hooks/                     # 9 hooks: use-chat, use-conversations,
│       │                              # use-kb-context, use-settings, use-theme,
│       │                              # use-model-router, use-model-switch,
│       │                              # use-smart-suggestions, use-live-metrics
│       ├── contexts/                  # KB injection context provider
│       ├── __tests__/                 # 506+ vitest tests
│       └── components/
│           ├── layout/                # Sidebar, status bar, split-pane
│           ├── chat/                  # Chat panel, input, bubbles, dashboard,
│           │                          # source attribution, model badges/dividers
│           ├── kb/                    # Knowledge pane, artifact cards, graph,
│           │                          # file upload, tag filter, domain filter
│           ├── monitoring/            # Health cards, charts, scheduler, ingestion
│           ├── audit/                 # Activity, costs, ingestion, queries,
│           │                          # hallucination panel, conversation stats
│           ├── memories/              # Memory management pane
│           ├── settings/              # Settings pane (server-synced)
│           └── ui/                    # shadcn/ui primitives (14 components)
│
└── stacks/
    ├── infrastructure/                # Neo4j, ChromaDB, Redis (pinned versions)
    │   ├── docker-compose.yml
    │   └── data/                      # Persistent DB data (.gitignored)
    └── bifrost/                       # LLM Gateway
```

---

## Configuration

### Key Files

| File | Purpose |
|------|---------|
| `.env` | All secrets (root, encrypted as `.env.age` with age) |
| `src/mcp/config/` | Domains, file extensions, AI tiers, taxonomy, DB URLs |
| `stacks/bifrost/data/config.json` | LLM routing, provider config |
| `scripts/validate-env.sh` | Pre-flight environment validation (14 checks) |
| `scripts/cerid-sync.py` | Knowledge base sync CLI (export/import/status) |
| `Makefile` | lock-python, install-hooks, deps-check targets |
| `docs/DEPENDENCY_COUPLING.md` | Cross-service version constraints |
| `docs/ISSUES.md` | Open issues and backlog (1 open) |
| `docs/OPERATIONS.md` | API keys, secrets rotation, rate limits, CI reference |

### Secrets Management

```bash
# Decrypt secrets (first time on a new machine, requires age key)
./scripts/env-unlock.sh

# Re-encrypt after editing .env
./scripts/env-lock.sh
```

The age decryption key lives outside the repo at `~/.config/cerid/age-key.txt`.

### Adding a New Domain

1. Edit `src/mcp/config/taxonomy.py` → add to `DOMAINS` list
2. Create folder: `mkdir ~/cerid-archive/<new_domain>`
3. Rebuild: `cd src/mcp && docker compose up -d --build`

### Adding a New File Type

1. Add extension to `SUPPORTED_EXTENSIONS` in `config/settings.py`
2. Register parser function in `parsers/` with `@register_parser([".ext"])`

---

## Dependency Management

### Python (pip-compile)

Dependencies are declared in `src/mcp/requirements.txt` (ranges) and locked in `requirements.lock` (exact versions with hashes). To regenerate after editing ranges:

```bash
make lock-python      # Regenerate requirements.lock
make lock-python-dev  # Regenerate requirements-dev.lock
make lock-all         # Both
```

### Pre-commit Hook

Install to block commits when lock files are out of sync:

```bash
make install-hooks
```

### Dependabot

Automated weekly PRs for Python, npm, GitHub Actions, and monthly for Docker. Configured in `.github/dependabot.yml` with grouped updates.

### Cross-Service Coupling

See `docs/DEPENDENCY_COUPLING.md` for constraints that span files (ChromaDB client/server, spaCy lib/model, Node version, Python version).

---

## Operations

### Start / Stop

```bash
# Start (4-step: Infrastructure → Bifrost → MCP → React GUI)
./scripts/start-cerid.sh

# Start with rebuild (after pulling code changes)
./scripts/start-cerid.sh --build

# Stop all stacks
cd ~/cerid-ai/src/mcp && docker compose down
cd ~/cerid-ai/stacks/bifrost && docker compose down
cd ~/cerid-ai/stacks/infrastructure && docker compose down
```

### Rebuild Individual Services

```bash
# Rebuild MCP server only
cd ~/cerid-ai/src/mcp && docker compose up -d --build mcp-server

# Rebuild React GUI only
cd ~/cerid-ai/src/web && docker compose up -d --build cerid-web
```

### View Logs

```bash
docker logs ai-companion-mcp --tail 50 -f
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

Sync knowledge bases across machines via Dropbox using JSONL exports.

**Setup (Dropbox):**
```bash
# Symlink archive folder so raw files sync across machines
ln -s ~/Dropbox/cerid-archive ~/cerid-archive

# Database snapshots sync via a separate directory
# Set in .env: CERID_SYNC_DIR=~/Dropbox/cerid-sync (default)
```

**Usage:**
```bash
python3 scripts/cerid-sync.py export          # dump to ~/Dropbox/cerid-sync/
python3 scripts/cerid-sync.py import          # merge from sync dir
python3 scripts/cerid-sync.py import --force  # overwrite local
python3 scripts/cerid-sync.py status          # compare local vs sync
```

Auto-import on startup: when MCP starts with an empty Neo4j database and a valid manifest exists in the sync directory, it automatically imports all data for zero-config bootstrap.

---

## Service Ports

| Port | Service | Container | Image | Purpose |
|------|---------|-----------|-------|---------|
| 3000 | **React GUI** | cerid-web | node:22 → nginx:1.27 | **Primary UI** |
| 8080 | Bifrost | bifrost | bifrost | LLM Gateway |
| 8888 | MCP Server | ai-companion-mcp | python:3.11.14 | Knowledge Base API |
| 8001 | ChromaDB | ai-companion-chroma | chroma:0.5.23 | Vector Store |
| 7474 | Neo4j HTTP | ai-companion-neo4j | neo4j:5.26.21 | Graph DB Browser |
| 7687 | Neo4j Bolt | ai-companion-neo4j | neo4j:5.26.21 | Graph DB Protocol |
| 6379 | Redis | ai-companion-redis | redis:7.4.8-alpine | Cache + Audit |

---

## Development Roadmap

### Phase 0: Infrastructure ✅
- [x] Docker stacks deployed on `llm-network`
- [x] Bifrost + MCP integration
- [x] MCP SSE transport — tools discoverable via MCP protocol

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

### Phase 3: Integrations ✅
- [x] Obsidian vault watcher

### Phase 4: Optimization & Polish ✅
- [x] **4A:** Modular refactor — split main.py into FastAPI routers
- [x] **4B:** Hybrid BM25+vector search, knowledge graph traversal, cross-domain connections, temporal awareness
- [x] **4C:** Scheduled maintenance (APScheduler), proactive knowledge surfacing, webhooks
- [x] **4D:** 36 tests, GitHub Actions CI, security cleanup, centralized encrypted `.env`

### Phase 5: Multi-Machine Sync ✅
- [x] Infrastructure compose (Neo4j, ChromaDB, Redis in `stacks/infrastructure/`)
- [x] Startup script, environment validation (`validate-env.sh`)
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

### Phase 8: Extensibility & Hardening ✅
- [x] **8A:** Plugin system — manifest-based loading, feature tiers (community/pro), feature flags, OCR scaffold
- [x] **8B:** Smart ingestion — new parsers (.eml, .mbox, .epub, .rtf), semantic dedup
- [x] **8C:** Hierarchical taxonomy — TAXONOMY dict, sub-categories/tags, taxonomy API
- [x] **8D:** Encryption & sync — field-level Fernet encryption, pluggable sync backends
- [x] **8E:** Infrastructure audit — 31 findings, security fixes, test DRY, N+1 fix

### Phase 9: GUI Feature Parity ✅
- [x] **9A:** Fix 3 user-reported bugs — KB error state, Neo4j health normalization, audit stats
- [x] **9B:** Wire 5 structural gaps — hallucination auto-fetch, smart suggestions, memory trigger, settings sync, live metrics
- [x] **9C:** 3 feature enhancements — file upload, sub-category/tag display, tag browsing
- [x] **9D:** Neo4j auth hardening — docker-compose env var fix, Cypher auth validation

### Phase 10: Commercial & Open-Source Readiness ✅
- [x] **10A-10E:** Production quality, UX polish, structural splits, 564 backend tests, smart model switching

### Phase 11: Knowledge Intelligence ✅
- [x] Interactive audit controls, taxonomy tree sidebar, curation agent design, operations documentation

### Phase 12: RAG & Retrieval Excellence ✅
- [x] BM25s replacement (stemming, stopwords, 500x faster), configurable retrieval weights, eval harness

### Phase 13: Conversation Intelligence ✅
- [x] Conversation-aware KB queries, auto-injection with confidence gate, context budget optimization

### Phase 14: Artifact Quality ✅
- [x] Curation agent (4-dimension scoring), quality-weighted retrieval, metadata boost, AI synopses

### Phase 15: Realtime Accuracy Watcher ✅
- [x] Streaming verification via SSE, accuracy dashboard, claim feedback, model accuracy comparison

### Phase 16: Quality, Cleanup & Polish ✅
- [x] **16A-16H:** Security hardening, dead code cleanup, code quality, dependency optimization, feature wiring, artifact preview, documentation

### Phase 17: iPad & Responsive Touch UX ✅
- [x] Touch visibility, tablet layout, bottom sheet drawer, 44px touch targets, iOS safe area insets

### Phase 18: Network Access & Demo Deployment ✅
- [x] LAN auto-IP, Caddy HTTPS gateway, Cloudflare Tunnel for demos

### Phase 19: Expert Orchestration & Validation ✅
- [x] Circuit breakers, distributed tracing, semantic chunking, eval enhancements, adaptive quality feedback

### Phase 20: Smart Tags & Artifact Quality ✅
- [x] Per-domain tag vocabulary, typeahead UI, tag quality scoring, improved synopsis generation

### Phase 21: Knowledge Sync & Multi-Computer Parity ✅
- [x] **21A-21D:** Incremental sync, sync GUI, drag-drop ingestion, storage options

### Phase 22: Deferred Items ✅
- [x] CHANGELOG.md, ENV_CONVENTIONS.md, mypy type checking, frontend tests (271), Self-RAG validation loop

### Phase 23: Production Hardening ✅
- [x] Redis/ChromaDB auth, port binding, resource limits, CI timeouts, concurrency fixes

### Phase 24: RAG Evolution — Expanded Verification ✅
- [x] Four new claim type detectors (evasion, citation, recency, ignorance), verdict inversion, context-aware streaming

### Phase 25: Smart Routing & Context-Aware Chat ✅
- [x] **25A:** Direct-to-OpenRouter chat proxy, model catalog (9 models), `cerid_meta` SSE events
- [x] **25B:** Capability-based scoring, three-way routing mode, auto-routing, capability badges
- [x] **25C:** User corrections, token-budget KB injection, semantic dedup, domain headers, inline verification

### Production Audit ✅
- [x] Shared Bifrost utility, narrowed exception handling, nginx hardening, Docker resource limits, frontend consolidation

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
