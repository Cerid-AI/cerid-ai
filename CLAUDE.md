# CLAUDE.md - Cerid AI

> **Extends:** `~/Develop/CLAUDE.md` — all global workflow orchestration, core principles, and task
> management rules apply here. This file adds only project-specific context.

---

## Project Overview

Cerid AI is a self-hosted, privacy-first Personal AI Knowledge Companion. It unifies multi-domain knowledge bases (code, finance, projects, artifacts) into a context-aware LLM interface with RAG-powered retrieval and intelligent agents. All data stays local; only LLM API calls go external.

**Status:** Phase 16A–F complete. Phases 10A–15H shipped + Verification UX Overhaul + Phase 16 quality/cleanup pass (security hardening, dead code removal, code quality, dependency optimization, backend feature wiring). 811+ Python tests, 111+ frontend tests. 8 agents operational (query, triage, rectify, audit, maintenance, hallucination, memory, curator); 17 MCP tools; hybrid BM25s+vector search with configurable weights; quality-weighted retrieval; conversation-aware KB queries with confidence gating; streaming verification with accuracy analytics and ignorance-admission detection; scheduled maintenance; CI/CD pipeline with 6-job pipeline (lint, test, security, lock-sync, frontend, docker). Multi-machine sync via Dropbox. React GUI (port 3000) with streaming chat, source attribution, model switch dialog with cost estimation, summarize-and-switch, color-coded context gauge, KB context pane with taxonomy tree and CRUD, monitoring with KB operations, audit dashboards with accuracy analytics, hallucination panel with claim feedback and source links, smart model router, KB suggestions, file upload, tag browsing, memories pane with archive, settings pane with collapsible sections and tooltips, conversation analytics, and verification status bar with session metrics. Backend hardened with API key auth, rate limiting (XFF-aware with IETF headers), Redis query caching, conversation analytics, request ID tracing, and optional field-level encryption. Docker images pinned (including Bifrost, LibreChat, RAG API SHA digests), Python deps locked with pip-compile hashes, Dependabot configured. Plugin system with feature tiers (community/pro). Hierarchical taxonomy with sub-categories and tags. Codebase structurally split: `db/neo4j/` package, `sync/` package, `parsers/` package, `config/` package, `services/` layer, `eval/` harness.

## Architecture

Microservices architecture with Docker Compose orchestration on a shared `llm-network` bridge network. Services communicate by container name.

### Services

| Service | Port | Stack Path | Tech |
|---------|------|------------|------|
| LibreChat (UI) | 3080 | `stacks/librechat/` | Node.js/React |
| MCP Server (API) | 8888 | `src/mcp/` | FastAPI / Python 3.11 |
| Bifrost (LLM Gateway) | 8080 | `stacks/bifrost/` | Semantic intent routing |
| ChromaDB (Vectors) | 8001 | `stacks/infrastructure/` | Vector DB |
| Neo4j (Graph) | 7474/7687 | `stacks/infrastructure/` | Graph DB |
| Redis (Cache) | 6379 | `stacks/infrastructure/` | Cache + audit log |
| MongoDB (Chat) | 27017 | via `stacks/librechat/` | LibreChat persistence |
| PostgreSQL+pgvector | 5432 | via `stacks/librechat/` | RAG vector storage |
| Meilisearch | 7700 | via `stacks/librechat/` | Full-text search |
| RAG API | 8000 | via `stacks/librechat/` | Document processing |
| Dashboard (legacy) | 8501 | `src/gui/` | Streamlit admin UI |
| React GUI | 3000 | `src/web/` | React 19 + Vite + nginx |

### Key Data Flow

```
User → React GUI (3000) → Bifrost (8080) → OpenRouter → LLM Provider
                        → MCP Server (8888) → ChromaDB/Neo4j (RAG context)

Legacy: User → LibreChat (3080) → Bifrost (8080) → OpenRouter → LLM Provider

File Ingestion:
~/cerid-archive/ → Watcher → POST /ingest_file → Parse → Dedup → Chunk → ChromaDB + Neo4j + Redis
```

React GUI talks to Bifrost via nginx proxy (`/api/bifrost/`) and to MCP directly (CORS `*`). Bifrost classifies intent (coding/research/simple/general) and routes to the appropriate model.

## Directory Structure

