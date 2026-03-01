# Cerid AI — Task Tracker

> **Last updated:** 2026-03-01
> **Current status:** Phase 14F complete + tested. Bifrost model IDs fixed, synopsis rate-limit handling. 795+ tests.
> **Open issues:** [docs/ISSUES.md](../docs/ISSUES.md)

## Current: Phase 10 — Commercial & Open-Source Readiness

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

### Phase 15: Realtime Accuracy Watcher
- [ ] Streaming verification (wire existing SSE endpoint to frontend)
- [ ] Accuracy dashboard (hallucination rates by model/domain/time, persist to Neo4j)
- [ ] User claim feedback (per-claim correct/incorrect buttons)
- [ ] Model accuracy comparison charts

### Phase 16: Content & UX Polish
- [ ] E1 — Artifact preview (PDF, code, spreadsheet rendering)
- [ ] D2 remaining — Conversation fork/branch UI (exploratory)
- [ ] Frontend component test expansion (40+ untested components)
- [ ] F6 remaining — cerid-web compose separation

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
