# Phase 4: Smarter Retrieval, Workflow Automation & Showcase Polish

**Date:** February 21, 2026
**Status:** Active — ready for implementation
**Approach:** Feature-Led — refactor foundation, build headline features, then engineering polish

---

## Overview

Phase 4 enhances cerid-ai as a personal power tool and showcase piece. It adds intelligent retrieval (hybrid search, knowledge graph traversal, cross-domain connections, temporal awareness), workflow automation (scheduled maintenance, proactive knowledge surfacing, smart ingestion, webhooks), and engineering polish (tests, CI/CD, security, open-source readiness).

---

## Model Assignment & Handoff Protocol

Each sub-phase is assigned a recommended Claude model based on task complexity. **When a sub-phase is complete, STOP and note completion in this document. Do not continue to the next sub-phase without a new session.**

| Sub-Phase | Model | Rationale |
|-----------|-------|-----------|
| **4A: Modular Refactor** | **Sonnet** | Mechanical restructuring, well-known FastAPI patterns, no design decisions |
| **4B.1: Hybrid Search** | **Sonnet** | Library integration (rank_bm25), straightforward scoring combination |
| **4B.2: Knowledge Graph Traversal** | **Opus** | Novel relationship design, Cypher query authoring, architectural decisions |
| **4B.3: Cross-Domain Connections** | **Sonnet** | Configuration + scoring weight integration, builds on 4B.2 patterns |
| **4B.4: Temporal Awareness** | **Sonnet** | Time parsing + decay scoring, well-defined patterns |
| **4C.1: Scheduled Maintenance** | **Sonnet** | APScheduler integration, wiring existing agents to cron triggers |
| **4C.2: Proactive Surfacing** | **Opus** | Creative design — relationship discovery logic, digest generation |
| **4C.3: Smart Ingestion** | **Sonnet** | Pipeline improvements, priority queue, re-ingestion logic |
| **4C.4: Webhooks** | **Sonnet** | Standard HTTP POST notification system |
| **4D.1: Testing** | **Sonnet** | pytest boilerplate, mocking, standard test patterns |
| **4D.2: CI/CD** | **Sonnet** | GitHub Actions YAML, pyproject.toml config |
| **4D.3: Security Cleanup** | **Sonnet** | .env migration, file deletion, template creation |
| **4D.4: Open Source Readiness** | **Sonnet** | LICENSE, CONTRIBUTING.md, README polish |

### Handoff Rules

1. **Each sub-phase = one session.** Complete the sub-phase, verify it works, then stop.
2. **Mark completion** by updating the status checkbox below when done.
3. **Don't start the next sub-phase** — the user will open a new session and point the model at the next task.
4. **If blocked**, document what's blocking and stop. Don't attempt workarounds that cross sub-phase boundaries.
5. **Verification is mandatory** before marking complete — run the verification steps listed for each sub-phase.

---

## Sub-Phase Specifications

### 4A: Modular Refactor
- [x] **Complete**
- **Model:** Sonnet
- **Goal:** Split `main.py` (1,321 lines) into FastAPI `APIRouter` modules. Zero behavior change.

**Changes:**
```
src/mcp/
├── main.py              (~100 lines — app setup, middleware, DB client init, router includes)
├── routers/
│   ├── health.py        — /health, /collections
│   ├── artifacts.py     — /artifacts, /recategorize
│   ├── ingestion.py     — /ingest, /ingest_file
│   ├── query.py         — /query
│   ├── agents.py        — /agent/* endpoints
│   └── mcp_sse.py       — /mcp/sse, /mcp/messages (MCP protocol)
```

- Shared state (DB clients, httpx session) via FastAPI dependency injection
- Each router is a self-contained `APIRouter` with its own imports
- `main.py` creates app, initializes DB clients in lifespan, includes routers

**Critical files:** `src/mcp/main.py` (source), new `src/mcp/routers/` directory

