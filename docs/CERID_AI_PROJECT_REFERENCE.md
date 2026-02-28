# Cerid AI - Project Plan & Technical Reference

**Document Version:** 10.2
**Date:** February 28, 2026
**Status:** Phases 0–9 Complete + Phase 10A–10C + Codebase Audit + Dependency Management
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

- **Multi-Provider LLM Access** via Bifrost gateway (Claude, GPT, Grok, Gemini, DeepSeek, Llama)
- **7 Intelligent Agents** — Query (LLM reranking), Triage (LangGraph), Rectification, Audit, Maintenance, Hallucination Detection, Memory Extraction
- **15 MCP Tools** for knowledge base operations from LibreChat chat UI
- **Hybrid BM25+Vector Search** with knowledge graph traversal and cross-domain connections
- **React GUI** at port 3000 — streaming chat with source attribution, knowledge browser, monitoring & audit dashboards, model switch dividers, provider-colored badges
- **Streamlit Admin Dashboard** (legacy) with 5 panes (Overview, Artifacts, Query, Audit, Maintenance)
- **File-Based Ingestion Pipeline** with structure-aware parsing (PDF tables via pdfplumber, DOCX, XLSX, CSV, 30+ formats)
- **RAG-Powered Context Injection** for token-efficient knowledge retrieval (14k char budget)
- **Multi-Machine Sync** via Dropbox — JSONL export/import with auto-import on startup
- **Scheduled Maintenance** via APScheduler with proactive knowledge surfacing
- **Local Vector & Graph Storage** (ChromaDB, Neo4j, Redis)
- **MCP SSE Protocol** for tool integration with LibreChat UI
- **Three-Tier AI Categorization** (manual, smart, pro) for flexible ingestion workflows
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
| Phase 1.5 | Bulk Ingest Hardening | ✅ Complete |
| Phase 2 | Enhanced Search & Agent Workflows | ✅ Complete |
| Phase 3 | GUI & Dashboard | ✅ Complete |
| Phase 4 | Smarter Retrieval, Automation & Polish | ✅ Complete |
| Phase 5 | Multi-Machine Dev & Sync | ✅ Complete |
| Phase 6 | React GUI + Production Hardening | ✅ Complete |
| Phase 7 | Intelligence & Automation | ✅ Complete |
| Phase 8 | Extensibility & Hardening | ✅ Complete |
| Phase 9 | GUI Feature Parity | ✅ Complete |
| Phase 10A | Production Quality | ✅ Complete |
| Phase 10B | UX Polish — Model Context Breaks | ✅ Complete |
| Codebase Audit | Dead code, security, accessibility | ✅ Complete |
| Dependency Mgmt | Lock files, Dependabot, Docker pins | ✅ Complete |
| Phase 10C | Structural Splits + Security Hardening | ✅ Complete |
| Phase 10D–H | Test coverage, smart routing, audit UX, curation, RAG eval | Planned |

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

**Bulk Ingest Hardening (Phase 1.5):**
- [x] CLI concurrent ingestion via ThreadPoolExecutor (`--workers` flag, default 4)
- [x] Thread-safe progress output with per-request delay (0.3s)
- [x] Rich summary: elapsed time, files/hr, domain breakdown, failures by type
- [x] Watcher retry queue: failed files retry once after 30s delay
- [x] Watcher stability window extended (5 checks x 2s = ~30s max for large files)
- [x] Distinct duplicate logging (⊘ symbol) in watcher output
- [x] Atomic deduplication: Neo4j UNIQUE CONSTRAINT on content_hash (replaces INDEX)
- [x] Concurrent duplicate handling: ConstraintError catch + ChromaDB chunk cleanup
- [x] Query: real relevance scores from ChromaDB distances (not hardcoded 0.8)
- [x] Query: source attribution (artifact_id, filename, domain, chunk_index)
- [x] Query: 14,000-char token budget cap (~3,500 tokens) with truncation
- [x] Query: overall confidence = average relevance of returned sources
- [x] PDF parser upgraded: pypdf → pdfplumber for structure-aware extraction
- [x] PDF tables extracted as Markdown (header separators, column alignment preserved)
- [x] Non-table text extracted separately via bounding box exclusion (no duplication)

### Phase 2 Deliverables (Complete ✅)

- [x] Multi-domain search across all 5 ChromaDB collections with parallel retrieval
- [x] Query Agent with LLM reranking via Bifrost (60% LLM rank + 40% embedding score)
- [x] Triage Agent (LangGraph) — validate → parse → route → categorize → chunk
- [x] Rectification Agent — duplicate/stale/orphan detection with auto-fix
- [x] Audit Agent — activity tracking, cost estimation, query patterns
- [x] Maintenance Agent — system health, stale cleanup, collection analysis
- [x] MCP tool expansion: 5 → 12 tools

### Phase 3 Deliverables (Complete ✅)

- [x] Streamlit admin dashboard (5 panes: Overview, Artifacts, Query, Audit, Maintenance)
- [x] Obsidian vault watcher (`watch_obsidian.py`)

### Phase 4 Deliverables (Complete ✅)

- [x] **4A:** Modular refactor — main.py split into 7 FastAPI routers (`routers/`)
- [x] **4B:** Hybrid BM25+vector search (`utils/bm25.py`), knowledge graph traversal, cross-domain connections, temporal awareness
- [x] **4C:** APScheduler background jobs, proactive knowledge surfacing, smart ingestion, event-driven webhooks
- [x] **4D:** 36 pytest tests, GitHub Actions CI, security cleanup, centralized encrypted `.env`, Apache 2.0 license

### Phase 5 Deliverables (Complete ✅)

