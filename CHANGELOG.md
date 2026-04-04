# Changelog

All notable changes to cerid-ai are documented here.

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