```
├── README.md                         # Project overview and quick start
├── CLAUDE.md                         # This file — developer guide for AI sessions
├── .env.age                          # Encrypted secrets (age)
├── .env.example                      # Template for .env
├── Makefile                          # lock-python, install-hooks, deps-check
├── scripts/
│   ├── start-cerid.sh                # One-command 4-step stack startup
│   ├── validate-env.sh               # Pre-flight environment validation
│   ├── cerid-sync.py                 # Knowledge base sync CLI (export/import/status)
│   ├── env-lock.sh                   # Encrypt .env → .env.age
│   ├── env-unlock.sh                 # Decrypt .env.age → .env
│   └── hooks/pre-commit              # Lock file sync check (git pre-commit hook)
├── docs/CERID_AI_PROJECT_REFERENCE.md # Detailed technical reference
├── src/mcp/
│   ├── main.py                       # FastAPI MCP server entry point
│   ├── config/                       # Configuration package (split from config.py in 10C)
│   │   ├── settings.py               # Domains, tiers, URLs, sync, model IDs
│   │   ├── taxonomy.py               # TAXONOMY dict with sub-categories/tags
│   │   └── features.py               # Feature flags and tier gating
│   ├── db/neo4j/                     # Neo4j data layer (split from utils/graph.py in 10C)
│   │   ├── schema.py                 # init_schema (constraints, indexes)
│   │   ├── artifacts.py              # Artifact CRUD (6 functions)
│   │   ├── relationships.py          # Graph relationships + discovery
│   │   └── taxonomy.py               # Taxonomy + sub-category CRUD
│   ├── sync/                         # KB sync package (split from cerid_sync_lib.py in 10C)
│   │   ├── export.py                 # JSONL export functions
│   │   ├── import_.py                # JSONL import + ChromaDB helpers
│   │   ├── manifest.py               # Sync manifest read/write
│   │   ├── status.py                 # Compare local vs sync
│   │   └── _helpers.py               # Constants + utilities
│   ├── parsers/                      # File parser package (split from utils/parsers.py in 10C)
│   │   ├── registry.py               # Parser registry + parse_file orchestrator
│   │   ├── pdf.py, office.py         # PDF (pdfplumber), DOCX/XLSX
│   │   ├── structured.py             # CSV/TSV/HTML
│   │   ├── email.py, ebook.py        # EML/MBOX, EPUB/RTF
│   │   └── _utils.py                 # Shared parser utilities
│   ├── services/                     # Service layer (extracted in 10C)
│   │   └── ingestion.py              # ingest_content(), ingest_file(), dedup
│   ├── eval/                         # Retrieval evaluation harness (Phase 12)
│   │   ├── harness.py                # NDCG, MRR, Precision@K, Recall@K
│   │   ├── metrics.py                # Metric computation functions
│   │   └── benchmark.jsonl           # Eval benchmark data
│   ├── tools.py                      # MCP tool registry + dispatcher (17 tools)
│   ├── sync_check.py                 # Auto-import on startup if DB empty
│   ├── scheduler.py                  # APScheduler maintenance engine
│   ├── deps.py                       # Dependency injection (DB singletons, per-DB locks, retry)
│   ├── routers/                      # FastAPI routers
│   │   ├── health.py, query.py, ingestion.py, artifacts.py
│   │   ├── agents.py, digest.py, mcp_sse.py, taxonomy.py
│   │   ├── settings.py, upload.py, memories.py
│   │   └── __init__.py
│   ├── plugins/                      # Plugin system (Phase 8A)
│   │   └── ocr/                      # OCR parser plugin (pro tier, requires docling)
│   ├── middleware/                    # Request middleware
│   │   ├── auth.py                   # API key authentication (opt-in via CERID_API_KEY)
│   │   ├── rate_limit.py             # Sliding window rate limiting (XFF-aware, IETF headers)
│   │   └── request_id.py             # Request ID tracing (X-Request-ID header)
│   ├── utils/                        # Utilities (some are now re-export shims)
│   │   ├── parsers.py                # Re-export shim → parsers/ package
│   │   ├── graph.py                  # Re-export shim → db/neo4j/ package
│   │   ├── bm25.py                   # BM25s keyword search (stemming, stopwords)
│   │   ├── cache.py                  # Redis audit logging + conversation metrics
│   │   ├── query_cache.py            # Redis query cache (5-min TTL)
│   │   ├── embeddings.py             # Embedding model config scaffold
│   │   └── (time, metadata, chunker, dedup, encryption, sync_backend, features, temporal, llm_parsing)
│   ├── scripts/
│   │   ├── watch_ingest.py           # Watchdog folder watcher (host process)
│   │   ├── watch_obsidian.py         # Obsidian vault watcher (host process)
│   │   └── ingest_cli.py             # Batch CLI ingest tool
│   ├── agents/
│   │   ├── query_agent.py            # Multi-domain query with quality-weighted reranking
│   │   ├── curator.py                # Artifact quality scoring (Phase 14)
│   │   ├── triage.py                 # LangGraph triage agent
│   │   ├── rectify.py                # Knowledge base health checks
│   │   ├── audit.py                  # Operation tracking, cost estimation, verification analytics
│   │   ├── maintenance.py            # System health, stale cleanup
│   │   ├── hallucination.py          # Hallucination detection + streaming verification
│   │   └── memory.py                 # Memory extraction from conversations
│   ├── Dockerfile
│   ├── docker-compose.yml            # MCP server + Dashboard + React GUI
│   ├── requirements.txt              # Python deps (human-editable ranges)
│   ├── requirements.lock             # Pinned deps with hashes (generated by pip-compile)
│   └── requirements-dev.txt          # Test/dev deps (pytest, httpx, etc.)
├── src/gui/
│   ├── app.py                        # Streamlit dashboard (5 panes) — legacy
│   ├── Dockerfile
│   └── requirements.txt
├── src/web/                            # React GUI (Phase 6)
│   ├── package.json                   # React 19, Vite 7, Tailwind v4, shadcn/ui
│   ├── .nvmrc                         # Node version source of truth (22)
│   ├── vite.config.ts                 # Tailwind plugin, @/ alias, Bifrost proxy, manualChunks
│   ├── Dockerfile                     # Multi-stage: Node build → nginx:alpine (pinned)
│   ├── nginx.conf                     # SPA fallback + Bifrost reverse proxy
│   └── src/
│       ├── __tests__/                 # 111+ vitest tests (8+ test files)
│       ├── lib/types.ts, api.ts, model-router.ts, syntax-highlighter.ts  # Types, API clients, model engine
│       ├── hooks/                     # use-theme, use-chat, use-conversations, use-kb-context, use-settings, use-model-router, use-smart-suggestions, use-live-metrics, use-model-switch, use-verification-stream
│       ├── contexts/                  # SettingsContext, KBInjectionContext, ConversationsContext
│       └── components/
│           ├── layout/                # Sidebar nav, status bar (enhanced tooltips)
│           ├── chat/                  # Chat panel, message list, chat-dashboard, model router, smart suggestions, source attribution, model switch dialog, verification status bar
│           ├── kb/                    # KB context panel, artifact cards (quality badges), domain filter, graph preview, taxonomy tree, tag filter
│           ├── monitoring/            # Health cards, collection chart, scheduler status, KB operations
│           ├── audit/                 # Activity chart, ingestion timeline, cost breakdown, query stats, hallucination panel (claim feedback), conversation stats, accuracy dashboard, model accuracy chart
│           ├── memories/              # Memories browsing pane
│           ├── settings/              # Server-synced settings (collapsible sections, info tooltips)
│           └── ui/                    # shadcn/ui primitives (button, card, badge, etc.)
├── stacks/
│   ├── infrastructure/               # Neo4j, ChromaDB, Redis (Phase 5)
│   │   ├── docker-compose.yml
│   │   └── data/                     # Persistent DB data (.gitignored)
│   ├── bifrost/                      # LLM Gateway
│   └── librechat/                    # Chat UI
├── artifacts/ → ~/Dropbox/AI-Artifacts (symlink)
└── data/ → src/mcp/data (symlink)
```

