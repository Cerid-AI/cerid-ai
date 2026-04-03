# CLAUDE.md - Cerid AI (Internal)

> **This is the canonical development repo** (`Cerid-AI/cerid-ai-internal`, private).
> All development happens here. The public repo is a distribution artifact.

## Repository Architecture

Cerid AI uses a two-repo open-core model:

| Repo | Visibility | Location | Purpose |
|------|-----------|----------|---------|
| **cerid-ai-internal** (this repo) | Private | `~/Develop/cerid-ai` | Canonical development — all code, Pro/Enterprise plugins, full test harness, CI, internal docs |
| **cerid-ai** (public) | Public | `~/Develop/cerid-ai-public` | Apache-2.0 distribution — core product, SDK, community plugins |

### What's only in internal (not in public)

- **12 BSL-1.1 plugins:** audio, vision, OCR, analytics, workflow-builder, apple-notes, calendar, custom-rag, docling-parser, gmail, metamorphic, outlook
- **Billing:** `routers/billing.py`, `models/billing.py` (Stripe integration)
- **Trading SDK:** 5 trading endpoints + 5 MCP tools + `agents/trading_agent.py`
- **Boardroom SDK:** 3 boardroom endpoints
- **Full test harness:** 97+ test files (1673+ Python tests), beta E2E suite, eval harness
- **Eval suite:** `src/mcp/eval/` (NDCG, MRR, P@K, RAGAS)
- **Internal docs:** `docs/BRANDING.md`, `docs/MARKET_ANALYSIS.md`, `docs/COMPETITIVE_ANALYSIS_2026-04.md`
- **Claude Code config:** `.claude/` directory (agents, commands, hooks, settings)
- **Task tracking:** `tasks/todo.md`, `tasks/lessons.md`

### Syncing to public

```bash
# Core improvements → public (cherry-pick)
git checkout -b sync/feature public/main
git cherry-pick <commits>
git push public sync/feature
# Then open PR on public repo

# Community PRs → internal (merge)
git fetch public && git merge public/main
```

**Pre-push check:** Before syncing to public, verify no Pro content leaks:
```bash
grep -r "BSL-1.1" --include="*.py" --include="*.json" src/ plugins/ | grep -v "comment\|noqa"
```

### Public repo CI (6 jobs)

The public repo has a simplified CI: lint, typecheck, test (60% floor), security, frontend, docker.
The internal repo has the full 8-job pipeline with lock-sync and frontend-desktop.

---

## Project Overview

Cerid AI is a self-hosted, privacy-first Personal AI Knowledge Companion. It unifies multi-domain knowledge bases (code, finance, projects, artifacts) into a context-aware LLM interface with RAG-powered retrieval and intelligent agents. Knowledge base stays local; LLM API calls send query context to the configured provider. Optional cloud sync (Dropbox) for cross-machine settings/conversations, encrypted when CERID_ENCRYPTION_KEY is set.

**Status:** Version 0.80. See [`docs/COMPLETED_PHASES.md`](docs/COMPLETED_PHASES.md) for history.

**Next:** See [`docs/ROADMAP.md`](docs/ROADMAP.md).

