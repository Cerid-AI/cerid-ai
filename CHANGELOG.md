# Changelog

All notable changes to cerid-ai are documented here.

## v0.84.0 — Reliability Remediation (2026-04-17 → 2026-04-18)

Audit-driven reliability, data-wiring, UX, and LLM-integration fixes across 32 commits. Every P0/P1 concern from the 2026-04-17 live beta audit addressed. Full plan at [`tasks/2026-04-17-reliability-remediation-plan.md`](tasks/2026-04-17-reliability-remediation-plan.md).

### Correctness (Wave 0)
- **`QueryEnvelope` single-writer** — unified `/agent/query` response shape. External results are now always mirrored into `sources` and `source_breakdown.external`; the degraded-budget path no longer drops them.
- **VerificationReport edges + backfill** — writer now always creates `[:EXTRACTED_FROM]` and `[:VERIFIED]`; stores `source_urls` / `verification_methods` on the node for external-verified claims. One-shot `m0001` migration backfills pre-existing reports.
- **Frontend triple-fire killed** — one chat turn used to spawn 3 identical `/agent/query` POSTs. `useChatSend` now skips the redundant refetch when TanStack cache is warm.
- **`DegradedBanner`** — ungrounded answers surface an amber banner with the backend's `degraded_reason`.
- **External source attribution** — `source_breakdown.external` merges into the assistant's "Sources used" pane.

### Reliability (Wave 1)
- **Retrieval budget + CB tuning** — external data-source circuit breakers relaxed from `failure_threshold=1, recovery=120s` to `3 / 30s`. Router-level CRAG gate suppresses external fan-out when top KB relevance ≥ `RETRIEVAL_QUALITY_THRESHOLD`.
- **Cancellation-safe SSE** — `/chat/stream` polls `request.is_disconnected()` and catches `CancelledError` / `GeneratorExit` to close upstream OpenRouter sockets on client abort. O(chunks²) usage parse short-circuited.
- **Partitioned concurrency pools** — `KB` / `CHAT` / `HEALTH` replace the process-wide `_QUERY_SEMAPHORE(2)`. `/health` polling no longer serializes behind chat turns. Queue depth visible at `/observability/queue-depth`.
- **Frontend abort cleanup** — `useChat` aborts on unmount. `queryKBOrchestrated` accepts an `AbortSignal` and threads TanStack's `signal` through.
- **Graceful 429** — rate-limit middleware returns `429` with `Retry-After` header and JSON body `retry_after` instead of dropping connections under burst load.

### Trust (Wave 2)
- **Claim cache schema v2** — keyed on `(claim, model, tier, response_context)`. No more stale verdicts across model swaps or pronoun-resolved claim collisions.
- **Cited-URL verification** — claims with `source_urls` fetch the page and NLI-entail before considering web search. Fabricated citations no longer get confirmed from unrelated search hits.
- **Stream-abort claim finalizer** — pending claim cards flip to `uncertain` on verify-stream close; no more forever-spinning popovers.
- **Startup invariants in `/health`** — collection dim checks, `verification_report_orphans` count, NLI load status. `/health` returns 503 on critical invariant violation.
- **`X-Cache` header + `cached: true` body** — cache hits observable from the client.
- **Version SSOT** — `/`, `/health`, and FastAPI `app.version` all read from `pyproject.toml` via `core/utils/version.py`. `/api/v1/*` dual mount retired.
- **Smart-router scored classification** — replaces first-keyword-match with weighted signals. `cost_sensitivity` now plumbs from `/agent/query` through `route()` and `call_llm`. All registered model IDs carry the `openrouter/` prefix.