**Verification:**
- All existing REST endpoints return identical responses
- Smoke test every endpoint: `/health`, `/collections`, `/query`, `/ingest`, `/ingest_file`, `/artifacts`, `/recategorize`, `/ingest_log`
- All agent endpoints: `/agent/query`, `/agent/triage`, `/agent/rectify`, `/agent/audit`, `/agent/maintain`
- MCP SSE connection works from LibreChat
- Docker container builds and starts cleanly

---

### 4B.1: Hybrid Search (Vector + Keyword)
- [ ] **Complete**
- **Model:** Sonnet
- **Depends on:** 4A

**Changes:**
- New `utils/bm25.py` — BM25 index management (build, query, persist)
- Add `rank_bm25` to `requirements.txt`
- On ingestion: index document text for BM25 alongside ChromaDB vectors
- On query: run both searches, combine scores: `0.6 * vector + 0.4 * keyword` (configurable in `config.py`)
- Fallback: if BM25 index unavailable, gracefully degrade to vector-only

**Files:** New `utils/bm25.py`, modify `agents/query_agent.py`, `routers/query.py` (or `routers/agents.py` for agent query), `config.py`

**Verification:**
- Query for an exact API name or error code → appears in results (keyword match)
- Query for a conceptual topic → appears in results (vector match)
- Both types score and rank correctly in combined results
- BM25 index persists across container restarts

---

### 4B.2: Knowledge Graph Traversal
- [x] **Complete**
- **Model:** Opus
- **Depends on:** 4A

**Changes:**
- Add Neo4j relationship types: `RELATES_TO`, `DEPENDS_ON`, `SUPERSEDES`, `REFERENCES`
- Build relationships during ingestion:
  - Same-domain proximity (artifacts ingested from same directory)
  - Shared metadata (similar tags, categories, entities via spaCy)
  - Filename/content pattern matching (imports, references)
- Query expansion: when artifacts match, traverse 1-2 hops in the graph for related content
- Configurable traversal depth and relationship weight in `config.py`

**Files:** Modify `utils/graph.py` (relationship CRUD + traversal queries), `agents/query_agent.py` (graph-enhanced retrieval), `routers/ingestion.py` (relationship building on ingest)

**Verification:**
- Ingest 3+ related files (e.g., a Python module + its tests + its docs)
- Verify Neo4j relationships created between them
- Query for one → related artifacts surface via graph traversal
- Verify traversal depth respects configuration limit

---

### 4B.3: Cross-Domain Connections
- [ ] **Complete**
- **Model:** Sonnet
- **Depends on:** 4B.2

**Changes:**
- When querying one domain, also search adjacent domains with reduced weight
- Domain affinity matrix (configurable in `config.py`):
  - coding ↔ projects: 0.6
  - finance ↔ projects: 0.4
  - personal ↔ general: 0.5
  - (all others: 0.2)
- Integrate into Query Agent's multi-domain search with domain-aware scoring

**Files:** Modify `config.py` (add affinity matrix), `agents/query_agent.py`

**Verification:**
- Query "coding" domain for a topic that also exists in "projects" → cross-domain result appears with reduced score
- Query with `domains=["coding"]` → also returns relevant "projects" results at lower weight

---

### 4B.4: Temporal Awareness
- [ ] **Complete**
- **Model:** Sonnet
- **Depends on:** 4B.1

**Changes:**
- Add `ingested_at`, `modified_at`, `last_accessed_at` timestamps to Neo4j artifacts
- Recency boost in scoring: exponential decay factor (half-life configurable, default 30 days)
- Time-based query parsing: detect phrases like "last week", "this month", "recent"
- Filter artifacts by time range when temporal intent detected

**Files:** Modify `utils/graph.py`, `agents/query_agent.py`, new `utils/temporal.py` (time parsing + decay scoring)

**Verification:**
- Ingest a file, wait, ingest another → newer file scores higher for equivalent relevance
- Query "what did I add recently" → returns most recent artifacts
- Query "last week" → filters to artifacts from that time range

