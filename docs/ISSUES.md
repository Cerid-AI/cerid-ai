# Cerid AI — Issues & Backlog

> **Created:** 2026-02-25
> **Last updated:** 2026-02-28
> **Status:** Phase 12 complete. 42 resolved, 1 research (E1), 1 informational (F6).
> **Purpose:** Track known bugs, feature gaps, structural issues, and architecture evaluations for upcoming phases.

---

## A. UI Layout Bugs

### A1. Chat Input & Metrics Dashboard Viewport Overflow

**Severity:** Medium
**Status:** Resolved (Phase 10A, 2026-02-26)

**Resolution:** Added `min-h-0` at every flex container level in the chat layout chain (`split-pane.tsx` fallback div + Panel, `chat-panel.tsx` chatArea div + ScrollArea). Pure CSS fix — no JS changes. Verified at mobile viewport (375x812): textarea bottom at 764px, fully visible with 48px to spare.

**Files changed:** `split-pane.tsx` (2 edits), `chat-panel.tsx` (2 edits)

---

## B. Missing GUI Features

### B1. No Interactive Audit Agent in GUI

**Severity:** Medium
**Status:** ✅ Resolved (Phase 11A, 2026-02-28)

The audit pane (`audit-pane.tsx`) auto-fetches reports every 60 seconds via `useQuery`, but there's no way for users to manually trigger the audit agent, choose which reports to run, change the time window, or force a refresh.

**What exists:** Backend `POST /agent/audit` supports configurable `reports` array and `hours` parameter. Frontend `fetchAudit()` in `api.ts` maps to it. The pane just hardcodes `["activity", "ingestion", "costs", "queries", "conversations"]` and `hours: 24`.

**Suggested fix:** Add report filter toggles (checkboxes), time range selector, manual "Run Audit" button, and force-refresh. Consider adding the rectify and maintain agents as interactive controls too.

**Files:**
- `src/web/src/components/audit/audit-pane.tsx` (lines 24–92)
- `src/web/src/lib/api.ts` (fetchAudit, lines 104–115)
- `src/mcp/agents/audit.py` (backend agent)

### B2. Source Attribution Missing in Chat

**Severity:** High
**Status:** Resolved (Phase 10A, 2026-02-26)

**Resolution:** Added `SourceRef` type to `types.ts` (lightweight subset of `KBQueryResult`), `sourcesUsed?: SourceRef[]` field on `ChatMessage`. At send time, injected KB context is captured as `SourceRef[]` before `clearInjected()`, threaded through `useChat.send()`, and attached to the assistant message. New `SourceAttribution` component renders a collapsible list below each response (Radix Collapsible, domain badges, relevance percentages). Persists in localStorage via existing conversation serialization.

**Files changed:** `types.ts`, `use-chat.ts`, `chat-panel.tsx`, `source-attribution.tsx` (new), `message-bubble.tsx`

### B3. No Model Context Break Indicator

**Severity:** Medium
**Status:** Resolved (Phase 10B, 2026-02-26)

**Resolution:** Model switch dividers computed at render time — when consecutive assistant messages use different models, a "Switched from [Model A] to [Model B]" divider appears between them. Per-message model badges are now always visible with provider-colored pills (amber for Anthropic, emerald for OpenAI, blue for Google, etc.). Added `PROVIDER_COLORS` map, `findModel()` helper, `ModelBadge` component, and `ModelSwitchDivider` component. No storage format changes — dividers are derived from existing `message.model` field.

**Remaining:** "Start fresh context" option on model switch (deferred to Phase 10C with token cost awareness).

**Files changed:** `types.ts`, `message-bubble.tsx`, `model-switch-divider.tsx` (new), `chat-panel.tsx`

---

## C. Knowledge & Taxonomy Improvements

### C1. Knowledge Context & Tagging Needs Taxonomy Update

**Severity:** Medium
**Status:** ✅ Resolved (Phase 11B, 2026-02-28)

The GUI has basic tag pills and sub-category badges (added in Phase 9C) but doesn't fully leverage the hierarchical taxonomy system (TAXONOMY dict in `config.py` with domains, sub-categories, and tags). Missing:
- Taxonomy-aware hierarchical browsing (domain > sub-category > tags)
- Tag management UI (add/remove/rename tags on artifacts)
- Bulk re-tagging across artifacts
- Taxonomy tree visualization
- Filter by taxonomy path (e.g., `coding/python/fastapi`)