### Hygiene (Wave 3)
- Favicon / apple-touch-icon / viewport zoom-lock + conversation-search a11y labels + rapid-Enter race guard.
- Rate-limit now covers `/setup/*`, `/admin/*` GETs, `/observability/*` GETs.
- **Semantic-cache dim self-heal** — stored HNSW blobs carry a magic+dim header; mismatch on load deletes the blob and cold-starts.
- **Dropbox `EDEADLK` retry** — 3-attempt exponential backoff; structured final-failure warning surfaced to the GUI.
- **Shared `httpx.AsyncClient` for OpenRouter credits** — module-level client + lazy getter eliminates per-poll socket churn.
- **Cross-machine settings/conversations reconciliation** — `updatedAt`-based version vector; drift now resolves on the next load.
- **Graceful reranker fallback** — ONNX cross-encoder failure returns results in original order with `reranker_status: "onnx_failed_no_fallback"` instead of crashing.

### Bifrost retirement (audit C-4)
- Bifrost **fully retired** — no container, no helper module, no URL. `utils/bifrost.py`, `core/utils/bifrost.py`, `stacks/bifrost/` deleted.
- Three pipeline callers migrated to `core.utils.llm_client.call_llm` (`utils/metadata.py` topic extraction, `core/utils/contextual.py` chunk summaries, `core/agents/maintenance.py` health probe).
- `USE_BIFROST` / `CERID_USE_BIFROST` env vars removed. `BIFROST_TIMEOUT` kept as a legacy name for a generic LLM timeout.
- `bifrost-*` circuit-breaker names preserved as historical identifiers for call-site categories (rerank / claims / verify / synopsis / memory / compress / decompose).
- `call_llm` / `call_llm_raw` now raise `RuntimeError` when `OPENROUTER_API_KEY` is unset — no silent re-route.
- nginx `/api/bifrost/` proxy removed.

### Testing + CI
- **Smoke harness** (`src/mcp/tests/load/smoke.py` + `make smoke`) — 8 scenarios covering `/health` concurrency, response-shape invariants, cache hit-rate, 429 graceful behaviour, SSE cancel, HOL blocking, CB flap.
- **4 previously-skipped tests restored** (+5 active / -5 skipped): SSE generator `CancelledError` path, reranker graceful-fallback contract, 3 agent-query budget-fixture tests.
- **CRAG inner-gate regression test** scans `query_agent.py` source to prevent re-introduction of the duplicate gate.
- **+16 new backend tests** across VerificationReport persistence, dim-validation, envelope shape-invariants, SSE cancellation, concurrency pools, claim cache key, cited-URL verification, startup invariants, metrics middleware, rate limiting, settings-sync retry, reranker fallback.
- **CI hygiene** — lint / typecheck / tsc / lockfile blockers cleared. Pinned `ruff==0.15.4` + `pip-tools==7.5.3` to match CI exactly.

### Deferred
- **Task 18 — chat-messages virtualization.** First attempt broke 46 testing-library measurement-dependent tests under jsdom. Needs a `@tanstack/react-virtual` approach that doesn't interfere with `measureElement` in jsdom.

### Post-deploy actions
- Run `python -m scripts.run_migrations` (m0001) to backfill existing `VerificationReport` provenance.
- `make version-file` is now part of `scripts/start-cerid.sh --build` so `get_version()` returns the real version in the MCP image.
- Operators see a one-time `embedding_dim_mismatch` ERROR log per mismatched Chroma collection, pointing at `POST /admin/collections/repair`.
- Claim cache v2 cold-starts existing `verf:claim:*` entries — expect 10-20× latency on first verification pass until the cache rewarms.

## v0.83.0 — Verification Hardening + Memory Efficacy + Bug-Hunt Sprint (2026-04-10 → 2026-04-15)

### Verification Pipeline Hardening (2026-04-13)
- **Round-2 claim sweep** — timed-out claims re-verified in a second pass with full conversation context
- **Expert verification mode** — Grok 4 as dedicated verification model for high-stakes claims (`VERIFICATION_EXPERT_MODEL`)
- **Authoritative external verification** — LLM synthesizes from external data sources rather than parametric memory
- **Graph-guided verification** — Neo4j relationship structure used as evidence for fact-relationship checks
- **Fact-relationship verification** — temporal/entity/specificity alignment validation
- **Dynamic confidence scoring** — per-source tuning (Wikipedia title match boost, Wolfram non-answer detection, DuckDuckGo .gov boost)