- [x] Infrastructure compose: `stacks/infrastructure/docker-compose.yml` (Neo4j, ChromaDB, Redis)
- [x] 4-step startup script, environment validation (`scripts/validate-env.sh`)
- [x] Knowledge base sync CLI (`scripts/cerid-sync.py` — export/import/status)
- [x] Auto-import on startup (`src/mcp/sync_check.py`) for empty databases
- [x] Secrets management: age encryption (`env-lock.sh`, `env-unlock.sh`)

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
| Docker Infrastructure | ✅ Healthy | All 13 containers running |
| **React GUI** | ✅ Healthy | **Port 3000, primary UI** |
| Dashboard | ✅ Healthy | Port 8501, Streamlit admin UI (legacy) |
| LibreChat UI | ✅ Healthy | Port 3080, MCP tools connected (legacy) |
| Bifrost Gateway | ✅ Healthy | Port 8080, OpenRouter connected |
| MCP Server | ✅ Healthy | Port 8888, REST + SSE + Ingestion + Agents |
| ChromaDB | ✅ Healthy | 0.5.23, per-domain collections operational |
| Neo4j | ✅ Healthy | 5.26.21, auth-validated via Cypher |
| Redis | ✅ Healthy | 7.4.8, audit logging + query cache active |
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
│         /artifacts /upload      │               ▼
│  Agents: /agent/query           │    ┌──────────────────────────┐
│          /agent/triage          │    │    OpenRouter API         │
│          /agent/rectify         │    │  (Claude, GPT, Gemini,   │
│          /agent/audit           │    │   Grok, DeepSeek, etc.)  │
│          /agent/maintain        │    └──────────────────────────┘
│          /agent/hallucination   │
│          /agent/memory          │
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
~/cerid-ai/                                # Main repository
├── README.md                              # Project overview & quick start
├── CLAUDE.md                              # AI developer guide
├── CONTRIBUTING.md                        # Contribution guidelines
├── LICENSE                                # Apache-2.0
├── NOTICE                                 # Attribution notice
├── Makefile                               # lock-python, install-hooks, deps-check
├── pyproject.toml                         # Ruff + pytest config
├── .env.age                               # Encrypted secrets (age)
├── .env.example                           # Template for .env
├── artifacts -> ~/Dropbox/AI-Artifacts    # Symlink to artifacts storage
├── data -> src/mcp/data                   # Symlink to persistent data
│
├── .github/
│   ├── workflows/ci.yml                   # 6-job CI pipeline
│   └── dependabot.yml                     # Weekly grouped PRs
│
├── docs/
│   ├── CERID_AI_PROJECT_REFERENCE.md      # This document
│   ├── DEPENDENCY_COUPLING.md             # Cross-service version constraints
│   ├── ISSUES.md                          # Issue tracker (5 open)
│   ├── PHASE4_PLAN.md                     # Phase 4 design
│   └── plans/                             # Implementation plans (6 docs)
│
├── scripts/
│   ├── start-cerid.sh                     # One-command 4-step startup
│   ├── validate-env.sh                    # Pre-flight validation (--quick, --fix)
│   ├── cerid-sync.py                      # Knowledge base sync CLI
│   ├── env-lock.sh                        # Encrypt .env → .env.age
│   ├── env-unlock.sh                      # Decrypt .env.age → .env
│   ├── migrate_taxonomy.py                # Taxonomy migration utility
│   ├── setup.sh                           # Initial setup script
│   └── hooks/pre-commit                   # Lock file sync guard
│
├── tasks/
│   └── todo.md                            # Task tracker
│
├── src/mcp/                               # MCP Server (FastAPI + Python 3.11)
│   ├── main.py                            # FastAPI entry point (114 lines — routes via routers/)
│   ├── config/                            # Configuration package (split from config.py)
│   │   ├── settings.py                    # URLs, timeouts, env vars
│   │   ├── taxonomy.py                    # TAXONOMY dict, domains, sub-categories
│   │   └── features.py                    # Feature flags, tier constants
│   ├── deps.py                            # DB singletons, retry wrappers, auth validation
│   ├── scheduler.py                       # APScheduler maintenance engine
│   ├── tools.py                           # MCP tool registry + dispatcher (17 tools)
│   ├── sync_check.py                      # Auto-import on startup
│   ├── Dockerfile                         # python:3.11.14-slim, non-root user
│   ├── docker-compose.yml                 # MCP + Dashboard + React GUI
│   ├── requirements.txt                   # Human-editable dependency ranges
│   ├── requirements.lock                  # pip-compile with hashes (reproducible)
│   ├── requirements-dev.txt               # Test/dev dependencies
│   ├── requirements-dev.lock              # Dev lock file with hashes
│   │
│   ├── routers/                           # FastAPI routers (10 modules)
│   │   ├── health.py                      # GET /health (Cypher auth validation)
│   │   ├── query.py                       # POST /query
│   │   ├── ingestion.py                   # POST /ingest, /ingest_file, /recategorize
│   │   ├── artifacts.py                   # GET /artifacts
│   │   ├── agents.py                      # POST /agent/* (query, triage, rectify, audit, maintain, hallucination)
│   │   ├── digest.py                      # POST /digest
│   │   ├── mcp_sse.py                     # SSE transport (JSON-RPC 2.0)
│   │   ├── taxonomy.py                    # GET /taxonomy
│   │   ├── settings.py                    # GET/PATCH /settings
│   │   ├── upload.py                      # POST /upload
│   │   ├── memories.py                    # GET /memories, POST /agent/memory/*
│   │   └── __init__.py
│   │
│   ├── agents/                            # 7 Agent modules
│   │   ├── query_agent.py                 # Multi-domain + LLM reranking
│   │   ├── triage.py                      # LangGraph triage pipeline
│   │   ├── rectify.py                     # KB health checks + auto-fix
│   │   ├── audit.py                       # Usage analytics + conversation costs
│   │   ├── maintenance.py                 # System health + cleanup
│   │   ├── hallucination.py               # Claim extraction + KB verification
│   │   └── memory.py                      # Memory extraction + archival
│   │
│   ├── services/                          # Service layer
│   │   └── ingestion.py                   # Core ingest pipeline (extracted from router)
│   │
│   ├── db/                                # Database layer
│   │   └── neo4j/                         # Neo4j CRUD package
│   │       ├── schema.py                  # Constraints, indexes, seed data
│   │       ├── artifacts.py               # Artifact CRUD (6 functions)
│   │       ├── relationships.py           # Relationship discovery (5 functions)
│   │       └── taxonomy.py                # Domain/tag management (5 functions)
│   │
│   ├── parsers/                           # File parser package
│   │   ├── registry.py                    # Parser registry + parse_file()
│   │   ├── pdf.py, office.py              # PDF, DOCX, XLSX parsers
│   │   ├── structured.py                  # CSV, HTML, plain text parsers
│   │   ├── email.py                       # EML, MBOX parsers
│   │   └── ebook.py                       # EPUB, RTF parsers
│   │
│   ├── sync/                              # KB sync package
│   │   ├── export.py                      # Export Neo4j/Chroma/BM25/Redis
│   │   ├── import_.py                     # Import with merge/overwrite
│   │   ├── manifest.py                    # Manifest read/write
│   │   ├── status.py                      # Local vs sync comparison
│   │   └── _helpers.py                    # Constants + utility functions
│   │
│   ├── plugins/                           # Plugin system (manifest-based, feature tiers)
│   │   └── ocr/                           # OCR parser plugin (pro tier)
│   │
│   ├── utils/                             # Utility modules (shims + standalone)
│   │   ├── parsers.py                     # Re-export shim → parsers/
│   │   ├── graph.py                       # Re-export shim → db/neo4j/
│   │   ├── metadata.py                    # Metadata extraction + AI categorization
│   │   ├── chunker.py                     # Token-based text chunking
│   │   ├── cache.py                       # Redis audit logging
│   │   ├── query_cache.py                 # Redis query cache (5-min TTL)
│   │   ├── bm25.py                        # BM25 keyword search index
│   │   ├── dedup.py                       # Semantic dedup (embedding similarity)
│   │   ├── encryption.py                  # Field-level Fernet encryption
│   │   ├── features.py                    # Feature flags + tier gating
│   │   ├── temporal.py                    # Temporal intent parsing + recency scoring
│   │   ├── time.py                        # Timezone-aware UTC helpers
│   │   ├── llm_parsing.py                 # LLM response JSON parsing
│   │   ├── sync_backend.py                # Pluggable sync backends
│   │   └── webhooks.py                    # Webhook event publishing
│   │
│   ├── scripts/
│   │   ├── watch_ingest.py                # Folder watcher (host process)
│   │   ├── watch_obsidian.py              # Obsidian vault watcher
│   │   └── ingest_cli.py                  # Batch CLI ingest tool
│   │
│   ├── middleware/                         # Auth + rate limiting
│   │   ├── auth.py                        # X-API-Key validation (opt-in)
│   │   └── rate_limit.py                  # Sliding window rate limiter
│   │
│   └── tests/                             # 156 pytest tests (11 test files)
│       ├── conftest.py                    # Shared fixtures
│       ├── test_bm25.py, test_encryption.py
│       ├── test_graph.py, test_hallucination.py
│       ├── test_memory.py, test_plugins.py
│       ├── test_scheduler.py, test_smart_ingestion.py
│       ├── test_taxonomy.py, test_temporal.py
│       └── test_webhooks.py
│
├── src/web/                               # React GUI (Phase 6+)
│   ├── .nvmrc                             # Node version source of truth (22)
│   ├── package.json                       # React 19, Vite 7, Tailwind v4, shadcn/ui
│   ├── vite.config.ts                     # Bundle splitting, Bifrost proxy
│   ├── Dockerfile                         # Multi-stage: node:22 → nginx:1.27
│   ├── nginx.conf                         # SPA fallback + Bifrost reverse proxy
│   └── src/
│       ├── App.tsx                        # Lazy-loaded pane routing
│       ├── lib/                           # types.ts, api.ts, model-router.ts, utils.ts, humanize-trigger.ts
│       ├── hooks/                         # 8 hooks (use-chat, use-conversations, etc.)
│       ├── contexts/                      # KB injection context provider
│       ├── __tests__/                     # 68 vitest tests (5 test files)
│       └── components/
│           ├── layout/                    # Sidebar, status bar, split-pane
│           ├── chat/                      # Chat panel, input, bubbles, dashboard,
│           │                              # source attribution, model badges/dividers
│           ├── kb/                        # Knowledge pane, artifact cards, graph,
│           │                              # file upload, tag filter, domain filter
│           ├── monitoring/                # Health cards, charts, scheduler, ingestion
│           ├── audit/                     # Activity, costs, queries, hallucination, conversations
│           ├── memories/                  # Memory management pane
│           ├── settings/                  # Settings pane (server-synced)
│           └── ui/                        # shadcn/ui primitives (14 components)
│
├── src/gui/                               # Streamlit Dashboard (legacy)
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
│
└── stacks/
    ├── infrastructure/                    # Neo4j, ChromaDB, Redis (pinned versions)
    │   ├── docker-compose.yml
    │   └── data/                          # Persistent DB data (.gitignored)
    ├── bifrost/                           # LLM Gateway
    │   ├── docker-compose.yml
    │   └── data/config.json               # Bifrost configuration
    └── librechat/                         # Chat UI + RAG
        ├── docker-compose.yml
        └── librechat.yaml                 # LibreChat configuration