**What exists:** `GET /taxonomy` endpoint returns the full taxonomy tree. Artifact cards show sub-category badge and tag pills. Client-side tag filtering extracts tags from loaded artifacts.

**Suggested fix:** Build a taxonomy sidebar/tree component. Add tag CRUD operations. Wire the taxonomy API into the Knowledge pane's filter system. Support hierarchical drill-down.

**Files:**
- `src/web/src/components/kb/knowledge-pane.tsx` (tag filter strip)
- `src/web/src/components/kb/artifact-card.tsx` (sub-category badge, tag pills)
- `src/mcp/config.py` (TAXONOMY dict, lines 20–51)
- `src/mcp/routers/taxonomy.py` (taxonomy API)

### C2. Knowledge Curation Agent Needed

**Severity:** High
**Status:** ✅ Design complete (Phase 11C, 2026-02-28) — implementation deferred to post-Phase 12

No agent exists for improving artifact quality. Current artifact cards show raw metadata (auto-extracted summaries, AI-generated keywords). Missing:
- Content optimization (improve summaries, refine keywords, fix OCR artifacts)
- Relevance scoring tuning (adjust scores based on user feedback)
- Recommendation system (suggest related artifacts, "you might also need")
- Artifact quality grades (completeness, freshness, relevance scores)
- Integration with the scoring/recommendation pipeline in query_agent.py

**Suggested approach:** Create a new `agents/curator.py` that:
1. Scores artifact quality (summary length, keyword relevance, freshness)
2. Suggests improvements (re-summarize, re-chunk, re-categorize)
3. Feeds into the query agent's reranking pipeline
4. Exposes via GUI for manual curation + scheduled auto-improvement

**Files:**
- `src/mcp/agents/` (new curator agent)
- `src/mcp/agents/query_agent.py` (reranking integration)
- `src/web/src/components/kb/artifact-card.tsx` (quality indicators)

---

## D. Smart Routing & Model Management

### D1. Smart Routing Needs Context/Token Cost Evaluation

**Severity:** High
**Status:** Resolved (Phase 10E, 2026-02-28)

**Resolution:** Added `calculateSwitchCost()` and `buildSwitchOptions()` to `model-router.ts` for context replay cost estimation. Color-coded context usage gauge (green/yellow/red) in chat dashboard. `summarizeConversation()` API for compressing history before model switch. `useModelSwitch` hook orchestrates 3 strategies: continue (full replay), summarize (compress then switch), and fresh (clear history). Model switch dialog shows per-strategy cost estimates, a "Recommended" badge, and context overflow warnings. 26 new frontend tests covering cost calculation and dialog component.

**Files changed:** `model-router.ts`, `api.ts`, `chat-dashboard.tsx`, `use-model-switch.ts` (new), `model-switch-dialog.tsx` (new), `chat-panel.tsx`, `use-conversations.ts`, `types.ts`

### D2. Chat Model Switch UX

**Severity:** Medium
**Status:** Mostly Resolved (Phase 10E, 2026-02-28)

**Resolved items:**
- ✅ Per-message model badge with provider colors (always visible) — Phase 10B
- ✅ "Switched from X to Y" divider between model switches — Phase 10B
- ✅ Context summary on switch (summarize-and-switch strategy) — Phase 10E
- ✅ "Start fresh" option (clear history and switch) — Phase 10E

**Remaining (deferred):**
- [ ] Conversation fork/branch UI (exploratory — Phase 13)

**Files:** Same as B3 + D1.

---

## E. Architecture Evaluations

### E1. Artifact Preview/Generation & Interactive Editing

**Severity:** Low (exploratory)
**Status:** Open — needs research

Evaluate options for in-GUI artifact handling:
- **Preview:** PDF rendering (pdf.js), code syntax highlighting (already have markdown), spreadsheet/table preview, email rendering
- **Generation:** Save chat responses as artifacts ("Save to KB" button), generate artifacts from templates
- **Interactive editing:** Edit artifact metadata (title, domain, tags) in-place, edit content with re-chunking, annotation/highlighting on artifacts
- **Version history:** Track changes to artifacts over time

**Dependencies:** Requires decisions on E2 (how artifacts are stored/vectorized) before implementation.

### E2. RAG Integration & Vectorization Strategy

**Severity:** High (foundational)
**Status:** ✅ Resolved (Phase 12, 2026-02-28)

