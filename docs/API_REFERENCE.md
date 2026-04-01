# Cerid AI — API & Operations Reference

> **Extracted from CLAUDE.md** to keep the main developer guide concise.
> For project overview, architecture, and conventions, see [CLAUDE.md](../CLAUDE.md).

---

## MCP Server API (src/mcp/main.py)

**Core endpoints:**
- `GET /health` — Full health check with circuit breaker states (cached 10s)
- `GET /health/live` — Liveness probe (always 200 unless process crashed)
- `GET /health/ready` — Readiness probe (503 when critical deps unreachable)
- `GET /health/status` — Detailed degradation report with circuit breaker states, pipeline providers, feature tier, and per-capability flags (`can_retrieve`, `can_verify`, `can_generate`)
- `GET /collections` — List ChromaDB collections
- `GET /scheduler` — Scheduled job status
- `POST /query` — Query knowledge base (domain, top_k)
- `POST /ingest` — Ingest text content

**Ingestion endpoints:**
- `POST /ingest_file` — Ingest a file with parsing, metadata, optional AI categorization
- `POST /ingest_batch` — Batch ingest multiple text items
- `POST /ingest/feedback` — Submit ingestion quality feedback
- `POST /recategorize` — Move artifact between domains (moves chunks between collections)
- `GET /artifacts` — List ingested artifacts (filter by domain)
- `GET /artifacts/{artifact_id}` — Full artifact detail (Neo4j metadata + reassembled ChromaDB chunks)
- `GET /artifacts/{artifact_id}/related` — Related artifacts via graph expansion
- `POST /artifacts/{artifact_id}/feedback` — Submit quality feedback (inject/dismiss signals)
- `GET /ingest_log` — View audit trail from Redis
- `GET /digest` — Summary of recent KB activity, connections, and health status

**Agent endpoints:**
- `POST /agent/query` — Multi-domain query with LLM reranking, context assembly, optional Self-RAG validation, and unified RAG modes (manual/smart/custom_smart)
- `POST /agent/memory/recall` — Direct memory recall endpoint for manual mode browsing
- `POST /agent/triage` — LangGraph-powered file triage (validate → parse → categorize → chunk)
- `POST /agent/triage/batch` — Batch triage with per-file error recovery
- `POST /agent/rectify` — Knowledge base health checks (duplicates, stale, orphans, distribution)
- `POST /agent/audit` — Audit reports (activity, ingestion stats, costs, query patterns, conversations)
- `POST /agent/maintain` — Maintenance routines (health, stale detection, collection analysis, orphan cleanup)

**Verification & hallucination endpoints:**
- `POST /agent/hallucination` — Check LLM response for hallucinations against KB with 4-level verification fallback (KB-only → external cross-model/web-search for unverified/uncertain claims)
- `GET /agent/hallucination/{conversation_id}` — Retrieve stored hallucination report
- `POST /agent/hallucination/feedback` — Record user feedback on a verification claim (correct/incorrect)
- `POST /agent/verify-stream` — SSE streaming truth verification with keepalive heartbeats, supports expert mode (Grok 4) and anti-circularity via `source_artifact_ids`
- `POST /verification/save` — Persist verification report to Neo4j
- `GET /verification/{conversation_id}` — Retrieve saved verification report
- `POST /agent/memory/extract` — Extract and store memories from conversation
- `POST /agent/memory/archive` — Archive old conversation memories
- `POST /agent/curate` — Score artifact quality across the KB- `POST /agent/curate/estimate` — Estimate synopsis generation cost before running

**Trading agent KB enrichment endpoints (gated by `CERID_TRADING_ENABLED`):**
- `POST /agent/trading/signal` — Enrich a trading signal with KB context
- `POST /agent/trading/herd-detect` — Detect herd behavior via correlation graph violations
- `POST /agent/trading/kelly-size` — Query historical CV_edge for Kelly criterion position sizing
- `POST /agent/trading/cascade-confirm` — Confirm cascade liquidation pattern against historical data
- `POST /agent/trading/longshot-surface` — Query stored calibration surface for longshot probability estimates

**Auth endpoints (conditional on `CERID_MULTI_USER=true`):**
- `POST /auth/register` — Create new user account (returns JWT tokens)
- `POST /auth/login` — Authenticate with email/password (returns JWT tokens)
- `POST /auth/refresh` — Refresh access token using refresh token
- `POST /auth/logout` — Revoke refresh token (Redis blacklist)
- `GET /auth/me` — Get current user profile (requires Bearer token)
- `PUT /auth/me/api-key` — Generate per-user API key (Fernet-encrypted in Neo4j)
- `DELETE /auth/me/api-key` — Revoke per-user API key
- `GET /auth/me/api-key/status` — Check if user has an active API key
- `GET /auth/me/usage` — Get per-user usage metrics from Redis

