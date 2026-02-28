# Cerid AI — Task Tracker

> **Last updated:** 2026-02-28
> **Current status:** Phase 10D in progress. 564 tests passing (was 156). All backend modules tested. Remaining: CI hardening (G12-G15).
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

### 10D: Test Coverage + CI Hardening
- [x] Tests for `middleware/auth.py`, `middleware/rate_limit.py`, `middleware/request_id.py` (49 tests: auth bypass/enforcement, exempt paths, IP redaction, rate limit headers/enforcement/expiry, XFF proxy resolution, request ID generation/propagation)
- [x] Tests for `services/ingestion.py` (15 tests: content hashing, path validation, duplicate detection, concurrent constraint handling, response shapes, ChromaDB collection naming, Redis logging)
- [x] F5 — Tests for all 5 agents: query_agent (27 tests: dedup, context assembly, cross-domain affinity, rerank fallback, response shape), triage (23 tests: node validation, parse, routing, metadata merge, chunking), rectify (19 tests: duplicate/stale/orphan detection, resolution, distribution), audit (27 tests: activity summary, ingestion stats, cost estimation, query patterns, conversation analytics), maintenance (24 tests: health checks, bifrost sync, purge, collection analysis)
- [x] Tests for `sync/` package (41 tests: SHA-256 file hashing, JSONL read/write/iterate, manifest read/write/validation, Neo4j export, Redis export/import with deduplication)
- [x] Tests for `mcp/tools.py` (24 tests: registry validation, dispatch for all tool types, error paths, argument defaults, async agent tool dispatch)
- [x] Tests for `parsers/` sub-package (108 tests: HTML/RTF stripping, registry validation, parse_file orchestration, text/HTML/EML/RTF/EPUB parsers with real files, PDF/DOCX/XLSX/CSV parsers with mocks)
- [x] Tests for `db/neo4j/` package (54 new tests: schema init, artifact CRUD, relationship creation/discovery/traversal, taxonomy CRUD, subcategory management, tag listing — expanded from 9 to 63 total)
- [ ] G12 — Fix pip-audit to scan installed packages (transitive dependency vulnerabilities)
- [ ] G13 — Add CodeQL SAST workflow (`.github/workflows/codeql.yml`)
- [ ] G14 — Raise coverage threshold from 35% to 55%
- [ ] G15 — Add bundle size monitoring in CI (fail if chunk >800KB)
- [ ] Frontend component tests (40+ components with 0 tests)

### 10E: Smart Routing Intelligence
- [ ] D1 — Token estimator + context replay cost calculation
- [ ] Context usage indicator in chat dashboard
- [ ] Summarize-and-switch option for large contexts
- [ ] "Start fresh" option on model switch (from 10B)

### 10F: Interactive Audit, Taxonomy + Operations Docs
- [ ] B1 — Audit agent report filter toggles, time range selector, manual refresh
- [ ] C1 — Taxonomy-aware hierarchical KB filtering
- [ ] G17 — Document API key rotation procedure (`docs/OPERATIONS.md`)
- [ ] G18 — Document secrets rotation policy
- [ ] G19 — Add pip-compile version to `DEPENDENCY_COUPLING.md`
- [ ] G20 — Add Bifrost version to coupling constraints
- [ ] G21 — Document branch protection rules
- [ ] G22 — Document rate limiter in-memory state limitation

### 10G: Knowledge Curation Agent (Design)
- [ ] C2 — Design doc for artifact quality improvement agent

### 10H: RAG Evaluation (Research)
- [ ] E2 — Evaluate embedding models, hybrid weights, chunk sizes
- [ ] G16 — Evaluate BM25 alternatives (rank_bm25 unmaintained since 2020)
- [ ] E1 — Artifact preview/generation (depends on E2 decisions)

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
