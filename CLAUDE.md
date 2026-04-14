# CLAUDE.md - Cerid AI

> **Extends:** `sunrunnerfire/dotfiles` — all global workflow orchestration, core principles,
> commit policy, and task management rules apply here. This file adds only project-specific context.

## ⚠️ Two-Repo Architecture — Read Before ANY Sync

This is the **internal** canonical development repo. The **public** repo (`Cerid-AI/cerid-ai`) is a lean Apache-2.0 distribution. They are NOT mirrors.

**Use the automated sync script** — never copy files manually between repos:

```bash
python3 scripts/sync-repos.py to-public [--dry-run]    # Internal → Public
python3 scripts/sync-repos.py from-public [--dry-run]   # Public → Internal
python3 scripts/sync-repos.py validate                  # Check for leaks
```

The script reads `.sync-manifest.yaml` which declares internal-only files, mixed files (truncated at hook markers), and forbidden strings. Internal-only code lives in `*_internal.py` files that are never copied to public. Mixed files have `# -- Internal ...` hook markers — everything below the marker is stripped for public.

**Key architecture (as of 2026-04-10):**
- Internal-only code extracted to `*_internal.py` companion files (7 Python + 1 TypeScript)
- `app/main_internal.py` bootstraps all internal features: `extend_settings()` → `extend_taxonomy()` → register routers
- NLI entailment service at `core/utils/nli.py` (ONNX, <10ms) powers verification, Self-RAG, RAGAS, and RAG pipeline
- Source authority: chat transcripts discounted 0.35x, verified memories boosted 0.25x, standard memories 0.15x (tiered in `patterns.py:memory_authority_boost()`)
- External sources (Wikipedia, DuckDuckGo, etc.) run in parallel with KB queries via source-aware query construction (`adapt_query()`/`is_relevant()` per source)
- Verified-fact-to-memory promotion: `core/agents/verified_memory.py` auto-promotes high-confidence verified claims to empirical `:Memory` nodes with `VERIFIED_BY` provenance
- CRAG quality gate in `query_agent.py`: supplements with external sources when top KB relevance < `RETRIEVAL_QUALITY_THRESHOLD` (0.4)

**Files that DIFFER between repos (internal has MORE):**

| File | Internal-only content |
|------|----------------------|
| `config/settings.py` | `CERID_TRADING_ENABLED`, `TRADING_AGENT_URL`, `CERID_BOARDROOM_ENABLED`, trading/boardroom consumer registry entries |
| `config/taxonomy.py` | 6 boardroom domains (strategy, competitive_intel, marketing, advertising, operations, audit) + affinities + tag vocabularies |
| `app/routers/agents.py` | 5 trading endpoints (`/agent/trading/*`), trading model imports |
| `app/routers/sdk.py` | 5 trading SDK endpoints, 4 boardroom ops endpoints, trading/boardroom response models |
| `app/main.py` | alerts, migration, ws_sync, trading_proxy, eval router imports + registrations; trading proxy shutdown; billing router |
| `app/tools.py` | 5 trading MCP tools (`pkb_trading_signal` etc.) |
| `app/scheduler.py` | 3 trading scheduler jobs |
| `app/models/sdk.py` | 5 trading SDK response models |
| `.github/workflows/ci.yml` | `frontend-desktop` job; `docker` needs `frontend-desktop` |
| `CLAUDE.md` | Full architecture docs, sync protocol, session learnings |

**If a sync breaks CI** with `Module has no attribute "CERID_TRADING_ENABLED"` or similar — the public `settings.py` was copied over the internal one. Restore the internal-only config from git history.

## Session Start (Required)

Before beginning any development work in this repo, if not already done in this session:

1. Read global framework context from the dotfiles repo using the GitHub MCP tool:
   - `sunrunnerfire/dotfiles` → `CLAUDE.md` (workflow principles, commit policy)
   - `sunrunnerfire/dotfiles` → `CLAUDE_CODE_SETUP.md` (plugins, MCP servers)
2. Never add AI attribution to commits — see dotfiles `CLAUDE.md` commit policy.
3. Read the sync warning above before ANY cross-repo operation.

---

## Project Overview