**Resolution:**
- **BM25 replaced:** `rank_bm25` → `bm25s` + PyStemmer (stemming, stopwords, 500x faster)
- **Hybrid weights configurable:** `HYBRID_VECTOR_WEIGHT`, `HYBRID_KEYWORD_WEIGHT`, `RERANK_LLM_WEIGHT`, `RERANK_ORIGINAL_WEIGHT` via env vars
- **Embedding evaluation:** Documented in `docs/EMBEDDING_EVALUATION.md`. Current model (all-MiniLM-L6-v2) adequate; `EMBEDDING_MODEL` config scaffold for future swap
- **Eval harness:** `eval/` package with NDCG, MRR, Precision@K, Recall@K, Average Precision metrics (31 tests)

---

## F. Structural / Modularity

Issues identified by the modularity assessment (2026-02-26). These are mechanical refactors that don't change behavior but reduce file sizes, fix layering violations, and unblock test coverage.

### F1. Service Layer Extraction — `routers/ingestion.py`

**Severity:** High (causes circular import)
**Status:** Resolved (Phase 10C-S1, 2026-02-26)

**Resolution:** Extracted `ingest_content()`, `ingest_file()`, `validate_file_path()`, and all private helpers (`_content_hash`, `_check_duplicate`, `_reingest_artifact`) into `services/ingestion.py`. `routers/ingestion.py` is now a thin router (Pydantic models + endpoint handlers). Updated 4 importers: `agents/memory.py`, `routers/mcp_sse.py`, `routers/upload.py`, `routers/agents.py`. Circular import eliminated.

**Files changed:** `routers/ingestion.py`, `services/ingestion.py` (new), `services/__init__.py` (new), `agents/memory.py`, `routers/mcp_sse.py`, `routers/upload.py`, `routers/agents.py`

### F2. MCP Tool Registry Extraction — `routers/mcp_sse.py`

**Severity:** Medium
**Status:** Resolved (Phase 10C-S2, 2026-02-26)

**Resolution:** Extracted `MCP_TOOLS` list (17 tool schemas) and `execute_tool()` dispatcher into `tools.py`. `routers/mcp_sse.py` reduced from 593 to ~170 lines — now a thin SSE transport + JSON-RPC framing layer.

**Files changed:** `tools.py` (new), `routers/mcp_sse.py`

### F3. Neo4j Data Layer — `utils/graph.py`

**Severity:** Medium
**Status:** Resolved (Phase 10C-S3, 2026-02-27)

**Resolution:** Split `utils/graph.py` (827 lines, 18 functions) into `db/neo4j/` package with 4 sub-modules: `schema.py` (init_schema), `artifacts.py` (6 CRUD functions), `relationships.py` (5 functions incl. discovery), `taxonomy.py` (5 functions). Re-export shim in `utils/graph.py` preserves all 7 importers unchanged. All 156 tests pass.

**Files:**
- `src/mcp/db/neo4j/` (new package — 4 modules + `__init__.py`)
- `src/mcp/utils/graph.py` (now re-export shim)

### F4. Sync Library — `cerid_sync_lib.py`

**Severity:** Medium
**Status:** Resolved (Phase 10C-S3, 2026-02-27)

**Resolution:** Split `cerid_sync_lib.py` (1346 lines) into `sync/` package with 5 sub-modules: `export.py` (5 export functions), `import_.py` (5 import functions + 3 ChromaDB helpers), `manifest.py` (write/read), `status.py` (compare_status), `_helpers.py` (constants + 6 utility functions). Fixed 3 latent `collection_name` → `coll_name` bugs in error logging paths. Replaced duplicated `_utcnow_iso()` with canonical `utils.time.utcnow_iso()`. Re-export shim in `cerid_sync_lib.py` preserves all 3 importers unchanged. All 156 tests pass.

**Files:**
- `src/mcp/sync/` (new package — 5 modules + `__init__.py`)
- `src/mcp/cerid_sync_lib.py` (now re-export shim)

### F5. Test Coverage Gaps

**Severity:** High (quality risk)
**Status:** Resolved (Phase 10D, 2026-02-28)

**Resolution:** 564 backend tests across 27 test files achieving 75% code coverage. All previously-untested modules now covered:
- Middleware: auth (21 tests), rate_limit (19 tests), request_id (9 tests)
- Services: ingestion (15 tests)
- All 5 agents: query_agent (27), triage (23), rectify (19), audit (27), maintenance (24)
- Sync package (41 tests), parsers package (108 tests), MCP tools (24 tests), Neo4j data layer (63 tests)

Frontend: 94 vitest tests across 7 test files. ~40 frontend components remain untested (nice-to-have, not gating any release — tracked in Phase 13).