**Chat endpoints:**
- `POST /chat/stream` — Stream chat completion directly via OpenRouter proxy (SSE)
- `POST /chat/compress` — Compress conversation history to fit target token budget

**Sync endpoints:**
- `POST /sync/export` — Trigger incremental or full export to sync directory
- `POST /sync/import` — Trigger merge import from sync directory
- `GET /sync/status` — Compare local DB counts against sync directory manifest

**Settings & memories:**
- `GET /settings` — Server configuration and feature flags (includes `enable_self_rag`)
- `PATCH /settings` — Partial settings update (supports `enable_self_rag`)
- `GET /memories` — List/filter memories (type, conversation_id, limit, offset)
- `POST /memories/extract` — Extract memories from text (standalone endpoint)
- `PATCH /memories/{id}` — Update memory summary
- `DELETE /memories/{id}` — Delete a memory

**File upload:**
- `POST /upload` — Upload file with optional domain, sub_category, tags, categorize_mode (50MB max)
- `GET /upload/supported` — List supported file extensions
- `GET /archive/files` — List files in the archive directory

**Taxonomy & tags:**
- `GET /taxonomy` — Get full taxonomy tree
- `POST /taxonomy/domain` — Add a new domain
- `POST /taxonomy/subcategory` — Add a new sub-category
- `POST /taxonomy/artifact` — Assign taxonomy to an artifact
- `GET /tags` — List all tags
- `GET /tags/suggest` — Tag suggestions for typeahead
- `POST /tags/merge` — Merge duplicate tags

**User state:**
- `GET /user-state` — Current user state
- `GET /user-state/conversations` — List conversations
- `GET /user-state/conversations/{conv_id}` — Get single conversation
- `POST /user-state/conversations` — Save conversation
- `POST /user-state/conversations/bulk` — Bulk save conversations
- `DELETE /user-state/conversations/{conv_id}` — Delete conversation
- `PATCH /user-state/preferences` — Update user preferences

**KB admin:**
- `GET /admin/kb/capabilities` — Parser capabilities report
- `GET /admin/kb/stats` — KB statistics (artifact counts, chunk counts, domain distribution)
- `POST /admin/artifacts/{artifact_id}/reingest` — Re-ingest an existing artifact
- `POST /admin/kb/rebuild-index` — Rebuild search index
- `POST /admin/kb/rescore` — Rescore artifact quality
- `POST /admin/kb/regenerate-summaries` — Regenerate artifact summaries
- `POST /admin/kb/clear-domain/{domain}` — Clear all artifacts in a domain
- `DELETE /admin/artifacts/{artifact_id}` — Delete a specific artifact

**Scanner (folder watcher):**
- `POST /admin/scan` — Start async folder scan (returns scan_id)
- `GET /admin/scan/state` — Persistent scan state from Redis
- `GET /admin/scan/preview` — Preview files that would be scanned (GET with query params)
- `POST /admin/scan/preview` — Preview files (POST with JSON body, enhanced: skipped breakdown, storage estimate)
- `GET /admin/scan/{scan_id}` — Get scan progress/result
- `GET /admin/scan/{scan_id}/stream` — SSE stream of real-time scan progress with ETA
- `POST /admin/scan/{scan_id}/pause` — Pause an active scan
- `POST /admin/scan/{scan_id}/resume` — Resume a paused scan
- `POST /admin/scan/{scan_id}/cancel` — Cancel an active scan
- `POST /admin/scan/reset` — Clear all persistent scan state

**Settings management:**
- `GET /settings` — Current server settings
- `PATCH /settings` — Update runtime settings (auto_inject_threshold range: 0.0–1.0)
- `POST /settings/tier` — Runtime tier override (community/pro/enterprise, recomputes feature flags)

**Eval (retrieval evaluation):**
- `POST /api/eval/run` — Run evaluation benchmark
- `GET /api/eval/benchmarks` — List benchmark files

**MCP protocol:**
- `GET /mcp/sse` — SSE stream (MCP protocol, JSON-RPC 2.0)
- `POST /mcp/sse` — SSE stream (POST variant)
- `POST /mcp/messages?sessionId=X` — JSON-RPC handler