Cerid AI is a self-hosted, privacy-first Personal AI Knowledge Companion. It unifies multi-domain knowledge bases (code, finance, projects, artifacts) into a context-aware LLM interface with RAG-powered retrieval and intelligent agents. Knowledge base stays local; LLM API calls send query context to the configured provider. Optional cloud sync (Dropbox) for cross-machine settings/conversations, encrypted when CERID_ENCRYPTION_KEY is set.

**Status:** All phases through 50 complete + Phase C core extraction + Leapfrog Roadmap (6 features). 2370+ Python tests, 705+ frontend tests. 9 agents, 27 MCP tools (19 core + 5 trading + pkb_web_search + pkb_memory_recall + pkb_ingest_multimodal), hybrid BM25s+vector search with semantic chunking, cross-encoder reranking (ONNX, three modes), switchable client-side embeddings (Matryoshka, zero-migration), contextual chunking (LLM-generated situational summaries), advanced RAG pipeline (adaptive retrieval gate, query decomposition with parallel sub-retrieval, MMR diversity reordering, intelligent context assembly with facet coverage, ColBERT-inspired late interaction scoring, semantic query cache with quantized int8 embeddings), circuit breakers on all Bifrost + Neo4j calls, shared Bifrost call utility with singleton httpx connection pool, distributed request tracing, adaptive quality feedback, per-domain tag vocabulary with typeahead UI, improved synopsis generation, streaming verification with 4 claim types (evasion/citation/recency/ignorance) + interactive inline verification (ClaimOverlay popovers, footnote markers, source navigation) + expert verification mode (Grok 4) + per-message verification selection, Self-RAG validation loop, smart routing (direct-to-OpenRouter chat proxy, capability-based model scoring, three-way routing mode, proactive model switch on ignorance detection), context-aware chat (corrections, token-budget KB injection, semantic dedup), advanced response formatting (15 MD component overrides, collapsible code blocks, TOC for long responses), right-click context menus on toolbar icons, drag-drop ingestion on KB pane + chat input + artifact drag-to-chat, infrastructure settings + search tuning sliders, KB admin endpoints (rebuild/rescore/clear/delete/stats) + Settings GUI, incremental knowledge sync with tombstones and conflict resolution, sync GUI with export/import/status dashboard, archive storage mode, mypy type checking in CI, React GUI with iPad/tablet responsive touch UX, LAN access with robust multi-interface IP detection and stale-IP auto-fix, pre-flight validation (port conflicts, env vars, disk space), post-startup reachability checks, guided `setup.sh` installer, configurable port overrides (`CERID_PORT_*`), optional Caddy HTTPS gateway and Cloudflare Tunnel for demos, multi-user auth foundations (opt-in JWT, tenant context, per-user API keys, usage metering), marketing website at cerid.ai (Next.js 16 + Vercel), brand identity (teal accent color system), Simple/Advanced mode (progressive disclosure), settings reorganization (3-tab layout with user experience presets), first-run onboarding dialog, marketing site refreshed (changelog, SEO, animations), MCP server performance optimization (lightweight verification retrieval, connection pooling, parallel graph expansion, deferred cache persistence, startup pre-warming), source-aware external query construction (per-source adapt_query/is_relevant with intent-based routing), CRAG retrieval quality gate (supplements with external sources when KB results are poor), verified-fact-to-memory promotion pipeline (high-confidence claims auto-promote to empirical memories with VERIFIED_BY provenance), tiered memory authority boost (0.05-0.25 based on verification status), refresh-on-read memory decay (Ebbinghaus rehearsal pattern), NLI consolidation guard (prevents semantic drift during memory merges), fact-relationship verification (temporal/entity/specificity alignment), graph-guided verification (Neo4j relationship structure as evidence), authoritative external verification (LLM synthesizes from external data rather than parametric memory). Infrastructure security hardened (Redis auth, port binding, resource limits, security headers, nginx hardening). CI/CD 8-job pipeline with timeouts. See [`docs/COMPLETED_PHASES.md`](docs/COMPLETED_PHASES.md) for history.

**Next:** See [`tasks/todo.md`](tasks/todo.md).

**Open issues:** [`docs/ISSUES.md`](docs/ISSUES.md) (0 open — 160+ resolved).