**Current coverage:** 564 pytest functions (27 test files), 94 vitest tests (7 test files).

### F6. Secondary Structural Issues (Informational)

**Severity:** Low
**Status:** Partially resolved — 4 of 6 addressed in Phase 10C-S2

Resolved items:
- ✅ **`config.py` split:** Split into `config/taxonomy.py`, `config/settings.py`, `config/features.py` with `config/__init__.py` star re-exports (33 importers unchanged).
- ✅ **Duplicate `find_stale_artifacts`:** Enhanced `rectify.py` version (added `limit` param, `chunk_ids` in return). Removed duplicate from `maintenance.py`, which now imports from `rectify`.
- ✅ **`audit.log_conversation_metrics()`:** Moved to `utils/cache.py`. Import in `routers/ingestion.py` updated to canonical location.
- ✅ **`utils/parsers.py`:** Split into `parsers/` package (registry, pdf, office, structured, email, ebook, _utils). Phase 10C-S3, 2026-02-27.

Open items:
- **`use-chat.ts` post-send effects:** Not urgent at 83 lines total.
- **`cerid-web` in `src/mcp/docker-compose.yml`:** Conceptual coupling. Consider moving to its own compose file.

---

## G. Audit Hardening

Items identified by the full-stack audit (2026-02-26). Items G1–G7 resolved immediately; G8–G16 integrated into upcoming phases.

### G1. `cryptography` Not Declared in requirements.txt

**Severity:** Critical
**Status:** Resolved (Step 0, 2026-02-26)

**Resolution:** Added `cryptography>=42,<47` to `requirements.txt`. The package was imported at runtime by `utils/encryption.py` but only present as a transitive dependency — silent degradation if the dep chain changed.

### G2. Trivy Exit-Code Advisory Only

**Severity:** Critical
**Status:** Resolved (Step 0, 2026-02-26)

**Resolution:** Changed `exit-code: 0` to `exit-code: 1` in both Trivy steps in `.github/workflows/ci.yml`. Docker image scanning now fails CI on CRITICAL/HIGH vulnerabilities.

### G3. FastAPI Range Too Restrictive

**Severity:** Critical
**Status:** Resolved (Step 0, 2026-02-26)

**Resolution:** Broadened from `>=0.100,<0.120` to `>=0.100,<0.125` in `requirements.txt`. Allows Dependabot to pick up security patches.

### G4. httpx-sse Unpinned

**Severity:** High
**Status:** Resolved (Step 0, 2026-02-26)

**Resolution:** Pinned to `httpx-sse>=0.3,<0.5` in `requirements.txt`. Core MCP SSE transport dependency was previously unconstrained.

### G5. npm Audit Non-Blocking in CI

**Severity:** High
**Status:** Resolved (Step 0, 2026-02-26)

**Resolution:** Removed `|| true` from npm audit step in CI. Changed audit level to `--audit-level=high` to balance signal-to-noise.

### G6. pandas Range Too Broad

**Severity:** Medium
**Status:** Resolved (Step 0, 2026-02-26)

**Resolution:** Narrowed from `>=2.0,<3` to `>=2.0,<2.3` in `requirements.txt`.

### G7. Lock File Regenerated

**Severity:** N/A
**Status:** Resolved (Step 0, 2026-02-26)

**Resolution:** Regenerated `requirements.lock` with all G1–G6 changes.

### G8. Rate Limiter Lacks X-Forwarded-For Support

**Severity:** High
**Status:** Resolved (Phase 10C-S1, 2026-02-26)

**Resolution:** Added `TRUSTED_PROXIES` env var (comma-separated CIDRs). `get_client_ip()` walks X-Forwarded-For right-to-left, returning first untrusted IP. Secure by default: no trusted proxies configured means direct peer IP used.

**Files changed:** `src/mcp/middleware/rate_limit.py`

### G9. No Rate Limit Response Headers

**Severity:** High
**Status:** Resolved (Phase 10C-S1, 2026-02-26)

**Resolution:** Added `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset` headers on all rate-limited paths. 429 responses also include `Retry-After` header.

**Files changed:** `src/mcp/middleware/rate_limit.py`

### G10. Client IP in Auth Failure Logs

**Severity:** Medium
**Status:** Resolved (Phase 10C-S1, 2026-02-26)

**Resolution:** Auth failure logs now show SHA-256 hash prefix (12 chars) via `_redact_ip()` instead of raw IP.

**Files changed:** `src/mcp/middleware/auth.py`

### G11. No Request ID Tracing