### MCP Tools (26 total)

**Core tools (19):**
- `pkb_query` — Single-domain query
- `pkb_ingest` — Ingest raw text
- `pkb_ingest_file` — Ingest a file with parsing and metadata
- `pkb_health` — Service health check
- `pkb_collections` — List ChromaDB collections
- `pkb_agent_query` — Multi-domain query with LLM reranking and RAG modes (manual/smart/custom_smart)
- `pkb_artifacts` — List/filter ingested artifacts
- `pkb_recategorize` — Move artifact between domains
- `pkb_triage` — LangGraph-powered file triage
- `pkb_rectify` — Knowledge base health checks and auto-fix
- `pkb_audit` — Audit reports (activity, ingestion, costs, queries, conversations)
- `pkb_maintain` — Maintenance routines (health, stale, collections, orphans)
- `pkb_curate` — Score artifact quality across the knowledge base- `pkb_digest` — Summary of recent KB activity, connections, and health status
- `pkb_scheduler_status` — Get status of scheduled maintenance jobs
- `pkb_check_hallucinations` — Verify LLM claims against KB- `pkb_memory_extract` — Extract memories from conversations- `pkb_memory_archive` — Archive old conversation memories- `pkb_ingest_multimodal` — Multi-modal ingestion (OCR, audio, vision)
**Trading tools (5, gated by `CERID_TRADING_ENABLED`):**
- `pkb_trading_signal` — Trading signal enrichment via KB
- `pkb_herd_detect` — Herd behavior detection
- `pkb_kelly_size` — Kelly criterion position sizing
- `pkb_cascade_confirm` — Cascade liquidation confirmation
- `pkb_longshot_surface` — Longshot opportunity surfacing

**Additional tools:**
- `pkb_web_search` — Agentic web search with verification- `pkb_memory_recall` — Context-aware memory retrieval with decay scoring
### SDK Router (`/sdk/v1/`) — Stable External API

Versioned facade for cerid-series consumers (trading-agent, future projects). Delegates to existing agent endpoints but provides a stable contract that survives internal refactoring.

- `POST /sdk/v1/query` — KB query with reranking and RAG modes (delegates to `/agent/query`, supports `rag_mode` and `source_config`)
- `POST /sdk/v1/hallucination` — Hallucination detection (delegates to `/agent/hallucination`)
- `POST /sdk/v1/memory/extract` — Memory extraction (delegates to `/agent/memory/extract`)
- `GET /sdk/v1/health` — Health check with `version`, `services`, `features` (subset of feature toggles relevant to consumers), and `internal_llm` (current internal LLM provider and model)

**SDK Trading endpoints (gated by `CERID_TRADING_ENABLED`):**
- `POST /sdk/v1/trading/signal` — Trading signal enrichment via KB (delegates to `/agent/trading/signal`)
- `POST /sdk/v1/trading/herd-detect` — Herd behavior detection (delegates to `/agent/trading/herd-detect`)
- `POST /sdk/v1/trading/kelly-size` — Kelly criterion position sizing (delegates to `/agent/trading/kelly-size`)
- `POST /sdk/v1/trading/cascade-confirm` — Cascade liquidation confirmation (delegates to `/agent/trading/cascade-confirm`)
- `POST /sdk/v1/trading/longshot-surface` — Longshot opportunity surfacing (delegates to `/agent/trading/longshot-surface`)

**Client identification:** Send `X-Client-ID` header to get per-client rate limiting. Each client ID gets an independent rate budget:

| Client ID | `/agent/` & `/sdk/` | `/ingest` | `/recategorize` |
|-----------|---------------------|-----------|-----------------|
| `gui` (default) | 20 req/min | 10 req/min | 10 req/min |
| `trading-agent` | 80 req/min | — | — |
| `_default` (unknown) | 10 req/min | 5 req/min | 5 req/min |
| `boardroom-agent` | 60 req/min | — | — |

Configured in `CONSUMER_REGISTRY` in `config/settings.py`. Rate limits are auto-derived from the registry.

---

## Ingestion Pipeline

**File ingestion flow:** Parse file → Dedup check (SHA-256) → Extract metadata → AI categorize (optional) → Chunk → Batch store in ChromaDB + Neo4j + Redis

### Categorization Tiers