## Development

### Secrets Management

Single `.env` file at repo root, encrypted with `age`. The decryption key lives outside the repo at `~/.config/cerid/age-key.txt`.

```bash
# Decrypt secrets (first time on a new machine)
./scripts/env-unlock.sh

# Re-encrypt after editing .env
./scripts/env-lock.sh
```

**Second-machine bootstrap (complete sequence):**
```bash
# 1. Install dotfiles (age key + global CLAUDE.md)
git clone git@github.com:sunrunnerfire/dotfiles.git ~/dotfiles
cd ~/dotfiles && bash install.sh

# 2. Install age encryption tool
brew install age                          # macOS; use apt on Linux

# 3. Clone cerid-ai
git clone git@github.com:sunrunnerfire/cerid-ai.git ~/cerid-ai
cd ~/cerid-ai

# 4. Decrypt secrets (.env.age → .env)
./scripts/env-unlock.sh

# 5. Set up archive directory
# Option A: Dropbox sync (recommended)
ln -s ~/Dropbox/cerid-archive ~/cerid-archive
# Option B: Standalone
# mkdir -p ~/cerid-archive/{coding,finance,projects,personal,general,inbox}

# 6. Start stack (first run builds all images — ~5 min for MCP due to Python deps + spaCy model)
./scripts/start-cerid.sh                  # auto-imports KB from Dropbox sync if Neo4j is empty

# 7. Validate
./scripts/validate-env.sh
```

