# Cerid AI — API & Operations Reference

> **Extracted from CLAUDE.md** to keep the main developer guide concise.
> For project overview, architecture, and conventions, see [CLAUDE.md](../CLAUDE.md).

---

## MCP Server API (src/mcp/main.py)

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

### MCP Tools (15 total)

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

---

## Ingestion Pipeline

**File ingestion flow:** Parse file → Dedup check (SHA-256) → Extract metadata → AI categorize (optional) → Chunk → Batch store in ChromaDB + Neo4j + Redis

### Categorization Tiers

- `manual` — Domain from folder name only, no AI
- `smart` — Free model (Llama 3.1 via Bifrost) for classification
- `pro` — Premium model (Claude Sonnet via Bifrost)

AI calls are token-efficient: only first ~1500 chars sent for classification. Response format enforced as JSON.

### Supported File Types

PDF (structure-aware via pdfplumber — tables extracted as Markdown), DOCX (with tables), XLSX, CSV, HTML (tag-stripped), 30+ text/code/config formats. Binary files auto-detected and rejected.

### Watcher (host process)

```bash
python src/mcp/scripts/watch_ingest.py [--mode smart|pro|manual]
```

### CLI Batch Ingest (concurrent)

```bash
python src/mcp/scripts/ingest_cli.py --dir ~/cerid-archive/ [--mode smart] [--domain coding] [--workers 4]
```

### Obsidian Vault Watcher (host process)

```bash
python src/mcp/scripts/watch_obsidian.py --vault ~/Obsidian/MyVault [--domain personal] [--mode smart]
```

Monitors `.md` files only. Uses `/ingest` (text endpoint) since the vault isn't Docker-mounted. Higher debounce (5s) for Obsidian auto-save. Skips `.obsidian/`, `.trash/`, and files <10 bytes.

### Archive Folder Structure

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

---

## Agent Workflows

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

### Agent Dependencies

LangGraph >=0.3.0, langchain-core, langchain-openai

---

## Streamlit Dashboard (Legacy)

Admin and monitoring UI at `http://localhost:8501` (container: `ai-companion-dashboard`).

**Panes:**
- **Overview** — System health, domain distribution charts, collection listing
- **Artifacts** — Browse/filter artifacts by domain, recategorize from UI
- **Query** — Interactive multi-domain search with result visualization
- **Audit** — Activity timeline, ingestion stats, cost estimates, query patterns
- **Maintenance** — Health checks, stale detection, collection analysis, orphan cleanup

**Stack:** Streamlit + Plotly + Pandas, communicates with MCP server REST API.

**Start:** `cd src/mcp && docker compose up -d` (dashboard service included via `depends_on: mcp-server`)

---

## Adding a New Domain

1. Edit `src/mcp/config.py` → add to `DOMAINS` list
2. Create folder: `mkdir ~/cerid-archive/<new_domain>`
3. Rebuild: `cd src/mcp && docker compose up -d --build`

---

## Recategorizing Artifacts

```bash
# List artifacts in a domain
curl http://localhost:8888/artifacts?domain=coding

# Move to another domain
curl -X POST http://localhost:8888/recategorize \
  -H "Content-Type: application/json" \
  -d '{"artifact_id": "...", "new_domain": "projects"}'
```

---

## Configuration

- `.env` (repo root) — All secrets. Encrypted as `.env.age`. Never committed in plaintext.
- `src/mcp/config.py` — Domains, extensions, categorization tiers, DB URLs
- `stacks/bifrost/config.yaml` — Intent classification, model routing, budget
- `stacks/librechat/librechat.yaml` — MCP servers, endpoints, model list

**Key env vars (docker-compose.yml):**
- `CATEGORIZE_MODE=smart` — Default tier (manual/smart/pro)
- `BIFROST_URL=http://bifrost:8080/v1`
- `ARCHIVE_PATH=/archive` — Container-side mount point

---

## Verification

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

---

## Knowledge Base Sync

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

---

## Dependency Management

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

---

## Extensibility

- **Parsers:** Registry pattern in `utils/parsers.py`. PDF uses pdfplumber (structure-aware). Add Docling later for OCR via `@register_parser`.
- **Domains:** Add to `config.DOMAINS` list. Neo4j nodes auto-created.
- **File types:** Add to `config.SUPPORTED_EXTENSIONS` + register parser.