---

### 4C.1: Scheduled Maintenance Engine
- [ ] **Complete**
- **Model:** Sonnet
- **Depends on:** 4A

**Changes:**
- Add APScheduler (lightweight, in-process) to the MCP server
- Default schedules:
  - Rectification agent: daily at 3 AM
  - Health check: every 6 hours
  - Stale artifact detection: weekly
- Configurable via `config.py` with cron expressions
- Execution logs stored in Redis with status/duration
- Add `apscheduler` to `requirements.txt`

**Files:** New `scheduler.py`, modify `main.py` (startup/shutdown hooks), `config.py`

**Verification:**
- Start server → scheduler initializes (check logs)
- Manually trigger a scheduled job → executes correctly
- Check Redis for execution log entries with status and duration
- Verify graceful shutdown (scheduler stops cleanly)

---

### 4C.2: Proactive Knowledge Surfacing
- [ ] **Complete**
- **Model:** Opus
- **Depends on:** 4B.2, 4C.1

**Changes:**
- Post-ingestion hook: after successful ingest, query for related artifacts
- Return related artifacts in ingest response: `{"artifact_id": "...", "related": [...]}`
- Store discovered relationships in Neo4j (feeds graph traversal from 4B.2)
- Daily digest endpoint: `GET /digest` — summarizes new artifacts, connections, health status

**Files:** Modify `routers/ingestion.py`, new `routers/digest.py`, modify `utils/graph.py`

**Verification:**
- Ingest a file → response includes `related` array with relevant existing artifacts
- Neo4j shows new `RELATES_TO` edges created from discovery
- `GET /digest` returns summary of recent activity and connections

---

### 4C.3: Smart Ingestion Pipeline
- [ ] **Complete**
- **Model:** Sonnet
- **Depends on:** 4A

**Changes:**
- Content-based domain classification: use Bifrost to classify domain from content (not just folder path)
- Re-ingestion: detect file changes via modified timestamp + content hash comparison
  - Update existing artifact instead of creating duplicate
  - Preserve relationships and access history
- Async priority queue: large batch jobs run at lower priority than interactive queries
  - Use Python `asyncio.PriorityQueue` in the ingestion router

**Files:** Modify `routers/ingestion.py`, `agents/triage.py`, `config.py`

**Verification:**
- Ingest a file from `inbox/` → domain classified from content, not folder
- Modify and re-ingest same file → artifact updated (not duplicated), relationships preserved
- Start a batch job, then submit an interactive query → interactive query completes first

---

### 4C.4: Event-Driven Notifications (Webhooks)
- [ ] **Complete**
- **Model:** Sonnet
- **Depends on:** 4C.1

**Changes:**
- Webhook system: configurable endpoints called on events
- Events: `ingestion.complete`, `health.warning`, `digest.ready`, `rectify.findings`
- Config in `config.py`: list of webhook URLs + event filters
- Start with simple HTTP POST; extensible to Slack/email later

**Files:** New `utils/webhooks.py`, modify `scheduler.py`, `routers/ingestion.py`

**Verification:**
- Configure a test webhook URL (e.g., webhook.site)
- Ingest a file → webhook fires with `ingestion.complete` event
- Scheduler health check finds issue → webhook fires with `health.warning`

---

### 4D.1: Testing
- [ ] **Complete**
- **Model:** Sonnet
- **Depends on:** All 4A-4C sub-phases

**Changes:**
- Add `pytest` + `pytest-asyncio` + `httpx` to dev dependencies
- Test critical paths:
  - Ingestion pipeline (parse → dedup → chunk → store)
  - Query agent (hybrid search, graph traversal, scoring)
  - Graph operations (CRUD, relationships, dedup constraints)
  - Scheduled tasks (mock APScheduler execution)
- Use `httpx.AsyncClient` for API endpoint tests
- Target: cover Phase 4 code + critical existing paths