**Severity:** Low
**Status:** Resolved (Phase 10C-S1, 2026-02-26)

**Resolution:** New `RequestIDMiddleware` generates UUID per request or propagates incoming `X-Request-ID`. Stored in `request.state.request_id` and returned in response header. Added as outermost middleware (runs first).

**Files changed:** `src/mcp/middleware/request_id.py` (new), `src/mcp/main.py`

### G12. pip-audit Misses Transitive Dependencies

**Severity:** High
**Status:** ✅ Resolved — Phase 10D

CI's `pip-audit` now scans the full installed environment including transitive deps (added `--desc` flag). Fixed in `.github/workflows/ci.yml`.

### G13. No CodeQL SAST Workflow

**Severity:** Medium
**Status:** ✅ Resolved — Phase 10D

Added `.github/workflows/codeql.yml` — CodeQL SAST for Python + JavaScript on push to main, pull requests, and weekly schedule.

### G14. Coverage Threshold Only 35%

**Severity:** Medium
**Status:** ✅ Resolved — Phase 10D

Raised `--cov-fail-under` from 35% to 55%. Actual coverage is 75% after 400+ new tests across all backend modules.

### G15. No Bundle Size Monitoring

**Severity:** Medium
**Status:** ✅ Resolved — Phase 10D

Added bundle size check step in frontend CI job — fails if any JS chunk exceeds 800KB after vite build.

### G16. rank_bm25 Unmaintained

**Severity:** Medium
**Status:** ✅ Resolved (Phase 12A, 2026-02-28)

**Resolution:** Replaced `rank_bm25` with `bm25s>=0.3` + `PyStemmer>=2.2`. New implementation includes English stemming, stopword removal, and backward-compatible JSONL corpus migration. Old token-format corpus files are auto-migrated on load.

**Files changed:** `src/mcp/utils/bm25.py` (rewritten), `src/mcp/requirements.txt`, `src/mcp/tests/test_bm25.py` (12 tests)

### G17–G22. Documentation Gaps

**Severity:** Low
**Status:** ✅ Resolved — Phase 11D (2026-02-28)

**Resolution:** Created `docs/OPERATIONS.md` covering API key rotation (G17), secrets rotation policy (G18), rate limiter limitations (G22), and branch protection rules (G21). Updated `docs/DEPENDENCY_COUPLING.md` with pip-compile version (G19) and Bifrost version (G20). Added OPERATIONS.md link to README.

**Files changed:** `docs/OPERATIONS.md` (new), `docs/DEPENDENCY_COUPLING.md`, `README.md`

---

## Priority Order (Suggested)

Structural work before feature work — the splits reduce cost of all subsequent changes and unblock test coverage. Audit hardening items integrated by severity into existing phases.

1. ~~**A1** — Chat viewport fix~~ ✅ Resolved (Phase 10A)
2. ~~**B2** — Source attribution~~ ✅ Resolved (Phase 10A)
3. ~~**B3 + D2** — Model context break~~ ✅ Resolved (Phase 10B) — "start fresh" deferred to 10E
4. ~~**G1–G7** — Immediate audit fixes~~ ✅ Resolved (Step 0)
5. ~~**F1** — Service layer extraction~~ ✅ Resolved (Phase 10C-S1)
6. ~~**G8–G11** — Middleware hardening~~ ✅ Resolved (Phase 10C-S1)
7. ~~**F2** — MCP tool registry extraction~~ ✅ Resolved (Phase 10C-S2)
8. ~~**F6 partial** — Config split, dedup cleanup~~ ✅ Resolved (Phase 10C-S2)
9. ~~**F3** — Neo4j data layer split~~ ✅ Resolved (Phase 10C-S3)
10. ~~**F4** — Sync library split~~ ✅ Resolved (Phase 10C-S3)
11. ~~**F5** — Test coverage expansion~~ ✅ Resolved (Phase 10D)
12. ~~**G12–G15** — CI hardening~~ ✅ Resolved (Phase 10D)
13. ~~**D1** — Smart routing + token cost evaluation~~ ✅ Resolved (Phase 10E)
14. **B1** — Audit agent interactivity — Phase 11
15. **C1** — Taxonomy update — Phase 11
16. ~~**G17–G22** — Operations documentation~~ ✅ Resolved (Phase 11D)
17. **C2** — Curation agent (requires C1) — Phase 11 (design)
18. **E2** — RAG evaluation + **G16** (BM25 replacement) — Phase 12 (research)
19. **E1** — Artifact preview (depends on E2 decisions) — Phase 13
