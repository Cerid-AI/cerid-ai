# Changelog

All notable changes to cerid-ai are documented here.

## v0.82 — Unified Implementation Plan (2026-04-05)

### Phase 1: Tiered Inference Detection
- **InferenceConfig singleton** — auto-detects platform (macOS ARM/Intel, Linux, Windows), GPU (Metal/CUDA/ROCm/DirectML), Ollama, and FastEmbed sidecar at startup
- **Dynamic ONNX providers** — embeddings.py and reranker.py use detected GPU providers instead of hardcoded CPU
- **Health endpoint** — `/health` now includes `inference` field with provider, tier, GPU, latency
- **Performance baseline** — documented in `docs/PERF_BASELINE_2026-04-05.md`

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
- `docs/PERF_BASELINE_2026-04-05.md` — performance baseline
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
- **Typed Redis wrapper** — `utils/typed_redis.py` provides properly narrowed return types for sync `redis.Redis`, eliminating mypy `Awaitable|Any` errors (`c23c261`)
- **Response model annotations** — 77 endpoints across 15 routers now have `response_model=` for proper OpenAPI schema generation. 13 new Pydantic model files under `models/` (`93bc8b1`)
- **Code AST parser activated** — `parsers/code_ast.py` `@register_parser` decorators now fire via `__init__.py` import (`c23c261`)
- **Setup wizard** — 8-step onboarding with provider routing intelligence, degradation awareness, and health dashboard (`ee1a264`, `6ba6aa3`, `3c1b96e`)

### Bug Fixes
- **Duplicate endpoint removal** — removed `POST /chat/compress` from `chat.py` (duplicate with incompatible response key) and `GET /plugins` from `health.py` (shadowed by `plugins.py`) (`3319e26`)
- **Frontend API bugs** — `fetchOpenRouterCredits` fixed to call `/providers/credits` (was 404), `toggleAutomation` fixed to use `/enable`/`disable` endpoints (was 404) (`3319e26`)
- **error_handler.py** — bare `except: pass` replaced with debug logging for circuit breaker failures (`c23c261`)
- **main.py webhook bridge** — bare `except: pass` replaced with debug logging (`3cb45dd`)
- **Docker deployment** — resolved crashes when running without Bifrost (`6863e7b`)

### Code Quality
- **ESLint warnings** — resolved all 28 warnings across 24 frontend files: 12 set-state-in-effect, 7 only-export-components, 5 exhaustive-deps, plus purity/ref/directive fixes (`571328f`)
- **Dead code removed** — `utils/a2a_client.py`, `utils/agent_activity.py`, `utils/content_filter.py`, `tokenize_lower()` from `text.py` (`c23c261`)

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
