# Cerid AI — Development & Implementation Plan

## Phases A-D + 42-50

> **Document ID:** PLAN-2026-003
> **Author:** Development Team
> **Created:** 2026-03-21
> **Last Updated:** 2026-03-21
> **Status:** Approved — Ready for Execution
> **Methodology:** PMBOK/Agile Hybrid (2-week sprints, quality gates, DAG-ordered backlog)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Sprint Planning](#3-sprint-planning)
4. [Quality Gates & Audit Schedule](#4-quality-gates--audit-schedule)
5. [Dependency Graph](#5-dependency-graph)
6. [Risk Register](#6-risk-register)
7. [Definition of Done](#7-definition-of-done)
8. [Release Strategy](#8-release-strategy)

---

## 1. Executive Summary

### 1.1 Project Vision

Cerid AI is a self-hosted, privacy-first Personal AI Knowledge Companion. It unifies
multi-domain knowledge bases (code, finance, projects, artifacts) into a context-aware LLM
interface with RAG-powered retrieval and intelligent agents. All knowledge stays local; only
query context is sent to the user's chosen LLM provider.

The product differentiates on **verification depth** (hallucination detection, Self-RAG,
streaming claim verification with 4 claim types), **retrieval sophistication** (9-agent
pipeline, cross-encoder reranking, contextual chunking, adaptive retrieval, ColBERT-inspired
late interaction scoring, semantic query cache), and **user sovereignty** (BYOK model
configuration, local-first architecture, optional air-gapped deployment via Ollama).

### 1.2 Business Model

| Tier | License | Scope |
|------|---------|-------|
| **Core + App** | Apache-2.0 | KB engine, RAG pipeline, verification, SDK, GUI — always free |
| **Plugins** | BSL-1.1 | Multi-modal KB, advanced analytics, visual workflow — paid, source-available, converts to Apache-2.0 after 3 years |
| **Enterprise** | Commercial | Team features, SLA, priority support |

Revenue model: BYOK (users bring their own LLM keys). Zero cerid-hosted inference costs.

### 1.3 Competitive Positioning

Competitive analysis (2026-03-21) against the leading open-source AI platforms:

| Capability | Cerid | Dify (134K) | Open WebUI (128K) | RAGFlow (76K) | Mem0 (51K) | Khoj (34K) |
|-----------|-------|------|----------|---------|------|------|
| Hallucination Detection | Yes | No | No | No | No | No |
| Self-RAG Validation | Yes | No | No | No | No | No |
| Graph-Enhanced RAG | Yes | No | No | Yes | No | No |
| Multi-Agent Pipeline (9) | Yes | Yes (3) | No | No | No | Yes (4) |
| Typed SDK + MCP | Yes | Yes | No | No | Yes | No |
| User Automations | Planned | Yes | No | No | No | Yes |
| A2A Protocol | Planned | No | No | No | No | No |
| BYOK + Local LLM | Planned | Yes | Yes | Yes | No | Yes |

No competitor combines hallucination detection, GraphRAG, Self-RAG, a 9-agent pipeline,
and a typed SDK with MCP tooling. This plan extends the moat with web search fallback,
user automations, A2A protocol, and a desktop application.

### 1.4 Timeline Overview

| Milestone | Target Sprint | Target Date | Version |
|-----------|--------------|-------------|---------|
| Unified Compose + BYOK (Phases A+B) | Sprint 1-2 | 2026-04-18 | v2.0.0 |
| Web Search Fallback (Phase 42) | Sprint 3 | 2026-05-02 | v2.1.0 |
| User Automations + Enhanced Memory (43+44) | Sprint 4-5 | 2026-05-30 | v2.2.0 |
| A2A Protocol (Phase 45) | Sprint 5-6 | 2026-06-13 | v2.3.0 |
| Repo Architecture Separation (Phase C) | Sprint 6-8 | 2026-07-11 | v3.0.0 |
| Quality Audit Sprint 1 | Sprint 7 | 2026-06-27 | -- |
| Multi-Modal KB (Phase 46) | Sprint 8-9 | 2026-07-25 | v3.1.0 |
| Observability + Local LLM (47+48) | Sprint 9-10 | 2026-08-08 | v3.2.0 |
| Electron Desktop App (Phase D) | Sprint 10-12 | 2026-09-05 | v3.3.0 |
| Quality Audit Sprint 2 | Sprint 11 | 2026-08-22 | -- |
| Plugin Foundation (Phase 49) | Sprint 12-13 | 2026-09-19 | v3.4.0 |
| Visual Workflow (Phase 50) | Sprint 14-15 | 2026-10-17 | v3.5.0 |
| Final Quality Audit + Release Prep | Sprint 16 | 2026-10-31 | v4.0.0 |

Sprint cadence: 2-week sprints starting 2026-03-24.

---

## 2. Architecture Overview

### 2.1 Current State (Phase 41)

```
                    +-----------+
                    |  React    |
                    |  GUI      |
                    |  :3000    |
                    +-----+-----+
                          |
              +-----------+-----------+
              |                       |
        +-----v-----+         +------v------+
        |  Bifrost   |         |  MCP Server |
        |  LLM GW    |         |  FastAPI    |
        |  :8080     |         |  :8888      |
        +-----+------+         +------+------+
              |                        |
              v                 +------+------+------+
        OpenRouter /            |             |      |
        LLM Providers     +----v----+  +-----v--+ +-v------+
                           | ChromaDB|  | Neo4j  | | Redis  |
                           | :8001   |  | :7474  | | :6379  |
                           +---------+  +--------+ +--------+

Services: 6 containers across 4 Docker Compose files
Agents: 9 (query, curator, triage, rectify, audit, maintenance, hallucination, memory, self_rag)
MCP Tools: 23 (18 core + 5 trading)
Tests: 1376 Python + 545 frontend
CI/CD: 7-job GitHub Actions pipeline
```

Key architectural characteristics:
- **4 separate Docker Compose files** (infrastructure, bifrost, mcp, web) with manual startup ordering
- **Hardcoded Bifrost config.yaml** requiring manual editing for model changes
- **Monolithic `src/mcp/` directory** mixing core engine with application logic
- **No first-run experience** (requires manual `.env` configuration)
- **No web search capability** (ignorance claims have no resolution path)

### 2.2 Target State (After Phase 50)

```
                  +------------------+
                  |  Electron App    |
                  |  (.dmg / .exe)   |
                  |  System tray     |
                  +--------+---------+
                           |
                    +------v------+
                    |  React GUI  |
                    |  + Visual   |
                    |  Workflow   |
                    |  Builder    |
                    +------+------+
                           |
              +------------+------------+
              |                         |
        +-----v-----+           +------v------+     +-------------+
        |  Bifrost   |           |  MCP Server |     |  A2A Server |
        |  (BYOK     |           |  + Plugins  |     |  /.well-    |
        |  providers)|           |  + Web Srch |     |  known/     |
        +-----+------+           +------+------+     |  agent.json |
              |                         |             +------+------+
              v                  +------+------+------+      |
        User-selected            |             |      |      v
        LLM Providers       +---v-----+  +----v---+ +v------++ External
        (OpenRouter,         | ChromaDB|  | Neo4j  | | Redis  | A2A
         OpenAI,             | + OCR   |  | + Mem  | | + Auto | Agents
         Anthropic,          | + Audio |  | Nodes  | | + Sched|
         xAI, Ollama)        | + Image |  |        | |        |
                              +--------+  +--------+ +--------+

Repository Structure:
  core/       (Apache-2.0)  — KB engine, RAG, verification, SDK
  app/        (Apache-2.0)  — API server, GUI, agents
  plugins/    (BSL-1.1)     — Multi-modal, analytics, visual workflow
  enterprise/ (Commercial)  — Team features, SLA
  desktop/    (Apache-2.0)  — Electron wrapper
```

Key architectural improvements:
- **Single `docker-compose.yml`** with health-check-based dependency ordering
- **First-run wizard** for guided setup (API key entry, model selection, health monitoring)
- **BYOK provider system** with runtime Bifrost config generation from templates
- **4-part repository structure** with clear licensing boundaries
- **Web search fallback** resolving ignorance claims through verified external data
- **A2A + MCP dual protocol** for agent interoperability
- **Plugin architecture** with manifest-based loading and tier-gated registration
- **Electron desktop app** with Docker lifecycle management
- **Visual workflow builder** for custom agent pipeline composition

### 2.3 Repository Restructure Diagram (Phase C)

```
BEFORE (Phase 41):                       AFTER (Phase C):

src/mcp/                                 core/
  agents/         --> move to -->           src/
  utils/          --> move to -->             agents/
  db/             --> move to -->             utils/
  config/         --> move to -->             db/
  parsers/        --> move to -->             config/
  services/       --> move to -->             parsers/
  models/         --> move to -->             services/
  plugins/        --> move to -->             models/
  eval/           --> move to -->             eval/
  tools.py        --> move to -->             tools.py
  main.py         --> split into -->       app/
  routers/        --> move to -->           api/
  middleware/     --> move to -->             routers/
  scripts/        --> move to -->             middleware/
                                             main.py
                                           plugins/
                                             multimodal/
                                             analytics/
                                             workflow/
                                           enterprise/
                                             (future)
                                           desktop/
                                             electron/
                                             (Phase D)

Migration: 6 atomic PRs with re-export bridges at every step.
Tests must pass after each PR. Zero import breakage.
```

### 2.4 Service Architecture Evolution

| Phase | Services Added/Changed | Net Container Count |
|-------|----------------------|---------------------|
| Current (41) | 6 containers, 4 compose files | 6 |
| Phase A | Merge to 1 compose file, add setup API | 6 |
| Phase B | Bifrost config templating, provider management | 6 |
| Phase 42 | Web search provider (in-process, no new container) | 6 |
| Phase 45 | A2A endpoints (in-process on MCP server) | 6 |
| Phase 48 | Ollama as optional provider (user-managed) | 6 (+1 optional) |
| Phase D | Electron wrapping existing containers | 6 + desktop |

---

## 3. Sprint Planning

### Sprint 1 (2026-03-24 — 2026-04-04)

**Sprint Goal:** Unify Docker deployment and begin BYOK provider system.

#### Phase A: Unified Docker Compose (Part 1)

**User Stories:**
- As a new user, I can start Cerid with a single `docker compose up` command
- As a developer, I can see health-check-based startup ordering without manual intervention

**Tasks:**
1. Merge 4 Docker Compose files into single root `docker-compose.yml`
2. Add `depends_on` with health check conditions for startup ordering
3. Update `scripts/start-cerid.sh` to use single compose file
4. Implement Setup API scaffolding (`routers/setup.py`)
5. Add MCP "setup mode" — serve wizard when `OPENROUTER_API_KEY` is empty, skip Bifrost
6. Update `scripts/validate-env.sh` for new compose structure

**Acceptance Criteria:**
- [ ] `docker compose up` from repo root starts all 6 services in correct order
- [ ] Services wait for dependency health checks before starting
- [ ] `start-cerid.sh` works with new single compose file
- [ ] When API key is missing, MCP server enters setup mode (returns 503 for KB endpoints, 200 for setup endpoints)
- [ ] All existing tests pass without modification

#### Phase B: BYOK Model Configuration (Part 1)

**User Stories:**
- As a user, I can configure my preferred LLM providers through a settings UI
- As a user, I can validate my API keys before saving them

**Tasks:**
1. Design `PROVIDER_REGISTRY` config (`config/providers.py`)
2. Implement provider management endpoints (`routers/providers.py`)
3. Create API key validation logic per provider (test endpoint calls)
4. Design Bifrost `config.yaml.template` with Jinja2 templating

**Acceptance Criteria:**
- [ ] `PROVIDER_REGISTRY` defines supported providers (OpenRouter, OpenAI, Anthropic, xAI, Ollama) with base URLs and test endpoints
- [ ] `POST /providers/{name}/validate` tests API key connectivity
- [ ] Provider keys stored encrypted in `.env` via existing Fernet encryption
- [ ] 15+ new backend tests covering provider CRUD and validation

**Quality Gate:**
- CI green on all 7 jobs
- No regressions in existing 1376 Python tests
- New code has >= 80% test coverage (measured by Codecov)

---

### Sprint 2 (2026-04-07 — 2026-04-18)

**Sprint Goal:** Complete first-run wizard and BYOK model assignment.

#### Phase A: First-Run Wizard (Part 2)

**User Stories:**
- As a first-time user, I am guided through setup with an interactive wizard
- As a returning user, the wizard does not appear

**Tasks:**
1. React first-run wizard: 4-step flow (Welcome > API Key > Model Prefs > Health Monitor)
2. API key entry with inline validation (calls `/providers/{name}/validate`)
3. Model preference selection (chat, verification, categorization)
4. Health dashboard component (reusable in Settings "Infrastructure" tab)
5. Wizard completion writes config and triggers Bifrost restart

**Acceptance Criteria:**
- [ ] First-run wizard appears when no provider keys are configured
- [ ] API key validation provides real-time feedback (valid/invalid/checking)
- [ ] Model preference changes generate valid Bifrost config from template
- [ ] Health dashboard shows real-time status of all 6 services
- [ ] Wizard does not appear on subsequent launches
- [ ] 20+ new frontend tests for wizard components

#### Phase B: Model Assignment + Bifrost Templating (Part 2)

**User Stories:**
- As a user, I can assign specific models to specific tasks (chat, verification, etc.)
- As a user, my model preferences persist across restarts

**Tasks:**
1. Model assignment backend: per-task model selection endpoints
2. Bifrost `config.yaml` generation from template + user preferences
3. Settings UI: "Providers & Models" tab with provider cards + model dropdowns
4. Bifrost restart trigger on config change (Docker API via `dockerode` or exec)
5. Migration path: existing hardcoded `config.yaml` users get prompted to migrate

**Acceptance Criteria:**
- [ ] Users can assign models to: chat, verification, categorization, reranking, synopsis
- [ ] `config.yaml` is generated from template on every preference change
- [ ] Bifrost restarts cleanly with new config (< 5s downtime)
- [ ] Settings UI shows current provider status and model assignments
- [ ] Existing users with hardcoded configs are not broken (graceful fallback)
- [ ] 15+ new backend tests, 15+ new frontend tests

**Quality Gate:**
- CI green, no regressions
- First-run wizard works end-to-end (manual smoke test on clean Docker environment)
- `docker compose up` on a machine with no `.env` triggers wizard flow

**Sprint 2 Deliverable:** **v2.0.0** — Unified compose + BYOK provider system

---

### Sprint 3 (2026-04-21 — 2026-05-02)

**Sprint Goal:** Add agentic web search to resolve knowledge gaps.

#### Phase 42: Agentic Web Search Fallback

**User Stories:**
- As a user, when the AI admits ignorance, it should automatically search the web for answers
- As a user, web search results should be verified through Self-RAG before being presented
- As a user, I can optionally auto-ingest web search results into my KB

**Tasks:**
1. `WebSearchProvider` abstraction (`utils/web_search.py`)
   - Tavily API as primary provider
   - SearXNG self-hosted as alternative
   - Provider interface for future additions
2. Integration with hallucination pipeline (`agents/hallucination/streaming.py`)
   - On "ignorance" claim detection, trigger web search
   - Feed results through Self-RAG validation before surfacing
3. New `pkb_web_search` MCP tool for explicit web searches
4. Optional auto-ingest feature (`ENABLE_AUTO_LEARN` env var)
   - Verified web results stored as artifacts with `source: web_search` provenance
5. Frontend: web search indicator in chat (search icon + source URLs)
6. Rate limiting: 10 searches/minute per user, configurable

**Acceptance Criteria:**
- [ ] Ignorance claims trigger automatic web search (when provider configured)
- [ ] Web results pass through Self-RAG before appearing in responses
- [ ] `pkb_web_search` MCP tool available for explicit searches
- [ ] Auto-ingest stores verified results with correct provenance metadata
- [ ] Web search disabled gracefully when no provider key is configured
- [ ] Rate limiting prevents abuse
- [ ] 25+ new Python tests, 10+ new frontend tests

**Quality Gate:**
- CI green, no regressions
- Manual test: ask a question about a recent event, verify web search triggers and results are cited

**Sprint 3 Deliverable:** **v2.1.0** — Web search fallback

---

### Sprint 4 (2026-05-05 — 2026-05-16)

**Sprint Goal:** User automations and begin enhanced memory layer.

#### Phase 43: User-Facing Scheduled Automations

**User Stories:**
- As a user, I can schedule recurring knowledge tasks (e.g., daily digest, weekly research summary)
- As a user, I can create automations with custom prompts and schedules

**Tasks:**
1. Automations CRUD API (`routers/automations.py`)
   - Create, read, update, delete, enable/disable
   - Redis-backed persistence
   - Cron expression support + preset templates (daily/weekly/monthly)
2. Automation execution engine (extends existing APScheduler)
   - Actions: `notify` (SSE push), `digest` (accumulate + summarize), `ingest` (auto-store)
   - Full agent pipeline execution (query + hallucination + Self-RAG)
3. Automation management GUI (`components/automations/`)
   - List view with status indicators (active/paused/error)
   - Create/edit dialog with schedule builder
   - Execution history log
4. SSE push notifications for automation results

**Acceptance Criteria:**
- [ ] CRUD operations work for automations with cron schedules
- [ ] Automations execute on schedule with full agent pipeline
- [ ] Three action types (notify, digest, ingest) function correctly
- [ ] GUI allows creation, editing, and monitoring of automations
- [ ] Automation failures are logged and surfaced in GUI
- [ ] 20+ new Python tests, 15+ new frontend tests

#### Phase 44: Enhanced Memory Layer (Part 1)

**User Stories:**
- As a user, the AI remembers facts across conversations with conflict resolution
- As a user, memories decay naturally but are reinforced by repeated access

**Tasks:**
1. Memory conflict detection: embed new memory, search existing at >0.85 similarity
2. LLM-powered conflict classification: supersede / coexist / merge
3. Decay/reinforcement scoring: `base * (1 + log(accesses)) * decay(age)`

**Acceptance Criteria:**
- [ ] Conflicting memories are detected and resolved (supersede/coexist/merge)
- [ ] Memory scores decay with age and increase with access frequency
- [ ] 15+ new Python tests for conflict detection and scoring

**Quality Gate:**
- CI green, no regressions
- Manual test: create conflicting memories, verify resolution behavior

---

### Sprint 5 (2026-05-19 — 2026-05-30)

**Sprint Goal:** Complete enhanced memory and begin A2A protocol.

#### Phase 44: Enhanced Memory Layer (Part 2)

**Tasks:**
1. Neo4j `:Memory` node type with relationships to `:Artifact` and `:Conversation`
2. New `pkb_memory_recall` MCP tool for context-aware retrieval
3. Memory management GUI (view, edit, delete, force-reinforce)
4. Memory injection into chat context (alongside KB injection)

**Acceptance Criteria:**
- [ ] `:Memory` nodes in Neo4j with full relationship graph
- [ ] `pkb_memory_recall` returns context-relevant memories ranked by score
- [ ] GUI shows memory timeline with decay visualization
- [ ] Memories are injected into chat context when relevant
- [ ] 15+ new Python tests, 10+ new frontend tests

#### Phase 45: A2A Protocol (Part 1)

**User Stories:**
- As an external agent, I can discover Cerid's capabilities via Agent Card
- As an external agent, I can submit tasks to Cerid via A2A protocol

**Tasks:**
1. Agent Card at `/.well-known/agent.json`
2. A2A task lifecycle endpoints: create, status, cancel
3. Task-to-agent-call mapping (thin wrappers around existing agent calls)

**Acceptance Criteria:**
- [ ] Agent Card served at `/.well-known/agent.json` with correct schema
- [ ] Tasks can be created, queried, and cancelled via A2A endpoints
- [ ] Task execution delegates to existing agents (query, verification, etc.)
- [ ] 15+ new Python tests

**Sprint 5 Deliverable:** **v2.2.0** — User automations + enhanced memory

---

### Sprint 6 (2026-06-02 — 2026-06-13)

**Sprint Goal:** Complete A2A protocol and begin repo architecture separation.

#### Phase 45: A2A Protocol (Part 2)

**Tasks:**
1. A2A client utility (`utils/a2a_client.py`) for discovering and invoking external A2A agents
2. Agent discovery UI in settings (scan network for A2A-compatible agents)
3. Bidirectional task delegation (Cerid as both server and client)
4. Documentation: A2A integration guide

**Acceptance Criteria:**
- [ ] Cerid can discover and invoke external A2A agents
- [ ] Dual MCP + A2A protocol operational
- [ ] A2A client handles timeouts, retries, and error reporting
- [ ] 15+ new Python tests, 5+ new frontend tests

#### Phase C: Repo Architecture Separation (Part 1)

**Tasks:**
1. Design final directory structure and import map
2. PR 1: Create `core/` directory, move utility modules (`utils/`, `db/`, `config/`, `parsers/`, `services/`, `models/`, `eval/`)
3. PR 1 re-export bridges: `src/mcp/utils.py` re-exports from `core/src/utils/`, etc.

**Acceptance Criteria:**
- [ ] PR 1 merged: utility modules live in `core/src/`
- [ ] All existing imports work via re-export bridges
- [ ] All 1376+ Python tests pass without modification
- [ ] CI green

**Sprint 6 Deliverable:** **v2.3.0** — A2A protocol

---

### Sprint 7 (2026-06-16 — 2026-06-27)

**Sprint Goal:** Quality Audit Sprint 1.

**This is a dedicated quality sprint. No new features.**

#### Quality Audit Tasks

1. **AI Slop Cleanup**
   - Scan all files for generic AI-generated comments ("This function does X", "Handle the response")
   - Remove or replace with meaningful comments
   - Identify and remove unnecessary abstractions added by AI tooling
   - Target: zero generic single-line comments

2. **Dead Code Removal**
   - Run `vulture` on Python codebase
   - Run `ts-prune` on TypeScript codebase
   - Remove all confirmed dead code
   - Target: zero `vulture` findings at 80% confidence

3. **Type Annotation Completeness**
   - Run `mypy --strict` on `core/src/` (newly separated)
   - Fix all type errors
   - Add `py.typed` marker file
   - Target: mypy strict pass on core module

4. **Security Audit**
   - Run `pip-audit` and resolve all CVEs
   - Run `npm audit` and resolve all critical/high
   - Scan for hardcoded secrets with `detect-secrets`
   - Review CORS, auth, and rate limiting configuration
   - Target: zero critical/high CVEs, zero leaked secrets

5. **Documentation Accuracy**
   - Verify `CLAUDE.md` matches current architecture
   - Verify `API_REFERENCE.md` matches current endpoints
   - Verify `ISSUES.md` — close resolved, add newly discovered
   - Verify inline docstrings match function signatures
   - Target: all docs reflect Phase C PR 1 structure

6. **Performance Regression Check**
   - Run retrieval eval harness (NDCG, MRR, P@K)
   - Compare against Phase 41 baseline
   - Profile cold query latency
   - Target: no metric regression > 5%

**Acceptance Criteria:**
- [ ] Zero generic AI comments in codebase
- [ ] Zero confirmed dead code
- [ ] mypy strict passes on `core/src/`
- [ ] Zero critical/high CVEs
- [ ] All documentation files accurate
- [ ] No performance regression > 5%
- [ ] CI green, test coverage >= 70%

**Quality Gate:**
- Sign-off checklist reviewed and all items confirmed
- Audit findings documented in `docs/AUDIT_LOG.md`

---

### Sprint 8 (2026-06-30 — 2026-07-11)

**Sprint Goal:** Complete repo architecture separation and begin multi-modal KB.

#### Phase C: Repo Architecture Separation (Part 2-3)

**Tasks:**
1. PR 2: Move agents to `core/src/agents/`
2. PR 3: Move `tools.py` to `core/src/tools.py`
3. PR 4: Split `main.py` — core initialization to `core/src/`, API server to `app/api/main.py`
4. PR 5: Move routers and middleware to `app/api/`
5. PR 6: Create `plugins/` directory with manifest-based loader
6. License key validation: offline signed JWT, no phone-home
7. Update all CI paths, Docker build contexts, and import references
8. Remove all re-export bridges (final PR)

**Acceptance Criteria:**
- [ ] All 6 PRs merged in sequence, tests pass at every step
- [ ] Final structure: `core/`, `app/`, `plugins/`, `enterprise/`
- [ ] `core/` has Apache-2.0 license header on all files
- [ ] `plugins/` has BSL-1.1 license header
- [ ] Plugin loader reads `manifest.json`, validates license tier
- [ ] License key validation works offline (signed JWT with RSA)
- [ ] Docker build updated for new directory structure
- [ ] CI pipeline updated (paths, exclusions, build contexts)
- [ ] Zero re-export bridges remain (all imports point to final locations)
- [ ] All tests pass (1376+ Python, 545+ frontend)

#### Phase 46: Multi-Modal KB (Part 1)

**Tasks:**
1. Activate OCR plugin (already scaffolded in Phase 8A)
2. Image ingestion via vision LLM (extract descriptions, text, metadata)

**Acceptance Criteria:**
- [ ] OCR processes images and PDFs with embedded images
- [ ] Image descriptions stored as searchable artifacts
- [ ] 10+ new Python tests

**Quality Gate:**
- CI green on updated paths
- `docker compose build` succeeds with new directory structure
- Manual smoke test: full workflow (ingest, query, verify) on restructured codebase

**Sprint 8 Deliverable:** **v3.0.0** — Repo architecture separation (breaking: new structure)

---

### Sprint 9 (2026-07-14 — 2026-07-25)

**Sprint Goal:** Complete multi-modal KB, begin observability and local LLM.

#### Phase 46: Multi-Modal KB (Part 2)

**User Stories:**
- As a user, I can ingest audio files and have them transcribed and searchable
- As a user, I can ingest images and search by visual content

**Tasks:**
1. Audio transcription via `faster-whisper` (local, privacy-preserving)
2. Audio artifact type with transcript + metadata storage
3. Vision LLM integration for rich image understanding
4. Multi-modal search: text queries match against transcripts and image descriptions
5. Plugin packaging: multi-modal as first BSL-1.1 plugin with `manifest.json`

**Acceptance Criteria:**
- [ ] Audio files (.mp3, .wav, .m4a, .ogg) transcribed and stored as searchable artifacts
- [ ] Images (.png, .jpg, .gif, .webp) analyzed via vision LLM and stored
- [ ] Multi-modal content appears in standard search results
- [ ] Plugin loads via manifest, respects license tier
- [ ] 20+ new Python tests

#### Phase 47: Observability Dashboard (Part 1)

**Tasks:**
1. Metrics collection layer (latency, cost, retrieval quality per request)
2. Time-series storage in Redis (1h/24h/7d/30d aggregation windows)
3. NDCG@5 tracking on every query (using eval harness)

**Acceptance Criteria:**
- [ ] Metrics collected for every query: latency, token cost, NDCG@5
- [ ] Redis time-series with automatic aggregation
- [ ] 10+ new Python tests

---

### Sprint 10 (2026-07-28 — 2026-08-08)

**Sprint Goal:** Complete observability dashboard and local LLM support, begin Electron app.

#### Phase 47: Observability Dashboard (Part 2)

**User Stories:**
- As a user, I can see real-time performance metrics for my Cerid instance
- As a power user, I can track retrieval quality trends over time

**Tasks:**
1. Dashboard API endpoints (`routers/observability.py`)
2. Frontend observability dashboard component
   - Latency chart (P50/P95/P99)
   - Cost tracking (daily/weekly/monthly)
   - Retrieval quality trend (NDCG@5 over time)
   - Model usage breakdown
3. Basic metrics in core (free), advanced analytics as plugin (paid)

**Acceptance Criteria:**
- [ ] Dashboard shows real-time latency, cost, and quality metrics
- [ ] Time range selector (1h/24h/7d/30d)
- [ ] Basic metrics available to all users
- [ ] Advanced analytics gated by plugin license
- [ ] 10+ new Python tests, 10+ new frontend tests

#### Phase 48: Local LLM via Ollama

**User Stories:**
- As a privacy-conscious user, I can run Cerid with a local LLM and zero external API calls
- As a user in an air-gapped environment, I can use Cerid without internet access

**Tasks:**
1. Add Ollama as provider in BYOK system (`config/providers.py`)
2. Ollama API adapter (compatible with OpenAI chat completions format)
3. Model pull/management UI in Settings (list available, pull new, delete)
4. Air-gapped deployment documentation
5. Performance recommendations (model size vs RAM requirements)

**Acceptance Criteria:**
- [ ] Ollama appears as provider option in BYOK settings
- [ ] Chat, verification, and categorization work with Ollama models
- [ ] Model management (list/pull/delete) works from Settings UI
- [ ] Air-gapped deployment guide documented
- [ ] 15+ new Python tests, 5+ new frontend tests

#### Phase D: Electron Desktop App (Part 1)

**Tasks:**
1. Electron project scaffolding (`desktop/electron/`)
2. Docker lifecycle management via `dockerode`
3. System tray integration (start/stop/status)

**Acceptance Criteria:**
- [ ] Electron app starts and manages Docker containers
- [ ] System tray shows container status
- [ ] 5+ new tests

**Sprint 10 Deliverable:** **v3.2.0** — Observability + Local LLM

---

### Sprint 11 (2026-08-11 — 2026-08-22)

**Sprint Goal:** Quality Audit Sprint 2.

**Dedicated quality sprint. No new features.**

#### Quality Audit Tasks

1. **AI Slop Cleanup (Round 2)**
   - Re-scan after Phases 42-48 development
   - Focus on newly written code quality
   - Target: zero generic comments in new code

2. **Dead Code Removal (Round 2)**
   - Re-run `vulture` and `ts-prune`
   - Check for orphaned re-export bridges from Phase C migration
   - Target: zero dead code

3. **Type Annotation Completeness**
   - `mypy --strict` on `app/api/` (post-Phase C split)
   - TypeScript strict mode on all new frontend components
   - Target: mypy strict pass on both `core/` and `app/`

4. **Security Audit (Round 2)**
   - Full dependency audit (pip-audit, npm audit)
   - Electron-specific security review (CSP, nodeIntegration, contextIsolation)
   - Ollama integration security (local network exposure)
   - Target: zero critical/high CVEs

5. **Documentation Accuracy (Round 2)**
   - Update all docs for new directory structure
   - Plugin development guide
   - A2A integration guide
   - Target: all docs current

6. **Integration Test Suite**
   - End-to-end test: ingest file, query, verify, web search fallback
   - Plugin loading test: manifest validation, tier gating
   - A2A test: create task, check status, receive result
   - Target: 5+ integration tests passing

**Acceptance Criteria:**
- [ ] All audit checklist items pass
- [ ] Integration test suite passes
- [ ] CI green, coverage >= 70%

---

### Sprint 12 (2026-08-25 — 2026-09-05)

**Sprint Goal:** Complete Electron desktop app, begin plugin foundation.

#### Phase D: Electron Desktop App (Part 2-3)

**User Stories:**
- As a user, I can install Cerid as a native desktop application
- As a user, the app auto-updates when new versions are available

**Tasks:**
1. Window management: embed React GUI in Electron BrowserWindow
2. Auto-update via GitHub Releases (`electron-updater`)
3. macOS: `.dmg` packaging with code signing
4. Windows: `.exe` installer with code signing
5. First-launch Docker check and guided installation
6. Settings integration: Electron-specific preferences (launch at login, minimize to tray)
7. Menu bar integration (File, Edit, View, Help)
8. Deep linking: `cerid://` protocol handler

**Acceptance Criteria:**
- [ ] `.dmg` installs and launches on macOS (Intel + Apple Silicon)
- [ ] `.exe` installs and launches on Windows 10+
- [ ] Auto-update checks GitHub Releases and prompts user
- [ ] Docker containers start/stop with app lifecycle
- [ ] System tray with status indicators and quick actions
- [ ] Code signing configured for both platforms
- [ ] 15+ new tests (Electron + integration)

#### Phase 49: Plugin Foundation (Part 1)

**Tasks:**
1. Plugin packaging standard: `manifest.json` schema definition
2. Plugin loader: discovery, validation, registration
3. Plugin management API (`routers/plugins.py`)

**Acceptance Criteria:**
- [ ] `manifest.json` schema documented and validated
- [ ] Plugin loader discovers and loads plugins from `plugins/` directory
- [ ] Management API supports list, enable, disable, info
- [ ] 10+ new Python tests

**Quality Gate:**
- Electron app builds successfully on CI (macOS + Windows runners)
- Manual smoke test: install from DMG, verify Docker lifecycle

**Sprint 12 Deliverable:** **v3.3.0** — Electron desktop app

---

### Sprint 13 (2026-09-08 — 2026-09-19)

**Sprint Goal:** Complete plugin foundation.

#### Phase 49: Plugin Foundation (Part 2)

**User Stories:**
- As a developer, I can create plugins following a documented standard
- As a user, I can install, enable, and disable plugins from the GUI
- As a user, paid plugins require a valid license key

**Tasks:**
1. Plugin management GUI (`components/plugins/`)
   - Plugin marketplace/list view
   - Enable/disable toggles
   - License key entry for paid plugins
   - Plugin settings pages (dynamic, per-plugin)
2. Plugin SDK: base classes, hook points, documentation
3. License key validation in plugin loader (check JWT signature, expiry, tier)
4. Migrate multi-modal (Phase 46) to plugin packaging format
5. Plugin development guide documentation

**Acceptance Criteria:**
- [ ] GUI shows installed plugins with status and actions
- [ ] License key validation gates paid plugin activation
- [ ] Multi-modal KB runs as a properly packaged plugin
- [ ] Plugin development guide is complete and accurate
- [ ] 15+ new Python tests, 15+ new frontend tests

**Quality Gate:**
- Plugin system works end-to-end: install, configure, use, uninstall
- Multi-modal plugin passes all existing tests when loaded via plugin system

**Sprint 13 Deliverable:** **v3.4.0** — Plugin foundation

---

### Sprint 14 (2026-09-22 — 2026-10-03)

**Sprint Goal:** Begin visual workflow builder.

#### Phase 50: Visual Workflow Builder (Part 1)

**User Stories:**
- As a power user, I can see a visual representation of the RAG pipeline
- As a power user, I can toggle pipeline stages on/off via the visual interface

**Tasks:**
1. Pipeline DAG model: define stages, connections, parameters as JSON schema
2. Workflow visualization component (React Flow or similar)
   - Nodes for each pipeline stage (retrieval, reranking, verification, etc.)
   - Edges showing data flow
   - Color-coded status (active/disabled/error)
3. Stage toggle interaction: click node to enable/disable pipeline stage
4. Real-time pipeline execution visualization (highlight active node during query)

**Acceptance Criteria:**
- [ ] Pipeline stages rendered as interactive DAG
- [ ] Clicking a node toggles the corresponding `ENABLE_*` feature flag
- [ ] Pipeline execution highlights active nodes in real-time
- [ ] 15+ new frontend tests

---

### Sprint 15 (2026-10-06 — 2026-10-17)

**Sprint Goal:** Complete visual workflow builder.

#### Phase 50: Visual Workflow Builder (Part 2)

**User Stories:**
- As a power user, I can create custom pipelines by composing stages in a DAG editor
- As a user, I can save and load pipeline configurations

**Tasks:**
1. Custom DAG composition: drag-and-drop node placement, edge drawing
2. Pipeline validation: cycle detection, required stage enforcement
3. Save/load pipeline configurations (Redis-backed)
4. Pipeline templates: "Essential", "Research", "Maximum Quality"
5. Export/import pipeline configs as JSON
6. Paid plugin gate: custom composition is BSL-1.1, visualization is free

**Acceptance Criteria:**
- [ ] Custom DAGs can be created, validated, saved, and loaded
- [ ] Cycle detection prevents invalid pipelines
- [ ] Pipeline templates provide quick-start configurations
- [ ] Free users see visualization; paid users get composition
- [ ] 20+ new frontend tests, 10+ new Python tests

**Quality Gate:**
- Visual workflow renders correctly on desktop and tablet viewports
- Custom pipeline executes correctly end-to-end

**Sprint 15 Deliverable:** **v3.5.0** — Visual workflow builder

---

### Sprint 16 (2026-10-20 — 2026-10-31)

**Sprint Goal:** Final quality audit and release preparation.

#### Final Quality Audit

1. **Full Codebase Scan**
   - AI slop cleanup (final pass)
   - Dead code removal (final pass)
   - `mypy --strict` on entire Python codebase
   - TypeScript strict mode on entire frontend
   - Target: zero findings

2. **Security Audit (Final)**
   - Full dependency audit
   - Electron security review (CSP, sandbox, permissions)
   - Plugin sandboxing review
   - Secrets detection scan
   - OWASP top 10 checklist against API surface
   - Target: zero critical/high findings

3. **Performance Audit**
   - Cold query latency benchmark
   - Retrieval quality benchmark (NDCG, MRR, P@K)
   - Memory usage profiling (all containers)
   - Electron startup time
   - Target: published performance baseline

4. **Integration Testing**
   - End-to-end: fresh install (Electron) > wizard > ingest > query > verify > web search > automation
   - Cross-platform: macOS (Intel + ARM), Windows 10/11
   - Plugin lifecycle: install > configure > use > update > uninstall
   - A2A: bidirectional task exchange with mock external agent
   - Target: all integration tests pass

5. **Documentation Finalization**
   - `CLAUDE.md` reflects final architecture
   - `API_REFERENCE.md` covers all endpoints
   - `CHANGELOG.md` covers all phases
   - Plugin development guide finalized
   - User documentation (getting started, configuration, troubleshooting)
   - Target: documentation review complete

6. **Accessibility Audit**
   - WCAG 2.1 AA compliance check on all GUI pages
   - Keyboard navigation verification
   - Screen reader compatibility
   - Target: zero critical accessibility issues

#### Release Preparation

1. Version bump to v4.0.0
2. GitHub Release with changelog, binaries (.dmg, .exe), migration guide
3. Marketing site update: new features, updated screenshots, pricing page
4. README.md overhaul for public repository

**Acceptance Criteria:**
- [ ] All audit checklists pass
- [ ] All integration tests pass
- [ ] Documentation complete and reviewed
- [ ] Electron binaries built and tested on both platforms
- [ ] GitHub Release drafted with all assets
- [ ] Marketing site updated

**Sprint 16 Deliverable:** **v4.0.0** — Complete platform release

---

## 4. Quality Gates & Audit Schedule

### 4.1 Sprint-Level Quality Gates

Every sprint close requires:

| Gate | Criteria | Enforcement |
|------|----------|-------------|
| CI Green | All 7 CI jobs pass | Automated (GitHub Actions) |
| No Regressions | All pre-existing tests pass | Automated (test suite) |
| Coverage | >= 70% on new code | Automated (Codecov) |
| Type Safety | mypy + tsc pass | Automated (CI lint jobs) |
| Lint | ruff + eslint clean | Automated (CI lint jobs) |
| Security | pip-audit + npm audit clean | Automated (CI security job) |
| Docs | CLAUDE.md + ISSUES.md updated | Manual (PR review checklist) |

### 4.2 Dedicated Quality Audit Sprints

| Sprint | Focus Areas | Trigger |
|--------|------------|---------|
| Sprint 7 | Post-Phase C restructure audit | After repo migration |
| Sprint 11 | Post-feature-development audit | After Phases 42-48 |
| Sprint 16 | Pre-release comprehensive audit | Before v4.0.0 |

### 4.3 Quality Audit Checklist (used in every audit sprint)

```
[ ] AI Slop Cleanup
    [ ] Generic comments removed/replaced
    [ ] Unnecessary abstractions identified and simplified
    [ ] Copy-paste patterns consolidated
    [ ] Variable names are meaningful (no 'data', 'result', 'item' without context)

[ ] Dead Code Removal
    [ ] vulture scan (Python, 80% confidence threshold)
    [ ] ts-prune scan (TypeScript)
    [ ] Orphaned test files
    [ ] Unused dependencies

[ ] Type Annotation Completeness
    [ ] mypy --strict on target modules
    [ ] TypeScript strict mode
    [ ] No untyped function signatures in public APIs

[ ] Security Audit
    [ ] pip-audit: zero critical/high CVEs
    [ ] npm audit: zero critical/high CVEs
    [ ] detect-secrets: zero findings
    [ ] CORS configuration review
    [ ] Rate limiting configuration review
    [ ] Auth bypass review

[ ] Documentation Accuracy
    [ ] CLAUDE.md matches architecture
    [ ] API_REFERENCE.md matches endpoints
    [ ] ISSUES.md is current
    [ ] Inline docstrings match signatures

[ ] Performance Regression Check
    [ ] Retrieval eval harness (NDCG, MRR, P@K, R@K)
    [ ] Cold query latency benchmark
    [ ] Memory usage profiling
    [ ] No metric regression > 5% from baseline
```

### 4.4 Continuous Quality Practices

- **Pre-commit hooks:** ruff lint, lock file sync check
- **PR checklist:** type check, test coverage, doc update
- **Post-tool hooks (Claude Code):** typecheck on `.ts`/`.tsx` edits, pythonlint on `.py` edits
- **Dependabot:** weekly dependency update PRs

---

## 5. Dependency Graph

### 5.1 Phase Dependencies (DAG)

```
                    +----------+     +----------+
                    | Phase A  |     | Phase B  |
                    | Unified  |     | BYOK     |
                    | Compose  |     | Models   |
                    +----+-----+     +----+-----+
                         |                |
                         |   +------------+
                         |   |
                    +----v---v---+
                    |  Phase C   |
                    |  Repo      |
                    |  Restructure
                    +----+-------+
                         |
              +----------+----------+
              |                     |
         +----v----+          +----v----+
         | Phase D |          | Phase 49|
         | Electron|          | Plugins |
         +---------+          +----+----+
                                   |
                              +----v----+
                              | Phase 50|
                              | Visual  |
                              | Workflow|
                              +---------+


         +----------+
         | Phase 42 |  (independent — needs only Phase 41 baseline)
         | Web Srch |
         +----------+

         +----------+
         | Phase 43 |  (independent)
         | Automate |
         +----------+

         +----------+
         | Phase 44 |  (independent)
         | Memory   |
         +----------+

         +----------+
         | Phase 45 |  (independent)
         | A2A      |
         +----------+

         +----------+
         | Phase 46 |  depends on Phase C (plugin packaging)
         | Multi-   |
         | Modal    |
         +----------+

         +----------+
         | Phase 47 |  (independent)
         | Observe  |
         +----------+

         +----------+
         | Phase 48 |  depends on Phase B (BYOK provider system)
         | Ollama   |
         +----------+
```

### 5.2 Critical Path

The critical path (longest dependency chain) is:

```
Phase A + B (Sprint 1-2)
    --> Phase C (Sprint 6-8)
        --> Phase D (Sprint 10-12) + Phase 49 (Sprint 12-13)
            --> Phase 50 (Sprint 14-15)
```

Total critical path duration: **15 sprints (30 weeks)**.

Phases 42-45, 47 are independent and run in parallel with the critical path,
providing flexibility to re-order if priorities shift.

### 5.3 Parallelization Opportunities

| Parallel Track 1 (Infrastructure) | Parallel Track 2 (Features) |
|-----------------------------------|-----------------------------|
| Sprint 1-2: Phase A + B | Sprint 3: Phase 42 |
| Sprint 6-8: Phase C | Sprint 4-5: Phase 43 + 44 |
| Sprint 10-12: Phase D | Sprint 5-6: Phase 45 |
| Sprint 12-13: Phase 49 | Sprint 9-10: Phase 47 + 48 |
| Sprint 14-15: Phase 50 | Sprint 8-9: Phase 46 |

For a solo developer, these tracks are serialized. With 2+ developers, both tracks
can proceed simultaneously, reducing total wall time by approximately 40%.

---

## 6. Risk Register

| ID | Risk | Impact | Probability | Mitigation | Owner |
|----|------|--------|-------------|------------|-------|
| R1 | Bifrost config template breaks on Bifrost version update | High | Medium | Pin Bifrost image version (SHA256 digest), test template generation in CI, maintain fallback to static config.yaml | Backend |
| R2 | Repo restructure (Phase C) breaks imports across 50+ files | High | Medium | Re-export bridges at every PR, atomic PRs (6 total), full test suite runs between each PR, rollback plan per PR | Backend |
| R3 | Electron code signing complexity delays desktop release | Medium | High | Start Apple Developer and Windows Authenticode cert process in Sprint 6 (4 sprints before needed), test signing in CI early | Desktop |
| R4 | License key system complexity spirals | Medium | Low | Start with simplest viable approach (RSA-signed JWT, offline validation, no server), iterate only if needed | Backend |
| R5 | faster-whisper model size exceeds Docker image budget | Medium | Medium | Use `tiny` or `base` model (39-74MB), lazy download on first use instead of baking into image, document VRAM requirements | Backend |
| R6 | Ollama integration performance insufficient for verification | Medium | Medium | Benchmark Ollama models for claim verification quality, set minimum model size recommendation, allow mixing (Ollama for chat, cloud for verification) | Backend |
| R7 | A2A protocol spec changes during implementation | Low | Medium | Pin to specific A2A spec version, abstract protocol layer for future updates, implement only stable subset | Backend |
| R8 | Visual workflow builder (React Flow) licensing conflict | Medium | Low | Verify React Flow license compatibility with BSL-1.1, evaluate alternatives (elkjs, dagre) if conflict found | Frontend |
| R9 | Semantic cache re-ingest required after Phase C restructure | Medium | High | Plan re-ingest window during Sprint 8, document in release notes, provide automated migration script | DevOps |
| R10 | Electron auto-update fails silently on user machines | Medium | Medium | Implement update health check (verify new version runs), rollback mechanism, telemetry on update success/failure | Desktop |
| R11 | Plugin sandboxing insufficient (malicious plugins) | High | Low | Plugins run in same process (trust model), document security implications, consider WASM isolation for v5.0 | Security |
| R12 | Web search provider (Tavily) rate limits or pricing changes | Low | Medium | Abstraction layer supports multiple providers, SearXNG self-hosted as fallback, configurable rate limits | Backend |
| R13 | Test suite execution time grows beyond CI timeout (45 min) | Medium | Medium | Parallelize test execution (pytest-xdist), split frontend/backend into separate CI jobs (already done), prune redundant tests during audit sprints | DevOps |
| R14 | Docker Compose V2 behavior differences across platforms | Low | Low | Test on macOS + Linux + Windows Docker Desktop in CI, pin Docker Compose version in docs | DevOps |

### Risk Response Triggers

| Risk ID | Trigger Condition | Escalation Action |
|---------|-------------------|-------------------|
| R1 | Bifrost releases breaking change | Freeze Bifrost version, file upstream issue |
| R2 | > 5 test failures after any Phase C PR | Revert PR, re-plan migration |
| R3 | Cert not obtained by Sprint 9 | Defer Electron signing, release unsigned beta |
| R5 | Docker image > 6 GB after multi-modal | Lazy model download, separate model container |
| R13 | CI > 35 min consistently | Emergency test split/parallelization |

---

## 7. Definition of Done

### 7.1 Phase-Level Definition of Done

Every phase is complete when ALL of the following are satisfied:

| # | Criterion | Verification Method |
|---|-----------|-------------------|
| 1 | All acceptance criteria from sprint planning met | Checklist review |
| 2 | CI green (all 7 jobs: lint, type-check, test, security, coverage, docker, bundle) | GitHub Actions |
| 3 | Test coverage >= 70% on new code | Codecov report |
| 4 | `CLAUDE.md` updated to reflect changes | Manual review |
| 5 | `docs/ISSUES.md` updated (new issues added, resolved issues closed) | Manual review |
| 6 | `docs/COMPLETED_PHASES.md` updated with phase summary | Manual review |
| 7 | No critical or high security vulnerabilities | pip-audit + npm audit + CodeQL |
| 8 | Code review passed (self-review checklist for solo dev) | Checklist |
| 9 | No `TODO` or `FIXME` comments introduced without tracking in ISSUES.md | Grep scan |
| 10 | Docker build succeeds and all services start cleanly | `docker compose up --build` |

### 7.2 Self-Review Checklist (Solo Developer)

```
Code Quality:
[ ] No generic/AI-slop comments
[ ] Meaningful variable and function names
[ ] No unnecessary abstractions
[ ] Error handling is specific (no bare `except Exception`)
[ ] Logging uses structlog with context

Architecture:
[ ] Changes follow existing patterns in codebase
[ ] No circular imports introduced
[ ] New files in correct directory per project structure
[ ] Public APIs have type annotations and docstrings

Testing:
[ ] New code has unit tests
[ ] Edge cases covered (empty input, error states, boundary values)
[ ] Tests are independent (no shared mutable state)
[ ] Test names describe behavior, not implementation

Security:
[ ] No secrets in code or comments
[ ] User input validated and sanitized
[ ] Auth/rate-limiting applied to new endpoints
[ ] CORS headers reviewed for new routes

Performance:
[ ] No N+1 queries introduced
[ ] Async operations use asyncio properly (no blocking event loop)
[ ] Large operations have timeouts
[ ] No unnecessary data loading (lazy where possible)
```

### 7.3 Release Definition of Done (v2.0.0, v3.0.0, v4.0.0)

Major releases additionally require:

| # | Criterion | Verification Method |
|---|-----------|-------------------|
| 1 | All phase-level DoD criteria met | Aggregate check |
| 2 | Integration test suite passes | CI + manual |
| 3 | Migration guide written for breaking changes | Documentation review |
| 4 | CHANGELOG.md updated with all changes since last release | Manual review |
| 5 | Performance benchmark published (latency, quality, memory) | Benchmark script |
| 6 | Marketing site updated (features, screenshots, pricing) | Visual review |
| 7 | GitHub Release created with binaries (if applicable) | GitHub |

---

## 8. Release Strategy

### 8.1 Versioning Scheme

Semantic versioning: `MAJOR.MINOR.PATCH`

- **MAJOR:** Breaking changes (deployment model, repository structure, API contracts)
- **MINOR:** New features (additive, backward-compatible)
- **PATCH:** Bug fixes, security patches, performance improvements

### 8.2 Release Schedule

| Version | Phases Included | Breaking Changes | Sprint |
|---------|----------------|-----------------|--------|
| **v2.0.0** | A + B | New deployment model (single compose), BYOK config format | Sprint 2 |
| **v2.1.0** | 42 | None (additive: web search) | Sprint 3 |
| **v2.2.0** | 43 + 44 | None (additive: automations, memory) | Sprint 5 |
| **v2.3.0** | 45 | None (additive: A2A protocol) | Sprint 6 |
| **v3.0.0** | C | Repository restructure, new import paths, license separation | Sprint 8 |
| **v3.1.0** | 46 | None (additive: multi-modal plugin) | Sprint 9 |
| **v3.2.0** | 47 + 48 | None (additive: observability, Ollama) | Sprint 10 |
| **v3.3.0** | D | None (additive: Electron desktop app) | Sprint 12 |
| **v3.4.0** | 49 | None (additive: plugin system) | Sprint 13 |
| **v3.5.0** | 50 | None (additive: visual workflow) | Sprint 15 |
| **v4.0.0** | Final audit | None (quality + polish) | Sprint 16 |

### 8.3 Migration Guides Required

| Version | Migration Required | Scope |
|---------|--------------------|-------|
| v2.0.0 | **Docker Compose Migration** | Users must switch from 4 compose files to 1. Automated migration script provided. Existing `.env` files compatible. |
| v2.0.0 | **BYOK Setup** | Users with hardcoded `config.yaml` prompted to migrate to BYOK template system. Fallback to existing config if not migrated. |
| v3.0.0 | **Import Path Migration** | Developers using `src/mcp/` imports must update to `core/src/` or `app/api/`. Find-and-replace guide provided. SDK users unaffected (API endpoints unchanged). |
| v3.0.0 | **Plugin Migration** | Multi-modal features move from built-in to plugin. Users must enable plugin and (for paid features) enter license key. |

### 8.4 Release Process

```
1. Feature freeze (2 days before release)
2. Release branch: git checkout -b release/vX.Y.Z
3. Version bump: update version in pyproject.toml, package.json, CLAUDE.md
4. CI pipeline: full suite on release branch
5. Manual smoke test: clean install, core workflow, upgrade from previous version
6. Changelog: finalize CHANGELOG.md entries
7. Tag: git tag vX.Y.Z
8. GitHub Release: create with changelog, binaries (if applicable)
9. Marketing: update cerid.ai if warranted
10. Post-release: merge release branch back to main
```

### 8.5 Hotfix Process

For critical issues discovered post-release:

```
1. Branch from release tag: git checkout -b hotfix/vX.Y.Z+1 vX.Y.Z
2. Fix issue with test
3. Bump patch version
4. CI pipeline: full suite
5. Tag and release
6. Cherry-pick fix into main
```

---

## Appendix A: Sprint Calendar

| Sprint | Dates | Phases | Deliverable |
|--------|-------|--------|-------------|
| 1 | 2026-03-24 — 2026-04-04 | A (Part 1), B (Part 1) | -- |
| 2 | 2026-04-07 — 2026-04-18 | A (Part 2), B (Part 2) | **v2.0.0** |
| 3 | 2026-04-21 — 2026-05-02 | 42 | **v2.1.0** |
| 4 | 2026-05-05 — 2026-05-16 | 43, 44 (Part 1) | -- |
| 5 | 2026-05-19 — 2026-05-30 | 44 (Part 2), 45 (Part 1) | **v2.2.0** |
| 6 | 2026-06-02 — 2026-06-13 | 45 (Part 2), C (Part 1) | **v2.3.0** |
| 7 | 2026-06-16 — 2026-06-27 | Quality Audit 1 | -- |
| 8 | 2026-06-30 — 2026-07-11 | C (Part 2-3), 46 (Part 1) | **v3.0.0** |
| 9 | 2026-07-14 — 2026-07-25 | 46 (Part 2), 47 (Part 1) | **v3.1.0** |
| 10 | 2026-07-28 — 2026-08-08 | 47 (Part 2), 48, D (Part 1) | **v3.2.0** |
| 11 | 2026-08-11 — 2026-08-22 | Quality Audit 2 | -- |
| 12 | 2026-08-25 — 2026-09-05 | D (Part 2-3), 49 (Part 1) | **v3.3.0** |
| 13 | 2026-09-08 — 2026-09-19 | 49 (Part 2) | **v3.4.0** |
| 14 | 2026-09-22 — 2026-10-03 | 50 (Part 1) | -- |
| 15 | 2026-10-06 — 2026-10-17 | 50 (Part 2) | **v3.5.0** |
| 16 | 2026-10-20 — 2026-10-31 | Final Quality Audit | **v4.0.0** |

**Total duration:** 32 weeks (16 sprints)
**Major releases:** 3 (v2.0.0, v3.0.0, v4.0.0)
**Minor releases:** 7 (v2.1.0 through v3.5.0)
**Quality audit sprints:** 3 (Sprints 7, 11, 16)

---

## Appendix B: Metrics & Success Criteria

### Project Health Metrics (tracked per sprint)

| Metric | Target | Measurement |
|--------|--------|-------------|
| Test count (Python) | Monotonically increasing | `pytest --co -q \| wc -l` |
| Test count (Frontend) | Monotonically increasing | `vitest --reporter=json` |
| Test coverage | >= 70% | Codecov |
| CI pass rate | >= 95% | GitHub Actions history |
| Open issues (critical) | 0 | `docs/ISSUES.md` |
| Open issues (total) | <= 10 | `docs/ISSUES.md` |
| Cold query latency (P95) | < 2.0s | Retrieval eval harness |
| NDCG@5 | >= 0.75 | Retrieval eval harness |
| Docker image size (MCP) | < 4 GB | `docker images` |

### Phase Success Criteria

| Phase | Primary Success Metric |
|-------|----------------------|
| A | Time from `git clone` to first query < 10 minutes |
| B | Users can switch LLM provider without editing YAML |
| 42 | Ignorance claims resolved with cited web sources |
| 43 | Automations execute on schedule with full pipeline |
| 44 | Conflicting memories detected and resolved correctly |
| 45 | External A2A agent can query Cerid KB successfully |
| C | All tests pass on restructured codebase, clean licensing |
| 46 | Audio and image content searchable alongside text |
| 47 | Real-time latency/cost/quality dashboard operational |
| 48 | Full query-verify cycle works with Ollama (zero cloud) |
| D | Electron app installs and manages Docker on fresh machine |
| 49 | Third-party plugin loads and functions correctly |
| 50 | Custom pipeline DAG executes and produces correct results |

---

*End of document.*