~/cerid-archive/                           # User knowledge archive (HOST, mounted read-only)
├── coding/                                # → domain="coding", mode="manual"
├── finance/                               # → domain="finance", mode="manual"
├── projects/                              # → domain="projects", mode="manual"
├── personal/                              # → domain="personal", mode="manual"
├── general/                               # → domain="general", mode="manual"
├── conversations/                         # → domain="conversations" (feedback loop)
└── inbox/                                 # → domain="" (triggers AI categorization)
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
| Image | python:3.11.14-slim (custom build) |
| Container | `ai-companion-mcp` |
| Port | 8888 |
| Source | `src/mcp/main.py` (114 lines — routes via 10 router modules) |
| Version | 1.0.0 |

**REST Endpoints:**

| Method | Endpoint | Description | Phase |
|--------|----------|-------------|-------|
| GET | `/` | Service info | 0 |
| GET | `/health` | Health check (ChromaDB, Neo4j, Redis, auth-validated) | 0 |
| GET | `/collections` | List ChromaDB collections | 0 |
| GET | `/stats` | Database statistics | 0 |
| POST | `/query` | Query knowledge base by domain | 0 |
| POST | `/ingest` | Ingest raw text content | 0 |
| POST | `/ingest_file` | Parse + ingest file with metadata | 1 |
| POST | `/upload` | Upload file for ingestion | 9C |
| POST | `/recategorize` | Move artifact between domains | 1 |
| GET | `/artifacts` | List artifacts (filter by domain) | 1 |
| GET | `/ingest_log` | Redis audit trail | 1 |
| POST | `/agent/query` | Multi-domain query with LLM reranking | 2 |
| POST | `/agent/triage` | LangGraph file triage | 2 |
| POST | `/agent/triage/batch` | Batch triage with error recovery | 2 |
| POST | `/agent/rectify` | KB health checks + auto-fix | 2 |
| POST | `/agent/audit` | Audit reports (activity, ingestion, costs, queries, conversations) | 2 |
| POST | `/agent/maintain` | Maintenance routines | 2 |
| POST | `/digest` | Daily knowledge digest | 4C |
| POST | `/agent/hallucination` | Check LLM response for hallucinations against KB | 7A |
| GET | `/agent/hallucination/{id}` | Retrieve stored hallucination report | 7A |
| POST | `/agent/memory/extract` | Extract and store memories from conversation | 7C |
| POST | `/agent/memory/archive` | Archive old conversation memories | 7C |
| GET | `/memories` | List extracted memories | 7C |
| GET | `/taxonomy` | Hierarchical taxonomy tree (domains, sub-categories, tags) | 8C |
| GET | `/settings` | Server settings and feature flags | 9B |
| PATCH | `/settings` | Update server settings | 9B |

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
| `pkb_agent_query` | Multi-domain query with LLM reranking | `query`, `domains`, `top_k` |
| `pkb_artifacts` | List/filter artifacts | `domain`, `limit` |
| `pkb_recategorize` | Move artifact between domains | `artifact_id`, `new_domain` |
| `pkb_triage` | LangGraph file triage | `file_path` |
| `pkb_rectify` | KB health checks + auto-fix | `auto_fix`, `stale_days` |
| `pkb_audit` | Audit reports | `reports`, `hours` |
| `pkb_maintain` | Maintenance routines | `actions`, `auto_purge` |
| `pkb_check_hallucinations` | Verify LLM claims against KB | `conversation_id`, `response`, `context` |
| `pkb_memory_extract` | Extract memories from conversations | `conversation_id`, `messages` |
| `pkb_memory_archive` | Archive old conversation memories | `days_old` |

