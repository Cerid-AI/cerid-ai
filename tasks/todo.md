# Cerid AI — Task Tracker

> **Last updated:** 2026-02-22
> **Current phase:** Phase 5 — Multi-Machine Dev Environment & Knowledge Sync
> **Plan:** `docs/plans/2026-02-22-multi-machine-sync-plan.md`

## Pre-Phase 5: Machine Setup (if on pre-Phase 4 machine)

- [ ] `git pull` to get all Phase 4 changes
- [ ] Install `age`: `brew install age`
- [ ] Copy `~/.config/cerid/age-key.txt` from primary machine (or generate new key pair and re-encrypt)
- [ ] Run `./scripts/env-unlock.sh` to decrypt `.env`
- [ ] Stop and remove any legacy standalone containers that will conflict with new infrastructure compose
- [ ] Verify Docker daemon running, `llm-network` exists

## Phase 5A: Fix Infrastructure

- [ ] **A1.** Create `stacks/infrastructure/docker-compose.yml` — Neo4j, ChromaDB, Redis with healthchecks and bind-mount volumes
- [ ] **A2.** Update `scripts/start-cerid.sh` — add infrastructure as step 1/4
- [ ] **A3.** Create `scripts/validate-env.sh` — pre-flight checks with `--quick` and `--fix` flags
- [ ] **A4.** Update `.gitignore` — add `stacks/infrastructure/data/`
- [ ] **A5.** Update `CLAUDE.md` — add validation instructions
- [ ] **A-verify.** Run `./scripts/validate-env.sh` → exit 0, all containers healthy

## Phase 5B: Knowledge Sync

- [ ] **B1.** Add sync config to `.env.example` and `src/mcp/config.py` (CERID_SYNC_DIR, CERID_MACHINE_ID)
- [ ] **B2.** Create `src/mcp/cerid_sync_lib.py` — export/import logic for Neo4j, ChromaDB, BM25, Redis
- [ ] **B3.** Create `scripts/cerid-sync.py` — CLI wrapper (export, import, status)
- [ ] **B4.** Create `src/mcp/sync_check.py` — auto-import on startup for empty databases
- [ ] **B5.** Update `src/mcp/main.py` — add auto-import call in lifespan
- [ ] **B-verify.** Export → wipe infra data → restart → auto-import → verify data restored

## Completed Phases

- [x] Phase 0-1: Core ingestion pipeline, metadata, AI categorization, deduplication
- [x] Phase 1.5: Bulk ingest hardening, concurrent CLI, atomic dedup
- [x] Phase 2: Agent workflows (Query, Triage, Rectification, Audit, Maintenance), 12 MCP tools
- [x] Phase 3: Streamlit dashboard, Obsidian vault watcher
- [x] Phase 4A: Modular refactor — split main.py into FastAPI routers
- [x] Phase 4B: Hybrid BM25+vector search, knowledge graph traversal, cross-domain connections
- [x] Phase 4C: Scheduled maintenance (APScheduler), proactive knowledge surfacing, webhooks
- [x] Phase 4D: 36 tests passing, GitHub Actions CI, security cleanup, centralized encrypted `.env`
