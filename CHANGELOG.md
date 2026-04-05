# Changelog

All notable changes to cerid-ai are documented here.

## v0.82.0 (2026-04-05)

### GPU-Accelerated Inference
- **Auto-detection** — detects platform (macOS ARM/Intel, Linux, Windows), GPU (Metal/CUDA/ROCm), Ollama, and FastEmbed sidecar at startup
- **FastEmbed sidecar** — native GPU-accelerated embeddings outside Docker (`scripts/install-sidecar.sh`)
- **Dynamic ONNX providers** — uses GPU execution providers when available instead of CPU-only
- **Inference tier in health** — `/health` includes provider, tier, GPU status, latency
- **Settings UI** — inference tier badge (green/blue/yellow) in Settings panel
- **Auto-switch** — background re-check every 300s detects Ollama start/stop

### UX Improvements
- **Health dashboard** — grouped by Infrastructure / AI Pipeline / Optional with auto-refresh
- **Toolbar menus** — consistent padding, font weights, and separator spacing
- **Recent uploads** — collapsible list (4 visible, "Show N more" to expand)
- **Ollama wizard** — hardware detection, CPU-only warning, platform-specific install commands
- **Model selector** — compliance note for non-US model availability
- **KB title editing** — inline rename with double-click on artifact cards
- **File picker** — browse button for archive path (File System Access API)
- **Feedback buttons** — thumbs up/down on assistant messages

### Performance
- **ChromaDB HNSW tuning** — M=12, EF_CONSTRUCTION=400 for better recall
- **Reranker warmup gating** — skipped when disabled (~1s faster startup)
- **Ollama connection pool** — keep-alive increased 5→8
- **Configurable model preload** — `CERID_PRELOAD_MODELS=false` for smaller Docker images

### Backend
- **Ollama LLM routing** — ai_categorize and contextualize_chunks route through local Ollama when configured
- **Synopsis regeneration** — `POST /artifacts/regenerate-all-synopses` with background processing
- **Conversation grouping** — feedback from same conversation appends to existing KB artifact
- **Memory system fix** — collections auto-created on first access
- **Data source logging** — structured query diagnostics in external search pipeline

### Dependency Cleanup
- Removed 8 unused dependencies (stripe, faster-whisper, requests, structlog, pytesseract, Pillow, bcrypt, PyJWT)
- Docker image: 4.09 GB → 3.18 GB (-22%)
- Dependabot vulnerabilities: 33 → 2 (-94%)
- Startup prerequisites: checks for python3, curl, port availability, Docker memory

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