## Architecture

Microservices architecture with Docker Compose orchestration on a shared `llm-network` bridge network. Services communicate by container name.

### Services

| Service | Port | Stack Path | Tech |
|---------|------|------------|------|
| MCP Server (API) | 8888 | `src/mcp/` | FastAPI / Python 3.11 |
| Bifrost (LLM Gateway) | 8080 | `stacks/bifrost/` | Semantic intent routing |
| ChromaDB (Vectors) | 8001 | `stacks/infrastructure/` | Vector DB |
| Neo4j (Graph) | 7474/7687 | `stacks/infrastructure/` | Graph DB |
| Redis (Cache) | 6379 | `stacks/infrastructure/` | Cache + audit log |
| React GUI | 3000 | `src/web/` | React 19 + Vite + nginx |
| Marketing Site | cerid.ai (3001 dev) | `packages/marketing/` | Next.js 16 + Vercel |
| Ollama (Optional) | 11434 | External / Docker | Local LLM for pipeline tasks |

### Key Data Flow

```
User → React GUI (3000) → Bifrost (8080, optional) → OpenRouter → LLM Provider
                        → MCP Server (8888) → ChromaDB/Neo4j (RAG context)

File Ingestion:
~/cerid-archive/ → Watcher → POST /ingest_file → Parse → Dedup → Chunk → ChromaDB + Neo4j + Redis
```

React GUI talks to Bifrost via nginx proxy (`/api/bifrost/`) and to MCP directly (CORS `*`). Bifrost classifies intent (coding/research/simple/general) and routes to the appropriate model.

## Directory Structure

```
├── CLAUDE.md                    # This file
├── docker-compose.yml           # Unified root compose (replaces 4-step startup)
├── .env.age / .env.example      # Encrypted secrets / template
├── Makefile                     # lock-python, install-hooks, deps-check
├── scripts/                     # start-cerid.sh, validate-env.sh, cerid-sync.py, env-(un)lock.sh
├── docs/                        # API_REFERENCE.md, ISSUES.md, OPERATIONS.md, plans/
├── plugins/                     # BSL-1.1 pro-tier plugins (multimodal/, workflow/)
├── src/mcp/                     # FastAPI MCP server (Python 3.11)
│   ├── core/                    # Portable orchestrator core (Apache-2.0)
│   │   ├── agents/              # Query, memory, hallucination, curator, self_rag
│   │   ├── contracts/           # ABCs: VectorStore, GraphStore, CacheStore, LLMClient
│   │   ├── retrieval/           # BM25, reranker, semantic cache, query decomposition
│   │   ├── routing/             # Smart router, model providers
│   │   └── utils/               # Embeddings, circuit breaker, LLM client
│   ├── app/                     # Application layer (concrete implementations)
│   │   ├── routers/             # FastAPI routers (27+ endpoints)
│   │   ├── stores/              # ChromaVectorStore, Neo4jGraphStore, RedisCacheStore
│   │   ├── db/neo4j/            # Cypher queries, schema, artifacts
│   │   ├── services/            # ingestion.py (ingest_content, ingest_file, dedup)
│   │   ├── middleware/          # auth, rate_limit, request_id, jwt, tenant_context
│   │   ├── parsers/             # PDF, office, structured, email, ebook
│   │   ├── eval/                # Retrieval evaluation harness + benchmark suite
│   │   ├── sync/                # CRDT, export, import, manifest, status
│   │   ├── models/              # Pydantic schemas
│   │   ├── main.py              # Entry point
│   │   └── tools.py             # MCP tool registry + dispatcher (21 core + 5 trading)
│   ├── enterprise/              # Enterprise overlay (ABAC, SSO, classification, immutable audit)
│   ├── config/                  # settings.py, taxonomy.py, features.py, providers.py
│   ├── agents/                  # Bridge modules (re-export from core.agents)
│   ├── utils/                   # Bridge modules (re-export from core.utils)
│   ├── services/                # Bridge modules (re-export from app.services)
│   └── requirements.txt/.lock   # Python deps (ranges / pinned with hashes)
├── src/web/                     # React GUI (React 19, Vite 7, Tailwind v4, shadcn/ui)
│   ├── docker-compose.yml       # cerid-web service (separate from MCP)
│   ├── src/lib/                 # types.ts, api.ts, model-router.ts
│   ├── src/hooks/               # use-chat, use-kb-context, use-settings, use-verification-stream, use-drag-drop, use-verification-orchestrator, etc.
│   ├── src/contexts/            # SettingsContext, KBInjectionContext, ConversationsContext, AuthContext
│   ├── src/components/          # layout/, chat/, kb/, monitoring/, audit/, memories/, settings/, workflows/, setup/, ui/
│   └── src/__tests__/           # 705+ vitest tests
├── packages/marketing/          # Next.js 16 marketing site (cerid.ai, Vercel)
├── packages/desktop/            # Electron desktop app (macOS + Windows)
├── stacks/                      # infrastructure/ (Neo4j, ChromaDB, Redis), bifrost/
├── artifacts/ → ~/Dropbox/AI-Artifacts (symlink)
└── data/ → src/mcp/data (symlink)
```