- `manual` — Domain from folder name only, no AI
- `smart` — Free model (Llama 3.3 70B Instruct via OpenRouter) for classification
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
- Parallel retrieval across all configured ChromaDB collections
- Deduplication by (artifact_id + chunk_index), keeping highest relevance
- Cross-encoder reranking (ONNX) — blends 60% reranker score + 40% embedding score
- Token budget enforcement (14k character limit)
- Source attribution with confidence scoring

**Usage:**
```bash
curl -X POST http://localhost:8888/agent/query \
  -H "Content-Type: application/json" \
  -d '{"query": "tax deductions", "domains": ["finance", "general"], "top_k": 5}'

# With Self-RAG validation (requires response_text):
curl -X POST http://localhost:8888/agent/query \
  -H "Content-Type: application/json" \
  -d '{"query": "tax deductions", "response_text": "LLM response to validate...", "enable_self_rag": true}'
```

**Self-RAG fields (optional):**
- `response_text` — LLM response text to validate against KB (triggers Self-RAG when provided)
- `model` — Generating model name (for metadata tracking)
- `enable_self_rag` — Override server-side `ENABLE_SELF_RAG` toggle (null = use server config)

**RAG mode fields (optional):**
- `rag_mode` — `"manual"` (KB only, default), `"smart"` (KB + memory + external in parallel), or `"custom_smart"` (Pro tier, configurable weights/toggles)
- `source_config` — Custom Smart weights/toggles (Pro tier only). Keys: `kb_enabled`, `memory_enabled`, `external_enabled`, `kb_weight`, `memory_weight`, `external_weight`, `memory_types`

When `rag_mode` is `"smart"` or `"custom_smart"`, the response includes:
- `source_breakdown` — `{kb: [...], memory: [...], external: [...]}` with per-source results
- `rag_mode` — The active mode used for the query
- Memory results are appended to `context` under a `[Memory Context]` header

**Key Functions:**
- `multi_domain_query()` — Parallel ChromaDB queries across domains
- `deduplicate_results()` — Remove duplicate chunks
- `rerank_results()` — Cross-encoder (ONNX) relevance reranking (falls back to embedding sort)
- `assemble_context()` — Build context within token budget
- `agent_query()` — Main orchestration function
- `orchestrated_query()` — Unified orchestrator wrapping agent_query + memory recall + external separation (in `agents/retrieval_orchestrator.py`)
- `self_rag_enhance()` — Iterative claim verification and targeted retrieval refinement (in `agents/self_rag.py`)

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

LangGraph >=0.3.0 (pulls langchain-core transitively)

---

## Adding a New Domain

1. Edit `src/mcp/config/settings.py` → add to `DOMAINS` list
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
- `src/mcp/config/settings.py` — Domains, extensions, categorization tiers, DB URLs
- `stacks/bifrost/config.yaml` — Intent classification, model routing, budget

**Key env vars (docker-compose.yml):**
- `CATEGORIZE_MODE=smart` — Default tier (manual/smart/pro)
- `BIFROST_URL=http://bifrost:8080/v1`
- `ARCHIVE_PATH=/archive` — Container-side mount point
- `INTERNAL_LLM_PROVIDER` — Internal LLM provider for pipeline tasks (`ollama` or `bifrost`, default: `bifrost`)
- `INTERNAL_LLM_MODEL` — Internal LLM model ID (empty = auto-selected; set during Ollama setup wizard or via `OLLAMA_DEFAULT_MODEL`)
- `OLLAMA_DEFAULT_MODEL` — Default Ollama model (auto-recommended based on hardware if not set; fallback: `llama3.2:3b`)

---

## Verification

```bash
curl http://localhost:8888/health              # full health check (cached 10s)
curl http://localhost:8888/health/live          # liveness probe (always 200)
curl http://localhost:8888/health/ready         # readiness probe (503 if deps down)
curl http://localhost:8888/health/status        # degradation report + pipeline providers
curl http://localhost:8888/collections
curl http://localhost:8888/artifacts
curl http://localhost:8888/ingest_log?limit=10

# With API key auth enabled (set CERID_API_KEY env var):
curl http://localhost:8888/artifacts \
  -H "X-API-Key: $CERID_API_KEY"
# Exempt from auth: /health, /health/*, /api/v1/health, /, /docs, /openapi.json, /redoc, /mcp/*
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

**REST API:**
```bash
# Trigger incremental export (auto-reads last_exported_at from manifest)
curl -X POST http://localhost:8888/sync/export \
  -H "Content-Type: application/json" \
  -d '{}'

