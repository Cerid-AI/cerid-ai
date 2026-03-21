# Cerid AI — Task Tracker

> **Last updated:** 2026-03-21
> **Current status:** All phases through 50 complete + Production Readiness Audit. 1376+ Python tests, 485+ frontend tests.
> **Open issues:** [docs/ISSUES.md](../docs/ISSUES.md) — 0 open
> **Development plan:** [docs/plans/DEVELOPMENT_PLAN_PHASE42-50.md](../docs/plans/DEVELOPMENT_PLAN_PHASE42-50.md) (Phases A-D + 42-50)
> **Completed phases:** [docs/COMPLETED_PHASES.md](../docs/COMPLETED_PHASES.md)

## Development Roadmap: Phases A-D + 42-50

Competitive analysis (2026-03-21) against Dify (134K stars), Open WebUI (128K), RAGFlow (76K), Mem0 (51K), Khoj (34K). Cerid's moat: hallucination detection, GraphRAP, Self-RAG, 9 agents, typed SDK. No competitor has all of these.

Roadmap covers two tracks: **Infrastructure** (deployment, BYOK, packaging, repo architecture) and **Features** (competitive capabilities). Both tracks run in parallel.

### P0 — Critical (ALL COMPLETE)

- [x] **Phase A: Unified Docker Compose + First-Run Wizard** ✅ 2026-03-21
  - Root `docker-compose.yml` with depends_on healthchecks (Sprint 1)
  - Setup API (`/setup/*`) for first-run config (Sprint 1)
  - React 4-step setup wizard (Sprint 2)
  - Model assignment backend + Bifrost templating (Sprint 2)

- [x] **Phase B: BYOK Model Configuration** ✅ 2026-03-21
  - `PROVIDER_REGISTRY` for 5 providers + async key validation (Sprint 1)
  - `routers/providers.py` REST API (Sprint 1)
  - Bifrost `config.yaml.template` + model assignment UI (Sprint 2)

- [x] **Phase 42: Agentic Web Search Fallback** ✅ 2026-03-21
  - `WebSearchProvider` (Tavily, SearXNG, OpenRouter online) (Sprint 3)
  - `pkb_web_search` MCP tool #24 (Sprint 3)
  - Auto-ingest via `ENABLE_AUTO_LEARN` (Sprint 3)

### P1 — High Value (ALL COMPLETE)

- [x] **Phase 43: User-Facing Scheduled Automations** ✅ 2026-03-21
  - CRUD API + Redis persistence + APScheduler integration (Sprint 4)
  - 3 action types: notify, digest, ingest (Sprint 4)
  - React automations management GUI (Sprint 4)

- [x] **Phase 44: Enhanced Memory Layer** ✅ 2026-03-21
  - Conflict detection + LLM resolution (supersede/coexist/merge) (Sprint 4)
  - Decay/reinforcement scoring formula (Sprint 4)
  - Neo4j `:Memory` nodes + relationships (Sprint 5)
  - `pkb_memory_recall` MCP tool #25 (Sprint 5)

- [x] **Phase 45: A2A (Agent-to-Agent) Protocol** ✅ 2026-03-21
  - Agent Card at `/.well-known/agent.json` (Sprint 5)
  - Task lifecycle (create/status/cancel) with Redis storage (Sprint 5)
  - A2A client for remote agent discovery/invocation (Sprint 5)
  - Dual MCP + A2A = first personal KB with both protocols (Sprint 5)

- [x] **Phase C: Repo Architecture Separation** ✅ 2026-03-21
  - Restructured: `core/` (Apache-2.0), `app/` (Apache-2.0), `plugins/` (BSL-1.1), `enterprise/` (commercial)
  - License files per directory, re-export bridges, CI paths updated (Sprint 6)

### P2 — Valuable

- [x] **Phase 46: Multi-Modal KB** ✅ 2026-03-21
  - OCR (pytesseract), audio (faster-whisper), vision (LLM) plugins — all BSL-1.1 pro-tier
  - Plugin loader: dual-directory scanning, pkb_ingest_multimodal MCP tool, 26 tests
- [x] **Phase 47: Observability Dashboard** ✅ 2026-03-21
  - MetricsCollector: 8 Redis time-series metrics (latency, cost, NDCG, cache, verification)
  - React dashboard: 6 metric cards with SVG sparklines, health score A-F, 25 tests
- [x] **Phase 48: Local LLM via Ollama** ✅ 2026-03-21
  - Ollama proxy router: /ollama/chat (streaming), /ollama/models, /ollama/pull
  - Circuit breaker, OLLAMA_ENABLED gate, 15 tests
- [x] **Phase D: Electron Desktop App** ✅ 2026-03-21
  - Main process, Docker lifecycle (dockerode), system tray, auto-updater
  - CI/CD: electron-build.yml for macOS + Windows, 8,357 lines

### P3 — Long-Term

- [x] **Phase 49: Plugin Foundation** ✅ 2026-03-21
  - Plugin management router: 7 endpoints (list, enable/disable, config CRUD, scan)
  - React settings: Plugins tab with card grid, status badges, tier gating, 15 tests
- [x] **Phase 50: Visual Workflow Builder** ✅ 2026-03-21
  - Workflow engine: CRUD, Kahn's DAG validation, topological execution, 4 templates
  - SVG canvas: drag-to-reposition, type-colored nodes, live execution status
  - Editor: add/delete nodes+edges, config sidebar, template selector, 30+ tests
  - BSL-1.1 pro-tier plugin wrapper

### Execution Dependencies

- **Phases A + B** can run in parallel (infrastructure vs settings)
- **Phase C** comes after A+B merge (moves files they modify)
- **Phase D** starts after A is done (needs unified compose)
- **Feature phases (42-50)** are independent of infrastructure phases
- **Phase 48** (Ollama) implements as a BYOK provider from Phase B
- **Phase 46** (multi-modal) requires Phase C4 plugin packaging

### Business Model

- **Core + App** (Apache-2.0): KB, RAG, verification, SDK, GUI — always free
- **Plugins** (BSL-1.1): Multi-modal, advanced analytics, visual workflow — paid, source-available, converts to Apache-2.0 after 3 years
- **Enterprise** (Commercial): Team features, SLA, priority support
- **BYOK**: Users bring their own LLM provider keys. No cerid-hosted LLM costs.

---

## Production Readiness Audit (2026-03-21) ✅

Two-pass audit of the full codebase after completing Phases A-D and 42-50.

### Pass 1 (Sprints A-D): 33 issues found and fixed
- [x] 28,000 lines of dead code removed (`app/` + `core/` directories deleted after Phase C repo restructure)
- [x] All Docker healthchecks standardized across services
- [x] Trading proxy connection pooling (shared httpx client)
- [x] Exception handling hardened across 19 instances (narrowed `except Exception`)
- [x] 27 new frontend tests added (485 total)

### Pass 2: 3 low-severity issues found and fixed
- [x] Stale import references cleaned up
- [x] Unused feature flag defaults corrected
- [x] Minor documentation inconsistencies resolved

---

## Phase 41: SDK Hardening & Multi-Agent Extensibility (2026-03-21) ✅

- [x] Typed Pydantic response models for all 9 SDK endpoints (`models/sdk.py`)
- [x] OpenAPI descriptions, summaries, and error codes on SDK router
- [x] CONSUMER_REGISTRY with per-consumer `allowed_domains` and `strict_domains`
- [x] Domain access control enforced in query pipeline (consumer isolation)
- [x] Trading SDK/proxy endpoints gated by `CERID_TRADING_ENABLED`
- [x] `outputSchema` on all 23 MCP tools
- [x] SDK test suite (`tests/test_router_sdk.py`, 15+ tests)
- [x] Integration guide for new cerid-series agents (`docs/INTEGRATION_GUIDE.md`)
- [x] CeridClient API key auth wired in cerid-trading-agent
- [x] Documentation updated (CLAUDE.md, ISSUES.md, DEPENDENCY_COUPLING.md, tasks/todo.md)
- [x] Code quality: docstrings + structlog on trading proxy endpoints

---

## Phase 40: Semantic Cache, Verification OOM, CI/Docker Hardening (2026-03-16) ✅

### M1: Semantic Cache Activation
- [x] Multi-stage Dockerfile with Arctic Embed M v1.5 ONNX pre-download
- [x] Switch EMBEDDING_MODEL default to Snowflake/snowflake-arctic-embed-m-v1.5
- [x] Update _HNSW_DIM default from 384 to 768
- [x] Add EMBEDDING_MODEL + SEMANTIC_CACHE_DIM env vars to docker-compose.yml
- [x] Add 3 dimension config tests

### J1: Verification OOM Fix
- [x] Raise MCP container memory limit 3G → 4G
- [x] Add cgroup-aware memory guard (_wait_for_memory) with VERIFY_MEMORY_FLOOR_MB=512
- [x] Add 5 memory guard tests

### CI/Docker Hardening
- [x] K7: Convert MCP Dockerfile to 3-stage build (~200MB image reduction)
- [x] K1: Add Codecov upload to CI test job
- [x] K2: Add pip-licenses (Python) + license-checker (Node) to CI
- [x] K3: Add dlint DUO138 ReDoS regex audit to CI security job