> For API endpoints, agent details, ingestion pipeline, and sync commands, see [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md).

## Phase C Architecture

The `src/mcp/` codebase is split into three layers:

- **`core/`** contains portable orchestration logic: agents, retrieval algorithms (BM25, reranker, semantic cache, query decomposition), routing (smart router, model providers), and contracts (ABCs for `VectorStore`, `GraphStore`, `CacheStore`, `LLMClient`). This layer has zero FastAPI or infrastructure dependencies and is licensed Apache-2.0 for standalone reuse.
- **`app/`** contains concrete implementations: FastAPI routers, store adapters (`ChromaVectorStore`, `Neo4jGraphStore`, `RedisCacheStore`), parsers, middleware, sync, eval, and the application entry point.
- **Bridge modules** (`agents/`, `utils/`, `services/` at the `src/mcp/` root) re-export from `core` and `app` to provide backward-compatible import paths. Existing code that imports `from agents.query_agent import ...` continues to work unchanged.
- **import-linter** enforces layer boundaries: `core/` must not import `app/`, `config/` must not import `core/`. Violations fail CI.
- **`enterprise/`** is a standalone overlay (ABAC, SSO, classification, immutable audit) — scaffolded but not wired for beta.

## Development

### Secrets Management

Single `.env` file at repo root, encrypted with `age`. Key at `~/.config/cerid/age-key.txt`.

```bash
./scripts/env-unlock.sh          # Decrypt .env.age → .env
./scripts/env-lock.sh            # Re-encrypt after editing
```

### Starting the Stack

```bash
./scripts/start-cerid.sh            # start all 4 service groups
./scripts/start-cerid.sh --build    # rebuild images after code changes
```

### KB Backup and Restore

```bash
./scripts/backup-kb.sh              # snapshot neo4j + chroma + redis to ~/cerid-archive/backups/
./scripts/restore-kb.sh <timestamp> # restore from a specific snapshot
```

Backups pause containers, copy data directories, and trigger Redis BGSAVE. Stored at `~/cerid-archive/backups/YYYY-MM-DDTHH-MM-SS/`.

Startup order: `[1/4]` Infrastructure (Neo4j, ChromaDB, Redis) → `[2/4]` Bifrost → `[3/4]` MCP → `[4/4]` React GUI.

### Environment Validation

```bash
./scripts/validate-env.sh          # full validation (14 checks)
./scripts/validate-env.sh --quick  # containers only (Docker + health checks)
./scripts/validate-env.sh --fix    # auto-start missing infrastructure
```

### Second-Machine Bootstrap

```bash
git clone git@github.com:sunrunnerfire/dotfiles.git ~/dotfiles && cd ~/dotfiles && bash install.sh
brew install age
git clone git@github.com:sunrunnerfire/cerid-ai.git ~/cerid-ai && cd ~/cerid-ai
./scripts/env-unlock.sh
ln -s ~/Dropbox/cerid-archive ~/cerid-archive
./scripts/start-cerid.sh          # auto-imports KB from Dropbox sync if Neo4j empty
./scripts/validate-env.sh
```

### Dependency Management

```bash
make lock-python                   # Regenerate requirements.lock after editing requirements.txt
make install-hooks                 # Git pre-commit hook (lock file sync check)
make deps-check                    # Verify all lock files are current
```