**After pulling code changes:**
```bash
./scripts/start-cerid.sh --build          # rebuilds MCP, Dashboard, React GUI images
```

### Starting the Stack

```bash
./scripts/start-cerid.sh            # start all 4 service groups
./scripts/start-cerid.sh --build    # rebuild images after code changes
```

Startup order: `[1/4]` Infrastructure (Neo4j, ChromaDB, Redis) → `[2/4]` Bifrost → `[3/4]` MCP + Dashboard + React GUI → `[4/4]` LibreChat.

### Environment Validation

Run before starting work to verify the stack is ready:
```bash
./scripts/validate-env.sh          # full validation (14 checks)
./scripts/validate-env.sh --quick  # containers only (Docker + health checks)
./scripts/validate-env.sh --fix    # auto-start missing infrastructure
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
- `POST /agent/audit` — Audit reports (activity, ingestion stats, costs, query patterns, conversations)
- `POST /agent/maintain` — Maintenance routines (health, stale detection, collection analysis, orphan cleanup)

**Phase 7 endpoints:**
- `POST /agent/hallucination` — Check LLM response for hallucinations against KB
- `GET /agent/hallucination/{conversation_id}` — Retrieve stored hallucination report
- `POST /agent/memory/extract` — Extract and store memories from conversation
- `POST /agent/memory/archive` — Archive old conversation memories

**Settings & memories:**
- `GET /settings` — Server configuration and feature flags
- `PATCH /settings` — Partial settings update
- `GET /memories` — List/filter memories (type, conversation_id, limit, offset)
- `PATCH /memories/{id}` — Update memory summary
- `DELETE /memories/{id}` — Delete a memory

**File upload:**
- `POST /upload` — Upload file with optional domain, sub_category, tags, categorize_mode (50MB max)
- `GET /upload/supported` — List supported file extensions

**MCP protocol:**
- `GET /mcp/sse` — SSE stream (MCP protocol, JSON-RPC 2.0)
- `POST /mcp/messages?sessionId=X` — JSON-RPC handler

MCP tools (15 total):
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
- `pkb_audit` — Audit reports (activity, ingestion, costs, queries, conversations)
- `pkb_maintain` — Maintenance routines (health, stale, collections, orphans)
- `pkb_check_hallucinations` — Verify LLM claims against KB (Phase 7A)
- `pkb_memory_extract` — Extract memories from conversations (Phase 7C)
- `pkb_memory_archive` — Archive old conversation memories (Phase 7C)

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
├── conversations/ → domain="conversations" (feedback loop output)
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

- `.env` (repo root) — All secrets. Encrypted as `.env.age`. Never committed in plaintext.
- `src/mcp/config.py` — Domains, extensions, categorization tiers, DB URLs
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

# With API key auth enabled (set CERID_API_KEY env var):
curl http://localhost:8888/artifacts \
  -H "X-API-Key: $CERID_API_KEY"
# Exempt from auth: /health, /api/v1/health, /, /docs, /openapi.json, /redoc, /mcp/*
```

### Knowledge Base Sync

Multi-machine sync via Dropbox using JSONL exports. Raw files live at `~/cerid-archive/` (symlinked to `~/Dropbox/cerid-archive`). Database snapshots sync via `~/Dropbox/cerid-sync/`.