### Operational (Post-Deploy)
- [ ] Run `./scripts/backup-kb.sh` before re-ingest
- [ ] Clear all 6 domains via `/kb-admin/clear-domain/{domain}`
- [ ] Re-ingest from `~/cerid-archive/` via file watcher
- [ ] Verify semantic cache activates: `ENABLE_STEP_TIMER=true`, check for `semantic_cache: hit`

---

## Phase 39B: MCP Performance & Rate Limiting Overhaul (2026-03-16) ✅

Systemic evaluation and optimization of rate limiting, circuit breakers, and server-side
infrastructure for the cerid-ai / cerid-trading-agent integration (5 sessions, up to 67.5 calls/min worst-case burst).

### Trading Agent Client Fixes
- [x] Memory queries bypass oracle circuit breaker (`oracle.py`) — non-critical memory failures no longer trip the oracle breaker
- [x] Circuit breaker recovery timeout 60s → 20s (`settings.py`) — ample for local Docker restart + health check
- [x] Split rate limiter into oracle (50/min) + memory (20/min) independent pools (`cerid_client.py`) — oracle queries cannot be starved by background memory calls
- [x] Oracle result cache with 20s TTL (`cerid_client.py`) — 5 sessions hitting same signal = 1 cerid call instead of 5
- [x] 2-second session start stagger (`manager.py`) — eliminates t=0 startup burst

### Server-Side Fixes (cerid-ai)
- [x] Raise `trading-agent` rate limit 60 → 80 req/min (`config/settings.py`) — server no longer the binding constraint (client pools = 70/min are)
- [x] Neo4j pagecache 512 MB → 2 GB, container limit 2G → 4G (`infrastructure/docker-compose.yml`) — full graph in memory, ~600ms disk-read penalty eliminated
- [x] Graph traversal wrapped in `asyncio.to_thread()` (`query_agent.py:357`) — event loop freed during Neo4j Bolt call
- [x] Quality/summaries Neo4j call wrapped in `asyncio.to_thread()` (`query_agent.py:999`) — second blocking call offloaded to thread pool
- [x] BM25 search wrapped in `asyncio.to_thread()` (`query_agent.py:243`) — tokenization no longer blocks event loop
- [x] Quantized int8 ONNX reranker `model_quint8_avx2.onnx` (23 MB) replaces float32 `model.onnx` (91 MB) (`Dockerfile`, `docker-compose.yml`) — 3-4× faster cross-encoder inference

### Performance Results
| Metric | Before | After |
|--------|--------|-------|
| Cold query latency | ~2.0s | ~1.7-1.8s |
| ONNX model size | 91 MB | 22 MB |
| Concurrent throughput | Serialized (event loop blocked) | Parallel (3 ops offloaded) |
| Oracle calls for 5-session signal burst | 5 | 1 (cache) |
| Circuit breaker recovery | 60s | 20s |

---

## Task: Activate Semantic Cache (Phase 40 Candidate)

**Status:** Blocked — requires embedding model migration
**Tracked in:** `docs/ISSUES.md` → M1

**Why blocked:** `ENABLE_SEMANTIC_CACHE=true` is set but the cache never activates. It requires a
client-side embedding function to compute query embeddings for HNSW similarity matching. The default
embedding model (`all-MiniLM-L6-v2`) runs server-side inside ChromaDB — `get_embedding_function()`
returns `None`, so no query embedding is ever computed and the HNSW index is never populated.

### Implementation Steps

- [ ] **1. Choose client-side embedding model**
  - Recommended: `Snowflake/snowflake-arctic-embed-m-v1.5` (768d, 8192 ctx, MTEB SOTA for its size)
  - Alternative: `sentence-transformers/all-MiniLM-L6-v2` ONNX (384d, faster, backward compatible dim)
  - Set `EMBEDDING_MODEL=Snowflake/snowflake-arctic-embed-m-v1.5` in `.env` (or docker-compose.yml)

- [ ] **2. Update Dockerfile**
  - Add ONNX model download for chosen embedding model (alongside reranker downloads)
  - Use quantized variant if available (same pattern as `model_quint8_avx2.onnx`)

- [ ] **3. Update `SEMANTIC_CACHE_DIM` in `config/features.py`**
  - Match the model's output dimension: 768 for Arctic, 384 for MiniLM
  - `SEMANTIC_CACHE_DIM = int(os.getenv("SEMANTIC_CACHE_DIM", "768"))`

- [ ] **4. Re-ingest full KB**
  - Existing ChromaDB collections use server-side embeddings — incompatible dimension/space
  - Per-domain: `DELETE /kb-admin/clear-domain/{domain}` then re-run file watcher / manual ingest
  - Zero-downtime option: create new `-v2` collections, cut over, delete old (more complex)

- [ ] **5. Verify cache activates**
  - Enable `ENABLE_STEP_TIMER=true` temporarily
  - Check `semantic_cache` step in query response latency breakdown
  - Second semantically similar query should show cache hit in logs (`semantic_cache: hit`)

- [ ] **6. Benchmark**
  - Warm cache query: should fall between Redis exact-match (~7ms) and full retrieval (~1.7s)
  - Measure HNSW hit rate across real trading agent queries

**Files:** `config/features.py`, `Dockerfile`, `docker-compose.yml`, `utils/embeddings.py`, `utils/semantic_cache.py`, `deps.py`

---

## Phase 39: Privacy Hardening (2026-03-14) ✅

- [x] Tighten CORS default from wildcard to localhost
- [x] Bind service ports to localhost by default (CERID_BIND_ADDR)
- [x] Add email header anonymization (CERID_ANONYMIZE_EMAIL_HEADERS)
- [x] Add 30-day TTL to Redis ingest audit log
- [x] Add sync directory encryption (auto-enabled with CERID_ENCRYPTION_KEY)
- [x] Add KB context injection transparency indicator in chat
- [x] Update marketing site and CLAUDE.md privacy claims

---

## Feature: Multi-Session Cloud Sync (2026-03-14) ✅

