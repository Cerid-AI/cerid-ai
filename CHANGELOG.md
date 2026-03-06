# Changelog

All notable changes to Cerid AI are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.12.0] - 2026-03-05

### Changed
- **Production Audit:** Consolidated 7 inline Bifrost httpx+circuit-breaker+tracing patterns into shared `utils/bifrost.py` utility across 4 agents (memory, query_agent, curator, hallucination)
- Narrowed `except Exception` catches to specific types (`json.JSONDecodeError`, `KeyError`, `httpx.HTTPStatusError`, `ValueError`) in all 4 agent modules
- Frontend API error handling consolidated via `extractError()` helper (eliminated 4 inline patterns)
- Extracted `useSyncedToggle()` factory for 3 server-synced settings toggles in `use-settings.ts`

### Added
- nginx security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`), SSE proxy timeouts (300s), `gzip_comp_level 4`
- Docker container resource limits: MCP (4cpu/2G), Dashboard (1cpu/512M), Web (1cpu/256M), Bifrost (2cpu/1G)
- Vite production sourcemaps disabled (`sourcemap: false`)
- Makefile frontend targets (`lint-frontend`, `test-frontend`, `typecheck-frontend`, `build-frontend`, `check-all`, `help`)
- 9 new Bifrost utility tests
- Final counts: 950 Python tests, 320 frontend tests

## [0.11.0] - 2026-03-05

### Added
- **Phase 24:** Expanded Verification — four new claim type detectors in hallucination agent: evasion (model hedging on factual questions), citation (fabricated sources), recency (stale data), ignorance (knowledge gaps). Each type has dedicated system prompts, verdict inversion logic, and frontend rendering (orange for evasion, purple for citation). Streaming pipeline accepts `user_query` and `conversation_history` for context-aware verification
- **Phase 25A:** Direct-to-OpenRouter Chat Proxy — new `/chat/stream` MCP endpoint bypasses Bifrost for user chat, `cerid_meta`/`cerid_meta_update` SSE events for model confirmation, model catalog updated to 9 models with March 2026 pricing, `formatCost()` utility
- **Phase 25B:** Intelligent Model Selection — capability-based `scoreModelForQuery()` with intent detection, three-way routing mode (`manual`/`recommend`/`auto`), enhanced recommendation reasoning, auto-routing with toast indicator, model dropdown capability badges
- **Phase 25C:** Context-Aware Chat — user correction injection, token-budget KB injection (replaces hardcoded `slice(0, 3)`), semantic dedup via Jaccard similarity, domain headers on KB chunks, inline verification trigger
- New utilities: `kb-utils.ts` (`jaccardSimilarity`, `deduplicateChunks`, `formatChunkWithHeader`), `verification-utils.ts` (`getClaimDisplayStatus`)
- Copyright standardization across all source files
- UUID fallback for insecure contexts (LAN access over plain HTTP)
- 51 model-router tests, 13 KB utils tests, expanded hallucination tests

### Fixed
- `crypto.randomUUID()` breaks on LAN (non-secure context) — migrated all call sites to `uuid()` helper with `crypto.getRandomValues()` fallback

## [0.10.0] - 2026-03-04

### Added
- **Phase 23:** Production Hardening — infrastructure security, CI/CD improvements, concurrency fixes, operational improvements, test coverage
- Redis authentication (`--requirepass` + `REDIS_PASSWORD` env var with backward-compatible default)
- MongoDB authentication (`MONGO_INITDB_ROOT_*` env vars, auth-enabled connection strings)
- ChromaDB `ALLOW_RESET=false` (prevents accidental/malicious DB wipe)
- Caddy security headers (HSTS, X-Content-Type-Options, X-Frame-Options, CSP, Referrer-Policy)
- Port binding restrictions — Neo4j, Redis, ChromaDB bound to `127.0.0.1` (not `0.0.0.0`)
- Container resource limits — Neo4j (4CPU/2GB), ChromaDB (2CPU/1GB), Redis (1CPU/512MB)
- CI job timeouts on all 7 jobs (5-45 min), pip caching for test job, hadolint Dockerfile linting
- Neo4j circuit breaker with `call_sync()` for synchronous operations
- Smart health check polling in `start-cerid.sh` (`wait_for_service()` replaces fixed `sleep 30`)
- Docker Compose V2 version check in startup script
- 30 new Python tests: router health (7), router settings (11), MCP SSE protocol (12)
- Cloudflare Tunnel image pinned to `2025.4.2` (was `latest`)

### Fixed
- Semaphore race condition in `hallucination.py` — changed lazy initialization to module-level (safe in Python 3.10+)
- Bounded MCP session queues (`maxsize=100`) with oldest-session eviction at capacity (`_MAX_SESSIONS=100`)
- Neo4j `.single()` → `.single(strict=False)` in ingestion — returns None instead of raising on 0 results
- Configurable `NEAR_DUPLICATE_THRESHOLD` env var (was hardcoded `0.92`)

## [0.9.0] - 2026-03-04

### Added
- **Phase 22:** Deferred items — 5 of 6 deferred items completed
- Self-RAG validation loop (`agents/self_rag.py`) — iterative claim-based retrieval refinement, claims-as-queries (zero extra LLM calls), lightweight KB assessment via `multi_domain_query`, dedup by `(artifact_id, chunk_index)`, disabled by default (`ENABLE_SELF_RAG`), per-request override on `/agent/query`
- mypy type checking — `[tool.mypy]` config in `pyproject.toml`, `types-redis` stub, CI step in GitHub Actions
- Frontend component tests expanded from 139 to 271 — 13 new test files (Tier 1: conversation-list, message-bubble, artifact-card, status-bar, health-cards, hallucination-panel, settings-pane, memories-pane; Tier 2: use-chat, use-settings, use-kb-context, use-theme hooks; Tier 3: domain-filter, tag-filter, verification-status-bar)
- Retroactive CHANGELOG.md from git history (Keep a Changelog format, Phases 0-21)
- ENV_CONVENTIONS.md — env var naming inventory, grouping rules, recommendations for new variables
- 28 new Self-RAG Python tests, 132 new frontend tests

### Fixed
- Verification fallback to external for uncertain/unverified claims — `verify_claim()` now has 4 fallback levels: (1) no KB results, (2) very low KB similarity, (3) KB says "unverified", (4) KB says "uncertain". Previously only levels 1-2 existed, causing claims with moderate KB similarity to stay "uncertain" without external cross-model/web-search verification
- UI polish: KB artifact card overflow (constrained width, icon-only action buttons), chat dashboard single-row responsive layout with `hidden xl:inline` progressive disclosure, consistent green icon colors across status indicators, "unassessed" → "uncertain" label rename in verification display

## [0.8.0] - 2026-03-03

### Added
- **Phase 21A:** Incremental knowledge sync engine — JSONL export/import, tombstone support, conflict detection (remote_wins/local_wins/keep_both/manual_review), scheduled sync, selective domain/date filtering, REST endpoints and CLI flags
- **Phase 21B:** Sync GUI in settings pane — status dashboard, local vs sync count comparison, export/import buttons, conflict strategy selector
- **Phase 21C:** Drag-drop ingestion — drop zone on Knowledge pane, pre-upload options dialog (domain, categorization mode), batch file support
- **Phase 21D:** Storage options — `CERID_STORAGE_MODE` (extract_only/archive), file archiving on upload, `GET /archive/files` endpoint, storage mode selector in settings
- 20 new tests (11 Python, 9 frontend)

## [0.7.0] - 2026-03-02

### Added
- **Phase 17:** iPad & responsive touch UX — `@media (hover: none)` touch visibility, bottom Sheet drawer for KB pane, toolbar overflow Popover, `@media (pointer: coarse)` 44px touch targets, iOS safe area insets, input zoom prevention
- **Phase 18:** Network access & demo deployment — LAN auto-IP detection, Caddy HTTPS gateway, Cloudflare Tunnel for demos, OPERATIONS.md documentation
- **Phase 19:** Expert orchestration & validation — async circuit breakers on 5 Bifrost call sites, distributed request tracing (contextvars + Redis audit), semantic chunking with contextual headers, eval harness enhancements (latency P50/P95/P99, per-domain breakdowns, A/B pipeline comparison), adaptive quality feedback endpoint
- **Phase 20:** Smart tags & artifact quality — per-domain tag vocabulary, typeahead tag filter UI, tag quality scoring, improved synopsis generation with sentence-aware extraction
- Codebase audit: Neo4j rollback on failure, dead code removal, typing modernization (`Dict`/`List`/`Optional` to `dict`/`list`/`X | None`), frontend cleanup, test deduplication
- New UI components: Sheet (bottom/right drawer), Popover (Radix wrapper)

### Fixed
- CI detect-secrets false positive
- TS type errors and lock file sync

## [0.6.0] - 2026-03-02

### Added
- **Phase 16A:** Security hardening — Bifrost/LibreChat/RAG API Docker image SHA pinning, PostgreSQL/Meilisearch credential externalization, secret detection in CI, runtime MCP_URL config for web container
- **Phase 16B:** Dead code cleanup — removed 6 unused frontend API functions, orphaned tests, unused imports
- **Phase 16C:** Backend code quality — extracted `_format_chroma_result()` helper, `defaultdict(Counter)`, `scan_iter()`, try/except on Neo4j delete, `get_chroma()` factory usage
- **Phase 16D:** Frontend code quality — extracted `tokenCost()`, `getAccuracyTier()`, `parseTags()` shared utilities, `useMemo` optimization, unstable React key fix
- **Phase 16E:** Dependency optimization — removed `langchain-community`, narrowed `langgraph`/`langchain-openai` bounds, spaCy model version as Docker ARG, CI coverage threshold raised to 70%
- **Phase 16F:** Feature wiring — `enable_model_router` setting, taxonomy CRUD (create domain/sub-category), recategorize artifacts, archive memories
- **Phase 16G:** Artifact preview dialog with syntax highlighting (PrismLight), cerid-web compose separation
- **Phase 16H:** CLAUDE.md slim (685 to 204 lines), API reference and completed phases extracted to separate docs
- Custom Claude Code skills and environment improvements
- 19 new frontend tests (130 total)

### Fixed
- CI failures across lint, security, lock-sync, and frontend jobs
- Alpine base-image CVEs (openssl, libpng)

### Changed
- nginx bumped from 1.27-alpine to 1.29-alpine

## [0.5.0] - 2026-03-01

### Added
- **Phase 15E:** Settings pane scroll fix, collapsible sections with localStorage persistence, per-setting info tooltips, verification toggle in toolbar, status bar service tooltips, dashboard metric tooltips, verification status bar
- **Phase 15F:** Polling for verification reports (exponential backoff), KB empty state conditional
- **Phase 15G:** Audit pane layout restructure (operations separated from analytics)
- **Phase 15H:** Streaming verification via SSE (replaced polling), accuracy dashboard with model comparison chart, user claim feedback (thumbs up/down), model accuracy BarChart
- Verification UX overhaul: refuted/unverified distinction, source URL extraction from OpenRouter annotations, staleness detection with web search escalation, generator model context in verification prompts, session metrics accumulator, ignorance-admission detection and verdict inversion
- 23 new hallucination tests (125 total)

## [0.4.0] - 2026-02-28

### Added
- **Phase 10E:** Smart model switching — token cost estimation, context replay cost calculation, summarize-and-switch, model switch dialog with 3 strategies (continue/summarize/fresh), context overflow warning
- **Phase 11:** Knowledge intelligence — interactive audit controls, taxonomy tree sidebar with CRUD, knowledge curation agent design, operations documentation
- **Phase 12:** RAG excellence — BM25 replacement (rank_bm25 to bm25s + PyStemmer), configurable retrieval weights, embedding evaluation harness (NDCG, MRR, P@K, R@K)
- **Phase 13:** Conversation intelligence — conversation-aware KB queries, auto-injection with confidence gate, context budget optimization
- **Phase 14:** Artifact quality — curation agent (4-dimension scoring), quality-weighted retrieval, metadata boost, quality badges in GUI, AI synopsis generation via Bifrost
- 26 new frontend tests (94 total), Dependabot CI action bumps

### Fixed
- Bifrost model IDs updated with `openrouter/` prefix
- Synopsis rate-limit handling (8s inter-request throttle, 60s retry on 429)

### Performance
- PrismLight lazy loading (1619KB to 104KB)
- Debounced localStorage writes during SSE streaming (500ms)
- Batched Neo4j tag creation with UNWIND
- Redis SCAN replacing KEYS
- Dead code removal (-701 lines)

## [0.3.0] - 2026-02-27

### Added
- **Phase 10A:** Production quality — chat viewport overflow fix, source attribution in chat, Apache-2.0 headers, foundational frontend tests (34), CI hardening
- **Phase 10B:** Model switch divider, always-visible model badge with provider colors
- **Phase 10C:** Structural splits — `services/ingestion.py` extraction, `tools.py` MCP tool registry, config split (settings/taxonomy/features), `db/neo4j/` package, `sync/` package, `parsers/` sub-package, middleware hardening (X-Forwarded-For, rate limit headers, IP redaction, request ID)
- **Phase 10D:** 564 backend tests (middleware, ingestion, all 5 agents, sync, tools, parsers, Neo4j), CI hardening (pip-audit, coverage threshold 55% to 70%, bundle size monitoring)
- Codebase audit: dependency purge (sentence-transformers, pandas savings ~700MB Docker), Docker security (non-root user, pinned images), dead code removal, logic consolidation, error handling overhaul, accessibility fixes (33 across 14 components)
- Dependency management: Node 22 standardization, pip-compile with hashes, CI tool pinning, Dependabot weekly grouped PRs, pre-commit hook, DEPENDENCY_COUPLING.md

### Fixed
- Full-stack audit remediation (23 items: G1-G22)
- ChromaDB sync import and mount sync dir
- Healthcheck failures, MCP SSRF bypass, JWT secrets

## [0.2.0] - 2026-02-25

### Added
- **Phase 7A:** Audit intelligence — hallucination detection, conversation analytics, feedback loop
- **Phase 7B:** Smart orchestration — model router, expanded to 15 MCP tools
- **Phase 7C:** Proactive knowledge — memory extraction, smart KB suggestions, memory archival
- **Phase 8A:** Plugin system — manifest-based loading, feature tiers, OCR scaffold
- **Phase 8B:** Smart ingestion — new parsers (.eml, .mbox, .epub, .rtf), semantic dedup
- **Phase 8C:** Hierarchical taxonomy — TAXONOMY dict, sub-categories/tags, taxonomy API
- **Phase 8D:** Encryption & sync — field-level Fernet encryption, pluggable sync backends
- **Phase 8E:** GUI intelligence — settings, upload, memories, truth panel
- **Phase 9:** GUI feature parity — wire Phase 7/8 backend into React GUI
- Infrastructure audit (8E): 31 findings across security, concurrency, correctness

### Fixed
- Neo4j auth hardening — empty password detection, credential validation via Cypher query
- Security cleanup — secrets removal, untracked database files
- Comprehensive audit — security, concurrency, correctness, AI slop cleanup

## [0.1.0] - 2026-02-20

### Added
- **Phase 6A:** React 19 + Vite + Tailwind v4 + shadcn/ui scaffold, TypeScript types, API client, layout shell with sidebar/status bar/theme toggle, streaming chat via SSE, conversation history, Docker + nginx production build, responsive design
- **Phase 6B-6D:** Knowledge context pane (split-pane, artifact cards, domain filters, graph preview), monitoring pane (health cards, collection chart), audit pane (cost breakdown), backend hardening (API key auth, rate limiting, Redis query cache, bundle splitting)
- Claude Code project configuration (hooks, launch.json, .claudeignore)

## [0.0.1] - 2026-02-15

### Added
- **Phase 0:** Project structure, MCP SSE transport, tool discovery and invocation
- **Phase 1:** File ingestion pipeline — parsing, AI categorization, deduplication
- **Phase 1.5:** Bulk ingest hardening, concurrent CLI, atomic dedup
- **Phase 2:** Agent workflows — Query, Triage, Rectification, Audit, Maintenance; 12 MCP tools
- **Phase 3:** Streamlit dashboard with 5 admin panes, Obsidian vault watcher
- **Phase 4A:** Modular refactor — split main.py into FastAPI routers
- **Phase 4B:** Hybrid BM25+vector search, knowledge graph traversal, cross-domain connections
- **Phase 4C:** Scheduled maintenance (APScheduler), proactive knowledge surfacing, webhooks
- **Phase 4D:** 36 tests, GitHub Actions CI, security cleanup, centralized encrypted `.env`
- **Phase 5A:** Infrastructure compose (Neo4j, ChromaDB, Redis), startup script, env validation
- **Phase 5B:** Knowledge base sync — JSONL export/import CLI, auto-import on startup, Dropbox sync
- PDF parser upgrade to pdfplumber for structure-aware table extraction