```bash
# Export local KB to sync directory
python3 scripts/cerid-sync.py export

# Import from sync directory (non-destructive merge)
python3 scripts/cerid-sync.py import

# Force-overwrite local data from sync
python3 scripts/cerid-sync.py import --force

# Compare local vs sync snapshot
python3 scripts/cerid-sync.py status
```

**Auto-import on startup:** When MCP starts with an empty Neo4j database and a valid `manifest.json` in the sync directory, it automatically imports all data. This enables zero-config bootstrap on a new machine.

**Sync directory structure:**
```
~/Dropbox/cerid-sync/
├── manifest.json           # Timestamps, counts, checksums
├── neo4j/                  # artifacts.jsonl, domains.jsonl, relationships.jsonl
├── chroma/                 # domain_*.jsonl (with embeddings)
├── bm25/                   # BM25 corpus files
└── redis/                  # audit_log.jsonl
```

### Dependency Management

Python uses `pip-compile` for reproducible builds with hash verification. NPM uses `package-lock.json`.

```bash
# Regenerate Python lock files after editing requirements.txt
make lock-python

# Install git pre-commit hook (checks lock files are in sync)
make install-hooks

# Verify all lock files are current
make deps-check
```

**Key files:**
- `src/mcp/requirements.txt` — Human-editable ranges (source of truth)
- `src/mcp/requirements.lock` — Generated by `pip-compile --generate-hashes`
- `src/mcp/requirements-dev.txt` — Test/dev deps only
- `Makefile` — Convenience targets
- `scripts/hooks/pre-commit` — Blocks commits when lock files are stale
- `.github/dependabot.yml` — Weekly grouped PRs for pip, npm, actions, Docker
- `docs/DEPENDENCY_COUPLING.md` — Cross-service version constraints

**Cross-service version coupling:** See `docs/DEPENDENCY_COUPLING.md` for constraints (ChromaDB client/server, spaCy lib/model, Node version, Python version). CI enforces lock file sync via `lock-sync` job.

### Extensibility

- **Parsers:** Registry pattern in `utils/parsers.py`. PDF uses pdfplumber (structure-aware). Add Docling later for OCR via `@register_parser`.
- **Domains:** Add to `config.DOMAINS` list. Neo4j nodes auto-created.
- **File types:** Add to `config.SUPPORTED_EXTENSIONS` + register parser.

## Claude Code Setup (New Machine)

If you are a Claude Code instance on a new machine, follow these steps to get the development environment working:

1. **Verify prerequisites:** Docker running, `.env` decrypted, `age` installed, archive directory exists
2. **Run `./scripts/validate-env.sh`** to check all 14 environment validations
3. **If containers are down:** `./scripts/start-cerid.sh` (or `--build` after a `git pull`)
4. **Check `.claude/settings.json`** — shared hooks config is committed; per-machine permissions go in `.claude/settings.local.json` (gitignored)
5. **MCP server is at `http://localhost:8888/mcp/sse`** — configured in `.mcp.json` (committed), exposes 17 `pkb_*` tools
6. **React GUI dev server:** configured in `.claude/launch.json` (committed) — Vite on port 5173

**Key files for Claude Code:**
- `.mcp.json` — MCP server connection (Cerid KB tools)
- `.claude/settings.json` — shared hooks config (session-start, safety-check, typecheck, pythonlint)
- `.claude/settings.local.json` — per-machine permission allowlist (gitignored, create from scratch)
- `.claude/launch.json` — React dev server config
- `.claudeignore` — excludes node_modules, dist, runtime data, binaries, lock files

**Hooks (4 total, run automatically):**
- `session-start.sh` (SessionStart) — Docker + MCP + GUI health check
- `safety-check.sh` (PreToolUse/Bash) — blocks destructive commands
- `typecheck.sh` (PostToolUse/Edit|Write) — `npx tsc --noEmit` for `.ts`/`.tsx` in `src/web/`
- `pythonlint.sh` (PostToolUse/Edit|Write) — `ruff check` for `.py` in `src/mcp/`

**Tests:** Run Python tests in Docker (`host macOS lacks chromadb`):
```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ -v"
```
Frontend tests: `cd src/web && npx vitest run`