### Memory Efficacy (2026-04-13)
- **Source-aware external query construction** — per-source `adapt_query()`/`is_relevant()` with intent-based routing across 7 data sources
- **CRAG retrieval quality gate** — supplements with external sources when top KB relevance < `RETRIEVAL_QUALITY_THRESHOLD` (0.4)
- **Verified-fact-to-memory promotion** — high-confidence verified claims auto-promote to empirical `:Memory` nodes with `VERIFIED_BY` provenance
- **Tiered memory authority boost** — 4-tier system (0.05-0.25) based on verification status and confidence
- **Refresh-on-read memory decay** — Ebbinghaus rehearsal pattern resets `decay_anchor` on retrieval
- **NLI consolidation guard** — prevents semantic drift during memory merges via entailment threshold

### Bug-Hunt Sprint (2026-04-15) — 15 bugs → 8 root causes
- **Embedding singleton** — fixed split instantiation causing dimension mismatch on fresh installs + startup dim-check + `/admin/collections/repair` endpoint
- **Agent activity stream** — `/agents/activity/*` alias router + SSE exponential backoff (500ms base, 30s max) + abort-on-unmount
- **Healthcheck rewrite** — shared `scripts/lib/healthcheck.sh` library with auth-aware Redis/Neo4j checks + Bifrost skip + zombie container cleanup
- **Onboarding polish** — `CERID_SYNC_DIR_HOST` rename (backward-compat fallback), removed `age` from public README prereqs, fixed CONTRIBUTING.md Node/router path drift
- **Verification wiring** — `MIN_VERIFIABLE_LENGTH` FE/BE alignment 200→25, `onSelectForVerification` prop threaded through to `VerificationBadge`
- **UX fixes** — tab title "Cerid Core"→"Cerid AI", KB counter unification (`Showing X of Y`), Knowledge Digest errors drill-through modal with `DigestErrorItem` type

### Dependency Upgrades
- langgraph 0.6 → 1.1 (major)
- neo4j driver 5.28 → 6.1 (major)
- TypeScript 5.9 → 6.0 (major)
- Vite 7 → 8, @vitejs/plugin-react 5 → 6 (major)
- jsdom 28 → 29, lucide-react v0.577 → v1.8
- React 19.2.5, @tanstack/react-query 5.99

