# Cerid AI

**Self-Hosted Personal AI Knowledge Companion**

A privacy-first, local-first workspace that unifies multi-domain knowledge bases (code, finance, projects, personal artifacts) into a context-aware LLM interface with RAG-powered retrieval, file ingestion, and intelligent agents.

[![Status](https://img.shields.io/badge/Status-Active-green)]()
[![License](https://img.shields.io/badge/License-Apache%202.0-blue)](LICENSE)

---

## 5-minute quickstart

If you have Docker, an OpenRouter API key, and macOS or Linux, this gets you a running chat in under 5 minutes.

```bash
# 1. Clone
git clone git@github.com:Cerid-AI/cerid-ai.git && cd cerid-ai

# 2. Configure (add your OPENROUTER_API_KEY to .env)
cp .env.example .env
$EDITOR .env          # only OPENROUTER_API_KEY is required

# 3. Create archive folders (shell-portable helper — replaces brace
#    expansion which breaks on dash/POSIX sh)
./scripts/setup-archive.sh

# 4. Start the stack
./scripts/start-cerid.sh
```

**It's working when:** you can open <http://localhost:3000> and the header reads "Cerid AI" with green service dots (`chromadb`, `redis`, `neo4j` → `connected`) in the bottom-left status bar.

**First query:** click **New Conversation**, then one of the suggested prompt cards. You'll see the AI respond and the verification pipeline flag any factual claims for cross-checking against your knowledge base.

**Need help?**
- `./scripts/validate-env.sh` — 14-check pre-flight that tells you what's missing or misconfigured
- `curl http://localhost:8888/health` — JSON status of every backend service
- `docker logs ai-companion-mcp --tail 50 -f` — tail the backend logs

**Cost context:** the default model is the cheapest capable one from whichever provider you configured (e.g. `gpt-4o-mini` for OpenAI, ~$0.15 per 1M input tokens). $5 in OpenRouter credits typically gets a few hundred chat messages before you need to top up.

**Stop the stack:** `docker compose down` from the repo root.

For everything below — detailed prerequisites, architecture, API reference, second-machine setup, sync, backup — see the rest of this README or jump to the section you need.

---

## Overview

Cerid AI provides a unified interface for interacting with multiple LLM providers while maintaining complete control over your personal knowledge. All data stays local; only LLM API calls go external.

**Key Capabilities:**

- **React GUI** at port 3000 — streaming chat, knowledge browser, monitoring & audit dashboards
- **Multi-Provider LLM Access** via OpenRouter (Claude, GPT, Grok, Gemini, DeepSeek, Llama)
- **9 Intelligent Agents** — Query (LLM reranking), Triage (LangGraph), Rectification, Audit, Maintenance, Hallucination Detection, Memory Extraction, Curation, Self-RAG
- **Trading Agent Integration** — 5 MCP tools + SDK endpoints for signal enrichment, herd detection, Kelly sizing, cascade confirmation, and longshot calibration (opt-in via `CERID_TRADING_ENABLED`)
- **26 MCP Tools** for knowledge base, trading, web search, memory, and multi-modal operations via MCP protocol
- **A2A Protocol** — Agent-to-Agent communication for remote agent discovery and task invocation
- **Plugin System** — Extensible via manifest-based plugins (multi-modal KB, visual workflow builder)
- **Observability Dashboard** — 8 Redis time-series metrics, health score grading, SVG sparklines
- **Local LLM via Ollama** — Air-gapped deployment with local model routing
- **Visual Workflow Builder** — DAG-based workflow engine with drag-and-drop SVG canvas
- **Electron Desktop App** — Native macOS + Windows app with Docker lifecycle management
- **Hallucination Detection** — claim extraction + KB verification with per-message truth audit
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
- **GitHub Actions CI/CD** with 3,100+ tests (2,413 pytest + 719 frontend)
- **Three-Tier AI Categorization** (manual, smart, pro) via OpenRouter
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
    │   Container: cerid-web  │
    │   Port: 3000            │
    └────────────┬────────────┘
                 │ direct API calls
                 ▼
┌─────────────────────────────────┐
│    AI Companion MCP Server      │
│  Container: ai-companion-mcp    │
│  Port: 8888                     │
│                                 │
│  REST:  /health /collections    │
│         /query /ingest          │
│         /artifacts              │
│  Agents: /agent/query           │
│          /agent/triage          │
│          /agent/rectify         │
│          /agent/audit           │
│          /agent/maintain        │
│  SSE:   /mcp/sse /mcp/messages  │
│  Tools: 26 MCP tools (pkb_*)   │
│  Search: Hybrid BM25 + vector   │
│  Middleware: auth, rate-limit    │
│  Scheduler: APScheduler         │
└────────────┬────────────────────┘
             │              ┌──────────────────────────┐
             │              │    OpenRouter API         │
   ┌─────────┼─────────┐    │  (Claude, GPT, Gemini,   │
   │         │         │    │   Grok, DeepSeek, etc.)  │
   ▼         ▼         ▼    └──────────────────────────┘
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

### 1. Clone & Configure

```bash
git clone git@github.com:Cerid-AI/cerid-ai.git
cd cerid-ai

# Copy and edit environment file
cp .env.example .env
# Edit .env and add your OPENROUTER_API_KEY and other secrets

# Optional: if you have an encrypted `.env.age` from another machine,
# decrypt it with `./scripts/env-unlock.sh`. Otherwise, edit `.env` directly.
```

### 2. Create Archive Folders

```bash
./scripts/setup-archive.sh        # shell-portable; creates ~/cerid-archive/{coding,finance,projects,personal,general,inbox}
```

### 3. Start Services

```bash
# Start all services (Infrastructure → MCP → React GUI)
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
| Neo4j Browser | http://localhost:7474 | Graph database UI |

---

## Second Machine Setup

To set up Cerid AI on an additional machine with existing encrypted secrets and optional Dropbox sync:

### Prerequisites

- Docker & Docker Compose v2+
- `age` encryption tool (`brew install age` on macOS, `apt install age` on Linux)
- Your `age` decryption key at `~/.config/cerid/age-key.txt`
- Dropbox installed and syncing (optional, for knowledge base sync)

### Steps

```bash
# 1. Install age
brew install age                    # macOS (use apt on Linux)

# 2. Clone the repo
git clone git@github.com:Cerid-AI/cerid-ai.git ~/cerid-ai
cd ~/cerid-ai

# 3. Place your age key
mkdir -p ~/.config/cerid
# Copy your existing age-key.txt to ~/.config/cerid/age-key.txt

# 4. Decrypt secrets
./scripts/env-unlock.sh             # .env.age → .env

# 5. Set up archive directory (choose one)
# Option A: Dropbox sync (recommended for multi-machine)
ln -s ~/Dropbox/cerid-archive ~/cerid-archive
# Option B: Standalone (no sync)
./scripts/setup-archive.sh        # shell-portable; creates ~/cerid-archive/{coding,finance,projects,personal,general,inbox}

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
| `smart` | Llama 3.3 70B Instruct (via OpenRouter) | Free | Default for inbox/unknown |
| `pro` | Claude Sonnet 4 (via OpenRouter) | Paid | Explicit request |

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

**MCP Tools (21 core + 5 trading):** `pkb_query`, `pkb_ingest`, `pkb_ingest_file`, `pkb_health`, `pkb_collections`, `pkb_agent_query`, `pkb_artifacts`, `pkb_recategorize`, `pkb_triage`, `pkb_rectify`, `pkb_audit`, `pkb_maintain`, `pkb_curate`, `pkb_digest`, `pkb_scheduler_status`, `pkb_check_hallucinations`, `pkb_memory_extract`, `pkb_memory_archive`, `pkb_memory_recall`, `pkb_web_search`, `pkb_ingest_multimodal` + 5 trading tools (opt-in)

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
├── src/mcp/                           # MCP Server (FastAPI + Python 3.11)
│   ├── main.py                        # FastAPI entry point (114 lines — routes via routers/)
│   ├── config/                        # Configuration package (split from config.py)
│   │   ├── settings.py                # URLs, timeouts, env vars
│   │   ├── taxonomy.py                # TAXONOMY dict, domains, sub-categories
│   │   └── features.py                # Feature flags, tier constants
│   ├── deps.py                        # DB singletons, retry wrappers, auth validation
│   ├── scheduler.py                   # APScheduler maintenance engine
│   ├── tools.py                       # MCP tool registry + dispatcher (18 tools)
│   ├── sync_check.py                  # Auto-import on startup
│   ├── Dockerfile                     # python:3.11.14-slim, non-root user
│   ├── docker-compose.yml             # MCP server service
│   ├── requirements.txt               # Human-editable dependency ranges
│   ├── requirements.lock              # pip-compile with hashes (reproducible)
│   ├── requirements-dev.txt           # Test dependencies
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
│   └── tests/                         # 1376+ pytest tests (27+ test files)
│
├── src/web/                           # React GUI (Phase 6+)
│   ├── .nvmrc                         # Node version source of truth (22)
│   ├── package.json                   # React 19, Vite 7, Tailwind v4, shadcn/ui
│   ├── vite.config.ts                 # Bundle splitting, dev proxy to MCP
│   ├── Dockerfile                     # Multi-stage: node:22 build → nginx:1.27
│   ├── nginx.conf                     # SPA fallback + MCP reverse proxy
│   └── src/
│       ├── App.tsx                    # Lazy-loaded pane routing
│       ├── lib/                       # types.ts, api.ts, model-router.ts, utils.ts
│       ├── hooks/                     # 9 hooks: use-chat, use-conversations,
│       │                              # use-kb-context, use-settings, use-theme,
│       │                              # use-model-router, use-model-switch,
│       │                              # use-smart-suggestions, use-live-metrics
│       ├── contexts/                  # KB injection context provider
│       ├── __tests__/                 # 545+ vitest tests (25+ test files)
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
    └── infrastructure/                # Neo4j, ChromaDB, Redis (pinned versions)
        ├── docker-compose.yml
        └── data/                      # Persistent DB data (.gitignored)
```

---

## Configuration

### Key Files

| File | Purpose |
|------|---------|
| `.env` | All secrets (root, encrypted as `.env.age` with age) |
| `src/mcp/config/` | Domains, file extensions, AI tiers, taxonomy, DB URLs |
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
# Start (3-step: Infrastructure → MCP → React GUI)
./scripts/start-cerid.sh

# Start with rebuild (after pulling code changes)
./scripts/start-cerid.sh --build

# Stop all stacks
cd ~/cerid-ai/src/mcp && docker compose down
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
  ~/cerid-ai/.env
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
| 8888 | MCP Server | ai-companion-mcp | python:3.11.14 | Knowledge Base API |
| 8001 | ChromaDB | ai-companion-chroma | chroma:0.5.23 | Vector Store |
| 7474 | Neo4j HTTP | ai-companion-neo4j | neo4j:5.26.21 | Graph DB Browser |
| 7687 | Neo4j Bolt | ai-companion-neo4j | neo4j:5.26.21 | Graph DB Protocol |
| 6379 | Redis | ai-companion-redis | redis:7.4.8-alpine | Cache + Audit |

---

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.

| Directory | License | Description |
|-----------|---------|-------------|
| `core/` | [Apache-2.0](src/mcp/core/LICENSE) | Orchestration engine, agents, retrieval, verification |
| `app/` | [Apache-2.0](LICENSE) | Application layer, routers, parsers, GUI |
| `plugins/` | [BSL-1.1](plugins/LICENSE) | Pro-tier extensions (converts to Apache-2.0 after 3 years) |