**Files:** New `tests/` directory with `conftest.py`, `test_ingestion.py`, `test_query.py`, `test_graph.py`, `test_scheduler.py`

**Verification:**
- `pytest` passes with no failures
- Coverage report shows critical paths covered

---

### 4D.2: CI/CD
- [ ] **Complete**
- **Model:** Sonnet

**Changes:**
- GitHub Actions workflow:
  - On push: ruff lint, pytest, Docker build verification
  - On PR: same + code coverage report
- Add `pyproject.toml` with project metadata, ruff config, pytest config

**Files:** New `.github/workflows/ci.yml`, new `pyproject.toml`

**Verification:**
- Push to branch → GitHub Actions runs and passes
- Ruff reports no lint errors

---

### 4D.3: Security Cleanup
- [ ] **Complete**
- **Model:** Sonnet

**Changes:**
- Move ALL secrets to `.env` files (already in .gitignore)
- Remove hardcoded `NEO4J_PASSWORD=REDACTED_PASSWORD` from docker-compose.yml → reference `${NEO4J_PASSWORD}`
- Delete `docs/inventory/bifrost-env.txt` (contains API key)
- Add `.env.example` template with placeholder values

**Files:** Modify `src/mcp/docker-compose.yml`, add `.env.example`, delete inventory file

**Verification:**
- `grep -r "REDACTED_PASSWORD" .` returns no results (excluding .git)
- `grep -r "OPENROUTER" . --include="*.txt" --include="*.md"` returns no API keys
- `.env.example` exists with all required variable names

---

### 4D.4: Open Source Readiness
- [ ] **Complete**
- **Model:** Sonnet

**Changes:**
- Add `LICENSE` (Apache 2.0 — permissive with patent protection for commercial path)
- Add `CONTRIBUTING.md` with dev setup, coding standards, PR process
- Update README.md:
  - Add demo screenshots/GIFs of dashboard and query flow
  - Add badges: CI status, license, Python version
  - Add "Why Cerid?" section highlighting differentiators
- Add API versioning prefix: `/api/v1/` (backward-compatible, existing paths still work)

**Files:** New `LICENSE`, new `CONTRIBUTING.md`, modify `README.md`, modify routers (add prefix)

**Verification:**
- LICENSE file present and correct
- CONTRIBUTING.md has clear setup instructions
- README has badges, "Why Cerid?" section
- `/api/v1/health` and `/health` both work

---

## Implementation Order & Dependencies

```
4A (Refactor) ──┬──→ 4B.1 (Hybrid Search) ──→ 4B.4 (Temporal)
                ├──→ 4B.2 (Graph Traversal) ──→ 4B.3 (Cross-Domain)
                │                            └──→ 4C.2 (Proactive Surfacing)
                ├──→ 4C.1 (Scheduler) ──→ 4C.4 (Webhooks)
                │                     └──→ 4C.2 (Proactive Surfacing)
                └──→ 4C.3 (Smart Ingestion)

All 4A-4C ──→ 4D.1 (Testing) → 4D.2 (CI/CD) → 4D.3 (Security) → 4D.4 (OSS)
```

**Parallel opportunities:** After 4A, sub-phases 4B.1, 4B.2, 4C.1, and 4C.3 can proceed independently.

---

## Commercial Path Notes

This plan intentionally lays groundwork for future commercialization:
- **API versioning** enables breaking changes without disrupting users
- **Apache 2.0 license** allows commercial use while protecting patents
- **Webhook system** is the foundation for SaaS integrations
- **Modular routers** enable feature-gated tiers (free vs. pro)
- **Scheduled engine** enables managed hosting (ops automation)
- **Graph traversal** is a defensible differentiator vs. basic RAG tools

Authentication and multi-tenancy are explicitly deferred — they're significant architectural changes that should be designed separately when the commercial path is chosen.

---

*Document created: February 21, 2026*