# Export specific domains since a timestamp
curl -X POST http://localhost:8888/sync/export \
  -H "Content-Type: application/json" \
  -d '{"since": "2026-03-01T00:00:00Z", "domains": ["coding", "finance"]}'

# Import with conflict strategy
curl -X POST http://localhost:8888/sync/import \
  -H "Content-Type: application/json" \
  -d '{"conflict_strategy": "remote_wins"}'

# Check sync status
curl http://localhost:8888/sync/status
```

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

---

## Additional Endpoints

### Setup & Configuration
- `GET /setup/status` — Check if system is configured
- `POST /setup/validate-key` — Test an API key
- `POST /setup/configure` — Apply initial configuration
- `GET /setup/health` — Service health dashboard

### Providers (BYOK)
- `GET /providers` — List configured LLM providers
- `GET /providers/configured` — List providers with active API keys
- `GET /providers/internal` — Current internal LLM provider
- `PUT /providers/internal` — Update internal LLM provider at runtime
- `GET /providers/credits` — OpenRouter credit balance
- `GET /providers/routing` — Current model routing configuration
- `GET /providers/config` — Provider configuration
- `PUT /providers/config` — Update provider configuration
- `GET /providers/{name}` — Get specific provider details
- `POST /providers/{name}/validate` — Validate provider API key

### Model Assignments
- `GET /models/assignments` — Current model assignments per task
- `PUT /models/assignments` — Update model assignments
- `GET /models/available` — Available models from configured providers

### Automations
- `GET /automations` — List user automations
- `POST /automations` — Create automation
- `GET /automations/presets` — List automation presets
- `GET /automations/{id}` — Get automation details
- `PUT /automations/{id}` — Update automation
- `DELETE /automations/{id}` — Delete automation
- `POST /automations/{id}/enable` — Enable automation
- `POST /automations/{id}/disable` — Disable automation
- `POST /automations/{id}/run` — Manual run
- `GET /automations/{id}/history` — Run history

### A2A Protocol
- `GET /.well-known/agent.json` — Agent Card
- `POST /a2a/tasks` — Create task
- `GET /a2a/tasks/{id}` — Task status
- `POST /a2a/tasks/{id}/cancel` — Cancel a running task
- `GET /a2a/tasks/{id}/history` — Task execution history

### Observability
- `GET /observability/metrics` — Aggregated metrics
- `GET /observability/metrics/{name}` — Time-series data for a specific metric
- `GET /observability/health-score` — Composite health score (0-100)
- `GET /observability/cost` — LLM cost breakdown
- `GET /observability/quality` — Retrieval quality metrics

### Plugins
- `GET /plugins` — List plugins with status
- `GET /plugins/{name}` — Get plugin details
- `POST /plugins/{name}/enable` — Enable plugin
- `POST /plugins/{name}/disable` — Disable plugin
- `GET /plugins/{name}/config` — Get plugin configuration
- `PUT /plugins/{name}/config` — Update plugin configuration
- `POST /plugins/scan` — Scan for new plugins

### Workflows
- `GET /workflows` — List workflows
- `POST /workflows` — Create workflow
- `GET /workflows/templates` — Predefined templates
- `GET /workflows/{id}` — Get workflow details
- `PUT /workflows/{id}` — Update workflow
- `DELETE /workflows/{id}` — Delete workflow
- `POST /workflows/{id}/run` — Execute workflow
- `GET /workflows/{id}/runs` — List workflow runs

### Data Sources (External Knowledge)
- `GET /data-sources` — List registered external data sources with enabled status
- `POST /data-sources/{name}/enable` — Enable an external data source
- `POST /data-sources/{name}/disable` — Disable an external data source

### Model Registry Validation
- `GET /providers/models/validate` — Validate model registry against OpenRouter (checks availability of all 20+ registered models)

### Ollama (Local LLM Add-On)

**Proxy endpoints** (require `OLLAMA_ENABLED=true`):
- `GET /ollama/models` — List installed Ollama models
- `POST /ollama/chat` — Chat with local model (streaming + non-streaming)
- `POST /ollama/pull` — Pull/download a model (streaming progress via NDJSON)

**Configuration & management (under `/providers/` router):**
- `GET /providers/ollama/status` — Ollama status: `{ enabled, url, reachable, models[], default_model, default_model_installed }`
- `GET /providers/ollama/recommendations` — Hardware-aware model recommendations: `{ hardware: { ram_gb, cpu, gpu, platform }, models: [{ id, name, origin, size_gb, min_ram_gb, description, strengths, tier, compatible, recommended }], recommended }`
- `POST /providers/ollama/enable` — Enable Ollama as internal LLM provider. Optional body: `{ "model": "llama3.1:8b" }` to override the default model. Checks connectivity, updates runtime config.
- `POST /providers/ollama/disable` — Disable Ollama, fall back to OpenRouter

**Model selection:** Configurable via `OLLAMA_DEFAULT_MODEL` env var or the setup wizard UI. The setup wizard detects system RAM/CPU/GPU and recommends from 3 tiers:

| Tier | Model | Size | Min RAM | Origin |
|------|-------|------|---------|--------|
| Lightweight | Llama 3.2 3B | 2.0GB | 8GB | Meta (US) |
| Balanced | Llama 3.1 8B | 4.7GB | 16GB | Meta (US) |
| Performance | Phi-4 14B | 9.1GB | 32GB | Microsoft (US) |

Model can be changed post-setup via Settings UI → Ollama → Change button.

**Pipeline tasks routed to internal LLM:**
- Claim extraction (verification)
- Query decomposition (multi-part queries)
- Memory conflict resolution (ADD/UPDATE/NOOP classification)
- Response topic extraction (disambiguation context)
- Reranking (cross-encoder ONNX default, LLM reranking fallback)

**Limitations:** The local model handles classification, extraction, and routing. It does NOT handle: user-facing chat, verification fact-checking, synopsis generation, or web search — those always use OpenRouter.

**Hardware detection:** `scripts/detect-gpu.sh` auto-detects NVIDIA GPU, AMD ROCm, macOS Metal, or CPU fallback. The `/providers/ollama/recommendations` endpoint detects RAM, CPU, and GPU at runtime for model recommendations. Docker Compose profile `ollama` starts the container with GPU passthrough when available. macOS Apple Silicon: runs natively for Metal acceleration.

**Cost:** $0 for all internal LLM calls when using Ollama. Falls back to OpenRouter (paid) when Ollama is unavailable.

### Billing & Licensing
- `POST /billing/create-checkout` — Create Stripe Checkout session for Pro tier upgrade
- `POST /billing/webhook` — Stripe webhook handler (checkout.session.completed, invoice.payment_succeeded, customer.subscription.deleted)
- `GET /billing/status` — Current license/subscription status
- `POST /billing/validate-key` — Validate a manually-entered license key for offline Pro activation

### Model Updates
- `GET /models/updates` — New and deprecated models since last catalog check (populated by scheduled job)

### Agent Activity
- `GET /agents/activity/stream` — SSE stream of real-time agent activity events (exempted from API key auth)

### Private Mode
- `POST /settings/private-mode` — Enable private mode with security level (1-4)
- `DELETE /settings/private-mode` — Disable private mode, optionally clear Redis cache
- `GET /settings/private-mode` — Get current private mode status

### Watched Folders
- `POST /watched-folders` — Create watched folder config
- `GET /watched-folders` — List watched folders
- `PATCH /watched-folders/{id}` — Update folder config
- `DELETE /watched-folders/{id}` — Remove watched folder

#### Boardroom Endpoints

Stable endpoints for the cerid-boardroom agent (`X-Client-ID: boardroom-agent`):

- `GET /sdk/v1/ops/health` — Boardroom-specific health check (tier, domains)
- `POST /sdk/v1/ops/competitive-scan` — Competitive landscape analysis against KB
- `POST /sdk/v1/ops/strategy-brief` — Strategy brief generation from KB context
- `GET /sdk/v1/ops/governance-log` — Query boardroom audit trail for agent actions and approvals

### Trading Proxy (gated by `CERID_TRADING_ENABLED`)

GUI proxy routes to the external trading agent at `TRADING_AGENT_URL`:
- `GET /api/trading/sessions` — List trading sessions
- `GET /api/trading/sessions/{name}/portfolio` — Session portfolio
- `GET /api/trading/sessions/{name}/positions` — Session positions
- `GET /api/trading/sessions/{name}/signals` — Session signals
- `GET /api/trading/aggregate/portfolio` — Aggregate portfolio across sessions
- `GET /api/trading/market-data` — Market data feed

### Web Search
- Tool: `pkb_web_search` — Search web with verification

### Memory Recall
- Tool: `pkb_memory_recall` — Context-aware memory retrieval with salience-aware decay scoring (6 memory types: empirical, decision, preference, project_context, temporal, conversational)