**Middleware (Phase 6D):**

| Middleware | File | Description |
|-----------|------|-------------|
| API Key Auth | `middleware/auth.py` | Opt-in via `CERID_API_KEY` env var. Checks `X-API-Key` header. Exempt: `/health`, `/`, `/docs`, `/redoc`, `/mcp/*` |
| Rate Limiting | `middleware/rate_limit.py` | In-memory sliding window per client IP. `/agent/` 20 req/60s, `/ingest` 10 req/60s, `/recategorize` 10 req/60s |
| Query Cache | `utils/query_cache.py` | Redis-backed, 5-min TTL. Caches `/query` and `/agent/query` responses |

### 5.4 Storage Services

#### ChromaDB (Vector Store)

| Property | Value |
|----------|-------|
| Image | `chromadb/chroma:0.5.23` |
| Container | `ai-companion-chroma` |
| Port | 8001 (internal 8000) |
| Data | `stacks/infrastructure/data/chroma/` |

**Collections:** Named `domain_{name}` (e.g., `domain_coding`, `domain_finance`). Created automatically on first use.

**Metadata Constraint:** ChromaDB does not support list values in metadata. Lists (keywords, chunk_ids) are stored as JSON-serialized strings.

#### Neo4j (Graph Database)

| Property | Value |
|----------|-------|
| Image | `neo4j:5.26.21-community` |
| Container | `ai-companion-neo4j` |
| Ports | 7474 (HTTP), 7687 (Bolt) |
| Data | `stacks/infrastructure/data/neo4j/` |
| Credentials | neo4j / (from .env `NEO4J_PASSWORD`) |

**Schema (auto-initialized on startup):**
```cypher
CREATE CONSTRAINT artifact_id IF NOT EXISTS FOR (a:Artifact) REQUIRE a.id IS UNIQUE;
CREATE CONSTRAINT domain_name IF NOT EXISTS FOR (d:Domain) REQUIRE d.name IS UNIQUE;
CREATE CONSTRAINT artifact_content_hash_unique IF NOT EXISTS FOR (a:Artifact) REQUIRE a.content_hash IS UNIQUE;
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
| Image | `redis:7.4.8-alpine` |
| Container | `ai-companion-redis` |
| Port | 6379 |
| Data | `stacks/infrastructure/data/redis/` |

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
| `.pdf` | `parse_pdf` | pdfplumber | Structure-aware: tables → Markdown, non-table text via bbox exclusion, image-only detection |
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
- `init_schema(driver)` — constraints (including UNIQUE on content_hash), seed Domain nodes (called on startup)
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
- File stability detection (polls file size 5 times at 2s intervals, ~30s max wait for large files)
- Debounce (2s) with automatic cleanup of stale entries (>60s)
- Retry queue: failed files get one automatic retry after 30s
- Distinct duplicate logging (⊘ symbol vs ✓ for success, ✗ for error)
- Handles `on_created`, `on_modified`, and `on_moved` events
- Colored terminal logging (green=success, yellow=warn, red=error)
- Skips hidden files and unsupported extensions

**Usage:**
```bash
python src/mcp/scripts/watch_ingest.py [--mode smart|pro|manual] [--folder ~/cerid-archive]
```

### 6.9 CLI Batch Ingest (`scripts/ingest_cli.py`)

Concurrent batch ingestion tool for processing existing files.

**Features:**
- Concurrent ingestion via ThreadPoolExecutor (`--workers` flag, default 4)
- Thread-safe progress output with per-request delay (0.3s)
- Recursive directory walking
- `--domain` flag to force domain for all files
- `--mode` flag for categorization tier
- `--dry-run` to preview without ingesting
- Skips `legacy-*` directories and hidden files
- Rich summary: elapsed time, files/hr, domain breakdown, failures grouped by type

**Usage:**
```bash
python src/mcp/scripts/ingest_cli.py --dir ~/cerid-archive/ [--mode smart] [--domain coding] [--workers 4] [--dry-run]
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
| DOMAINS | coding, finance, projects, personal, general, conversations | — |
| DEFAULT_DOMAIN | general | — |
| CATEGORIZE_MODE | smart | `CATEGORIZE_MODE` |
| CHUNK_MAX_TOKENS | 512 | — |
| CHUNK_OVERLAP | 0.2 | — |
| AI_SNIPPET_MAX_CHARS | 1500 | — |
| ARCHIVE_PATH | /archive | `ARCHIVE_PATH` |
| WATCH_FOLDER | ~/cerid-archive | `WATCH_FOLDER` |
| BIFROST_URL | http://bifrost:8080/v1 | `BIFROST_URL` |
| CERID_API_KEY | (disabled) | `CERID_API_KEY` |
| ENABLE_FEEDBACK_LOOP | true | `ENABLE_FEEDBACK_LOOP` |
| CORS_ORIGINS | * | `CORS_ORIGINS` |