**Open issues:** [`docs/ISSUES.md`](docs/ISSUES.md).

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
| Marketing Site | cerid.ai (3001 dev) | Separate repo: `Cerid-AI/cerid-ai-marketing` | Next.js 16 + Vercel |
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
│   ├── main.py                  # Entry point
│   ├── config/                  # settings.py, taxonomy.py, features.py, providers.py, constants.py
│   ├── db/neo4j/                # schema, artifacts, relationships, taxonomy, memory
│   ├── sync/                    # export, import_, manifest, status
│   ├── parsers/                 # registry, pdf, office, structured, email, ebook
│   ├── services/                # ingestion.py (ingest_content, ingest_file, dedup)
│   ├── eval/                    # Retrieval evaluation harness (NDCG, MRR, P@K, R@K)
│   ├── agents/                  # query, curator, triage, rectify, audit, maintenance, hallucination/, memory, self_rag, decomposer, assembler
│   ├── routers/                 # FastAPI routers (health, query, ingestion, agents, taxonomy, setup, providers, models, automations, a2a, observability, plugins, workflows, ollama_proxy, eval, data_sources, etc.)
│   ├── models/                  # user.py, sdk.py, trading.py (Pydantic schemas)
│   ├── middleware/              # 6 middleware: auth.py, rate_limit.py, request_id.py, jwt_auth.py, tenant_context.py, metrics.py
│   ├── tools.py                 # MCP tool registry + dispatcher (26 tools)
│   ├── plugins/                 # Plugin loader + built-in plugin scaffold
│   ├── utils/                   # bm25, cache, query_cache, embeddings, chunker, dedup, encryption, web_search, a2a_client, error_handler, degradation, retrieval_cache, hyde, ollama_models, model_registry, query_classifier, data_sources/, etc.
│   ├── scripts/                 # watch_ingest.py, watch_obsidian.py, ingest_cli.py
│   └── requirements.txt/.lock   # Python deps (ranges / pinned with hashes)
├── src/web/                     # React GUI (React 19, Vite 7, Tailwind v4, shadcn/ui)
│   ├── docker-compose.yml       # cerid-web service (separate from MCP)
│   ├── src/lib/                 # types.ts, api.ts, model-router.ts
│   ├── src/hooks/               # use-chat, use-kb-context, use-settings, use-verification-stream, use-drag-drop, use-verification-orchestrator, etc.
│   ├── src/contexts/            # SettingsContext, KBInjectionContext, ConversationsContext, AuthContext
│   ├── src/components/          # layout/, chat/, kb/, monitoring/, audit/, memories/, settings/, workflows/, setup/, ui/
│   └── src/__tests__/           # vitest tests
├── packages/marketing/          # Next.js 16 marketing site (being separated to Cerid-AI/cerid-ai-marketing)
├── packages/desktop/            # Electron desktop app (macOS + Windows)
├── stacks/                      # infrastructure/ (Neo4j, ChromaDB, Redis), bifrost/
├── artifacts/ → ~/Dropbox/AI-Artifacts (symlink)
└── data/ → src/mcp/data (symlink)
```

> For API endpoints, agent details, ingestion pipeline, and sync commands, see [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md).

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

### Dependency Management

```bash
make lock-python                   # Regenerate requirements.lock after editing requirements.txt
make install-hooks                 # Git pre-commit hook (lock file sync check)
make deps-check                    # Verify all lock files are current
```

Cross-service version constraints: see `docs/DEPENDENCY_COUPLING.md`.

### Configuration

- `.env` (repo root) — All secrets. Encrypted as `.env.age` via `age`. Key at `~/.config/cerid/age-key.txt`
- `src/mcp/config/settings.py` — Domains, tiers, URLs, sync, model IDs
- `stacks/bifrost/config.yaml` — Intent classification, model routing, budget

### Verification

```bash
curl http://localhost:8888/health
curl http://localhost:8888/collections
curl http://localhost:8888/artifacts
```

### Running Tests

**Python tests** (run in Docker since host macOS lacks chromadb):
```bash
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim bash -c "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ -v"
```

**Frontend tests:**
```bash
cd src/web && npx vitest run
```

## Claude Code Setup

See [`.claude/SETUP.md`](.claude/SETUP.md) for detailed Claude Code configuration, plugins, MCP servers, and new machine bootstrap.

## Cross-Cutting Patterns (THE ONE way to do each concern)

> **Context resilience:** After compaction events, AI agents must use THESE patterns — no alternatives. Each concern has exactly ONE canonical implementation.

| Concern | The ONE Pattern | Canonical Location |
|---------|----------------|--------------------|
| Error handling | `@handle_errors()` decorator | `utils/error_handler.py` |
| Error types | `CeridError` hierarchy | `errors.py` |
| Feature gating | `@require_feature()` decorator | `config/features.py` |
| Configuration | `os.getenv()` in `config/settings.py` | `config/settings.py` |
| Constants | Named constants (no magic numbers) | `config/constants.py` |
| Circuit breakers | `circuit_breaker(name)` context manager (registered: chromadb, neo4j, redis, ollama, bifrost) with client locks | `utils/circuit_breaker.py` |
| Redis cache keys | `cerid:{domain}:{key}` prefix convention | `utils/retrieval_cache.py` |
| API error responses | `CeridError` → auto-converted via FastAPI exception handler | `main.py` |
| Graceful degradation | `DegradationManager` (5 tiers: FULL→LITE→DIRECT→CACHED→OFFLINE) | `utils/degradation.py` |
| Retrieval cache | Redis with generation-counter invalidation | `utils/retrieval_cache.py` |
| Ollama routing | `get_stage_provider()` per-stage routing (8 stages) | `config/settings.py` |
| Model registry | `ModelRegistry` with OpenRouter auto-validation | `utils/model_registry.py` |
| Query classification | `classify_query()` intent detection (5 intents) | `utils/query_classifier.py` |
| External data sources | Pluggable data source framework | `utils/data_sources/` |

**Rules:**
- Typed errors (`CeridError` subclasses) are the ONLY way to signal failures. No `raise HTTPException` in business logic.
- `@require_feature("feature_name")` is the ONLY tier gate. No inline `if FEATURE_TIER == "pro"` checks.
- All numeric constants live in `config/constants.py`. Import from there.
- Every `except` block MUST either log + degrade or raise a typed error. Zero silent `pass` blocks.

## Module Responsibility Map

| Module | Responsibility | Key Classes/Functions |
|--------|---------------|---------------------|
| `agents/query_agent.py` | RAG retrieval pipeline (8 strategies) with parallel retrieval, circuit breakers, semantic caching, 3-layer dedup | `QueryAgent.query()` |
| `agents/hallucination/` | Claim extraction + verification (7 modules) | `verify_response_streaming()` |
| `agents/memory.py` | Memory extraction, decay, conflict resolution | `MemoryAgent` |
| `agents/curator.py` | KB quality scoring + recommendations | `CuratorAgent` |
| `services/ingestion.py` | File parsing → chunking → ChromaDB + Neo4j | `ingest_file()`, `ingest_content()` |
| `utils/llm_client.py` | Direct OpenRouter HTTP calls | `llm_call()` |
| `utils/smart_router.py` | Model capability scoring + routing | `SmartRouter.route()` |
| `utils/circuit_breaker.py` | Circuit breaker registry | `circuit_breaker(name)` |
| `config/settings.py` | All env var config | Module-level constants |
| `config/constants.py` | All magic numbers | Pure values, no logic |
| `config/features.py` | Feature flags + `@require_feature` | `FEATURE_FLAGS`, `@require_feature()` |
| `errors.py` | Exception hierarchy | `CeridError` and subclasses |
| `tools.py` | MCP tool registry (26 tools) | `@mcp_tool()` decorator |
| `agents/decomposer.py` | Query decomposition (extracted from query_agent.py) | `decompose_query()` |
| `agents/assembler.py` | Result assembly (extracted from query_agent.py) | `assemble_results()` |
| `agents/retrieval_orchestrator.py` | Smart mode orchestration (KB + memory + external in parallel) | `orchestrated_query()` |
| `utils/error_handler.py` | Centralized error handling decorator | `@handle_errors()` |
| `utils/degradation.py` | 5-tier graceful degradation manager | `DegradationManager` |
| `utils/retrieval_cache.py` | Redis retrieval cache with generation-counter invalidation | `retrieval_cache_get/set()` |
| `utils/hyde.py` | HyDE (Hypothetical Document Embedding) fallback | `hyde_expand()` |
| `utils/ollama_models.py` | Ollama model management | `list_models()`, `pull_model()` |
| `routers/sdk.py` | Stable external API (`/sdk/v1/`) | Versioned contract |
| `routers/data_sources.py` | External data source management endpoints | `/data-sources` CRUD |
| `utils/model_registry.py` | Dynamic model registry with OpenRouter validation | `ModelRegistry`, `validate_models()` |
| `utils/query_classifier.py` | Query intent classification | `classify_query()` |
| `utils/data_sources/` | Pluggable external data source framework | `DataSourceManager` |

## Product Tiers

| Tier | License | Env Var |
|------|---------|---------|
| Core | Apache-2.0 | `CERID_TIER=community` (default) |
| Pro | BSL-1.1 | `CERID_TIER=pro` |
| Enterprise | Commercial | `CERID_TIER=enterprise` |

- Gate features with `@require_feature("feature_name")` decorator (async endpoints)
- Gate sync functions with `check_feature("feature_name")` or `check_tier("pro")`
- Plugin tier: set `"tier": "pro"` in `manifest.json`
- Enterprise includes all Pro features. Pro includes all Core features.
- See `docs/TIER_MATRIX.md` for complete feature matrix.

## Compliance — USG Technology Restrictions

**No Chinese-origin AI models or technology.** The codebase has been purged of all Chinese-origin model references (DeepSeek, Qwen/Alibaba, Baichuan, Yi, GLM/Zhipu, MiniMax, Moonshot, 01.AI) for USG alignment.

**Rules:**
- Do NOT add model entries for DeepSeek, Qwen, Alibaba, or any Chinese-origin LLM provider
- Default Ollama model: `llama3.2:3b` (Meta, US-origin) — NOT Qwen
- Approved providers: OpenAI, Anthropic, Google, xAI, Meta (Llama), Microsoft (Phi), Mistral (French)
- When adding new models to the selector or pipeline config, verify the model's country of origin
- Run `grep -rn "deepseek\|qwen\|alibaba\|baichuan\|zhipu" src/ --include="*.py" --include="*.ts" --include="*.tsx"` to verify compliance before committing

## Conventions

- Docker services use container-name-based discovery on `llm-network`
- MCP protocol uses SSE transport with session-based message queuing
- Secrets go in root `.env`, encrypted as `.env.age` via `age`. Key at `~/.config/cerid/age-key.txt`
- User files (`~/cerid-archive/`) mounted read-only, never in git repo
- Symlinks used for `artifacts/` and `data/` — don't break them
- Infrastructure DB data at `stacks/infrastructure/data/` (.gitignored)
- ChromaDB metadata values are strings/ints only (lists stored as JSON strings)
- ChromaDB client version must match server (currently `>=0.5,<0.6`)
- Error responses use typed `CeridError` exceptions → auto-converted to JSON via FastAPI exception handler in `main.py`. Do NOT use `HTTPException` in business logic — only in routers for HTTP-specific concerns (404, 401)
- Neo4j Cypher: use explicit RETURN clauses, not map projections (breaks with Python string ops)
- Deduplication: SHA-256 of parsed text, atomic via Neo4j UNIQUE CONSTRAINT on `content_hash`
- Batch ChromaDB writes: single `collection.add()` call per ingest, not per-chunk
- PDF parsing: pdfplumber extracts tables as Markdown, non-table text extracted separately to avoid duplication
- **React GUI (`src/web/`):** Tailwind CSS v4 (uses `@tailwindcss/vite` plugin — no `tailwind.config.ts`); shadcn/ui New York style, Zinc base color; path alias `@/*` → `./src/*`; Bifrost CORS handled via Vite dev proxy (`/api/bifrost` → `localhost:8080`) and nginx proxy in Docker; `VITE_MCP_URL` and `VITE_BIFROST_URL` are `ENV` defaults baked into Dockerfile (not runtime-configurable without rebuild); `VITE_CERID_API_KEY` is a build `ARG`; bundle splitting via React.lazy + Vite manualChunks (75% main chunk reduction); iPad/tablet responsive: sidebar auto-collapses at 1024px, KB pane becomes bottom Sheet drawer on narrow viewports, toolbar overflow menu, `@media (hover: none)` touch visibility overrides, `@media (pointer: coarse)` 44px touch targets, iOS safe area insets, zoom prevention
- **Backend Hardening (`src/mcp/middleware/`):** API key auth is opt-in — set `CERID_API_KEY` env var to enable (header: `X-API-Key`). Multi-user JWT auth is opt-in — set `CERID_MULTI_USER=true` + `CERID_JWT_SECRET` to enable. Rate limiting uses in-memory sliding window with per-client isolation via `X-Client-ID` header. Stable external API at `/sdk/v1/` (`routers/sdk.py`) for cerid-series consumers. Redis query cache with 5-min TTL (`utils/query_cache.py`). CORS origins configurable via `CORS_ORIGINS` (defaults to `*`)
- **API Architecture (dual-path, intentional):** The React GUI calls internal `/agent/*` and service endpoints directly (via `api.ts` with `X-Client-ID: gui`). External cerid-series consumers use the stable `/sdk/v1/*` contract with typed response models and consumer domain isolation. Both paths share the same middleware stack.
- **Trading Agent Integration (`CERID_TRADING_ENABLED`):** When enabled, cerid-ai provides KB enrichment for cerid-trading-agent via 5 SDK endpoints and 5 MCP tools. Config: `CERID_TRADING_ENABLED=true` + `TRADING_AGENT_URL`. The trading agent calls INTO cerid-ai's SDK. All trading features are backward-compatible and default to disabled. See `docs/DEPENDENCY_COUPLING.md` for the full contract.
- **Docker build verification:** After `docker compose build`, verify with `docker compose build --progress=plain cerid-web 2>&1 | grep error`. TypeScript strict mode in `npm run build` catches errors that `npx tsc --noEmit` misses.
- **Circuit breaker naming:** All LLM call sites must use breaker names registered in `circuit_breaker.py`. Avoid `f"bifrost-{breaker_name}"` in fallback paths — it can double-prefix names.
- **Docker env var pattern:** `src/mcp/docker-compose.yml` uses `env_file: ../../.env` to load secrets. Do NOT add `${VAR}` interpolation in the `environment:` section for passthrough vars — it fails without `--env-file`. Always rebuild MCP via `docker compose -f src/mcp/docker-compose.yml --env-file .env up -d --build` or use `scripts/start-cerid.sh`.
- **Neo4j auth validation:** `deps.py` `get_neo4j()` validates credentials by running `RETURN 1` (not just `verify_connectivity()`). Empty `NEO4J_PASSWORD` raises `RuntimeError` immediately.
- **Trading domain segregation:** KB has a dedicated `trading` domain (`config/taxonomy.py`). Trading agent queries scoped to `domains=["trading"]`. Domain affinity: trading↔finance at 0.3 weight.
- **Embedding function returns `list[np.ndarray]`** for ChromaDB 0.5.x compatibility.
- **JWT startup validation:** When `CERID_MULTI_USER=true`, missing `CERID_JWT_SECRET` raises `RuntimeError` at startup.
- **Rate limit middleware reads X-Client-ID from headers directly**, not from `request.state`.
- **Keywords metadata uses `keywords_json`** (JSON-encoded string) consistently across ingest and Neo4j artifact creation.
- **Plugin development (`plugins/`):** Each plugin has a `manifest.json`. BSL-1.1 licensed. Plugins loaded via dual-directory scanning. Tier gating enforced at load time.
- **Workflow engine (`routers/workflows.py`):** DAG validation via Kahn's algorithm. Topological execution order. 4 built-in templates. BSL-1.1 pro-tier.
- **Observability (`routers/observability.py`):** `MetricsCollector` writes 8 Redis time-series metrics. Health score computed as weighted A-F grade.
- **A2A Protocol (`routers/a2a.py`):** Agent Card at `/.well-known/agent.json`. Task lifecycle: create → status → cancel with Redis-backed storage.
- **Ollama Add-On (optional):** Local LLM for pipeline intelligence. Enable via `OLLAMA_ENABLED=true` + `INTERNAL_LLM_PROVIDER=ollama`. Default model: `llama3.2:3b`. Circuit breaker: `"ollama"`. Fallback: automatic to OpenRouter when unavailable.
- **Setup Wizard (`components/setup/`):** 8-step onboarding: Welcome (system check) → API Keys (4 providers) → KB Config → Ollama → Review & Apply → Health → First Document → Mode. Uses `useReducer` with `WizardState`, progress persisted to localStorage (24h expiry), skip logic for steps 2/3/6. Provider capability assessment via `provider-capabilities.ts` (pure functions, zero React deps). Degradation banner in chat with adaptive polling, "Check now" + recovery toast. Model fallback notice surfaces backend fallback chain (OpenRouter → Bifrost → free Llama) to user. Settings Essentials tab has Provider Status section with runtime health grid.

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

**Version coupling:** Python 3.11 (Dockerfile + CI + pyproject.toml), Node 22 (.nvmrc + Dockerfile), ChromaDB client >=0.5,<0.6 must match server 0.5.23.

## CI Pipeline (8 jobs)

| Job | What |
|-----|------|
| lint | `ruff check src/mcp/` |
| typecheck | `mypy src/mcp/` |
| test | pytest (70% coverage floor) + Codecov upload + license audit |
| security | detect-secrets + bandit + pip-audit + dlint ReDoS |
| lock-sync | pip-compile lock file freshness check |
| frontend | tsc + ESLint + Vitest + Vite build + bundle size check (800KB limit) + npm audit + license audit |
| docker | hadolint + `docker build` + Trivy CRITICAL/HIGH scan |
| frontend-desktop | npm ci + `npm run typecheck` |

Docker gates on all 8 prior jobs. Trivy CVE ignore list is in `.github/workflows/ci.yml` with inline rationale for each ignored CVE.

## Sentry

Error monitoring via Sentry org `cerid-ai`. **MCP server Sentry is opt-in** — requires `ENABLE_SENTRY=true` in addition to `SENTRY_DSN`. When disabled (default), no telemetry data leaves the machine.

| Project | SDK | Where initialized |
|---------|-----|-------------------|
| `cerid-ai-mcp` | `sentry-sdk[fastapi]` | `src/mcp/main.py` — before `app = FastAPI()` |
| `cerid-ai-marketing` | `@sentry/nextjs` | Moved to `Cerid-AI/cerid-ai-marketing` repo |