Cross-service version constraints: see `docs/DEPENDENCY_COUPLING.md`.

### Configuration

- `.env` (repo root) — All secrets. Encrypted as `.env.age`. Never committed in plaintext.
- `src/mcp/config/settings.py` — Domains, tiers, URLs, sync, model IDs
- `stacks/bifrost/config.yaml` — Intent classification, model routing, budget

### Verification

```bash
curl http://localhost:8888/health
curl http://localhost:8888/collections
curl http://localhost:8888/artifacts
```

## Claude Code Setup (New Machine)

> **Global setup applies first** — see `sunrunnerfire/dotfiles` → `CLAUDE_CODE_SETUP.md` for global plugins and MCP servers shared across all projects.

### Project Setup

1. **Verify prerequisites:** Docker running, `.env` decrypted, `age` installed, archive directory exists
2. **Install global plugins + MCP servers** — follow `sunrunnerfire/dotfiles` → `CLAUDE_CODE_SETUP.md`
3. **Run `./scripts/validate-env.sh`** to check all 14 environment validations
4. **If containers are down:** `./scripts/start-cerid.sh` (or `--build` after a `git pull`)

### Project-Level Config (committed, auto-applied)

| File | Purpose |
|------|---------|
| `.mcp.json` | Cerid KB MCP at `http://localhost:8888/mcp/sse` (27 `pkb_*` tools) — Claude Code runs on the host so `localhost` is correct here |
| `.claude/settings.json` | Hooks config (session-start, safety-check, typecheck, pythonlint) |
| `.claude/hooks/session-start.sh` | SessionStart — Docker + MCP + GUI health check |
| `.claude/hooks/safety-check.sh` | PreToolUse/Bash — blocks destructive commands |
| `.claude/hooks/typecheck.sh` | PostToolUse/Edit\|Write — `npx tsc --noEmit` for `.ts`/`.tsx` in `src/web/` |
| `.claude/hooks/pythonlint.sh` | PostToolUse/Edit\|Write — `ruff check` for `.py` in `src/mcp/` |
| `.claude/commands/` | Custom commands: stack, test, sync, lock |
| `.claude/launch.json` | Dev server configs (cerid-web, react-gui, marketing) |
| `.claude/agents/kb-curator.md` | Opus subagent for KB schema-aware curation, dedup, and cross-store consistency checks |
| `.claudeignore` | Excludes node_modules, dist, runtime data, binaries, lock files |

### Per-Machine Config (gitignored)

| File | Purpose |
|------|---------|
| `.claude/settings.local.json` | Bash permission allowlists (auto-populated as you approve commands) |

### Global Plugins Required

See `sunrunnerfire/dotfiles` → `CLAUDE_CODE_SETUP.md` for full list. Key plugins for this project:

- `superpowers` — plan execution, TDD, code review, debugging workflows
- `pyright-lsp` — Python type checking
- `frontend-design` — React GUI development
- `claude-md-management` — CLAUDE.md maintenance

### Global MCP Servers Required

See `sunrunnerfire/dotfiles` → `CLAUDE_CODE_SETUP.md` for install commands:

- **context7** — live docs for React, FastAPI, pydantic, ChromaDB, Neo4j
- **github-mcp** — GitHub issues, PRs, actions

**Tests:** Run Python tests in Docker (`host macOS lacks chromadb`):
```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ -v"
```
Frontend tests: `cd src/web && npx vitest run`

## Conventions