---

## 7. Configuration Reference

### 7.1 Environment Variables

**Root `.env`** (encrypted as `.env.age` with age):
```bash
OPENROUTER_API_KEY=sk-or-v1-xxxxx    # Required - OpenRouter API key
OPENAI_API_KEY=sk-or-v1-xxxxx        # Same key - used by RAG API for embeddings
NEO4J_PASSWORD=...                    # Neo4j database password
# Plus other service credentials
```

**Secrets management:**
```bash
./scripts/env-lock.sh     # Encrypt .env → .env.age
./scripts/env-unlock.sh   # Decrypt .env.age → .env
# Age key: ~/.config/cerid/age-key.txt (installed from dotfiles repo)
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

**src/mcp/docker-compose.yml** — 3 services (MCP, Dashboard, React GUI):

```yaml
services:
  mcp-server:
    container_name: ai-companion-mcp
    build: { context: ., dockerfile: Dockerfile }
    ports: ["8888:8888"]
    env_file: ../../.env            # Secrets loaded from root .env
    environment:                    # Container-specific overrides only
      - CHROMA_URL=http://ai-companion-chroma:8000
      - NEO4J_URI=bolt://ai-companion-neo4j:7687
      - NEO4J_USER=neo4j
      - REDIS_URL=redis://ai-companion-redis:6379
      - PORT=8888
      - CATEGORIZE_MODE=smart
      - BIFROST_URL=http://bifrost:8080/v1
      - ARCHIVE_PATH=/archive
    volumes:
      - .:/app
      - ~/cerid-archive:/archive:ro
    networks: [llm-network]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8888/health"]
      interval: 30s
      start_period: 60s
```

**Important:** Passthrough secrets (e.g., `NEO4J_PASSWORD`) are loaded via `env_file` — do NOT redeclare them in `environment:` or empty values will override. See CLAUDE.md for details.

### 7.5 MCP Dependencies

**src/mcp/requirements.txt** (human-editable ranges, locked via `pip-compile`):
```
# Core framework
fastapi>=0.100,<0.120
uvicorn[standard]
pydantic>=2.0,<3

# HTTP client
httpx>=0.24,<0.28
httpx-sse

# Databases
chromadb>=0.5,<0.6
neo4j>=5.0,<6
redis>=4.0,<6

# AI / NLP
spacy>=3.5,<3.9
tiktoken
langgraph>=0.2.0,<0.4
langchain-core>=0.3.0,<0.4
langchain-openai>=0.2.0,<0.4
langchain-community>=0.3.0,<0.4

# File parsing
pdfplumber>=0.10,<0.12
python-docx
openpyxl>=3.1,<4
pandas>=2.0,<3

# Search
rank_bm25>=0.2.2

# Scheduling
apscheduler>=3.10,<4

# Utilities
python-dotenv
python-multipart
```

**Lock files:** `requirements.lock` and `requirements-dev.lock` are generated via `pip-compile --generate-hashes` and installed with `--require-hashes` in Docker for reproducible builds.

---

## 8. Operations Guide

### 8.1 Start All Services

```bash
# Using start script (recommended — 4-step automated startup)
./scripts/start-cerid.sh
# [1/4] Infrastructure (Neo4j, ChromaDB, Redis)
# [2/4] Bifrost
# [3/4] MCP + Dashboard
# [4/4] LibreChat

# Validate environment
./scripts/validate-env.sh          # full (14 checks)
./scripts/validate-env.sh --quick  # containers only
./scripts/validate-env.sh --fix    # auto-start missing
```

### 8.2 Stop All Services

```bash
cd ~/cerid-ai/stacks/librechat && docker compose down
cd ~/cerid-ai/src/mcp && docker compose down
cd ~/cerid-ai/stacks/bifrost && docker compose down
cd ~/cerid-ai/stacks/infrastructure && docker compose down
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

# Then ingest (concurrent, 4 workers by default)
python src/mcp/scripts/ingest_cli.py --dir ~/cerid-archive/ --mode smart --workers 4
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

Preferred method — knowledge base sync CLI:
```bash
python3 scripts/cerid-sync.py export          # dump to ~/Dropbox/cerid-sync/
python3 scripts/cerid-sync.py import          # merge from sync dir
python3 scripts/cerid-sync.py import --force  # overwrite local
python3 scripts/cerid-sync.py status          # compare local vs sync
```