## Conventions

- **Session start:** Run `./scripts/validate-env.sh --quick` at the beginning of every session
- Docker services use container-name-based discovery on `llm-network`
- MCP protocol uses SSE transport with session-based message queuing
- Secrets go in root `.env`, encrypted as `.env.age` via `age`. Key at `~/.config/cerid/age-key.txt`
- User files (`~/cerid-archive/`) mounted read-only, never in git repo. Symlinked to `~/Dropbox/cerid-archive` for multi-machine sync
- Symlinks used for `artifacts/` and `data/` — don't break them
- Infrastructure DB data at `stacks/infrastructure/data/` (.gitignored)
- ChromaDB metadata values are strings/ints only (lists stored as JSON strings)
- ChromaDB client version must match server (currently `>=0.5,<0.6`)
- Error responses use `HTTPException` (returns `{"detail": "..."}`)
- Neo4j Cypher: use explicit RETURN clauses, not map projections (breaks with Python string ops)
- Deduplication: SHA-256 of parsed text, atomic via Neo4j UNIQUE CONSTRAINT on `content_hash`
- Batch ChromaDB writes: single `collection.add()` call per ingest, not per-chunk
- PDF parsing: pdfplumber extracts tables as Markdown, non-table text extracted separately to avoid duplication
- Host: Mac Pro (16-Core Xeon W, 160GB RAM), macOS
- **React GUI (`src/web/`):** Tailwind CSS v4 (uses `@tailwindcss/vite` plugin — no `tailwind.config.ts`); shadcn/ui New York style, Zinc base color; path alias `@/*` → `./src/*`; Bifrost CORS handled via Vite dev proxy (`/api/bifrost` → `localhost:8080`) and nginx proxy in Docker; `VITE_MCP_URL` and `VITE_BIFROST_URL` are `ENV` defaults baked into Dockerfile (not runtime-configurable without rebuild); `VITE_CERID_API_KEY` is a build `ARG`; bundle splitting via React.lazy + Vite manualChunks (75% main chunk reduction)
- **Backend Hardening (`src/mcp/middleware/`):** API key auth is opt-in — set `CERID_API_KEY` env var to enable (header: `X-API-Key`). Rate limiting uses in-memory sliding window with path-specific limits (`/agent/` 20 req/min, `/ingest` and `/recategorize` 10 req/min). Redis query cache with 5-min TTL (`utils/query_cache.py`) — caches `/query` and `/agent/query` results. LLM feedback loop toggled via `ENABLE_FEEDBACK_LOOP` env var. CORS origins configurable via `CORS_ORIGINS` (defaults to `*`)
- **Docker env var pattern:** `src/mcp/docker-compose.yml` uses `env_file: ../../.env` to load secrets into the MCP container. Do NOT add `${VAR}` interpolation in the `environment:` section for passthrough vars (e.g., `NEO4J_PASSWORD`) — it fails when running without `--env-file` and the empty value overrides the env_file entry. Container-specific overrides (service URLs, paths) are fine in `environment:` since they're literal values. Always rebuild MCP via `docker compose -f src/mcp/docker-compose.yml --env-file .env up -d --build` or use `scripts/start-cerid.sh`.
- **Neo4j auth validation:** `deps.py` `get_neo4j()` validates credentials by running `RETURN 1` (not just `verify_connectivity()` which only checks transport). `/health` endpoint also runs a Cypher query on every call. Empty `NEO4J_PASSWORD` raises `RuntimeError` immediately.

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

LangGraph >=0.3.0, langchain-core, langchain-openai

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
- **Phase 4 (Complete):** See `docs/PHASE4_PLAN.md` for details.
  - **4A:** Modular refactor — split main.py into FastAPI routers
  - **4B:** Smarter retrieval — hybrid BM25+vector search, knowledge graph traversal, cross-domain connections, temporal awareness
  - **4C:** Workflow automation — scheduled maintenance (APScheduler), proactive knowledge surfacing, smart ingestion, webhooks
  - **4D:** Engineering polish — 36 tests passing, GitHub Actions CI, security cleanup (secrets scrubbed from history), centralized encrypted `.env`