### Testing & CI
- **+14 frontend tests** (705 → 719) — verification orchestrator, agent activity stream, KB counter, digest drill-through
- **+4 backend tests** — embedding singleton, startup dim-check, collections repair, agent console router
- Sync manifest hygiene — `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `__pycache__` excluded from public sync
- Dependabot: ignore ESLint majors until react-hooks plugin supports v10, revert chromadb/langgraph upper-bound widening

### Documentation Re-Baseline (2026-04-15)
- Comprehensive audit: all open issues validated against code (zero actual bugs remaining)
- Version aligned across pyproject.toml, package.json, CLAUDE.md, tasks/todo.md
- Test counts updated (2,413 Python / 719 frontend), tool counts corrected (26 = 21 core + 5 trading)
- CI coverage floor corrected in docs (20%, not 70%)
- Stale todo items archived (leapfrog merge completed April 5, all B-CRITICAL/B-HIGH resolved)

## v0.82.0 — Unified Implementation Plan + Phase C Architecture (2026-04-05 → 2026-04-10)

### Phase C: Core Extraction + NLI Architecture (2026-04-08 → 2026-04-10)
- **Core/App split** — portable orchestrator core (`core/`) separated from application layer (`app/`). Bridge modules in `agents/`, `utils/`, `services/` re-export for backward compat.
- **`*_internal.py` pattern** — 7 Python files + 1 TypeScript file hold internal-only code; an internal bootstrap module registers the corresponding private routers at startup.
- **NLI entailment service** — `core/utils/nli.py` (ONNX, <10ms) powers verification, Self-RAG, RAGAS, and RAG pipeline claim validation.
- **Sync manifest** — `.sync-manifest.yaml` declares internal-only files, mixed files (hook markers), and forbidden strings for automated repo sync via `scripts/sync-repos.py`.
- **Contract ABCs** — `core/contracts/` defines VectorStore, GraphStore, CacheStore, LLMClient interfaces.
- **Concrete stores** — `app/stores/` implements ChromaVectorStore, Neo4jGraphStore, RedisCacheStore.
- **Source authority** — chat transcripts discounted 0.35x, memories retain full relevance.

### Post-Phase: Dependency Cleanup + Remaining Items
- **Dependency cleanup** — removed 8 unused deps (stripe/public, faster-whisper, requests, structlog/public, pytesseract, Pillow, bcrypt, PyJWT). Docker image 4.09→3.18 GB. Dependabot 33→2 vulns.
- **packages/desktop/** removed from public repo (kept in internal)
- **B31: Conversation grouping** — feedback from same conversation_id appends to existing KB artifact
- **B33: Feedback buttons** — ThumbsUp/ThumbsDown on assistant messages (POST /artifacts/{id}/feedback)
- **B35: Model compliance note** — footer in model selector about non-US model availability
- **B36: File picker** — browse button on archive path using File System Access API
- **Memory system fix** — get_collection → get_or_create_collection (fixes 500 on fresh installs)
- **Configurable model preload** — `CERID_PRELOAD_MODELS=false` Dockerfile ARG for smaller images
- **Startup prerequisites** — python3, curl, port availability, Docker memory checks
- **CI fixes** — test mock targets (requests→httpx), import sorting (I001), BLE001 suppressions

### Phase 1: Tiered Inference Detection
- **InferenceConfig singleton** — auto-detects platform (macOS ARM/Intel, Linux, Windows), GPU (Metal/CUDA/ROCm/DirectML), Ollama, and FastEmbed sidecar at startup
- **Dynamic ONNX providers** — embeddings.py and reranker.py use detected GPU providers instead of hardcoded CPU
- **Health endpoint** — `/health` now includes `inference` field with provider, tier, GPU, latency
- **Performance baseline** — documented in `docs/archive/2026-Q2/PERF_BASELINE_2026-04-05.md`

### Phase 2: FastEmbed Sidecar + UX Polish
- **Sidecar server** — `scripts/cerid-sidecar.py` wraps ONNX embed/rerank with native GPU acceleration
- **Sidecar installer** — `scripts/install-sidecar.sh` auto-detects platform and GPU for correct onnxruntime variant
- **Sidecar HTTP client** — `utils/inference_sidecar_client.py` with circuit breaker and latency tracking
- **B18: Sub-menu formatting** — consistent padding (p-2), font-weight, separator spacing across all toolbar popovers
- **B23: Recent imports scroll** — collapsible list, 4 default visible, "Show N more" expandable
- **B26: Health dashboard** — grouped by Infrastructure / AI Pipeline / Optional with section headers and auto-refresh
- **B30: External search debugging** — structured logging in `DataSourceRegistry.query_all()`
- **HNSW tuning** — ChromaDB M=12, EF_CONSTRUCTION=400 for better recall on new collections
- **Reranker warmup gating** — skipped when RERANK_MODE=none (~1s faster startup)
- **Ollama pool** — keep-alive connections increased 5→8

### Phase 3: GUI Integration + Recheck Loop
- **Inference tier in Settings** — green/blue/yellow badge showing optimal/good/degraded with provider name
- **Periodic re-check** — background loop every 300s detects Ollama start/stop, emits SSE event
- **Ollama wizard UX** — CPU-only warning, platform-specific install commands (brew/curl), copy buttons

### Phase 4: Ollama LLM Routing + B-LOW Items
- **ai_categorize() routing** — routes through `call_internal_llm()` when INTERNAL_LLM_PROVIDER=ollama
- **contextualize_chunks() routing** — same internal LLM routing for free local inference
- **B32: Synopsis regeneration** — `POST /artifacts/regenerate-all-synopses` with background processing
- **B33: Feedback loop design** — `docs/FEEDBACK_LOOP_DESIGN.md` (opt-in per conversation, quality gates)
- **B41: KB title editing** — already implemented (inline-editable with double-click + PATCH)

### Phase 5: Wiring Checks + Final Audit
- All 8 subsystem wiring checks passed (setup, chat, KB, external API, settings, health, memory, analytics)
- USG compliance verified (no Chinese-origin AI references)
- Documentation updated (CLAUDE.md, CHANGELOG.md)

### New Files
- `src/mcp/utils/inference_config.py` — tiered inference detection
- `src/mcp/utils/inference_sidecar_client.py` — sidecar HTTP client
- `scripts/cerid-sidecar.py` — FastEmbed sidecar server
- `scripts/install-sidecar.sh` — platform-aware installer
- `docs/archive/2026-Q2/PERF_BASELINE_2026-04-05.md` — performance baseline
- `docs/FEEDBACK_LOOP_DESIGN.md` — feedback loop design doc

## v0.81 — Beta Test Implementation (2026-04-04)

### Phase 1 (P0 — Critical Path)
- **PDF Drag-Drop & Ingestion** — Fix macOS file handler interception, add ChromaDB write-flush check, add `skip_quality` for faster wizard ingestion
- **Provider Detection** — Strip env var quotes, add unified `detect_provider_status()`, structured validation errors
- **Dev Tier Switch** — Hidden in production builds
- **Quality Scoring v2** — 6-dimension domain-adaptive scoring (richness, metadata, freshness, authority, utility, coherence), star/evergreen support
- **Preview Fix** — Handle external artifacts and malformed `chunk_ids` gracefully
- **Wizard Cleanup** — Remove Domains card, rename step to "Storage & Archive"

### Phase 2 (P1 — Usability & Polish)
- **Wizard Overhaul** — Optional Features step (Ollama + data sources), Bifrost hidden from health, health tooltips and fix actions
- **Custom LLM** — Custom OpenAI-compatible provider input, credits link, usage explainer
- **Chat UX** — Plain-language tooltips on all toolbar controls, privacy color escalation (green→red), verification cost explainer
- **KB Improvements** — MessageSquarePlus icon, chunk tooltip, star/evergreen buttons
- **Settings Polish** — Chunk size tooltip, cursor-default on Row, section state version bump

### Phase 3 (P2 — Backlog)
- **External Enrichment** — Enrich button on chat messages (Globe icon)
- **Console Consistency** — Read-only RAG mode display, pulse animation on unread badge
- **Custom API Wizard** — CustomApiSource backend (3 auth modes), CustomApiDialog frontend

### New Files
- `src/web/src/components/setup/optional-features-step.tsx`
- `src/web/src/components/setup/custom-provider-input.tsx`
- `src/web/src/components/kb/custom-api-dialog.tsx`
- `src/mcp/utils/data_sources/custom.py`

## [0.81] - 2026-04-03

### Features
- **Eval router wired up** — `POST /api/eval/run` and `GET /api/eval/benchmarks` now registered in main.py (self-gated by `CERID_EVAL_ENABLED`) (`f5bfc28`)
- **Typed Redis wrapper** — `utils/typed_redis.py` provides properly narrowed return types for sync `redis.Redis`, eliminating 57 mypy errors in one place (`4400bdf`)
- **Response model annotations** — 77 endpoints across 15 routers now have `response_model=` for proper OpenAPI schema generation. 13 new Pydantic model files under `models/` (`05b84ec`, `e3a3988`)
- **Code AST parser activated** — `parsers/code_ast.py` `@register_parser` decorators now fire via `__init__.py` import (`1cdc94d`)
- **Setup wizard** — 8-step onboarding with provider routing intelligence, degradation awareness, and health dashboard (`07a64a6`, `c09d2f6`, `b3fc202`)

### Bug Fixes
- **custom_agents pagination** — `total` field now returns actual DB count via `count_agents()` Cypher, not page size (`7aa7059`)
- **custom_agents query delegation** — passes `model_override`, `top_k`, returns `agent_config` with system_prompt/temperature/rag_mode/tools (`7aa7059`)
- **Duplicate endpoint removal** — removed `POST /chat/compress` from `chat.py` (duplicate with incompatible response key) and `GET /plugins` from `health.py` (shadowed by `plugins.py`) (`ef8489c`)
- **Frontend API bugs** — `fetchOpenRouterCredits` fixed to call `/providers/credits` (was 404), `toggleAutomation` fixed to use `/enable`/`disable` endpoints (was 404) (`ef8489c`)
- **error_handler.py** — bare `except: pass` replaced with debug logging for circuit breaker failures (`1cdc94d`)
- **test_ingestion.py** — narrowed bare `except Exception: pass` to specific expected exceptions (`1cdc94d`)
- **Trading mock paths** — 5 stale mock paths in `test_router_sdk.py` updated from `routers.sdk` to `routers.agents` (`77669a0`)
- **TOC test** — updated for `queueMicrotask`-based heading scan (`b36f490`)
- **Docker deployment** — resolved crashes when running without Bifrost (`02e979d`)

### Code Quality
- **ESLint warnings** — resolved all 28 warnings across 24 frontend files: 12 set-state-in-effect, 7 only-export-components, 5 exhaustive-deps, plus purity/ref/directive fixes (`2229d7e`)
- **Mypy errors** — 59 → 2 (only unrelated `multimodal.py` stubs remain) via `TypedRedis` wrapper (`4400bdf`)
- **Ruff lint** — 0 errors across 199+ Python files (maintained)
- **Dead code removed** — `utils/a2a_client.py`, `utils/agent_activity.py`, `utils/content_filter.py`, `tokenize_lower()` from `text.py` (`ad1ff81`)

### Documentation
- **CLAUDE.md** — CI jobs 8→6, coverage 70%→60%, test counts updated, agent list completed (`06b950a`)
- **API_Reference.md** — removed 10 phantom endpoints (trading proxy, boardroom SDK), added 18 real endpoints (custom agents, plugin registry, system monitor, webhooks), marked billing as internal (`56515ef`)

### Infrastructure
- **CI fixes** — multiple rounds of lint, typecheck, and test stabilization after setup wizard merge (`fa9b9df`, `9d354dd`, `9ff9ea0`, `e496922`, `98dc16e`, `bb0a981`)

### New Files
- `src/mcp/utils/typed_redis.py` — typed Redis facade (35 methods)
- `src/mcp/models/agents_response.py` — 14 response models for agent endpoints
- `src/mcp/models/artifacts.py` — 7 response models for artifact endpoints
- `src/mcp/models/data_sources.py` — 11 response models for data source endpoints
- `src/mcp/models/digest.py` — 4 response models
- `src/mcp/models/ingestion.py` — 6 response models
- `src/mcp/models/memories.py` — 4 response models
- `src/mcp/models/query.py` — 2 response models
- `src/mcp/models/settings.py` — 3 response models
- `src/mcp/models/taxonomy.py` — 5 response models
- `src/mcp/models/upload.py` — 4 response models
- `src/mcp/models/user_state.py` — 3 response models
- `src/mcp/models/watched_folders.py` — 3 response models
- `src/mcp/models/webhooks.py` — 5 response models