Manual backup:
```bash
tar czf cerid-backup-$(date +%Y%m%d).tar.gz \
  ~/cerid-ai/src/mcp/data \
  ~/cerid-ai/stacks/infrastructure/data \
  ~/cerid-ai/.env \
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

### 9.9 Docker Compose env_file vs environment Override

**Error:** `NEO4J_PASSWORD` is empty inside the container even though `.env` has it set.

**Cause:** If `environment:` section in docker-compose.yml declares `NEO4J_PASSWORD=${NEO4J_PASSWORD}` and you run without `--env-file` on the CLI, `${NEO4J_PASSWORD}` resolves to empty, overriding the `env_file:` entry.

**Fix:** Don't put passthrough secrets in `environment:`. Let `env_file:` handle them. Only use `environment:` for container-specific literal values (service URLs, paths). Always rebuild with: `docker compose -f src/mcp/docker-compose.yml --env-file .env up -d --build`

### 9.10 spaCy Model Not Found

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
| Phase 1.5 | Bulk Ingest Hardening | ✅ Complete |
| Phase 2 | Enhanced Search & Agent Workflows | ✅ Complete |
| Phase 3 | GUI & Dashboard | ✅ Complete |
| Phase 4 | Smarter Retrieval, Automation & Polish | ✅ Complete |
| Phase 5 | Multi-Machine Dev & Sync | ✅ Complete |
| Phase 6 | React GUI + Production Hardening | ✅ Complete |
| Phase 7 | Intelligence & Automation | ✅ Complete |
| Phase 8 | Extensibility & Hardening | ✅ Complete |
| Phase 9 | GUI Feature Parity | ✅ Complete |
| Phase 10A | Production Quality | ✅ Complete |
| Phase 10B | UX Polish — Model Context Breaks | ✅ Complete |
| Codebase Audit | Dead code, security, accessibility, tests | ✅ Complete |
| Dependency Mgmt | Lock files, Dependabot, Docker pins | ✅ Complete |
| Phase 10C | Structural Splits + Security Hardening | ✅ Complete |
| Phase 10D–H | Test coverage, smart routing, audit UX, curation, RAG eval | Planned |

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

### Phase 1.5: Bulk Ingest Hardening (Complete ✅)

- [x] CLI concurrent ingestion (ThreadPoolExecutor, --workers flag)
- [x] Watcher retry queue (30s delay) and extended stability window (5x2s)
- [x] Atomic deduplication via Neo4j UNIQUE CONSTRAINT on content_hash
- [x] Query improvements: real relevance scores, source attribution, 14k-char token budget
- [x] PDF parser upgrade: pypdf → pdfplumber (tables → Markdown, bbox exclusion for non-table text)

### Phase 2: Enhanced Search & Agent Workflows (Complete ✅)

- [x] Multi-domain search across all 5 ChromaDB collections with parallel retrieval
- [x] Query Agent with LLM reranking (Llama 3.1 free tier, 60% LLM + 40% embedding blend)
- [x] Triage Agent (LangGraph) — validate → parse → route → categorize → chunk, with batch mode
- [x] Rectification Agent — duplicate/stale/orphan detection with auto-fix
- [x] Audit Agent — activity tracking, cost estimation, query patterns from Redis audit trail
- [x] Maintenance Agent — system health, stale cleanup, collection analysis, orphan detection
- [x] MCP tool expansion: 5 → 12 tools (pkb_agent_query, pkb_triage, pkb_rectify, pkb_audit, pkb_maintain, pkb_artifacts, pkb_recategorize)

### Phase 3: Dashboard & Integrations (Complete ✅)

- [x] Streamlit admin dashboard with 5 panes (Overview, Artifacts, Query, Audit, Maintenance)
- [x] Obsidian vault watcher (`watch_obsidian.py`) — monitors `.md` files, 5s debounce

### Phase 4: Smarter Retrieval, Automation & Polish (Complete ✅)

See `docs/PHASE4_PLAN.md` for full implementation details.

- [x] **4A:** Modular refactor — split main.py into 7 FastAPI routers (`routers/`)
- [x] **4B:** Hybrid BM25+vector search, knowledge graph traversal (RELATES_TO, DEPENDS_ON, SUPERSEDES, REFERENCES), cross-domain connections, temporal awareness (recency boost with 30-day half-life)
- [x] **4C:** APScheduler background jobs (daily rectification, 6-hourly health, weekly stale), proactive knowledge surfacing, smart ingestion, event-driven webhooks
- [x] **4D:** 36 pytest tests, GitHub Actions CI (ruff + pytest + Docker build), security cleanup, centralized encrypted `.env`, Apache 2.0 license, CONTRIBUTING.md

### Phase 5: Multi-Machine Dev & Sync (Complete ✅)

- [x] Infrastructure compose: `stacks/infrastructure/docker-compose.yml` (Neo4j 5-community, ChromaDB 0.5.23, Redis 7-alpine)
- [x] 4-step startup script, environment validation (`scripts/validate-env.sh` with --quick, --fix flags)
- [x] Knowledge base sync CLI (`scripts/cerid-sync.py` — export/import/status via JSONL)
- [x] Auto-import on startup for empty databases (`src/mcp/sync_check.py`)
- [x] Secrets management: age encryption (`env-lock.sh`, `env-unlock.sh`)

### Phase 6: React GUI + Production Hardening (Complete ✅)

See `docs/plans/2026-02-22-phase6-gui-design.md` for full design specification.

- [x] **6A:** Foundation + Chat — React 19 scaffold, sidebar nav, streaming chat via Bifrost SSE, health status bar, conversation persistence (localStorage), Docker/nginx deployment at port 3000
- [x] **6B:** Knowledge Context Pane — resizable split-pane, auto KB query on message send, artifact cards with relevance scoring, domain filters, graph preview with navigable connections, KB injection into chat via system prompt
- [x] **6C:** Monitoring + Audit Panes — health cards (ChromaDB/Neo4j/Redis/Bifrost), collection size charts, scheduler status, activity timeline, ingestion stats, cost breakdown by tier, query pattern analytics
- [x] **6D:** Backend Hardening — API key auth (opt-in, X-API-Key header), in-memory sliding window rate limiting (path-specific), Redis query cache (5-min TTL), LLM feedback loop toggle, CORS configuration, bundle splitting (React.lazy + manualChunks, 75% reduction)

**Tech Stack:** React 19, Vite 7, Tailwind CSS v4, shadcn/ui, TanStack React Query, Recharts, react-resizable-panels

### Phase 7: Intelligence & Automation (Complete ✅)

See `docs/plans/2026-02-23-phase7-plan.md` for full specification.

- [x] **7A:** Audit Intelligence — hallucination detection agent (claim extraction + KB verification), conversation analytics (per-model cost/token tracking), enhanced feedback loop (backend gate, async hallucination trigger, conversation metrics logging)
- [x] **7B:** Smart Orchestration — client-side model router (complexity scoring, cost sensitivity, tier-based recommendations), auto-switch toggle in toolbar, 15 MCP tools (3 new: `pkb_check_hallucinations`, `pkb_memory_extract`, `pkb_memory_archive`)
- [x] **7C:** Proactive Knowledge — memory extraction from conversations (facts, decisions, preferences, action items stored as KB artifacts with Neo4j relationships), smart KB suggestions (debounced real-time query as user types), memory archival with configurable retention

### Phase 8: Extensibility & Hardening (Complete ✅)

- [x] **8A:** Plugin system — manifest-based plugin loading, feature tiers (community/pro), feature flags, OCR parser plugin scaffold
- [x] **8B:** Smart ingestion — new parsers (.eml, .mbox, .epub, .rtf, enhanced CSV/TSV), semantic dedup (embedding similarity), parser registry expansion
- [x] **8C:** Hierarchical taxonomy — TAXONOMY dict with sub-categories/tags per domain, taxonomy API router, folder-based sub-category detection in watcher, custom domains via env var
- [x] **8D:** Encryption & sync — field-level Fernet encryption (opt-in), pluggable sync backends, sync manifest with checksums
- [x] **8E:** Infrastructure audit — comprehensive code audit (31 findings), security fixes, deprecated `datetime.utcnow()` replaced across 16 files, per-DB connection locks, retry wrappers, auth bypass fix, production Docker config, test stub DRY (~300 lines removed), N+1 session fix in sync import

### Phase 9: GUI Feature Parity (Complete ✅)

- [x] **9A:** Fix 3 user-reported bugs — Knowledge pane error state + retry, Neo4j health card status normalization, conversation stats + hallucination aggregate in Audit pane
- [x] **9B:** Wire 5 structural gaps — hallucination auto-fetch after chat (refreshKey + 2s delay), smart KB suggestions as-you-type, memory extraction auto-trigger (after 3+ user messages), server-synced settings (fetchSettings hydration + updateSettings push), ChatDashboard refactored to useLiveMetrics hook
- [x] **9C:** 3 feature enhancements — file upload button in Knowledge pane, sub-category badge + tag pills on artifact cards, client-side tag browsing/filtering from loaded artifacts
- [x] **9D:** Neo4j auth hardening — fixed docker-compose env var passthrough bug, health check validates auth via Cypher query, early RuntimeError on empty password, error detail in health responses

### Phase 10A: Production Quality (Complete ✅)

- [x] Apache-2.0 copyright headers on 132 source files
- [x] Source attribution in chat (SourceRef type, collapsible component with relevance scores)
- [x] Frontend test suite (vitest + @testing-library/react, 68 tests across 5 files)
- [x] CI hardening (6-job pipeline: lint, test, security, lock-sync, frontend, docker)
- [x] Documentation updates (CLAUDE.md, ISSUES.md, tasks/todo.md, README)

### Phase 10B: UX Polish — Model Context Breaks (Complete ✅)

- [x] Model switch dividers (computed at render time between consecutive model changes)
- [x] Always-visible model badges with provider-colored pills (amber=Anthropic, emerald=OpenAI, blue=Google, etc.)
- [x] PROVIDER_COLORS map, findModel() helper, ModelBadge + ModelSwitchDivider components

### Codebase Audit (Complete ✅)

- [x] Dependency purge (sentence-transformers, pandas removed from runtime, ~700MB Docker savings)
- [x] Docker security hardening (non-root `cerid` user, .dockerignore, pinned base images)
- [x] Dead code removal (unused imports, duplicate functions, AI slop comments)
- [x] Logic consolidation (collection name helper, LLM JSON parsing via `llm_parsing.py`, centralized constants)
- [x] Error handling overhaul (silent `except: pass` → logged exceptions, `print()` → `logger`)
- [x] Input validation (Pydantic response models, parameter bounds on API endpoints)
- [x] Accessibility fixes (33 across 14 components — aria-labels, keyboard nav, sr-only text)
- [x] Type safety (tags normalized to `string[]` at API boundary, error cast fixes)
- [x] CI hardening (security scanning with bandit+pip-audit, coverage thresholds, Docker image scanning with Trivy)
- [x] Frontend test expansion (34 → 68 tests: api.ts, model-router.ts, source-attribution, types, use-conversations)

### Dependency Management (Complete ✅)

- [x] Standardize Node version to 22 (.nvmrc, Dockerfile, package.json engines, CI)
- [x] Python lock files with pip-compile (requirements.lock with hashes, --require-hashes in Docker)
- [x] Pin CI tool versions (ruff==0.15.4, bandit==1.9.4, pip-audit==2.10.0, trivy SHA-pinned)
- [x] Pin Docker image tags (neo4j:5.26.21-community, redis:7.4.8-alpine, nginx:1.27-alpine, python:3.11.14-slim, node:22-alpine3.21)
- [x] Dependabot configuration (weekly grouped PRs for pip, npm, github-actions; monthly for Docker)
- [x] Pre-commit hook (lock file sync check in scripts/hooks/pre-commit)
- [x] Cross-service version coupling docs (docs/DEPENDENCY_COUPLING.md)
- [x] CI lock-sync job (regenerates lock file and diffs against committed version)
- [x] Makefile targets (lock-python, lock-python-dev, lock-all, install-hooks, deps-check)

### Modularity Assessment (Complete ✅)

Evidence-based analysis of codebase structure, coupling, and test coverage (2026-02-26).

- [x] File size analysis — flagged 10 Python files over 300 lines, 2 TypeScript files at threshold
- [x] Coupling analysis — `config.py` at 33 importers, `deps.py` at 14, `types.ts` at 36
- [x] Identified 4 structural splits needed (F1–F4 in `docs/ISSUES.md`)
- [x] Identified circular import: `agents/memory.py` → `routers/ingestion.py` (layer violation)
- [x] Identified duplicate function: `find_stale_artifacts` in both `rectify.py` and `maintenance.py`
- [x] Test coverage gap analysis — 5 of 7 agents untested, 2 largest files untested, security middleware untested
- [x] Identified 6 secondary cleanup items (F6 in `docs/ISSUES.md`)

### Phase 10C: Structural Splits + Security Hardening (Complete ✅)

Backend modularity — mechanical refactors with backward-compatible re-export shims, security middleware hardening, and bug fixes.

- [x] **F1:** Extract `ingest_content()`/`ingest_file()` from `routers/ingestion.py` to `services/ingestion.py` — fixes circular import from `agents/memory.py`
- [x] **G8–G11:** Middleware hardening — X-Forwarded-For support, rate limit headers, IP redaction, request ID tracing
- [x] **F2:** Extract MCP tool schemas + `execute_tool()` dispatcher from `routers/mcp_sse.py` to `tools.py`
- [x] Split `config.py` (33 importers) into `config/` package (settings, taxonomy, features)
- [x] Remove duplicate `find_stale_artifacts` in `maintenance.py` (reuse `rectify.py` version)
- [x] Move `audit.log_conversation_metrics()` to `utils/cache.py`
- [x] **F3:** Split `utils/graph.py` (827 lines, 18 functions) into `db/neo4j/` package (schema, artifacts, relationships, taxonomy)
- [x] **F4:** Split `cerid_sync_lib.py` (1346 lines) into `sync/` package (export, import_, manifest, status, _helpers). Fixed 3 latent `collection_name` bugs.
- [x] Split `utils/parsers.py` (875 lines) into `parsers/` sub-package (registry, pdf, office, structured, email, ebook)

### Phase 10D: Test Coverage + CI Hardening (Planned)

Target: cover all security-critical paths, all agents, and the data durability layer.

- [ ] Tests for `middleware/auth.py` + `middleware/rate_limit.py` (security-critical, 0 tests)
- [ ] Tests for `services/ingestion.py` (extracted service layer)
- [ ] Tests for 5 untested agents: query_agent, triage, rectify, audit, maintenance (~2000 lines, 0 tests)
- [ ] Tests for `sync/` package (~1300 lines, 0 tests)
- [ ] Tests for `parsers/` package (875 lines, 0 tests)
- [ ] Tests for `tools.py` (MCP tool registry + dispatch, 0 tests)
- [ ] Expand `db/neo4j/` coverage (9 tests for 18 functions)
- [ ] Frontend component tests (40+ components with 0 tests)
- [ ] G12: Fix pip-audit for transitive deps
- [ ] G13: Add CodeQL SAST workflow
- [ ] G14: Raise coverage threshold 35% → 55%
- [ ] G15: Bundle size monitoring

### Phase 10E–H: Planned

- [ ] **10E:** Smart routing intelligence — token estimator, context replay cost calculation, summarize-and-switch, "start fresh" option
- [ ] **10F:** Interactive audit & taxonomy — audit agent report filter toggles, time range selector, taxonomy-aware hierarchical KB filtering
- [ ] **10G:** Knowledge curation agent — design doc for artifact quality improvement agent
- [ ] **10H:** RAG evaluation — evaluate embedding models, hybrid weights, chunk sizes; artifact preview/generation

See `docs/ISSUES.md` for full backlog (8 open issues across 6 categories).

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
| Container Count | 13 | ✅ 13 |
| Test Coverage | Expanding | 224 total (156 pytest + 68 vitest) |
| CI Pipeline | Green | 6 jobs (lint, test, security, lock-sync, frontend, docker) |

---

## Appendix A: Port Reference

| Port | Service | Container | Image | Purpose |
|------|---------|-----------|-------|---------|
| 3000 | **React GUI** | cerid-web | node:22 → nginx:1.27 | **Primary UI** |
| 3080 | LibreChat | LibreChat | librechat-dev | Legacy Chat UI |
| 8080 | Bifrost | bifrost | bifrost | LLM Gateway |
| 8888 | MCP Server | ai-companion-mcp | python:3.11.14-slim | Knowledge Base API |
| 8501 | Dashboard | ai-companion-dashboard | python:3.11 | Legacy Streamlit Admin |
| 8000 | RAG API | rag_api | librechat-rag-api | Document Processing |
| 8001 | ChromaDB | ai-companion-chroma | chroma:0.5.23 | Vector Store |
| 7474 | Neo4j HTTP | ai-companion-neo4j | neo4j:5.26.21-community | Graph DB Browser |
| 7687 | Neo4j Bolt | ai-companion-neo4j | neo4j:5.26.21-community | Graph DB Protocol |
| 6379 | Redis | ai-companion-redis | redis:7.4.8-alpine | Cache + Audit |
| 5432 | PostgreSQL | vectordb | pgvector | RAG Vector Store |
| 27017 | MongoDB | chat-mongodb | mongo:8.0.17 | LibreChat Data |
| 7700 | Meilisearch | chat-meilisearch | meilisearch:v1.12.3 | Search Index |

## Appendix B: Credentials

| Service | Username | Source |
|---------|----------|--------|
| Neo4j | neo4j | `NEO4J_PASSWORD` in `.env` |
| VectorDB | myuser | Hardcoded in LibreChat stack |
| LibreChat | (user-created) | (user-created) |

All secrets stored in root `.env`, encrypted as `.env.age` with age.

## Appendix C: Key File Paths

| Purpose | Path |
|---------|------|
| Repository Root | `~/cerid-ai/` |
| **MCP Server** | |
| Entry Point | `src/mcp/main.py` (114 lines — routes via routers/) |
| Central Config | `src/mcp/config.py` |
| DB Singletons | `src/mcp/deps.py` |
| Scheduler | `src/mcp/scheduler.py` |
| Routers (10) | `src/mcp/routers/` |
| Agents (7) | `src/mcp/agents/` |
| Utils (15) | `src/mcp/utils/` |
| Middleware | `src/mcp/middleware/` (auth.py, rate_limit.py) |
| Tests | `src/mcp/tests/` (156 tests, 11 files) |
| Dependencies | `src/mcp/requirements.txt` (ranges) |
| Lock File | `src/mcp/requirements.lock` (pip-compile with hashes) |
| Docker Compose | `src/mcp/docker-compose.yml` |
| **React GUI** | |
| Package Config | `src/web/package.json` |
| Vite Config | `src/web/vite.config.ts` |
| Node Version | `src/web/.nvmrc` (22) |
| API Client | `src/web/src/lib/api.ts` |
| Types | `src/web/src/lib/types.ts` |
| Model Router | `src/web/src/lib/model-router.ts` |
| Hooks (8) | `src/web/src/hooks/` |
| Components | `src/web/src/components/` (8 dirs) |
| Tests | `src/web/src/__tests__/` (68 tests, 5 files) |
| Docker Build | `src/web/Dockerfile` (node:22 → nginx:1.27) |
| **Scripts** | |
| Start Script | `scripts/start-cerid.sh` |
| Validation | `scripts/validate-env.sh` |
| Sync CLI | `scripts/cerid-sync.py` |
| Pre-commit Hook | `scripts/hooks/pre-commit` |
| Folder Watcher | `src/mcp/scripts/watch_ingest.py` |
| Obsidian Watcher | `src/mcp/scripts/watch_obsidian.py` |
| CLI Ingest | `src/mcp/scripts/ingest_cli.py` |
| **Infrastructure** | |
| Infrastructure | `stacks/infrastructure/docker-compose.yml` |
| Infrastructure Data | `stacks/infrastructure/data/` |
| Bifrost Config | `stacks/bifrost/data/config.json` |
| LibreChat Config | `stacks/librechat/librechat.yaml` |
| **Docs & Config** | |
| Project Reference | `docs/CERID_AI_PROJECT_REFERENCE.md` |
| Issue Tracker | `docs/ISSUES.md` |
| Dependency Coupling | `docs/DEPENDENCY_COUPLING.md` |
| Root Env | `.env` (encrypted as `.env.age`) |
| Makefile | `Makefile` |
| CI Pipeline | `.github/workflows/ci.yml` |
| Dependabot | `.github/dependabot.yml` |
| User Archive | `~/cerid-archive/` |

---

*Document updated: February 26, 2026*
*Phases 0–9 complete. Phase 10A–10C + codebase audit + dependency management complete. See `docs/ISSUES.md` for open backlog.*
