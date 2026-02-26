# Cerid AI — Task Tracker

> **Last updated:** 2026-02-25
> **Current status:** Phase 9 complete + Neo4j auth hardening
> **Open issues:** [docs/ISSUES.md](../docs/ISSUES.md)

## Next: Phase 10 — Open Issues

See [docs/ISSUES.md](../docs/ISSUES.md) for the full backlog (9 items across 5 categories).

**Priority order:**
1. A1 — Chat viewport fix (CSS, quick win)
2. B2 — Source attribution in chat
3. B3 + D2 — Model context break indicator
4. D1 — Smart routing context/token cost evaluation
5. B1 — Interactive audit agent in GUI
6. C1 — Taxonomy-aware KB filtering
7. C2 — Knowledge curation agent
8. E2 — RAG integration & vectorization evaluation
9. E1 — Artifact preview/generation

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

<details>
<summary>Phase 5 Details (completed)</summary>

**Pre-Phase 5: Machine Setup**
- [x] `git pull` to get all Phase 4 changes
- [x] Install `age`: `brew install age`
- [x] Copy `~/.config/cerid/age-key.txt` from primary machine
- [x] Run `./scripts/env-unlock.sh` to decrypt `.env`
- [x] Stop legacy containers, verify Docker daemon running

**Phase 5A: Fix Infrastructure**
- [x] Create `stacks/infrastructure/docker-compose.yml`
- [x] Update `scripts/start-cerid.sh` — add infrastructure as step 1/4
- [x] Create `scripts/validate-env.sh`
- [x] Update `.gitignore` — add `stacks/infrastructure/data/`

**Phase 5B: Knowledge Sync**
- [x] Add sync config to `.env.example` and `config.py`
- [x] Create `cerid_sync_lib.py` — export/import for Neo4j, ChromaDB, BM25, Redis
- [x] Create `scripts/cerid-sync.py` — CLI wrapper
- [x] Create `sync_check.py` — auto-import on startup
- [x] Verify export → wipe → restart → auto-import → data restored

</details>