- [x] User state file I/O module (sync/user_state.py) — settings, conversations, preferences
- [x] User state API router — CRUD endpoints at /user-state/*
- [x] Settings persistence to sync dir on PATCH (survives restarts)
- [x] Startup hydration from sync dir (restores settings across machines)
- [x] Docker volume mount change (sync dir now read-write)
- [x] Frontend API client additions (6 new sync functions)
- [x] Conversation cloud sync in useConversations hook
- [x] UI mode + preferences cloud sync
- [x] Sync dir writable validation check
- 38 new Python tests, 13 new frontend tests (1340 + 453 total)

---

## Bugfix: Model Router Resilience (2026-03-13) ✅

- [x] Strengthen temporal query detection + web search routing bonus in chat model router (D4, D5)
- [x] Exclude stale-cutoff models from temporal query routing (D4, D5)
- [x] Add model fallback retry on chat stream errors in chat proxy (D6)
- [x] Handle model fallback metadata in frontend chat stream (D6)
- [x] Fix verification stream abort after extraction starts
- [x] Update docs — 3 new resolved issues, test count 440
- 6 new frontend tests (434 → 440)

---

## Bugfix: Model Router Auto Mode (2026-03-13) ✅

- [x] Fix Settings Pane Select to use `useSettings()` hook instead of boolean server field (D3)
- [x] Add `setRoutingMode` test — 418 frontend tests passing
- [x] Update `docs/ISSUES.md` — D3 resolved

---

## Current: Phase 38D — Expert Verification, UX Enhancements & Per-Message Verification Complete

### Multi-User Authentication (opt-in) ✅
- [x] Create `models/user.py` — User/Tenant Pydantic schemas (52 lines)
- [x] Create `routers/auth.py` — 9 auth endpoints: register, login, refresh, logout, me, API key CRUD, usage (362 lines)
- [x] Create `middleware/jwt_auth.py` — JWT Bearer validation with access/refresh token flow (94 lines)
- [x] Create `middleware/tenant_context.py` — ContextVar tenant/user propagation (55 lines)
- [x] Create `db/neo4j/users.py` — User/Tenant Neo4j CRUD (177 lines)
- [x] Create `utils/usage.py` — Redis per-user usage metering (51 lines)
- [x] Update `config/features.py` — `CERID_MULTI_USER` flag + JWT settings
- [x] Update `main.py` — conditional middleware/router registration
- [x] Update `middleware/auth.py` + `rate_limit.py` — per-user keying when multi-user enabled
- [x] Update `routers/chat.py` + `settings.py` — per-user API key resolution
- [x] 41 new Python tests (test_auth.py, 488 lines)

### Frontend Auth Integration ✅
- [x] Create `contexts/auth-context.tsx` — React auth state with JWT token management (152 lines)
- [x] Create `components/auth/login-page.tsx` — Login/Register UI (126 lines)
- [x] Create `components/auth/protected-route.tsx` — Auth guard component (38 lines)
- [x] Create `components/auth/api-key-settings.tsx` — API key management UI (99 lines)
- [x] Update `App.tsx` — ProtectedRoute wrapper
- [x] Update `lib/api.ts` — 8 auth API functions + token interceptor (87 lines)
- [x] Update `lib/types.ts` — AuthUser + auth request/response types (30 lines)
- [x] 24 new frontend tests (auth-api.test.ts, 184 lines)

### Marketing Website ✅
- [x] Create `packages/marketing/` — Next.js 16 static site with shadcn/ui
- [x] 4 pages: Home, Features, Pricing, Security
- [x] Deploy to Vercel (CLI, production build)
- [x] Configure custom domain: cerid.ai + www.cerid.ai with SSL

#### New Files Created
- `src/mcp/models/user.py`, `src/mcp/routers/auth.py`, `src/mcp/middleware/jwt_auth.py`
- `src/mcp/middleware/tenant_context.py`, `src/mcp/db/neo4j/users.py`, `src/mcp/utils/usage.py`
- `src/mcp/tests/test_auth.py`
- `src/web/src/contexts/auth-context.tsx`, `src/web/src/components/auth/`
- `src/web/src/__tests__/auth-api.test.ts`
- `packages/marketing/` (entire Next.js site)

---

## Phase 32 — Core Retrieval Quality Uplift Complete

### Cross-Encoder Reranker (P0) ✅
- [x] Create `utils/reranker.py` — ONNX cross-encoder inference (`cross-encoder/ms-marco-MiniLM-L-6-v2`)
- [x] Add `RERANK_MODE` config (cross_encoder/llm/none) with score blending weights
- [x] Refactor `rerank_results()` in query_agent.py — three-mode dispatch with auto-fallback
- [x] Pre-download model in Dockerfile (~91 MB baked into image)
- [x] 7 new tests (cross-encoder rerank, fallback, none mode, reranker module)

### Embedding Model Upgrade (P0) ✅
- [x] Create `utils/embeddings.py` — ONNX embedding function (mean pooling, L2 norm, Matryoshka truncation)
- [x] Create `_EmbeddingAwareClient` proxy in `deps.py` — auto-injects embedding function into all ChromaDB calls
- [x] Add `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `EMBEDDING_ONNX_FILENAME` config
- [x] Zero call-site changes — backward compatible when using server default model
- [x] 9 new tests (embedding function, get_embedding_function, aware client)

### Contextual Chunking (P1) ✅
- [x] Create `utils/contextual.py` — LLM-generated situational summaries per chunk
- [x] Add `ENABLE_CONTEXTUAL_CHUNKS` feature flag + `CONTEXTUAL_CHUNKS_MODEL` config
- [x] Integrate into both ingest and re-ingest paths in `services/ingestion.py`
- [x] Graceful failure — original chunks unchanged on any error
- [x] 11 new tests (disabled, empty, enrichment, batching, errors, code blocks, metadata, truncation)

---

## Phase 31 — Deferred Item Resolution Complete

### Type Consolidation ✅
- [x] Remove KBResult type — replaced with KBQueryResult (strict subset, 3 files: types.ts, kb-utils.ts, kb-utils.test.ts)
- [x] Extract BaseClaim interface — 11 shared fields, HallucinationClaim + StreamingClaim extend it
- [x] Rename StreamingClaim.confidence → similarity — aligns with HallucinationClaim naming (3 files)

### Hook Extraction ✅
- [x] Extract useChatSend hook from ChatPanel (chat-panel.tsx 554 → 481 lines, 13% reduction)
- [x] Fix stale-closure bug — kbContext.results missing from handleSend deps, now explicit param
- [x] Add resetAutoInjectCount callback for ChatInput onInputChange

#### New Files Created
- `src/web/src/hooks/use-chat-send.ts`

---

## Phase 30 — Codebase Audit & Debt Reduction Complete

### 10A: Production Quality ✅
- [x] A1 — Chat viewport overflow fix (CSS `min-h-0` cascade)
- [x] B2 — Source attribution in chat (`SourceRef`, collapsible component)
- [x] Apache-2.0 copyright headers on 132 source files
- [x] Update stale documentation (this file, ISSUES.md, reference doc, README)
- [x] Add vitest + @testing-library/react to frontend
- [x] Write foundational frontend tests (34 tests across 3 files)
- [x] Harden CI pipeline (frontend lint + types + tests + build)

### 10B: UX Polish — Model Context Breaks ✅
- [x] B3 + D2 — Model switch divider (visual break when changing models)
- [x] Always-visible model badge with provider colors
- [ ] "Start fresh" option on model switch (deferred to 10E)

### Codebase Audit ✅
- [x] Dependency purge (sentence-transformers, pandas removed, ~700MB Docker savings)
- [x] Docker security hardening (non-root user, .dockerignore, pinned images)
- [x] Dead code removal (unused imports, duplicate functions, AI slop comments)
- [x] Logic consolidation (collection name helper, LLM JSON parsing, centralized constants)
- [x] Error handling overhaul (silent `except: pass` → logged, `print()` → `logger`)
- [x] Input validation (Pydantic response models, parameter bounds)
- [x] Accessibility fixes (33 across 14 components — aria-labels, keyboard nav, sr-only)
- [x] Type safety (tags normalized to `string[]` at API boundary, error cast fixes)
- [x] CI hardening (security scanning, coverage thresholds, Docker image scanning)
- [x] Frontend test expansion (34 → 68 tests: api.ts, model-router.ts, source-attribution)

### Dependency Management ✅
- [x] Standardize Node version to 22 (.nvmrc, Dockerfile, package.json engines)
- [x] Python lock files with pip-compile (requirements.lock with hashes)
- [x] Pin CI tool versions (ruff, bandit, pip-audit, trivy-action SHA)
- [x] Pin Docker image tags (neo4j, redis, nginx, python, node)
- [x] Dependabot configuration (weekly grouped PRs for pip, npm, actions, Docker)
- [x] Pre-commit hook (lock file sync check)
- [x] Cross-service version coupling docs (DEPENDENCY_COUPLING.md)
- [x] CI lock-sync job
- [x] Makefile targets (lock-python, install-hooks, deps-check)

### Modularity Assessment ✅
- [x] Analyze file sizes, coupling, router complexity, agent complexity, utils sprawl
- [x] Identify 4 structural splits needed (F1–F4 in ISSUES.md)
- [x] Identify test coverage gaps (F5 in ISSUES.md)
- [x] Update project plan and tracking docs

### Full-Stack Audit ✅
- [x] Three parallel audits: project docs/plans, code quality, dependency/security/DevOps
- [x] Identified 23 improvement items (3 critical, 4 high, 6 medium, 10 low)
- [x] Step 0 immediate fixes: cryptography declared, httpx-sse pinned, FastAPI broadened, pandas narrowed, Trivy blocking, npm audit blocking
- [x] Integrated all findings into phases 10C–10H — no new phases needed
- [x] Updated ISSUES.md with G1–G22 audit findings section
- [x] Updated task tracker

### 10C: Structural Splits + Security Hardening ✅
- [x] F1 — Extract `ingest_content()` from `routers/ingestion.py` to `services/ingestion.py` (fixes circular import)
- [x] G8 — Add X-Forwarded-For support to rate limiter (configurable `TRUSTED_PROXIES`)
- [x] G9 — Add `RateLimit-Limit/Remaining/Reset` response headers (IETF standard)
- [x] G10 — Redact client IP in auth failure logs (SHA-256 hash prefix)
- [x] G11 — Add `RequestIDMiddleware` (UUID per request, `X-Request-ID` header)
- [x] F2 — Split `routers/mcp_sse.py` — extract tool registry + dispatcher to `tools.py`
- [x] Split `config.py` (33 importers) into `config/settings.py`, `config/taxonomy.py`, `config/features.py`
- [x] Remove duplicate `find_stale_artifacts` in `maintenance.py` (reuse `rectify.py` version)
- [x] Move `audit.log_conversation_metrics()` to `utils/cache.py`
- [x] F3 — Split `utils/graph.py` (827 lines) into `db/neo4j/` package (schema, artifacts, relationships, taxonomy)
- [x] F4 — Split `cerid_sync_lib.py` (1346 lines) into `sync/` package (export, import_, manifest, status, _helpers)
- [x] Split `utils/parsers.py` (875 lines) into `parsers/` sub-package (registry, pdf, office, structured, email, ebook)

### 10D: Test Coverage + CI Hardening ✅
- [x] Tests for `middleware/auth.py`, `middleware/rate_limit.py`, `middleware/request_id.py` (49 tests: auth bypass/enforcement, exempt paths, IP redaction, rate limit headers/enforcement/expiry, XFF proxy resolution, request ID generation/propagation)
- [x] Tests for `services/ingestion.py` (15 tests: content hashing, path validation, duplicate detection, concurrent constraint handling, response shapes, ChromaDB collection naming, Redis logging)
- [x] F5 — Tests for all 5 agents: query_agent (27 tests: dedup, context assembly, cross-domain affinity, rerank fallback, response shape), triage (23 tests: node validation, parse, routing, metadata merge, chunking), rectify (19 tests: duplicate/stale/orphan detection, resolution, distribution), audit (27 tests: activity summary, ingestion stats, cost estimation, query patterns, conversation analytics), maintenance (24 tests: health checks, bifrost sync, purge, collection analysis)
- [x] Tests for `sync/` package (41 tests: SHA-256 file hashing, JSONL read/write/iterate, manifest read/write/validation, Neo4j export, Redis export/import with deduplication)
- [x] Tests for `mcp/tools.py` (24 tests: registry validation, dispatch for all tool types, error paths, argument defaults, async agent tool dispatch)
- [x] Tests for `parsers/` sub-package (108 tests: HTML/RTF stripping, registry validation, parse_file orchestration, text/HTML/EML/RTF/EPUB parsers with real files, PDF/DOCX/XLSX/CSV parsers with mocks)
- [x] Tests for `db/neo4j/` package (54 new tests: schema init, artifact CRUD, relationship creation/discovery/traversal, taxonomy CRUD, subcategory management, tag listing — expanded from 9 to 63 total)
- [x] G12 — Fix pip-audit to scan installed packages including transitive deps (`--desc` flag)
- [x] G13 — Add CodeQL SAST workflow (`.github/workflows/codeql.yml` — Python + JavaScript, weekly + push/PR)
- [x] G14 — Raise coverage threshold from 35% to 55% (actual coverage: 75%)
- [x] G15 — Add bundle size monitoring in CI (fail if any JS chunk >800KB after vite build)
- [ ] Frontend component tests (40+ components with 0 tests — nice-to-have, not gating any release, tracked in Phase 13)

### 10E: Smart Routing Intelligence ✅
- [x] D1 — Token estimator + context replay cost calculation (`calculateSwitchCost`, `buildSwitchOptions` in model-router.ts)
- [x] Context usage indicator in chat dashboard (color-coded green/yellow/red progress bar)
- [x] Summarize-and-switch option for large contexts (`summarizeConversation` API, `useModelSwitch` hook)
- [x] "Start fresh" option on model switch (from 10B) — inline dialog with 3 strategies: continue/summarize/fresh
- [x] Model switch dialog with cost estimates, Recommended badge, context overflow warning
- [x] 26 new frontend tests (model-router cost tests, dialog component tests, conversations hook tests) — 94 total

### Post-10E Audit Fixes ✅
- [x] Debounced localStorage writes during SSE streaming (500ms trailing)
- [x] Lazy-loaded PrismLight syntax highlighter (1619KB → 104KB)
- [x] Batched Neo4j tag creation with UNWIND (N+1 → 1 query)
- [x] Redis SCAN replacing KEYS for production safety
- [x] Dead code removal (3 unused components, -701 lines)
- [x] Module-level ReactMarkdown components extraction

---

## Forward Plan

### Phase 11: Knowledge Intelligence + UI Wiring
- [x] 11A — Interactive audit + agent controls (B1 + rectify/maintain UI wiring)
- [x] 11B — Taxonomy tree sidebar + tag management CRUD (C1)
- [x] 11C — Knowledge curation agent design doc (C2)
- [x] 11D — Operations documentation (G17–G22: OPERATIONS.md, dep coupling, branch protection)

### Phase 12: RAG & Retrieval Excellence ✅
- [x] G16 — BM25 replacement: rank_bm25 → bm25s + PyStemmer (stemming, stopwords, 500x faster)
- [x] E2 — Embedding model evaluation: documented findings, configurable scaffold (EMBEDDING_EVALUATION.md)
- [x] Configurable retrieval weights: HYBRID_VECTOR_WEIGHT, HYBRID_KEYWORD_WEIGHT, RERANK_LLM_WEIGHT, RERANK_ORIGINAL_WEIGHT
- [x] Retrieval evaluation harness: NDCG, MRR, Precision@K, Recall@K, Average Precision (31 tests)

### Phase 13: Conversation Intelligence ✅
- [x] 13A — Conversation-aware KB queries: query enrichment from last 5 user messages, backend `_enrich_query()` with stopword filtering, frontend passes conversation history
- [x] 13B — Auto-injection with confidence gate: configurable threshold (0.82 default), max 3 auto-injected chunks, settings UI toggle + slider, visual indicator during streaming
- [x] 13C — Context budget optimization: max 2 chunks per artifact in assembled context, `continue` past oversized chunks instead of `break`

### Phase 14: Artifact Quality ✅
- [x] 14A — Curation agent: 4-dimension quality scoring (summary, keywords, freshness, completeness), batch Neo4j storage, `POST /agent/curate` endpoint, `pkb_curate` MCP tool (74 tests)
- [x] 14B — Quality-weighted retrieval: `apply_quality_boost()` multiplier after LLM reranking, `relevance * (0.8 + 0.2 * quality_score)`, `get_quality_scores()` batch lookup
- [x] 14C — Metadata boost in retrieval: `apply_metadata_boost()` before reranking, tags/sub_category/keywords matching query terms, capped at 0.15 additive boost
- [x] 14D — GUI wiring: QualityBadge on artifact cards, quality indicator in source attribution, Quality Audit card in monitoring, `fetchCurate()` API, quality_score on KBQueryResult/SourceRef types
- [x] 14E — UI fixes: taxonomy crash fix (sub_category type mismatch), dashboard two-row layout, artifact card OCR cleanup + keywords-as-tags fallback
- [x] 14F — Audit agent visibility + AI synopses: KBOperations moved outside analytics loading gate, Neo4j sub_category/CATEGORIZED_AS backfill migration, search result deduplication by artifact_id, AI synopsis generation via Bifrost Llama (curator agent extended with `generate_synopses` option), synopsis toggle in Quality Audit UI
- [x] 14G — Bifrost model fix + rate-limit hardening: `CATEGORIZE_MODELS` updated with `openrouter/` prefix + `llama-3.3-70b-instruct:free` (old model removed from OpenRouter), synopsis 8s inter-request throttle + 60s retry on 429 (free-tier 8 RPM limit), browser-verified all panes functional

### Phase 15: Realtime Accuracy Watcher & UI Polish

#### 15E: UI Polish ✅
- [x] Response verification panel → right-column upper third (vertical split with KB context)
- [x] Settings pane scroll fix (`min-h-0` on flex container)
- [x] Settings sections collapsible/expandable (localStorage persistence)
- [x] Per-setting info tooltips (replaced memory extraction explainer block)
- [x] Response verification toggle in chat toolbar (Shield icon)
- [x] Status bar service tooltips enhanced (tech name, purpose, status ✓/✗)
- [x] Dashboard metric tooltips (model info, token breakdown, cost breakdown, KB sources)
- [x] Verification status bar above chat input (accuracy %, coherence, claim counts)

#### 15F: Verification Timing + KB Fixes ✅
- [x] Polling for verification report (8 retries, exponential backoff ~31s)
- [x] Status bar states fixed ("Verification ready" instead of "Awaiting response...")
- [x] KB empty state conditional ("No matching knowledge found" vs "Send a message...")
- [x] Context-weighted query enrichment (recency-weighted terms + context alignment boost)

#### 15G: Audit Pane Layout Fix ✅
- [x] Audit pane scroll fix (`min-h-0`)
- [x] Audit pane layout restructure (Operations section separated from Analytics)
- [x] Analytics filters (Period + Show) colocated with analytics content, not in header
- [x] Updated project tracker with 15E/15F/15G completion

#### 15H: Streaming Verification & Accuracy Analytics ✅
- [x] 15H.1 — Streaming verification: replaced polling with SSE streaming (`use-verification-stream.ts` hook, `streamVerification()` API, progressive status bar)
- [x] 15H.2 — Accuracy dashboard: `log_verification_metrics()` in cache.py, `model` param threaded through `check_hallucinations()`, `get_verification_analytics()` in audit.py, `accuracy-dashboard.tsx` component, "Verification" report toggle in audit pane
- [x] 15H.3 — User claim feedback: `POST /agent/hallucination/feedback` endpoint, `log_claim_feedback()` in cache.py, thumbs up/down buttons on ClaimBadge, `submitClaimFeedback()` API
- [x] 15H.4 — Model accuracy comparison chart: `model-accuracy-chart.tsx` with Recharts horizontal BarChart, color-coded accuracy bars, integrated into accuracy dashboard

#### Verification UX Overhaul (Post-15H) ✅
- [x] H4 — Refuted/unverified display status distinction (frontend-only, `getClaimDisplayStatus()` shared utility)
- [x] H4 — Source URL extraction from OpenRouter web search annotations + link icons on claim cards
- [x] H4 — Staleness detection (6 regex patterns) + web search escalation for stale model responses
- [x] H4 — Generator model name in verification prompts for cross-model awareness
- [x] H4 — Session metrics accumulator (claims checked, estimated cost) in verification status bar
- [x] H4 — Feedback button tooltips, web search method badge, accuracy recalculation (refuted-only denominator)
- [x] H5 — Ignorance-admission detection (8 regex patterns, `_is_ignorance_admission()`)
- [x] H5 — Reframed verification prompt (`_SYSTEM_IGNORANCE_VERIFICATION`) — checks underlying facts, not model honesty
- [x] H5 — Verdict inversion (`_invert_ignorance_verdict()`) — supported→unverified if facts exist, refuted→verified if model was correct
- [x] H5 — 23 new hallucination tests (125 total): ignorance detection, verdict inversion, end-to-end verification

### Phase 16: Quality, Cleanup & Polish

> Full details: [docs/plans/DEVELOPMENT_PLAN_PHASE16-18.md](../docs/plans/DEVELOPMENT_PLAN_PHASE16-18.md)

#### 16A: Security & Infrastructure Hardening (Critical) ✅
- [x] Pin Bifrost Docker image (SHA256 digest)
- [x] ~~Pin LibreChat + RAG API Docker images~~ *(deprecated in Phase 27)*
- [x] ~~Externalize PostgreSQL credentials~~ *(deprecated in Phase 27)*
- [x] ~~Add Meilisearch master key env var~~ *(deprecated in Phase 27)*
- [x] Add OPENROUTER_API_KEY startup validation warning
- [x] Add credential vars to .env.example
- [x] Add secret detection to CI (detect-secrets in security job)
- [x] Runtime MCP_URL config for web container (docker-entrypoint.sh + window.__ENV__ + api.ts fallback chain)

#### 16B: Dead Code & API Cleanup ✅
- [x] Remove 6 dead frontend API functions (checkHallucinations, fetchCollections, fetchSupportedExtensions, fetchTags, mergeTags, updateArtifactTaxonomy)
- [x] Remove orphaned test (fetchCollections in api.test.ts), unused type imports (CollectionsResponse, TagInfo)
- [x] Move inline import to module-level (validate_file_path in routers/agents.py)
- [x] Inline single-use `_score_distribution()` helper in curator.py
- [x] Dependency audit: spacy, pandas, python-multipart all confirmed in-use; @tanstack/react-query, tw-animate-css all confirmed in-use

#### 16C: Backend Code Quality ✅
- [x] Extract `_format_chroma_result()` helper in query_agent.py (eliminated 22 lines of duplication)
- [x] Replace manual `extend()` loop with list comprehension in query_agent.py
- [x] Replace `defaultdict(lambda: defaultdict(int))` with `defaultdict(Counter)` in audit.py
- [x] Replace manual Redis `scan()` loop with `scan_iter()` in audit.py
- [x] Add try/except around Neo4j delete in rectify.py `resolve_duplicates()` (crash fix)
- [x] Use `get_chroma()` factory instead of ad-hoc `chromadb.HttpClient()` in query_agent.py
- [x] Move `REDIS_CONV_METRICS_PREFIX` import to module-level in audit.py
- [x] Remove redundant comment in memory.py
- Skipped (by design): dedup pre-check removal (prevents unnecessary ChromaDB writes), BM25 rebuild (already correct), over-abstraction items

#### 16D: Frontend Code Quality ✅
- [x] Extract `tokenCost()` shared utility — replaced 3 duplicated cost calculation patterns
- [x] Extract `getAccuracyTier()` shared utility — unified inconsistent thresholds (80%/0.8 mismatch)
- [x] Extract `parseTags()` shared utility — replaced unsafe inline IIFE with validated JSON parser
- [x] Apply `getAccuracyTier()` in accuracy-dashboard.tsx + verification-status-bar.tsx
- [x] Apply `tokenCost()` in chat-dashboard.tsx + use-live-metrics.ts
- [x] Apply `parseTags()` in api.ts (replaced 1-line IIFE with clean function call)
- [x] Add `useMemo` for modelData sort in accuracy-dashboard.tsx
- [x] Fix unstable React key in knowledge-pane.tsx (removed index from key)
- [x] 18 new utility tests (tokenCost, getAccuracyTier, parseTags) — 111 frontend tests total
- Skipped (by design): TagPills/LoadingDots/SettingsSection components (insufficient duplication), handleSend extraction (already readable), module-level PrismLight (already optimized)

#### 16E: Dependency & Docker Optimization ✅
- [x] Remove unused `langchain-community` dependency (zero imports, -3 transitive packages)
- [x] Narrow `langgraph` and `langchain-openai` lower bounds to `>=0.3.0`
- [x] Extract spaCy model version to `ARG` in MCP Dockerfile
- [x] Remove cache-defeating `apk upgrade` from web Dockerfile
- [x] Add `CHROMA_ANONYMIZED_TELEMETRY=false` and `LOG_LEVEL=WARNING` to ChromaDB config
- [x] Raise CI coverage threshold from 55% to 70%, add XML coverage report for Codecov
- [x] Regenerate `requirements.lock` (langchain-community + transitive deps removed)
- Skipped (by design): mypy (too many untyped third-party libs for useful CI), dependency license scanning (low priority)

#### 16F: Backend Feature Wiring ✅
- [x] Wire `enable_model_router` to server settings (backend `PATCH /settings`, frontend types, hook hydration, settings pane toggle)
- [x] Add `createDomain()`, `createSubCategory()`, `recategorizeArtifact()` API functions
- [x] Taxonomy CRUD in taxonomy tree (inline create domain, create sub-category with + buttons)
- [x] Recategorize action on artifact cards (Move button with domain picker inline)
- [x] Add `archiveMemories()` API function + Archive button in memories pane header
- Skipped (by design): batch triage UI (requires container-side paths, poor GUI UX), digest view (new component, lower priority), plugin management (no backend plugin API or plugin files exist)

#### 16G: Content Experience & Testing ✅
- [x] E1 — Artifact preview: backend `GET /artifacts/{artifact_id}`, preview dialog with syntax highlighting (PrismLight), file type detection utils, Eye button on artifact cards, 6 backend + 19 frontend tests (130 total)
- [x] F6 — cerid-web compose separation: extracted to `src/web/docker-compose.yml`, startup script updated to 5-step
- Deferred to Phase 17+: D2 conversation fork/branch UI (exploratory, 40-60 hrs), frontend component test expansion (20-30 hrs)

#### 16H: Documentation Updates ✅
- [x] Slim CLAUDE.md (685 → 204 lines): moved API reference, agent docs, completed phases to separate files
- [x] Create `docs/API_REFERENCE.md` (endpoints, agents, ingestion, sync, deps)
- [x] Create `docs/COMPLETED_PHASES.md` (all 28 completed phase entries)
- [x] Update ISSUES.md status, mark resolved items, clean priority section
- [x] Update this file with Phase 16-18 structure + doc links
- [x] F6 compose separation doc updates (CLAUDE.md startup order, ISSUES.md, todo.md)
- Deferred: CHANGELOG.md (retroactive from git history)

### Phase 17: iPad & Responsive Touch UX ✅

#### 17A: Touch-Visibility Fixes ✅
- [x] Global touch CSS — `@media (hover: none)` forces `group-hover:opacity-100` visible on touch devices (index.css)
- [x] All hover-hidden controls (copy buttons, delete buttons, taxonomy buttons) now visible via global CSS rule
- [x] Split pane separator touch target — `resizeTargetMinimumSize={22}` gives 44px+ hit area (split-pane.tsx, chat-panel.tsx)

#### 17B: Tablet Layout Optimization ✅
- [x] Responsive sidebar breakpoint (768px → 1024px, app-layout.tsx)
- [x] KB pane as bottom drawer on tablet (Sheet component, chat-panel.tsx)
- [x] Chat toolbar overflow menu — 3 secondary buttons collapse to popover on narrow viewports (chat-panel.tsx)
- [x] Touch-friendly button targets — `@media (pointer: coarse)` enforces 44px min on icon-xs/icon-sm buttons (index.css)
- [x] New components: Sheet (ui/sheet.tsx), Popover (ui/popover.tsx)

#### 17C: iPad-Specific Polish ✅
- [x] Safe area insets — CSS utilities + `viewport-fit=cover` meta tag (index.css, index.html, app-layout.tsx)
- [x] Input zoom prevention for iOS Safari — `font-size: max(16px, 1em)` on inputs (index.css)
- [x] Orientation change handling — matchMedia listener auto-collapses sidebar (app-layout.tsx)

### Phase 18: Network Access & Demo Deployment ✅

#### 18A: LAN Hostname Configuration ✅
- [x] Dynamic LAN IP detection in start-cerid.sh (macOS `ipconfig getifaddr en0` + Linux fallback)
- [x] `CERID_HOST` env var with auto-detection, exports `VITE_MCP_URL`
- [x] Web docker-compose `VITE_MCP_URL` environment passthrough
- [x] CORS already defaults to `*`; documented how to restrict in OPERATIONS.md
- [x] LAN access documentation with troubleshooting (OPERATIONS.md)
- [x] `CERID_HOST`, `CERID_GATEWAY`, `CLOUDFLARE_TUNNEL_TOKEN` added to .env.example

#### 18B: Caddy Reverse Proxy (Local HTTPS) ✅
- [x] Caddy docker-compose + Caddyfile (stacks/gateway/) — routes `/` → web, `/api/mcp/` → MCP, `/api/bifrost/` → Bifrost
- [x] `tls internal` for auto self-signed certs, SSE streaming support
- [x] Startup script optional step [6/6] when `CERID_GATEWAY=true`
- [x] Documentation in OPERATIONS.md

#### 18C: Cloudflare Tunnel (Public Demos) ✅
- [x] Cloudflared container + config (stacks/tunnel/docker-compose.yml)
- [x] `CLOUDFLARE_TUNNEL_TOKEN` env var triggers startup as step [7/7]
- [x] Documentation in OPERATIONS.md (email OTP access policy reference)

### Phase 19: Expert Orchestration & Validation ✅

#### 19A: Circuit Breakers & Resilience ✅
- [x] AsyncCircuitBreaker utility class (utils/circuit_breaker.py) — CLOSED/OPEN/HALF_OPEN states, configurable threshold/timeout
- [x] 5 named circuit breaker instances: bifrost-rerank, bifrost-claims, bifrost-verify, bifrost-synopsis, bifrost-memory
- [x] Wrap query_agent.py reranking (bifrost-rerank, falls back to embedding sort on CircuitOpenError)
- [x] Wrap hallucination.py claim extraction (bifrost-claims) and external verification (bifrost-verify)
- [x] Wrap curator.py synopsis generation (bifrost-synopsis) and memory.py extraction (bifrost-memory)
- [x] Exponential backoff with jitter in deps.py (replaced linear retry)
- [x] `exponential_backoff_with_jitter()` utility function in circuit_breaker.py

#### 19B: Distributed Tracing ✅
- [x] `contextvars.ContextVar` request ID in middleware/request_id.py (`request_id_var`, `get_request_id()`, `tracing_headers()`)
- [x] Request ID propagated to all 5 outbound httpx clients via `headers=tracing_headers()`
- [x] Request ID included in Redis audit log entries (`cache.log_event()`)
- [x] Frontend generates `X-Request-ID` via `crypto.randomUUID()` in `mcpHeaders()`

#### 19C: Chunking Quality Improvements ✅
- [x] Semantic chunking mode (`chunk_text_semantic()`) — paragraph-boundary aware, sentence-integrity preserving
- [x] Table preservation — Markdown table blocks kept as single paragraphs
- [x] Contextual headers (`make_context_header()`) — prepends `Source: | Domain: | Category:` to each chunk
- [x] `CHUNKING_MODE` env var: "semantic" (default) or "token" (original)
- [x] Headers applied in ingestion.py (both paths) and triage.py chunk node

#### 19D: Evaluation Enhancement ✅
- [x] Latency metrics — `time.perf_counter()` per query, P50/P95/P99 percentiles in `summarize()`
- [x] Per-domain metric breakdowns via `summarize_by_domain()`
- [x] Domain field added to `EvalResult` dataclass
- [x] A/B pipeline comparison via `compare_pipelines()` with paired difference analysis and win/loss/tie counts

#### 19E: Adaptive Quality Feedback ✅
- [x] `POST /artifacts/{artifact_id}/feedback` endpoint (inject/dismiss signals)
- [x] Reactive quality_score updates (+0.05 inject, -0.03 dismiss, clamped to [0, 1])
- [x] Feedback events logged to Redis audit trail (signal, query, old/new score)

### Phase 20: Smart Tags & Artifact Quality ✅

#### 20A: Smart Tag System ✅
- [x] Per-domain tag vocabulary (`TAG_VOCABULARY` in config/taxonomy.py) — 10-21 curated tags per domain
- [x] Taxonomy-constrained AI tag generation — prompt includes preferred tags per domain, prefers vocabulary
- [x] Tag quality scoring function (`score_tags()` in utils/metadata.py) — vocabulary match = 0.2, free-form = 0.1, capped at 1.0
- [x] `GET /tags/suggest` endpoint — vocabulary tags first, then popular existing tags, prefix filter, domain scoping
- [x] `TagFilter` component (tag-filter.tsx) — typeahead dropdown with sparkle/hash icons for vocabulary vs existing tags
- [x] Tag filter wired into KB context panel — client-side AND filtering on active tags, domain-scoped suggestions
- [x] `TagSuggestion` type + `fetchTagSuggestions()` API function
- [x] 12 new tests: tag vocabulary validation, tag scoring, suggest endpoint

#### 20B: Artifact Summary Quality ✅
- [x] "What is this?" synopsis prompt rewrite — structured rules, includes filename/domain context
- [x] Improved initial summary extraction (`_extract_summary()`) — sentence-boundary aware, strips Markdown headings
- [x] Synopsis generation passes filename/domain to prompt for context

### Codebase Audit (Post-Phase 20) ✅
- [x] Critical bug fixes: ingestion Neo4j rollback on failure, unbound `memory_type` variable, dead Bifrost sync check removal, fragile ChromaDB URL parsing → `get_chroma()` factory
- [x] Dead code removal: deleted `utils/embeddings.py` (always None), removed `check_semantic_duplicate_batch()`, `TriageState` dataclass, `should_continue_after_categorize()`, `MONTHLY_BUDGET`, `PanePlaceholder` component
- [x] Typing modernization: `Dict`/`List`/`Optional`/`Tuple` → `dict`/`list`/`X | None`/`tuple` across 30+ backend files (agents, routers, utils, services, config, db, sync, parsers, plugins, eval)
- [x] Frontend cleanup: removed duplicate `estimateTokens`, fixed `costSensitivity` always-zero bug, removed unstable React keys, cleaned AI slop comments
- [x] Test deduplication: rewrote `test_smart_ingestion.py` (eliminated overlap with `test_ingestion.py`)
- [x] Lint cleanup: fixed 9 unused imports left by typing modernization agent
- [x] Consolidated mid-file `import json as _json` to top-level in `agents/audit.py`
- [x] All tests pass: 808 Python (5 skipped, 1 xfailed), 130 frontend, ruff clean

### Phase 21: Knowledge Sync & Multi-Computer Parity (Medium)

#### 21A: Sync Infrastructure ✅
- [x] Incremental export (delta-only based on last_exported_at)
- [x] Tombstone support (propagate deletions across machines)
- [x] Conflict detection & resolution strategies (remote_wins, local_wins, keep_both, manual_review)
- [x] Scheduled sync (APScheduler cron, export-on-ingest hook)
- [x] Selective sync (domain/date-range filtering)
- [x] REST endpoints: POST /sync/export, POST /sync/import, GET /sync/status
- [x] CLI flags: --since, --domains, --force, --conflict-strategy

#### 21B: Sync GUI Integration ✅
- [x] Sync types (SyncStatus, SyncExportResult, SyncImportResult, ConflictStrategy) in types.ts
- [x] Sync API functions (fetchSyncStatus, triggerSyncExport, triggerSyncImport) in api.ts
- [x] SyncSection component — collapsible section in settings pane with:
  - Local vs sync count comparison table (artifacts, domains, relations, chunks by domain)
  - Last export timestamp and source machine from manifest
  - Export button with inline success/error feedback
  - Import button with conflict strategy selector (remote_wins, local_wins, keep_both, manual_review)
  - Result summaries showing artifacts created/updated/skipped/conflicts
- [x] 9 new frontend tests (fetchSyncStatus, triggerSyncExport, triggerSyncImport, fetchArchiveFiles)

#### 21C: Drag-Drop Ingestion ✅
- [x] Drop zone on Knowledge pane — drag enter/leave/over/drop handlers with counter-based tracking
- [x] Visual overlay during drag (dashed primary border, "Drop files to ingest" label, z-50)
- [x] Pre-upload options dialog (UploadDialog component) — domain picker (auto-detect + all domains), categorization mode (manual/smart/pro), file list with sizes, batch support
- [x] Single and multi-file drag-drop to options dialog flow
- [x] Touch-compatible — drag-drop works on desktop, existing upload button for touch

#### 21D: Storage Options ✅
- [x] CERID_STORAGE_MODE config (extract_only | archive), default extract_only
- [x] Archive mode copies uploaded files to archive/{domain}/ with collision-safe naming
- [x] GET /archive/files endpoint — lists files by domain, skips hidden/system dirs, includes size
- [x] Storage mode selector in settings pane (Ingestion section)
- [x] fetchArchiveFiles() API function with domain filter
- [x] storage_mode exposed via GET /settings and PATCH /settings
- [x] CERID_STORAGE_MODE added to .env.example
- [x] 11 new Python tests (_archive_file helper, archive listing, storage mode config)

### Phase 22: Deferred Items ✅
- [x] CHANGELOG.md — retroactive from git history, Keep a Changelog format, Phases 0-21
- [x] Env var naming standardization doc — `docs/ENV_CONVENTIONS.md`, current inventory + naming rules
- [x] mypy type checking — `[tool.mypy]` in pyproject.toml, `types-redis` stub, CI step in `.github/workflows/ci.yml`
- [x] Frontend component tests — 139 → 271 tests (13 new test files across Tier 1 components, Tier 2 hooks, Tier 3 secondary)
- [x] Self-RAG validation loop — `agents/self_rag.py`, claims-as-queries retrieval refinement, `ENABLE_SELF_RAG` toggle, per-request override, 28 tests

### Post-Phase 22: Bug Fixes & UI Polish ✅
- [x] H6 — Verification fallback to external for uncertain/unverified claims (4-level fallback chain in `verify_claim()`)
- [x] H7 — UI polish: KB card overflow, dashboard responsive data, icon colors, verification label rename
- [x] Chat dashboard restored inline data with `hidden xl:inline` progressive disclosure

### Phase 23: Production Hardening ✅

#### 23A: Infrastructure Security ✅
- [x] Redis authentication — `--requirepass` + `REDIS_PASSWORD` env var with backward-compatible defaults
- [x] ChromaDB `ALLOW_RESET=false` — disable reset endpoint
- [x] MongoDB authentication — `MONGO_INITDB_ROOT_*` env vars, auth-enabled connection strings
- [x] Caddy security headers — HSTS, X-Content-Type-Options, X-Frame-Options, CSP, Referrer-Policy, Server header removal
- [x] Port binding restrictions — Neo4j/Redis/ChromaDB bound to `127.0.0.1`
- [x] Container resource limits — CPU/memory deploy limits for Neo4j, ChromaDB, Redis

#### 23B: CI/CD Improvements ✅
- [x] Job timeouts — all 7 CI jobs have timeout-minutes (5–45 min)
- [x] Pip caching — `cache: pip` + `cache-dependency-path` on test job
- [x] Dockerfile linting — hadolint for MCP and Web Dockerfiles
- [x] Cloudflare Tunnel image pinned to `2025.4.2`

#### 23C: Backend Concurrency Fixes ✅
- [x] Semaphore race condition fix in `hallucination.py` — module-level initialization
- [x] Bounded MCP session queues (`maxsize=100`) + oldest-session eviction
- [x] Configurable `NEAR_DUPLICATE_THRESHOLD` env var
- [x] Neo4j `.single(strict=False)` in ingestion — safe on 0 results
- [x] Neo4j circuit breaker with `call_sync()` for synchronous operations

#### 23D: Operational Improvements ✅
- [x] Smart health check polling — `wait_for_service()` replaces fixed `sleep 30`
- [x] Docker Compose V2 version check in startup script
- [x] `.env.example` — required/optional header, infrastructure auth section

#### 23E: Test Coverage Quick Wins ✅
- [x] Router health tests (7) — `/health`, `/collections`, `/scheduler`, `/plugins`
- [x] Router settings tests (11) — `GET /settings`, `PATCH /settings` with validation
- [x] MCP SSE protocol tests (12) — JSON-RPC methods, session management, queue bounds, eviction

### Phase 24: RAG Evolution — Expanded Verification ✅
- [x] Evasion claim detection — model hedging on factual questions (dedicated system prompt)
- [x] Citation claim detection — fabricated source verification
- [x] Recency claim detection — stale data identification
- [x] Ignorance claim detection — knowledge gap awareness
- [x] Verdict inversion logic for each new claim type
- [x] Frontend rendering (orange for evasion, purple for citation)
- [x] Streaming pipeline accepts user_query and conversation_history for context-aware verification
- [x] 931 Python tests, 281 frontend tests

### Phase 25: Smart Routing & Context-Aware Chat ✅

#### 25A: Direct-to-OpenRouter Chat Proxy ✅
- [x] New `/chat/stream` endpoint in MCP server (FastAPI + httpx SSE proxy)
- [x] `cerid_meta` SSE event for model confirmation
- [x] Frontend `streamChat()` rerouted from Bifrost to MCP proxy
- [x] Model catalog updated to 9 models with March 2026 pricing/capabilities
- [x] Model IDs aligned across frontend, backend, and Bifrost config
- [x] Model router algorithm fixed: sensitivity gates switching threshold
- [x] Bifrost retained for internal agent/verification LLM calls only
- [x] `cerid_meta_update` SSE event parses actual model from OpenRouter stream
- [x] `formatCost()` utility with precision tiers ($0.00, <$0.01, $X.XX)
- [x] Boundary tests for model router edge cases

#### 25B: Intelligent Model Selection ✅
- [x] Capability-based model scoring (`scoreModelForQuery()`) with intent detection weights
- [x] Three-way routing mode (`RoutingMode: "manual" | "recommend" | "auto"`) replacing boolean `autoModelSwitch`
- [x] Enhanced recommendation reasoning with capability %, cost delta, detected intent
- [x] Auto-routing: silent model switch at send time with toast indicator
- [x] Settings pane three-way selector (Manual / Recommend / Auto)
- [x] Model dropdown capability badges (code/reason/create/facts) + cost-per-turn estimate
- [x] 51 model-router tests (scoring, intent detection, cost sensitivity)

#### 25C: Context-Aware Chat ✅
- [x] User correction injection — truncate at corrected message, inject `[Correction]` prefix, re-generate
- [x] Token-budget KB injection — context-window-aware chunk fitting (replaces hardcoded `slice(0, 3)`)
- [x] Semantic dedup — Jaccard word-set similarity removes overlapping KB chunks before injection
- [x] Domain headers — structured `--- domain > sub_category | filename ---` prefix on KB chunks
- [x] Inline verification trigger — per-message verify button, manual bump state
- [x] `kb-utils.ts` utilities: `jaccardSimilarity()`, `deduplicateChunks()`, `formatChunkWithHeader()`
- [x] 13 KB utils tests, correction flow integrated
- [x] 939 Python tests, 320 frontend tests (24 test files)

### Production Audit (Post-Phase 25) ✅
- [x] Shared Bifrost call utility (`utils/bifrost.py`) — consolidated 7 inline httpx+circuit-breaker+tracing patterns
- [x] Narrowed exception handling — replaced `except Exception` with specific types in 4 agents
- [x] nginx security headers (X-Content-Type-Options, X-Frame-Options, Referrer-Policy) + SSE timeouts (300s)
- [x] Docker resource limits on all services (MCP 4cpu/2G, Dashboard 1cpu/512M, Web 1cpu/256M, Bifrost 2cpu/1G)
- [x] Vite production sourcemaps disabled
- [x] Frontend API error handling consolidated (`extractError()` in 4 call sites)
- [x] Settings toggle factory (`useSyncedToggle()` for 3 server-synced toggles)
- [x] Makefile frontend targets (lint, test, typecheck, build, check-all, help)
- [x] 950 Python tests, 320 frontend tests

### Phase 26: User Review — Verification Logic, UX Fixes, and Backlog ✅

#### Sprint 1 — Immediate Fixes ✅
- [x] V9: Clear stale verification status when new response streams
- [x] V5: Touch-visible trash icon on conversation list
- [x] V8: Remove KBOperations duplicate from audit tab
- [x] V12: Add tooltips on confidence bars and quality badges
- [x] V7: KB auto-inject toggle in KB context pane

#### Sprint 2 — Quick Wins ✅
- [x] V17: Injection badge detail popover (artifact names, domains, snippets)
- [x] V4: Investigate settings pane scroll — confirmed structurally correct (no fix needed)

#### Sprint 3 — Medium Tasks ✅
- [x] V11: Compute quality_score during ingestion (7-signal scoring, both create + re-ingest paths)
- [x] V1a: Surface found data in ignorance verification (verification_answer in SSE + "Found answer" UI)
- [x] V2: Verification source URL click-through (KB artifact click → KB pane, external URLs with domain labels)
- [x] V16: Show artifact summary from Neo4j on knowledge cards (batch fetch + enrich pipeline)

#### Sprint 4 — Persistence + Investigation ✅
- [x] V15: Persist verification state across tab switches (in-memory cache + instant restore)
- [x] V10: Audit model switch cost calculation (3 new tests: cheap→expensive, expensive→cheap, same model)
- [x] V18: Investigate injection perception — confirmed injection code correct, V17 popover provides visibility

### Phase 28: UX Backlog Clearance ✅

#### Sprint 1 — Context Menus + Quick Fixes ✅
- [x] V6: Right-click context menus on toolbar icons (radix-ui ContextMenu wrapper, 4 toolbar menus)
- [x] V3: Quick-access memory extraction toggle (via Rss context menu + narrow viewport overflow)
- [x] V4: Settings scroll investigation (confirmed structurally correct, no fix needed)

#### Sprint 2 — Feature Tier + Infrastructure Settings ✅
- [x] V13: Feature tier tooltip (descriptive Community vs Pro tier info)
- [x] V14: Infrastructure settings section (read-only infra display + search tuning sliders)

#### Sprint 3 — Proactive Model Switch ✅
- [x] V1b: Post-verification model switch banner (ignorance + verification_answer → suggest webSearch model)

#### Sprint 4 — Drag-Drop ✅
- [x] V19: Drag-drop to KB context pane (file drop → UploadDialog → ingest)
- [x] V20: Drag-drop to chat input (file drop → UploadDialog → ingest)

#### Sprint 5 — Inline Verification Markups ✅
- [x] V22: Inline verification markups (matchClaimsToText + DOM mark highlighting, 7 new tests)

### Phase 30: Codebase Audit & Debt Reduction ✅

4 expert audit agents examined the entire codebase (frontend + backend) across orthogonal dimensions: dead code, dependency bloat, code quality/AI slop, and architecture/coupling. 49 issues found, 19 actioned across 5 sprints.

#### Sprint 1 — Dead Code Removal ✅
- [x] Remove dead types from types.ts (ArtifactChunk, CollectionsResponse, TagInfo, Domain)
- [x] Remove dead API exports (ArchiveFile, ArchiveFilesResponse, fetchArchiveFiles)
- [x] Mark model-router internal functions with @internal (scoreQueryComplexity, detectIntentWeights, scoreModelForQuery, calculateSwitchCost)
- [x] Un-export UseVerificationStreamReturn from use-verification-stream.ts
- [x] Remove dead Python code (get_plugin(), cerid_sync_lib.py compat shim)
- [x] Extract magic numbers (UPLOAD_STATUS_RESET_MS, MIN_SUGGESTION_LENGTH, model-router thresholds)

#### Sprint 2 — Duplication Extraction ✅
- [x] Extract useDragDrop hook — eliminated 3 copies of drag-and-drop handlers (~80 lines saved)
- [x] Extract CHART_TOOLTIP_STYLE constant — eliminated 3 copies across audit charts
- [x] Consolidate DOMAIN_COLORS — taxonomy-tree.tsx imports from domain-filter.tsx
- [x] Move formatFileSize to shared utils
- [x] Promote DomainBadge to @/components/ui/domain-badge (used by 7 files across 3 domains)

#### Sprint 3 — ChatPanel Decomposition ✅
- [x] Extract ChatToolbar component (275 lines — toolbar with 6+ context menus)
- [x] Extract useVerificationOrchestrator hook (195 lines — all verification state, report caching, saved report fetching)
- [x] Extract ChatMessages component (94 lines — message list with dividers + auto-scroll)
- [x] Rewrite chat-panel.tsx integrating all 3 modules (896 → 554 lines, 38% reduction)

#### Sprint 4 — Consistency & Patterns ✅
- [x] Standardize API error handling — 24 functions updated to use extractError()
- [x] Convert artifact-preview.tsx from useState/useEffect to useQuery (React Query)
- [x] Fix streaming re-render storm in use-live-metrics.ts (useRef + tick threshold, ~500 → ~5 re-renders)
- [x] Remove dead branch in knowledge-pane.tsx handleDrop

#### Sprint 5 — Python Backend Cleanup ✅
- [x] Document eval/harness.py as development tool (unreachable, no router/CLI)
- [x] Add deprecation notice to src/gui/app.py (React GUI is primary)

#### Deferred Items (resolved in Phase 31)
- [x] ClaimItem extraction — confirmed not real duplication; extracted BaseClaim, renamed confidence → similarity
- [x] useChatSend extraction — extracted to hook, fixed stale-closure bug (ChatPanel 554 → 481 lines)
- [x] Overlapping type consolidation — KBResult removed (subset of KBQueryResult), BaseClaim extracted

#### New Files Created
- `src/web/src/hooks/use-drag-drop.ts`
- `src/web/src/hooks/use-verification-orchestrator.ts`
- `src/web/src/lib/constants.ts`
- `src/web/src/components/chat/chat-toolbar.tsx`
- `src/web/src/components/chat/chat-messages.tsx`
- `src/web/src/components/ui/domain-badge.tsx`

### Phase 29: V21 Chat Response Formatting & Inline Verification ✅

#### Sprint 1 — Markdown Rendering Improvements ✅
- [x] Expanded MD_COMPONENTS with 15 element overrides (links, tables, blockquotes, headings, lists, images, hr)
- [x] External links with ExternalLink icon and target="_blank"
- [x] Bordered/striped tables with horizontal scroll wrapper
- [x] Styled blockquotes with left border + muted background
- [x] Heading hierarchy (h1-h4) with proper sizing and borders
- [x] CollapsibleCodeBlock for fenced blocks >25 lines (gradient fade + expand button)

#### Sprint 2 — Interactive Inline Verification ✅
- [x] ClaimOverlay component (hybrid DOM + React portal approach)
- [x] Enhanced DOM marks with data-claim-index + superscript footnotes [N]
- [x] Click-to-popover with status badge, claim text, source info, verification method, URLs
- [x] Hover tooltip (displayStatus + source_domain)
- [x] Extracted shared display utilities to verification-utils.ts (DISPLAY_STATUS_COLORS, verificationMethodLabel, verificationMethodColor)
- [x] Wired onArtifactClick through MessageBubble → chat-panel.tsx for KB navigation

#### Sprint 3 — Document Navigation + Tests ✅
- [x] extractText() utility for recursive React children text extraction
- [x] Heading IDs via slugification on h1-h4 overrides
- [x] MessageTOC component (clickable TOC with smooth-scroll, threshold: 3+ headings)
- [x] 9 new message-bubble tests (links, tables, blockquotes, headings, code collapse, TOC)
- [x] 8 new claim-overlay tests (popover rendering, status badges, source navigation, dismiss)
- [x] Documentation updates (ISSUES.md, todo.md, CLAUDE.md)

### Phase 27: Configuration Hardening (Open-Source Readiness) ✅

#### 27A: Robust Host Detection ✅
- [x] `detect_lan_ip()` function — iterates en0–en5, ifconfig scan, Linux hostname -I, with source logging
- [x] Force-recreate cerid-web when VITE_MCP_URL changes (prevents stale IP bug)

#### 27B: Pre-flight Validation ✅
- [x] `preflight_checks()` function — port conflict detection, required env var validation, disk space warning
- [x] `--force` flag to bypass pre-flight checks
- [x] Own-container skip (don't flag our running containers as conflicts)

#### 27C: Post-startup Reachability Validation ✅
- [x] Bifrost added to health wait chain
- [x] LAN MCP URL reachability check with actionable diagnostics
- [x] Structured colored health output via `check_health()` helper
- [x] Exit code 2 for partial start (critical services unreachable)

#### 27D: First-Run Experience ✅
- [x] `setup.sh` rewritten as guided 7-step installer (delegates to start-cerid.sh)
- [x] Interactive API key prompt + auto-generate NEO4J_PASSWORD
- [x] Idempotent — safe to re-run
- [x] `.env.example` inline `# REQUIRED` markers with signup URLs
- [x] `CONTRIBUTING.md` Quick Start + stale counts updated

#### 27E: Configuration Flexibility ✅
- [x] `CERID_PORT_*` env vars (8 ports) in 4 docker-compose files
- [x] `start-cerid.sh` exports port defaults, uses them in preflight/health/URLs
- [x] `.env.example` Port Overrides section
- [x] `docs/ENV_CONVENTIONS.md` Port Overrides table + naming rule

### Backlog

#### Verification UX
- [ ] Verification pane should show cards for the most recent response only; clicking a previous assistant reply should swap to that message's verification cards (currently verification state does not clear and re-run per reply)

#### Attachment & Privacy
- [ ] Attachment hand-off across model switches — ensure the next model receives the attachment (or relevant extracted data) when Bifrost routes to a different provider mid-conversation
- [ ] Privacy-aware attachment processing — pre-process attachments into KB artifact facts/context selections before outbound LLM calls, avoiding full-file transmission while keeping the UX seamless ("it just works")

#### Chat / History UX
- [ ] History pane: show trash/delete icon on mouseover of each conversation line item (not always visible)
- [ ] Move "New Chat" button out of the history list — place it near the top beside the Chat tab for easier access

### Dropped
- [x] ~~D2: Conversation fork/branch UI~~ — Dropped (2026-03-08). Core model-switch UX complete; fork UI has unclear ROI for 40-60 hrs

## Completed Phases

- [x] Phase 0–1: Core ingestion pipeline, metadata, AI categorization, deduplication
- [x] Phase 1.5: Bulk ingest hardening, concurrent CLI, atomic dedup
- [x] Phase 2: Agent workflows (Query, Triage, Rectification, Audit, Maintenance), 12 MCP tools
- [x] Phase 3: Streamlit dashboard, Obsidian vault watcher
- [x] Phase 4A: Modular refactor — split main.py into FastAPI routers
- [x] Phase 4B: Hybrid BM25+vector search, knowledge graph traversal, cross-domain connections
- [x] Phase 4C: Scheduled maintenance (APScheduler), proactive knowledge surfacing, webhooks
- [x] Phase 4D: 36 tests passing, GitHub Actions CI, security cleanup, centralized encrypted `.env`
- [x] Phase 5A: Infrastructure compose (Neo4j, ChromaDB, Redis), 4-step startup script, env validation
- [x] Phase 5B: Knowledge base sync — JSONL export/import CLI, auto-import on startup, Dropbox sync
- [x] Phase 6A: Foundation + Chat — React 19 scaffold, sidebar nav, streaming chat via Bifrost SSE
- [x] Phase 6B: Knowledge Context Pane — split-pane, artifact cards, domain filters, graph preview
- [x] Phase 6C: Monitoring + Audit Panes — health cards, collection charts, cost breakdown
- [x] Phase 6D: Backend Hardening — API key auth, rate limiting, Redis query cache, bundle splitting
- [x] Phase 7A: Audit Intelligence — hallucination detection, conversation analytics, feedback loop
- [x] Phase 7B: Smart Orchestration — model router, 15 MCP tools
- [x] Phase 7C: Proactive Knowledge — memory extraction, smart KB suggestions, memory archival
- [x] Phase 8A: Plugin system — manifest-based loading, feature tiers, OCR scaffold
- [x] Phase 8B: Smart ingestion — new parsers (.eml, .mbox, .epub, .rtf), semantic dedup
- [x] Phase 8C: Hierarchical taxonomy — TAXONOMY dict, sub-categories/tags, taxonomy API
- [x] Phase 8D: Encryption & sync — field-level Fernet encryption, pluggable sync backends
- [x] Phase 8E: Infrastructure audit — 31 findings, security fixes, test DRY, N+1 fix
- [x] Phase 9A: Fix 3 user-reported bugs — KB error state, Neo4j health normalization, audit stats
- [x] Phase 9B: Wire 5 structural gaps — hallucination auto-fetch, smart suggestions, memory trigger, settings sync, live metrics
- [x] Phase 9C: 3 feature enhancements — file upload, sub-category/tag display, tag browsing
- [x] Phase 9D: Neo4j auth hardening — docker-compose env var fix, Cypher auth validation, error detail
