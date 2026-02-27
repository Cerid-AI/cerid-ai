# Cerid AI — Task Tracker

> **Last updated:** 2026-02-26
> **Current status:** Phase 10A + 10B complete. Codebase audit + dependency management complete. 5 open issues remain.
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
- [ ] "Start fresh" option on model switch (deferred to 10C)

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

### 10C: Smart Routing Intelligence
- [ ] D1 — Token estimator + context replay cost calculation
- [ ] Context usage indicator in chat dashboard
- [ ] Summarize-and-switch option for large contexts
- [ ] "Start fresh" option on model switch (from 10B)

### 10D: Interactive Audit & Taxonomy
- [ ] B1 — Audit agent report filter toggles, time range selector, manual refresh
- [ ] C1 — Taxonomy-aware hierarchical KB filtering

### 10E: Knowledge Curation Agent (Design)
- [ ] C2 — Design doc for artifact quality improvement agent

### 10F: RAG Evaluation (Research)
- [ ] E2 — Evaluate embedding models, hybrid weights, chunk sizes
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