- **Session start:** Run `./scripts/validate-env.sh --quick` at the beginning of every session. If the session-start hook reports missing plugins or MCP servers, run `bash ~/dotfiles/install.sh` before proceeding with any other work
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
- **React GUI (`src/web/`):** Tailwind CSS v4 (uses `@tailwindcss/vite` plugin — no `tailwind.config.ts`); shadcn/ui New York style, Zinc base color; path alias `@/*` → `./src/*`; Bifrost CORS handled via Vite dev proxy (`/api/bifrost` → `localhost:8080`) and nginx proxy in Docker; `VITE_MCP_URL` and `VITE_BIFROST_URL` are `ENV` defaults baked into Dockerfile (not runtime-configurable without rebuild); `VITE_CERID_API_KEY` is a build `ARG`; bundle splitting via React.lazy + Vite manualChunks (75% main chunk reduction); iPad/tablet responsive: sidebar auto-collapses at 1024px, KB pane becomes bottom Sheet drawer on narrow viewports, toolbar overflow menu, `@media (hover: none)` touch visibility overrides, `@media (pointer: coarse)` 44px touch targets, iOS safe area insets, zoom prevention
- **Backend Hardening (`src/mcp/middleware/`):** API key auth is opt-in — set `CERID_API_KEY` env var to enable (header: `X-API-Key`). Multi-user JWT auth is opt-in — set `CERID_MULTI_USER=true` + `CERID_JWT_SECRET` to enable (adds 9 `/auth/*` endpoints, JWT Bearer middleware, tenant context propagation, per-user API keys with Fernet encryption, Redis usage metering). Rate limiting uses in-memory sliding window with per-client isolation via `X-Client-ID` header (`config/settings.py:CLIENT_RATE_LIMITS`); GUI 20 req/min on `/agent/`, trading-agent 80 req/min (raised from 30 to cover 5-session worst-case burst of 67.5/min), unknown clients 10 req/min. Stable external API at `/sdk/v1/` (`routers/sdk.py`) for cerid-series consumers. `CONSUMER_REGISTRY` in `config/settings.py` defines per-consumer `allowed_domains`, `strict_domains`, and rate limits — see `docs/INTEGRATION_GUIDE.md` for the full 13-step checklist for adding new agent integrations. Redis query cache with 5-min TTL (`utils/query_cache.py`) — caches `/query` and `/agent/query` results. LLM feedback loop toggled via `ENABLE_FEEDBACK_LOOP` env var. CORS origins configurable via `CORS_ORIGINS` (defaults to `*`)
- **API Architecture (dual-path, intentional):** The React GUI calls internal `/agent/*` and service endpoints directly (via `api.ts` with `X-Client-ID: gui`). External cerid-series consumers use the stable `/sdk/v1/*` contract with typed response models and consumer domain isolation. This separation is deliberate: the GUI needs full domain access and internal-only endpoints (settings, taxonomy, admin, chat streaming) that have no SDK equivalent. The SDK layer provides versioned contracts, OpenAPI schemas, and consumer scoping for external agents. Both paths share the same middleware stack (rate limiting, auth, request tracing). All frontend API calls route through `src/web/src/lib/api.ts` which sets `X-Client-ID: gui`, `X-Request-ID`, and optional auth headers on every request.
- **Trading Agent Integration (`CERID_TRADING_ENABLED`):** When enabled, cerid-ai integrates with cerid-trading-agent via 5 SDK endpoints (`/sdk/v1/trading/signal`, `herd-detect`, `kelly-size`, `cascade-confirm`, `longshot-surface`), 5 MCP tools (`pkb_trading_signal`, etc.), and 3 scheduler jobs (autoresearch at 01:00, Platt mirror at 02:00, longshot surface at 02:30). Config: `CERID_TRADING_ENABLED=true` + `TRADING_AGENT_URL=http://localhost:8090` (or `http://trading-agent:8090` in Docker). React GUI includes a lazy-loaded TradingPane with KPIs, session cards, and 10s polling via `/api/trading/*` proxy routes. All trading features are backward-compatible and default to disabled. See `docs/DEPENDENCY_COUPLING.md` for the full contract.
- **Docker build verification:** After `docker compose build`, always verify success with `docker compose build --progress=plain cerid-web 2>&1 | grep error`. TypeScript strict mode in `npm run build` catches errors that `npx tsc --noEmit` misses. Docker may silently reuse a cached image when the build stage fails with exit code 2.
- **Circuit breaker naming:** All LLM call sites must use breaker names registered in `circuit_breaker.py`. Update the registry when adding new call sites. Avoid `f"bifrost-{breaker_name}"` in fallback paths — it can double-prefix names that already start with `bifrost-`.
- **Docker env var pattern:** `src/mcp/docker-compose.yml` uses `env_file: ../../.env` to load secrets into the MCP container. Do NOT add `${VAR}` interpolation in the `environment:` section for passthrough vars (e.g., `NEO4J_PASSWORD`) — it fails when running without `--env-file` and the empty value overrides the env_file entry. Container-specific overrides (service URLs, paths) are fine in `environment:` since they're literal values. Always rebuild MCP via `docker compose -f src/mcp/docker-compose.yml --env-file .env up -d --build` or use `scripts/start-cerid.sh`.
- **Neo4j auth validation:** `deps.py` `get_neo4j()` validates credentials by running `RETURN 1` (not just `verify_connectivity()` which only checks transport). `/health` endpoint also runs a Cypher query on every call. Empty `NEO4J_PASSWORD` raises `RuntimeError` immediately.
- **Trading domain segregation:** KB has a dedicated `trading` domain (`config/taxonomy.py`) with sub-categories: signals, market-analysis, execution, post-analysis, strategy-research, risk-analysis. Trading agent queries are scoped to `domains=["trading"]` — personal finance data (tax returns, budgets) stays in `domain_finance`. Domain affinity: trading↔finance at 0.3 weight.
- **Event loop protection:** ChromaDB queries, ONNX embedding, BM25 tokenization, and cross-encoder reranking are **synchronous CPU-bound** operations. In async endpoints, always use `asyncio.to_thread()` to offload them. The `/agent/query` endpoint is gated by `_QUERY_SEMAPHORE(2)` to prevent concurrent KB queries from starving the event loop (which caused 47s+ stalls and watchdog kills). Frontend auto-inject KB queries use `AbortController` + 500ms timeout to free browser connection slots before the chat/stream fetch fires.
- **Embedding function returns `list[np.ndarray]`** for ChromaDB 0.5.x compatibility. The `OnnxEmbeddingFunction.__call__()` returns individual numpy array slices, NOT `.tolist()` (which would produce `list[list[float]]` that fails ChromaDB's `validate_embeddings` check).
- **JWT startup validation:** When `CERID_MULTI_USER=true`, missing `CERID_JWT_SECRET` raises `RuntimeError` at startup (not just a warning). Prevents running with empty secret.
- **Rate limit middleware reads X-Client-ID from headers directly**, not from `request.state` — eliminates middleware ordering dependency. Per-client isolation verified: trading-agent=80/min, gui=20/min.
- **Keywords metadata uses `keywords_json`** (JSON-encoded string) consistently across ingest_file, ingest_content, reingest, and Neo4j artifact creation. Previous inconsistency (`keywords` vs `keywords_json`) caused silent data loss.
- **Plugin development (`plugins/`):** Each plugin has a `manifest.json` (name, version, tier, description, entry point). BSL-1.1 licensed (converts to Apache-2.0 after 3 years). Plugins are loaded via dual-directory scanning (`src/mcp/plugins/` for built-in, `plugins/` for external). Tier gating enforced at load time (`CERID_TIER`). Plugin management via `routers/plugins.py` (7 endpoints: list, enable/disable, config CRUD, scan).
- **Workflow engine (`routers/workflows.py`):** DAG validation via Kahn's algorithm (rejects cycles). Topological execution order. 4 built-in templates (research, ingest, verify, custom). Nodes are typed (query, ingest, verify, transform, notify). SVG canvas for visual editing. BSL-1.1 pro-tier via `plugins/workflow/`.
- **Observability (`routers/observability.py`):** `MetricsCollector` writes 8 Redis time-series metrics (latency, cost, NDCG, cache hit rate, verification accuracy, error rate, throughput, memory usage). Health score computed as weighted A-F grade. Dashboard endpoint returns sparkline data for configurable time windows.
- **A2A Protocol (`routers/a2a.py`):** Agent Card served at `/.well-known/agent.json`. Task lifecycle: create → status → cancel with Redis-backed storage. A2A client (`utils/a2a_client.py`) discovers remote agents and invokes tasks. Cerid is the first personal KB with dual MCP + A2A protocol support.
- **Ollama Add-On (optional):** Local LLM for pipeline intelligence (claim extraction, query decomposition, memory resolution, topic extraction, LLM reranking). Default model: `llama3.2:3b` (3B params, ~2GB). Enable via `OLLAMA_ENABLED=true` + `INTERNAL_LLM_PROVIDER=ollama` (set by `start-cerid.sh` on first run). Docker profile: `--profile ollama`. macOS Apple Silicon: runs natively for Metal acceleration. Circuit breaker: `"ollama"` in `circuit_breaker.py`. Fallback: automatic to OpenRouter when Ollama unavailable. Cost: $0 (local inference). Hardware detection: `scripts/detect-gpu.sh` (NVIDIA/AMD ROCm/macOS Metal/CPU). Status: `/health` reports Ollama reachability when enabled. Config: `/providers/ollama/status`, `/providers/ollama/enable`, `/providers/ollama/disable`.

## Dependency Sync Guide

See [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) for the full developer reference.

**Critical sync points when making changes:**
- **New MCP tool** — update `tools.py` + tool count in `CLAUDE.md` and `README.md`
- **New endpoint** — register in `main.py` + update `docs/API_REFERENCE.md`
- **New env var** — add to `settings.py` + `.env.example`
- **Python deps** — edit `requirements.txt` then `make lock-python`
- **Backend schema change** — manually update `src/web/src/lib/types.ts`
- **SDK contract change** — update `docs/DEPENDENCY_COUPLING.md` + coordinate with external consumers
- **Version bump** — `pyproject.toml` + git tag

**Must NOT sync to public repo (`cerid-ai`):**
- Trading agent endpoints, models, scheduler jobs, MCP tools (`pkb_trading_signal`, `pkb_herd_detect`, `pkb_kelly_size`, `pkb_cascade_confirm`, `pkb_longshot_surface`)
- Boardroom endpoints and domains
- Billing/Stripe code
- Enterprise overlay (`src/mcp/enterprise/`)
- `structlog` dependency (internal-only structured logging)
- `packages/desktop/` (Electron app)

**Version coupling:** Python 3.11 (Dockerfile + CI + pyproject.toml), Node 22 (.nvmrc + Dockerfile), ChromaDB client >=0.5,<0.6 must match server 0.5.23.

## CI Pipeline (8 jobs)

| Job | What |
|-----|------|
| lint | `ruff check src/mcp/` |
| typecheck | `mypy src/mcp/` |
| test | pytest 2370+ tests (70% coverage floor) + Codecov upload + license audit |
| security | detect-secrets + bandit + pip-audit (CVE-ignores documented inline) + dlint ReDoS |
| lock-sync | pip-compile lock file freshness check |
| frontend | tsc + ESLint + Vitest + Vite build + bundle size check (800KB limit) + npm audit + license audit |
| docker | hadolint (mcp + web Dockerfiles) + `docker build` + Trivy CRITICAL/HIGH scan |
| frontend-desktop | npm ci + `npm run typecheck` |

Docker gates on all 7 prior jobs. Trivy CVE ignore list is in `.github/workflows/ci.yml` with inline rationale for each ignored CVE.

## Sentry

Error monitoring via Sentry org `cerid-ai`. Two projects in this repo:

| Project | SDK | Where initialized |
|---------|-----|-------------------|
| `cerid-ai-mcp` | `sentry-sdk[fastapi]` | `src/mcp/main.py` — before `app = FastAPI()` |
| `cerid-ai-marketing` | `@sentry/nextjs` | `packages/marketing/sentry.server.config.ts`, `sentry.edge.config.ts`, `src/instrumentation.ts`, `src/instrumentation-client.ts`, `src/app/global-error.tsx` |

**GitHub secrets required:**

| Secret | Used by |
|--------|---------|
| `SENTRY_AUTH_TOKEN` | Source map uploads during `next build` (CI `frontend-marketing` job) |
| `SENTRY_DSN_MCP` | Python FastAPI error reporting |
| `SENTRY_DSN_MARKETING` | Next.js error reporting + Vercel env var `NEXT_PUBLIC_SENTRY_DSN` |

**`withSentryConfig`** wraps `next.config.ts` in `packages/marketing/` with `org: "cerid-ai"`, `project: "cerid-ai-marketing"`, `tunnelRoute: "/monitoring"`, and `widenClientFileUpload: true`. Source maps are uploaded automatically on every `next build` in CI when `SENTRY_AUTH_TOKEN` is present.