- **Phase 5 (Complete):** Multi-machine dev environment & knowledge sync.
  - **5A:** Infrastructure compose for Neo4j/ChromaDB/Redis (`stacks/infrastructure/`), 4-step startup script, environment validation
  - **5B:** Knowledge base sync via JSONL — export/import CLI, auto-import on startup, Dropbox-based sync directory
- **Phase 6 (Complete):** React GUI + Production Hardening. See `docs/plans/2026-02-22-phase6-gui-design.md`.
  - **6A (Complete):** Foundation + Chat — React 19 scaffold, sidebar nav, streaming chat via Bifrost SSE, health status bar, conversation persistence, Docker/nginx deployment at port 3000
  - **6B (Complete):** Knowledge Context Pane — resizable split-pane, auto KB query on message send, artifact cards with relevance scoring, domain filters, graph preview with navigable connections, KB injection into chat via system prompt
  - **6C (Complete):** Monitoring + Audit Panes — health cards (ChromaDB/Neo4j/Redis/Bifrost), collection size charts, scheduler status, activity timeline, ingestion stats, cost breakdown by tier, query pattern analytics
  - **6D (Complete):** Backend Hardening — API key auth (opt-in, X-API-Key header), in-memory sliding window rate limiting (path-specific), Redis query cache (5-min TTL), LLM feedback loop toggle, CORS configuration, bundle splitting (React.lazy + manualChunks, 75% reduction)
- **Phase 7 (Complete):** Intelligence & Automation. See `docs/plans/2026-02-23-phase7-plan.md`.
  - **7A (Complete):** Audit Intelligence — hallucination detection agent (claim extraction + KB verification), conversation analytics (per-model cost/token tracking), enhanced feedback loop (backend gate, async hallucination trigger, conversation metrics logging)
  - **7B (Complete):** Smart Orchestration — client-side model router (complexity scoring, cost sensitivity, tier-based recommendations), auto-switch toggle in toolbar, 15 MCP tools (3 new: `pkb_check_hallucinations`, `pkb_memory_extract`, `pkb_memory_archive`)
  - **7C (Complete):** Proactive Knowledge — memory extraction from conversations (facts, decisions, preferences, action items stored as KB artifacts with Neo4j relationships), smart KB suggestions (debounced real-time query as user types), memory archival with configurable retention
- **Phase 8 (Complete):** Extensibility & Hardening.
  - **8A (Complete):** Plugin system — manifest-based plugin loading, feature tiers (community/pro), feature flags, OCR parser plugin scaffold
  - **8B (Complete):** Smart ingestion — new parsers (.eml, .mbox, .epub, .rtf, enhanced CSV/TSV), semantic dedup (embedding similarity), parser registry expansion
  - **8C (Complete):** Hierarchical taxonomy — TAXONOMY dict with sub-categories/tags per domain, taxonomy API router, folder-based sub-category detection in watcher, custom domains via env var
  - **8D (Complete):** Encryption & sync — field-level Fernet encryption (opt-in), pluggable sync backends, sync manifest with checksums
  - **8E (Complete):** Infrastructure audit — comprehensive code audit (31 findings), security fixes, deprecated `datetime.utcnow()` replaced across 16 files, per-DB connection locks, retry wrappers, auth bypass fix, production Docker config, test stub DRY (~300 lines removed), N+1 session fix in sync import
- **Phase 9 (Complete):** GUI Feature Parity — wire Phase 7/8 backend features into React GUI.
  - **9A (Complete):** Fix 3 user-reported bugs — Knowledge pane error state + retry, Neo4j health card status normalization (`ok`/`connected`/`healthy`), conversation stats + hallucination aggregate in Audit pane
  - **9B (Complete):** Wire 5 structural gaps — hallucination auto-fetch after chat (refreshKey + 2s delay), smart KB suggestions as-you-type (useSmartSuggestions wired into ChatInput), memory extraction auto-trigger (after 3+ user messages), server-synced settings (fetchSettings hydration + updateSettings push), ChatDashboard refactored to useLiveMetrics hook
  - **9C (Complete):** 3 feature enhancements — file upload button in Knowledge pane (uploadFile API), sub-category badge + tag pills on artifact cards, client-side tag browsing/filtering from loaded artifacts
  - **9D (Complete):** Neo4j auth hardening — fixed docker-compose env var passthrough bug (empty `NEO4J_PASSWORD` overriding env_file), health check validates auth via Cypher query (not just `verify_connectivity()`), early RuntimeError on empty password, error detail in health responses, config.py startup warning, health-cards case-insensitive error prefix
