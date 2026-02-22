# Cerid AI — Task Tracker

> **Last updated:** 2026-02-22
> **Current phase:** Phase 6 — React GUI + Production Hardening
> **Plan:** [docs/plans/2026-02-22-phase6-gui-design.md](../docs/plans/2026-02-22-phase6-gui-design.md)

## Phase 6: React GUI + Production Hardening

### 6A: Foundation + Chat

- [ ] Vite + React + TypeScript + Tailwind + shadcn/ui scaffold
- [ ] Docker container (nginx on `llm-network`, port 3000)
- [ ] Sidebar navigation with pane switching
- [ ] Chat interface with streaming SSE from Bifrost
- [ ] Model selection dropdown (Claude, GPT, Gemini, Grok, DeepSeek, Llama)
- [ ] Conversation history (localStorage)
- [ ] Markdown rendering (code blocks, tables, lists)
- [ ] Status bar with health indicator
- [ ] Dark/light theme toggle
- [ ] Responsive design

### 6B: Knowledge Context Pane

- [ ] Resizable split-pane layout (chat + KB context)
- [ ] Auto-query KB on user message via `/agent/query`
- [ ] Artifact cards (title, domain, relevance, preview)
- [ ] "Inject into chat" context button
- [ ] Manual search + domain filter chips
- [ ] Graph preview for selected artifacts

### 6C: Monitoring + Audit Panes

- [ ] Health cards (ChromaDB, Neo4j, Redis, Bifrost)
- [ ] Collection size charts + ingestion timeline
- [ ] Token usage / cost breakdown charts
- [ ] Query pattern analytics
- [ ] Auto-refresh (30s interval)

### 6D: Backend Hardening + Feedback Loop

- [ ] Redis query caching (TTL, invalidation on ingest)
- [ ] API authentication (X-API-Key middleware)
- [ ] LLM feedback loop (chat → KB ingestion, opt-in)
- [ ] CORS configuration
- [ ] Rate limiting

## Completed Phases

- [x] Phase 0-1: Core ingestion pipeline, metadata, AI categorization, deduplication
- [x] Phase 1.5: Bulk ingest hardening, concurrent CLI, atomic dedup
- [x] Phase 2: Agent workflows (Query, Triage, Rectification, Audit, Maintenance), 12 MCP tools
- [x] Phase 3: Streamlit dashboard, Obsidian vault watcher
- [x] Phase 4A: Modular refactor — split main.py into FastAPI routers
- [x] Phase 4B: Hybrid BM25+vector search, knowledge graph traversal, cross-domain connections
- [x] Phase 4C: Scheduled maintenance (APScheduler), proactive knowledge surfacing, webhooks
- [x] Phase 4D: 36 tests passing, GitHub Actions CI, security cleanup, centralized encrypted `.env`
- [x] Phase 5A: Infrastructure compose (Neo4j, ChromaDB, Redis), 4-step startup script, environment validation
- [x] Phase 5B: Knowledge base sync — JSONL export/import CLI, auto-import on startup, Dropbox-based multi-machine sync

### Phase 5 Details (completed)

<details>
<summary>Pre-Phase 5: Machine Setup</summary>

- [x] `git pull` to get all Phase 4 changes
- [x] Install `age`: `brew install age`
- [x] Copy `~/.config/cerid/age-key.txt` from primary machine (or generate new key pair and re-encrypt)
- [x] Run `./scripts/env-unlock.sh` to decrypt `.env`
- [x] Stop and remove any legacy standalone containers that will conflict with new infrastructure compose
- [x] Verify Docker daemon running, `llm-network` exists

</details>

<details>
<summary>Phase 5A: Fix Infrastructure</summary>

- [x] **A1.** Create `stacks/infrastructure/docker-compose.yml` — Neo4j, ChromaDB, Redis with healthchecks and bind-mount volumes
- [x] **A2.** Update `scripts/start-cerid.sh` — add infrastructure as step 1/4
- [x] **A3.** Create `scripts/validate-env.sh` — pre-flight checks with `--quick` and `--fix` flags
- [x] **A4.** Update `.gitignore` — add `stacks/infrastructure/data/`
- [x] **A5.** Update `CLAUDE.md` — add validation instructions
- [x] **A-verify.** Run `./scripts/validate-env.sh` → exit 0, all containers healthy

</details>

<details>
<summary>Phase 5B: Knowledge Sync</summary>

- [x] **B1.** Add sync config to `.env.example` and `src/mcp/config.py` (CERID_SYNC_DIR, CERID_MACHINE_ID)
- [x] **B2.** Create `src/mcp/cerid_sync_lib.py` — export/import logic for Neo4j, ChromaDB, BM25, Redis
- [x] **B3.** Create `scripts/cerid-sync.py` — CLI wrapper (export, import, status)
- [x] **B4.** Create `src/mcp/sync_check.py` — auto-import on startup for empty databases
- [x] **B5.** Update `src/mcp/main.py` — add auto-import call in lifespan
- [x] **B-verify.** Export → wipe infra data → restart → auto-import → verify data restored

</details>