- **Phase 10A (Complete):** Production Quality — copyright headers, source attribution in chat, frontend tests (68), CI hardening (security scanning, coverage, Docker scanning)
- **Phase 10B (Complete):** UX Polish — model switch dividers, per-message model badges with provider colors
- **Codebase Audit (Complete):** Accessibility fixes (33 across 14 components), type safety, error handling overhaul, dead code removal, logic consolidation, dependency management (pip-compile, Docker pinning, Dependabot, pre-commit hooks)
- **Phase 10C (Complete):** Structural Splits — `config/`, `db/neo4j/`, `sync/`, `parsers/`, `services/` packages; middleware hardening (XFF, IETF headers, IP redaction, request ID tracing)
- **Phase 10D (Complete):** Test Coverage — 564 Python tests (75% coverage), 94 frontend tests; CI hardening (pip-audit, CodeQL, coverage 55%, bundle size monitoring)
- **Phase 10E (Complete):** Smart Model Switching — token estimator, context replay cost, summarize-and-switch, model switch dialog with cost estimates
- **Phase 11 (Complete):** Knowledge Intelligence — interactive audit/agent controls, taxonomy tree sidebar, curation agent design, operations documentation
- **Phase 12 (Complete):** RAG Excellence — BM25s replacement (stemming, stopwords, 500x faster), configurable retrieval weights, embedding eval scaffold, eval harness (NDCG, MRR, P@K, R@K)
- **Phase 13 (Complete):** Conversation Intelligence — conversation-aware KB queries (enrichment from last 5 messages), auto-injection with confidence gate (0.82 threshold), context budget optimization
- **Phase 14 (Complete):** Artifact Quality — curation agent (4-dimension scoring), quality-weighted retrieval, metadata boost, GUI wiring (QualityBadge, quality audit), AI synopsis generation, Bifrost model fixes
- **Phase 15 (Complete):** Realtime Accuracy Watcher — UI polish (verification panel, settings scroll, collapsible sections, tooltips), streaming verification (SSE), accuracy dashboard, claim feedback, model accuracy comparison
- **Verification UX Overhaul (Complete):** Refuted/unverified status distinction, source URL extraction from web search, staleness detection with web search escalation, generator model context in prompts, session metrics, ignorance-admission detection with verdict inversion (checks underlying facts instead of model honesty)
- **Phase 16A (Complete):** Security & Infrastructure Hardening — Bifrost/LibreChat/RAG API Docker image pinning (SHA256), PostgreSQL/Meilisearch credential externalization, secret detection in CI, runtime MCP_URL config for web container
- **Phase 16B (Complete):** Dead Code & API Cleanup — removed 6 dead frontend API functions, orphaned tests, unused imports; inlined single-use helpers; dependency audit confirmed all deps in-use
- **Phase 16C (Complete):** Backend Code Quality — extracted `_format_chroma_result()` helper, `defaultdict(Counter)`, `scan_iter()`, try/except in resolve_duplicates, `get_chroma()` factory usage
- **Phase 16D (Complete):** Frontend Code Quality — extracted `tokenCost()`, `getAccuracyTier()`, `parseTags()` shared utilities; fixed unstable React keys; 18 new utility tests (111 total)
- **Phase 16E (Complete):** Dependency & Docker Optimization — removed unused `langchain-community` (-3 transitive packages), narrowed langchain bounds, extracted spaCy model ARG, removed `apk upgrade`, ChromaDB telemetry disabled, CI coverage threshold raised to 70%
- **Phase 16F (Complete):** Backend Feature Wiring — taxonomy CRUD UI (create domain/sub-category), artifact recategorize action, memory archive button, model router server-synced settings toggle
- **Next:** Phase 16G–H, then 17–18. See [`docs/plans/DEVELOPMENT_PLAN_PHASE16-18.md`](docs/plans/DEVELOPMENT_PLAN_PHASE16-18.md) and [`tasks/todo.md`](tasks/todo.md).
- **Open Issues:** See [`docs/ISSUES.md`](docs/ISSUES.md) for tracked items (3 open: E1 artifact preview, F6 compose separation, D2 conversation fork).
