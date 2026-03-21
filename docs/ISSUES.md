# Cerid AI — Issues & Backlog

> **Created:** 2026-02-25
> **Last updated:** 2026-03-21
> **Status:** All phases through 50 complete. 120+ resolved, 1 open. 1376+ Python tests, 545+ frontend tests.
> **Development plan:** [docs/plans/DEVELOPMENT_PLAN_PHASE16-18.md](plans/DEVELOPMENT_PLAN_PHASE16-18.md) (Phases 17-21 roadmap)
> **Completed phases:** [docs/COMPLETED_PHASES.md](COMPLETED_PHASES.md)
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
**Status:** ✅ Resolved (Phase 10E + dropped remaining, 2026-03-08)

**Resolved items:**
- ✅ Per-message model badge with provider colors (always visible) — Phase 10B
- ✅ "Switched from X to Y" divider between model switches — Phase 10B
- ✅ Context summary on switch (summarize-and-switch strategy) — Phase 10E
- ✅ "Start fresh" option (clear history and switch) — Phase 10E

**Remaining:**
- ~~Conversation fork/branch UI~~ — **Dropped** (2026-03-08). Exploratory 40-60 hr effort with unclear ROI. All core model-switch UX (badges, dividers, summarize-and-switch, start fresh) already shipped.

**Files:** Same as B3 + D1.

### D3. Model Router Auto Mode Broken in Settings Pane

**Severity:** Medium
**Status:** ✅ Resolved (2026-03-13)

**Problem:** Settings Pane dropdown derived its value from `settings.enable_model_router` (a boolean), so `true` always mapped to "Recommend" — "Auto" could never be displayed. Selecting "Auto" wrote to localStorage but the Select snapped back to "Recommend" on re-render.

**Resolution:** Settings Pane now uses `useSettings()` hook's `routingMode` / `setRoutingMode` (backed by localStorage with server boolean sync) instead of deriving from the binary server field. This matches how the toolbar cycle already worked. Frontend-only fix, 3 lines changed. 1 new test added.

**Files changed:** `settings-pane.tsx` (import + hook call + Select value/handler), `use-settings.test.ts` (new test)

### D4. Data Verification Temporal Claims Marked Uncertain

**Severity:** Medium
**Status:** ✅ Resolved (2026-03-13)

**Problem:** When asking about current/temporal events (e.g., "Who is the current PM of Canada?"), the chat model router scored temporal queries too low for web-search-capable models. The verification pipeline's `_is_current_event_claim()` handled temporal detection correctly, but the chat routing sent queries to models without real-time data access, causing verification to flag responses as uncertain.

**Resolution:** Strengthened temporal query detection in the chat model router with expanded keyword patterns and increased web search routing bonus from 0.15 to 0.25. Added stale-cutoff filtering to exclude models with outdated training data from temporal query routing. 4 new frontend tests.

**Files changed:** `model-router.ts`, `model-router.test.ts`

### D5. Auto Model Router Doesn't Re-Route for Real-Time Data Queries

**Severity:** Medium
**Status:** ✅ Resolved (2026-03-13)

**Problem:** The auto model router failed to route real-time data queries (news, stock prices, current events) to web-search-capable models. The web search bonus (0.15) was too small to overcome base model scoring, and models with stale training cutoffs were still eligible for temporal queries.

**Resolution:** Increased web search routing bonus to 0.25 and added stale-cutoff model filtering for temporal queries. Models whose training data cutoff predates the query's temporal scope are now excluded from scoring. Same fix as D4 — both symptoms had the same root cause.

**Files changed:** `model-router.ts`, `model-router.test.ts`

### D6. Llama Model Failed With Error, No Retry

**Severity:** Medium
**Status:** ✅ Resolved (2026-03-13)

**Problem:** When the auto-routed model (e.g., Llama) failed with a streaming error, the chat proxy returned the error directly with no fallback. Users had to manually switch models. The frontend had no mechanism to detect or display model fallback metadata.

**Resolution:** Added model fallback retry logic in the chat proxy (`chat.py`) — on stream error, retries with a fallback model and includes fallback metadata in the SSE stream. Frontend updated to parse `model_fallback` metadata from the stream and display it. 2 new frontend tests.

**Files changed:** `chat.py`, `types.ts`, `api.ts`, `api.test.ts`

---

## E. Architecture Evaluations

### E1. Artifact Preview

**Severity:** Low (exploratory)
**Status:** ✅ Resolved (Phase 16G, 2026-03-02)

**Resolution:** Added artifact content preview dialog to the Knowledge pane. Backend `GET /artifacts/{artifact_id}` endpoint fetches Neo4j metadata + reassembled ChromaDB chunks (sorted by index). Frontend: `ArtifactPreview` dialog (lazy-loaded) with conditional rendering — code files use PrismLight syntax highlighting, markdown/table/text use formatted `<pre>` blocks. Eye icon button on every artifact card. File type detection utilities (`getFileRenderMode`, `getLanguageFromFilename`). shadcn/ui Dialog primitive. 6 backend tests, 19 new frontend tests (130 total).

**Files changed:** `routers/artifacts.py`, `types.ts`, `api.ts`, `utils.ts`, `artifact-preview.tsx` (new), `artifact-card.tsx`, `knowledge-pane.tsx`, `dialog.tsx` (new, shadcn), `test_artifact_detail.py` (new), `utils.test.ts`

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

Frontend: 130+ vitest tests across 9+ test files. ~40 frontend components remain untested (nice-to-have, not gating any release — tracked in Phase 13).

**Current coverage:** 564 pytest functions (27 test files), 130+ vitest tests (9+ test files).

### F6. Secondary Structural Issues (Informational)

**Severity:** Low
**Status:** ✅ Resolved (Phase 16G-H, 2026-03-02)

Resolved items:
- ✅ **`config.py` split:** Split into `config/taxonomy.py`, `config/settings.py`, `config/features.py` with `config/__init__.py` star re-exports (33 importers unchanged).
- ✅ **Duplicate `find_stale_artifacts`:** Enhanced `rectify.py` version (added `limit` param, `chunk_ids` in return). Removed duplicate from `maintenance.py`, which now imports from `rectify`.
- ✅ **`audit.log_conversation_metrics()`:** Moved to `utils/cache.py`. Import in `routers/ingestion.py` updated to canonical location.
- ✅ **`utils/parsers.py`:** Split into `parsers/` package (registry, pdf, office, structured, email, ebook, _utils). Phase 10C-S3, 2026-02-27.

Remaining (non-blocking):
- **`use-chat.ts` post-send effects:** Not urgent at 83 lines total.
- ✅ **`cerid-web` compose separation:** Moved to `src/web/docker-compose.yml`. Startup script updated to 5-step (Phase 16G-H, 2026-03-02).

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

## H. Response Verification & KB Context Issues

### H1. Verification Returns "No Factual Claims" for Verifiable Responses

**Severity:** High
**Status:** Resolved (2026-03-01)

**Resolution:** Complete rewrite of `agents/hallucination.py` with three-pronged fix:

1. **Heuristic fallback extractor:** New `_extract_claims_heuristic()` function uses regex patterns to identify factual sentences when LLM extraction fails. Detects years, percentages, numbers, state verbs, release verbs, and version references. Requires >=2 factual pattern matches per sentence. Filters out greetings, questions, and code blocks.

2. **Configurable thresholds:** `MIN_RESPONSE_LENGTH` lowered from 100 to 50 chars (configurable via `HALLUCINATION_MIN_RESPONSE_LENGTH` env var). All hallucination constants now configurable via env vars in `config/settings.py`: `HALLUCINATION_THRESHOLD`, `HALLUCINATION_UNVERIFIED_THRESHOLD`, `HALLUCINATION_MAX_CLAIMS`.

3. **Failure propagation to frontend:** `extract_claims()` now returns `Tuple[List[str], str]` (claims + method). New `extraction_complete` SSE event with `method` field ("llm", "heuristic", or "none"). `claim_verified` event now includes `claim` text for frontend display. Frontend `use-verification-stream.ts` updated to handle new events and expose `extractionMethod`.

**Files changed:**
- `src/mcp/agents/hallucination.py` (complete rewrite)
- `src/mcp/config/settings.py` (4 new env-configurable constants)
- `src/web/src/hooks/use-verification-stream.ts` (new event handling + extractionMethod state)

### H2. Verification UX Too Passive — Should Actively Display Checked Claims

**Severity:** Medium
**Status:** Resolved (2026-03-01)

**Resolution:** Two UI enhancements:

1. **Collapsible inline claims in status bar:** `VerificationStatusBar` now has a clickable summary row that expands to show all individual claims with status icons (CheckCircle2/XCircle/AlertCircle), claim text, source filename, and similarity percentage. During streaming, claims are shown in real-time with animated spinner for pending claims. Extraction method label shown during verification.

2. **Per-message verification badge:** New `VerificationBadge` component on `MessageBubble` shows compact "X/Y verified" pill on the latest assistant message. Badge color indicates status: green (>=80% verified), yellow (50-80%), red (unverified claims present). Shows spinner during verification. Badge only appears on the most recent assistant message.

**Files changed:**
- `src/web/src/components/audit/verification-status-bar.tsx` (rewritten: collapsible claims list, streaming claims view, ClaimStatusIcon component)
- `src/web/src/components/chat/message-bubble.tsx` (new VerificationBadge + MessageVerificationStatus type)
- `src/web/src/components/chat/chat-panel.tsx` (wire verification status to latest assistant message, pass streaming claims to status bar)

### H3. KB Context Pane Shows Current Conversation as Artifacts

**Severity:** Medium
**Status:** Resolved (2026-03-01)

**Resolution:** When `conversation_messages` are provided to `agent_query()` (indicating the query originates from the chat flow), the `conversations` domain is now automatically excluded from the domain list. This prevents feedback-ingested conversation turns from appearing as KB context results. Same pattern already used by `hallucination.py` for verification queries. Manual searches (without conversation context) still include all domains.

**Files changed:**
- `src/mcp/agents/query_agent.py` (added `effective_domains` filtering when `conversation_messages` provided)

### H4. Verification UX: "Unverified" Ambiguity + Missing Source Attribution

**Severity:** High
**Status:** Resolved (2026-03-01)

**Resolution:** Nine-step Verification UX Overhaul:

1. **Refuted vs. unverified distinction:** Frontend-only status mapping using `getClaimDisplayStatus(status, verification_method)` — claims actively found wrong by cross-model/web-search show "Refuted" (red, XOctagon icon), while claims with no KB evidence show "Unverified" (yellow, AlertTriangle icon). Zero backend enum changes.

2. **Source URL extraction:** `_verify_claim_externally()` now extracts `source_urls` from OpenRouter web search annotations (`url_citation` type). Propagated through `verify_claim()` → `verify_response_streaming()` → `claim_verified` SSE event → frontend. Displayed as external link icons on claim cards.

3. **Staleness detection & web search escalation:** 6 compiled regex patterns (`_STALE_KNOWLEDGE_PATTERNS`) detect when a static verification model admits stale knowledge (e.g., "as of my training data"). When detected on "supported" verdicts for current-event claims, automatically re-verifies via web search model (`force_web_search=True`).

4. **Generator model context:** Both `_SYSTEM_DIRECT_VERIFICATION` and `_SYSTEM_CURRENT_EVENT_VERIFICATION` prompts now say "You are verifying a claim made by a different AI model." User prompt includes which model generated the claim for cross-model awareness.

5. **Session metrics:** `useRef({ claimsChecked: 0, estCost: 0 })` accumulates across verification runs. Displayed in status bar as "Session: N facts • ~$X.XXXX".

6. **Feedback tooltips:** Thumbs up/down buttons now have descriptive `title` attributes.

7. **Web search badge:** `VerificationMethodBadge` handles `web_search` and `web_search_failed` methods with blue styling.

8. **Accuracy recalculation:** Only refuted claims count as failures; unverified (no evidence) excluded from accuracy denominator.

**Files changed:**
- `src/mcp/agents/hallucination.py` (source URL extraction, staleness patterns, model context in prompts)
- `src/web/src/lib/verification-utils.ts` (new shared utility)
- `src/web/src/lib/types.ts` (`source_urls` field, `web_search` method)
- `src/web/src/hooks/use-verification-stream.ts` (source_urls wiring, session metrics)
- `src/web/src/components/audit/verification-status-bar.tsx` (refuted/unverified, source links, session metrics)
- `src/web/src/components/audit/hallucination-panel.tsx` (display status, web search badge, tooltips)
- `src/web/src/components/chat/chat-panel.tsx` (session metrics passthrough)

### H5. Verification Confirms Model Honesty Instead of Checking Facts

**Severity:** High
**Status:** Resolved (2026-03-01)

**Resolution:** When a model says "I don't have information about X", the verifier was confirming the model's honesty ("yes, you don't know") instead of checking whether X actually exists. Fixed with ignorance-admission detection and verdict inversion:

1. **Ignorance-admission patterns:** 8 compiled regex patterns (`_IGNORANCE_ADMISSION_PATTERNS`) detect claims that admit ignorance (e.g., "I don't have specific information about...", "beyond my knowledge cutoff", "I cannot confirm whether...").

2. **Web search routing:** Ignorance claims always route to web search model (Grok `:online`) regardless of `_is_current_event_claim()` result, since the model admitted stale/missing knowledge.

3. **Reframed verification prompt:** New `_SYSTEM_IGNORANCE_VERIFICATION` prompt tells the verifier: "Do NOT evaluate whether the model is being honest about its limitations. Instead, search the web for authoritative sources about the UNDERLYING TOPIC."

4. **Verdict inversion:** `_invert_ignorance_verdict()` flips results — if the verifier finds the information exists (supported), the original claim is marked "unverified" (model was factually inadequate). If the verifier confirms the information doesn't exist (refuted), the original claim is marked "verified" (model was correct).

**Tests:** 23 new tests across 3 test classes: `TestIgnoranceAdmissionDetection` (13 tests), `TestIgnoranceVerdictInversion` (5 tests), `TestIgnoranceClaimVerification` (5 tests). Total hallucination tests: 125.

**Files changed:**
- `src/mcp/agents/hallucination.py` (ignorance patterns, `_is_ignorance_admission()`, `_invert_ignorance_verdict()`, `_SYSTEM_IGNORANCE_VERIFICATION`, modified `_verify_claim_externally()`)
- `src/mcp/tests/test_hallucination.py` (23 new tests, 125 total)

### H6. Verification Always Returns "Uncertain" — Missing External Fallback

**Severity:** High
**Status:** Resolved (2026-03-04)

**Resolution:** `verify_claim()` only attempted external (cross-model/web-search) verification when KB similarity was below `EXTERNAL_VERIFY_KB_THRESHOLD` (0.3). Since the KB almost always returns results above 0.3 for any query, claims landed in the "uncertain" band (0.4–0.75) and never triggered external verification.

Added two new fallback levels to `verify_claim()`:
- **Fallback 3 (unverified):** When KB similarity < `HALLUCINATION_UNVERIFIED_THRESHOLD` (0.4), try external verification. Use external result if it returns a definitive "verified" or "unverified" verdict; otherwise fall back to KB-only "unverified".
- **Fallback 4 (uncertain):** When KB similarity is in the uncertain band (0.4–0.75), try external verification. Use external result if definitive; otherwise return KB-only "uncertain" with source context.

The `verify_claim()` docstring now documents all 4 fallback levels. Only KB-verified claims (similarity ≥ threshold) skip external verification.

**Files changed:**
- `src/mcp/agents/hallucination.py` (added Fallback 3 + 4, updated docstring)

### H7. UI Polish — KB Card Overflow, Dashboard Data Loss, Icon Colors, Verification Labels

**Severity:** Medium
**Status:** Resolved (2026-03-04)

**Resolution:** Four UI fixes from user testing:

1. **KB artifact card overflow:** Cards with long filenames or many tags could overflow their container. Added `max-w-full` constraint, text truncation on filenames, and switched to icon-only action buttons (Eye/Syringe/Move) with tooltips.

2. **Chat dashboard data loss:** Single-row condensing removed provider name, token counts, "session" label, last message cost, and "injected" label. Restored all data with `hidden xl:inline` responsive classes for progressive disclosure at ≥1280px viewport.

3. **Inconsistent icon colors:** Status indicators used mixed green shades. Standardized to `text-green-500` across status bar and chat panel.

4. **Verification "unassessed" label:** Renamed to "uncertain" for consistency with backend status enum. Updated display logic in hallucination panel, verification status bar, and verification utils.

**Files changed:**
- `src/web/src/components/kb/artifact-card.tsx`, `knowledge-pane.tsx`
- `src/web/src/components/chat/chat-dashboard.tsx`, `chat-panel.tsx`
- `src/web/src/lib/verification-utils.ts`
- `src/web/src/components/audit/hallucination-panel.tsx`, `verification-status-bar.tsx`

---

## I. Phase 26 — User Review: Verification Logic, UX Fixes, and Backlog

Post-Phase 25 + production audit user review surfaced **22 items** spanning verification logic flaws, UX gaps, missing tooltips, tab persistence issues, and deferred features. Items are categorized by sprint priority. See `tasks/todo.md` for sprint grouping.

### I1. V1a — Surface Found Data in Ignorance Verification

**Severity:** High
**Status:** ✅ Resolved (Phase 26)

When model says "I don't have access to real-time data", verification confirms the limitation as accurate instead of showing the actual answer. Backend sends to Grok for web search and inverts the verdict (`hallucination.py` lines 1509-1519), but the `verification_answer` (raw Grok response with actual data) is NOT included in the SSE `claim_verified` event — only truncated `reason` is sent.

**Fix:** Add `verification_answer` field to SSE event payload. Add to `StreamingClaim` TypeScript type. In `verification-status-bar.tsx`, render expandable "Found answer" section when `claim_type === "ignorance"` and status is `"unverified"`.

**Files:** `src/mcp/agents/hallucination.py`, `src/web/src/lib/types.ts`, `src/web/src/hooks/use-verification-stream.ts`, `src/web/src/components/audit/verification-status-bar.tsx`

### I2. V1b — Proactive Model Switch for Real-Time Queries

**Severity:** Medium
**Status:** ✅ Resolved (Phase 28 Sprint 3)

Smart routing gives +10 bonus for `webSearch` models when `CURRENT_INFO_RE` matches (`model-router.ts` line 130), but doesn't proactively trigger when verification finds the model deflected. After verification detects ignorance on a real-time query, should surface recommendation to switch to Grok.

**Fix:** Post-verification feedback loop in `chat-panel.tsx`: when verification finds unverified ignorance claims, call `recommendModel()` with boosted `webSearch` weight, show "Switch to Grok for real-time data?" banner.

**Files:** `src/web/src/components/chat/chat-panel.tsx`, `src/web/src/lib/model-router.ts`, `src/web/src/hooks/use-verification-stream.ts`

### I3. V2 — Verification Source URLs Not Clickable

**Severity:** Medium
**Status:** ✅ Resolved (Phase 26)

Backend sends `source_urls` and `source_domain` in `claim_verified` events. Frontend renders `ExternalLink` icons when URLs exist, but KB-verified claims have no URLs and no click-through to the artifact.

**Fix:** For KB-verified claims, generate an artifact link (click to focus in KB pane). Make `source_urls` links more prominent with domain label.

**Files:** `src/web/src/components/audit/verification-status-bar.tsx`, `src/web/src/components/chat/chat-panel.tsx`

### I4. V3 — Quick-Access Toggles for Memory Extraction

**Severity:** Low
**Status:** ✅ Resolved (Phase 28 Sprint 1)

Memory extraction toggle only accessible via Settings. Should be in a right-click context menu or overflow popover on toolbar icon.

### I5. V4 — Settings Pane Scroll Issue

**Severity:** Medium
**Status:** ✅ Resolved (Phase 28 Sprint 1 — investigated, structure correct, no fix needed)

Settings pane may not scroll properly. Code uses `<ScrollArea className="flex-1">` inside `<div className="flex h-full min-h-0 flex-col">` which looks correct. Needs browser DevTools inspection — may be ancestor missing `overflow: hidden` or `h-full`.

**Files:** `src/web/src/components/settings/settings-pane.tsx`, parent layout

### I6. V5 — Trash Icon Invisible on Touch Devices

**Severity:** Medium
**Status:** ✅ Resolved (Phase 26)

`conversation-list.tsx` line 46: `opacity-0 group-hover:opacity-100`. iPad/touch has no hover state.

**Fix:** Add `[@media(pointer:coarse)]:opacity-60` matching existing touch-visibility pattern from Phase 17.

**Files:** `src/web/src/components/chat/conversation-list.tsx`

### I7. V6 — Right-Click Context Menus on Toolbar Icons

**Severity:** Low
**Status:** ✅ Resolved (Phase 28 Sprint 1)

No context menu infrastructure exists. User wants right-click on model router icon → routing mode switch, right-click on KB icon → auto-inject toggle, etc.

**Fix:** Install shadcn/ui `ContextMenu` (`@radix-ui/react-context-menu`). Add menus to model router icon, KB toggle, hallucination shield.

**Files:** `src/web/src/components/ui/context-menu.tsx` (new), `src/web/src/components/chat/chat-panel.tsx`

### I8. V7 — KB Auto-Inject Toggle in KB Context Pane

**Severity:** Medium
**Status:** ✅ Resolved (Phase 26)

Auto-inject toggle only in Settings > Knowledge. Users want it in the KB context pane for quick access.

**Fix:** Add a small `Switch` + label in `kb-context-panel.tsx` header bar. Wire to `useSettings()` hook (`autoInject` / `patch({ enable_auto_inject })`).

**Files:** `src/web/src/components/kb/kb-context-panel.tsx`

### I9. V8 — Monitoring/Audit Tab Overlap

**Severity:** Low
**Status:** ✅ Resolved (Phase 26)

Both `monitoring-pane.tsx` (line 61) and `audit-pane.tsx` (line 92) render `<KBOperations />`. Monitoring = operations + real-time health. Audit = analytics + historical reports.

**Fix:** Remove `KBOperations` from `audit-pane.tsx`.

**Files:** `src/web/src/components/audit/audit-pane.tsx`

### I10. V9 — Stale Verification Status Between Responses

**Severity:** High
**Status:** ✅ Resolved (Phase 26)

When a new assistant response streams in, the previous "x/x verified" status bar persists until the new verification starts. Should clear immediately.

**Root cause:** `use-verification-stream.ts` resets on `conversationId` change but not on new `responseText` arrival within the same conversation.

**Fix:** Set `savedReport = null` when `isStreaming` is true. Add `useEffect` that clears claims/summary when `responseText` changes to a shorter string.

**Files:** `src/web/src/hooks/use-verification-stream.ts`, `src/web/src/components/chat/chat-panel.tsx`

### I11. V10 — Model Switch Cost Comparison Logic

**Severity:** Low
**Status:** ✅ Resolved (Phase 26)

In `model-router.ts`, switching FROM expensive TO cheap can show `replayCost < summarizeCost`. This may be correct behavior (cheap target processes full history cheaper than expensive current model produces a summary). Needs unit tests to confirm.

**Fix:** Add unit tests with specific model pairs (expensive→cheap, cheap→expensive, same model).

**Files:** `src/web/src/__tests__/model-router.test.ts`

### I12. V11 — All Knowledge Cards Show Q50

**Severity:** High
**Status:** ✅ Resolved (Phase 26)

Quality badges uniformly show Q50 because `quality_score` is never set during ingestion. `get_quality_scores()` defaults unscored artifacts to 0.5. Curator agent computes real scores but only runs on-demand.

**Fix:** Compute basic quality signal during ingestion: has_summary (0.2), has_tags (0.15), chunk_count > 1 (0.15), parsed_size > 500 chars (0.15), domain_set (0.1), sub_category_set (0.1), dedup_passed (0.15). Write to Neo4j Artifact node on CREATE.

**Files:** `src/mcp/services/ingestion.py`, `src/mcp/db/neo4j/artifacts.py`

### I13. V12 — Missing Tooltips on Confidence Bars and Quality Badges

**Severity:** Medium
**Status:** ✅ Resolved (Phase 26)

Relevance bar, quality badge, and confidence bar have no hover explanation.

**Fix:** Wrap each in `<Tooltip>` from shadcn/ui. Texts: "Relevance: {pct}% match to query", "Quality: Q{score}", "Confidence: {pct}% retrieval confidence".

**Files:** `src/web/src/components/kb/artifact-card.tsx`, `src/web/src/components/kb/kb-context-panel.tsx`

### I14. V13 — Feature Tier Not Configurable

**Severity:** Low
**Status:** ✅ Resolved (Phase 28 Sprint 2 — descriptive tooltip added)

Feature tier is server-determined (`config/settings.py`), displayed read-only. Descriptive tooltip now explains Community vs Pro tier capabilities and how to set via CERID_TIER env var.

### I15. V14 — Infrastructure/Account Settings in UI

**Severity:** Medium
**Status:** ✅ Resolved (Phase 28 Sprint 2)

Added Infrastructure section (read-only: Bifrost, ChromaDB, Neo4j, Redis, Archive, Chunking) and Search Tuning section (sliders: vector/keyword weights, rerank weights) to Settings pane. Backend exposes config values via GET /settings and accepts search tuning PATCH.

**Fix:** New "Infrastructure" section in settings-pane with Bifrost URL (read-only display), OpenRouter API key (masked input, update via API), MCP URL.

**Files:** `src/mcp/config/settings.py`, `src/mcp/routers/settings.py`, `src/web/src/components/settings/settings-pane.tsx`

### I16. V15 — Verification State Lost on Tab Switch

**Severity:** High
**Status:** ✅ Resolved (Phase 26)

Switching to monitoring/audit tab and back causes `conversationId` to reset → stream aborted → state cleared. Redis fallback fetches saved report but with visible loading flash.

**Fix:** Store completed `HallucinationReport` per conversation ID in `ConversationsContext`. On tab return, read stored report immediately without re-fetching.

**Files:** `src/web/src/contexts/conversations-context.tsx`, `src/web/src/components/chat/chat-panel.tsx`, `src/web/src/hooks/use-verification-stream.ts`

### I17. V16 — Knowledge Card Summaries Show Raw Data

**Severity:** Medium
**Status:** ✅ Resolved (Phase 26)

`artifact-card.tsx` shows `result.content` (raw ChromaDB chunk text), not the generated summary. The `summary` field exists on Neo4j Artifact nodes but isn't returned in query results.

**Fix:** Include artifact-level `summary` from Neo4j in query results. Prefer `result.summary` over `result.content` in browse mode.

**Files:** `src/mcp/agents/query.py`, `src/web/src/lib/types.ts`, `src/web/src/components/kb/artifact-card.tsx`

### I18. V17 — KB Injection Badge Shows Count but No Detail

**Severity:** Medium
**Status:** ✅ Resolved (Phase 26)

`chat-input.tsx` shows `<Badge>{N} source(s)</Badge>` with no information about what was injected.

**Fix:** Wrap in `<Popover>` showing injected artifact names, domains, and content snippet preview.

**Files:** `src/web/src/components/chat/chat-input.tsx`, `src/web/src/components/chat/chat-panel.tsx`

### I19. V18 — Model Doesn't Appear to Receive Injected Data

**Severity:** Medium
**Status:** ✅ Resolved (Phase 26)

Code confirms KB IS injected as system message (`chat-panel.tsx` lines 309-325). Likely perception issue — model receives context but doesn't explicitly reference it. Improving V17 (injection detail popover) will help verify what was sent.

**Fix:** Add debug logging on Bifrost side. Add "Show prompt" debug mode in dev. Improve V17 first for visibility.

**Files:** `src/web/src/components/chat/chat-panel.tsx`, Bifrost logs

### I20. V19 — Drag-Drop to KB Context Pane

**Severity:** Low
**Status:** ✅ Resolved (Phase 28 Sprint 4)

KB context split-pane doesn't accept file drops. Knowledge-pane has drag-drop for ingestion but the context pane doesn't.

**Fix:** Add drag-drop handlers to `kb-context-panel.tsx`. File drop → ingest + auto-inject. Internal artifact drag → add to injection queue.

**Files:** `src/web/src/components/kb/kb-context-panel.tsx`, `src/web/src/components/kb/artifact-card.tsx`

### I21. V20 — Drag-Drop to Chat Input

**Severity:** Low
**Status:** ✅ Resolved (Phase 28 Sprint 4)

No drag-drop on chat input. Want: drop file → ingest + add to context, or drop artifact → inject.

**Files:** `src/web/src/components/chat/chat-input.tsx`, `src/web/src/components/chat/chat-panel.tsx`

### I22. V21 — Advanced Response Re-Formatting & Inline Verification

**Severity:** Low
**Status:** ✅ Resolved (Phase 29, 2026-03-07)

**Resolution:** Three-sprint enhancement of the chat response rendering pipeline:

1. **Markdown rendering improvements (Sprint 1):** Expanded `MD_COMPONENTS` with 15 element overrides — external links with icons and `target="_blank"`, bordered/striped tables with horizontal scroll, styled blockquotes, heading hierarchy (h1-h4) with IDs, tighter list spacing, bordered images with lazy-loading. Added `CollapsibleCodeBlock` for fenced blocks exceeding 25 lines (gradient fade + expand/collapse button).

2. **Interactive inline verification (Sprint 2):** Created `ClaimOverlay` component using hybrid DOM + React portal approach. DOM TreeWalker marks get `data-claim-index` attributes and superscript footnotes `[N]`. Clicking a mark opens a fixed-positioned popover with status badge, claim text, source filename (clickable → KB pane), domain badge, verification method, similarity percentage, source snippet, and external URLs. Hover shows lightweight tooltip. Extracted shared display utilities (`DISPLAY_STATUS_COLORS`, `verificationMethodLabel`, `verificationMethodColor`) to `verification-utils.ts` for reuse across claim UI components. Wired `onArtifactClick` through `MessageBubble` → `chat-panel.tsx` for KB source navigation.

3. **Document navigation (Sprint 3):** Added `extractText()` utility for recursive text extraction from React children. Heading IDs generated via slugification. `MessageTOC` component renders a clickable table of contents with smooth-scroll navigation when 3+ headings detected. 17 new tests (9 message-bubble, 8 claim-overlay) bringing total to 347.

**Files changed:**
- `src/web/src/components/chat/message-bubble.tsx` (MD_COMPONENTS, CollapsibleCodeBlock, heading IDs, MessageTOC, ClaimOverlay wiring, onArtifactClick)
- `src/web/src/components/chat/claim-overlay.tsx` (new — interactive claim popovers)
- `src/web/src/components/chat/chat-panel.tsx` (onArtifactClick prop)
- `src/web/src/lib/verification-utils.ts` (shared display utilities)
- `src/web/src/components/audit/hallucination-panel.tsx` (refactored to shared imports)
- `src/web/src/__tests__/message-bubble.test.tsx` (9 new tests)
- `src/web/src/__tests__/claim-overlay.test.tsx` (new — 8 tests)

### I23. V22 — Inline Verification Markups in Chat Response

**Severity:** Medium
**Status:** ✅ Resolved (Phase 28 Sprint 5)

Verification results only appear in the status bar and hallucination panel sidebar. Claims are not visually linked to specific text in the response.

**Fix:** Add optional inline annotations to `message-bubble.tsx`: highlight verified claims in green, unverified in red/orange, uncertain in yellow. Fuzzy match claim text to locate spans in rendered markdown. Toggle via settings (default off).

**Files:** `src/web/src/components/chat/message-bubble.tsx`, `src/web/src/hooks/use-verification-stream.ts`, `src/web/src/hooks/use-settings.ts`, `src/web/src/lib/verification-utils.ts`

---

## J. Phase 35 — Verification & Infrastructure

### J1. Verification Stream OOM — Memory Optimization Needed

**Severity:** Medium
**Status:** ✅ Resolved (Phase 40, 2026-03-16)

**Problem:** Verification of LLM responses with 10+ factual claims causes the MCP container to OOM-kill under the 2 GB memory limit. Each claim verification loads a BM25 index (bm25s library), runs ONNX cross-encoder reranking (ms-marco-MiniLM-L-6-v2), and issues ChromaDB vector queries — all memory-intensive. When 10+ claims run in parallel via `asyncio.gather()`, peak memory exceeds 2 GB and the Linux OOM killer sends SIGKILL (uncatchable, exit code 137).

**Mitigated (not fully fixed):** Added `VERIFY_CLAIM_MAX_CONCURRENT=3` semaphore to bound parallelism. This keeps peak memory at ~1.84 GB under a 2 GB limit — functional but leaves only ~160 MB headroom. A response with 15+ claims or concurrent users could still OOM.

**Resolution:** Raised container memory 3G→4G. Added cgroup-aware memory guard (`_wait_for_memory`) that pauses verification when available container memory drops below 512MB. Uses cgroup v2 files — no-op on host.

**Root cause details:**
- `docker inspect` shows `OOMKilled=false` and `ExitCode=0` after container auto-restart — misleading. Only `docker events` reveals the truth (`container oom` + `exitCode=137`).
- Module-level `signal.signal()` handlers get overwritten by uvicorn during startup; must register in FastAPI's `lifespan` hook.

**Optimization opportunities:**
1. **Singleton BM25/ONNX models:** Currently each verification may re-load the cross-encoder and rebuild BM25 indices. A process-global model cache would eliminate repeated allocations.
2. **Container memory limit:** Consider raising from 2 GB to 3 GB (host has 160 GB RAM).
3. **Streaming batch size:** Currently fixed at 3. Could be made adaptive based on `psutil.virtual_memory()` available headroom.
4. **BM25 index caching:** The bm25s library rebuilds indices per query. A per-collection index cache with TTL would reduce memory churn.
5. **ONNX model sharing:** Ensure the `onnxruntime.InferenceSession` is created once and reused across all verifications within a request.

**Files:**
- `src/mcp/agents/hallucination.py` — `_claim_verify_semaphore`, `_get_claim_verify_semaphore()`
- `src/mcp/config/settings.py` — `VERIFY_CLAIM_MAX_CONCURRENT`
- `src/mcp/main.py` — Signal handler in lifespan (for debugging future crashes)

---

## K. Deferred Backlog (from Phase 16–18 Plan)

> Originally deferred post-Phase 21. Archived 2026-03-13. See [`docs/plans/DEVELOPMENT_PLAN_PHASE16-18.md`](plans/DEVELOPMENT_PLAN_PHASE16-18.md) for context.

### K1. Codecov XML Reports

**Severity:** Low | **Effort:** ~1 hr
**Status:** ✅ Resolved (Phase 40, 2026-03-16)
**Notes:** CI already produces XML coverage output. Needs Codecov integration for PR coverage gates. **Resolution:** Added `codecov/codecov-action@v5` to CI test job.

### K2. Dependency License Scanning

**Severity:** Low | **Effort:** ~1–2 hrs
**Status:** ✅ Resolved (Phase 40, 2026-03-16)
**Notes:** Add `pip-licenses` (Python) and `license-report` (Node) to CI. Catch GPL-incompatible deps. **Resolution:** Added `pip-licenses` + `license-checker` to CI.

### K3. ReDoS Regex Audit

**Severity:** Low | **Effort:** ~2–3 hrs
**Status:** ✅ Resolved (Phase 40, 2026-03-16)
**Notes:** Low risk given current regex patterns. Audit with `rxxr2` or `safe-regex` for completeness. **Resolution:** Added `dlint DUO138` ReDoS audit to CI security job.

### K4. Plugin Management UI

**Severity:** Low | **Effort:** ~4–6 hrs
**Status:** 🔲 Open
**Notes:** No backend plugin API exists yet. Scaffold needed before UI work.

### K5. Digest View / Generation

**Severity:** Low | **Effort:** ~3–4 hrs
**Status:** ✅ Resolved (2026-03-13)
**Resolution:** DigestCard component added to Monitoring pane. Shows summary stats (artifacts, domains, relationships, events), domain breakdown badges, and recent artifacts list with time-period selector (24h/3d/7d). Uses existing `GET /digest` API. 8 new tests.
**Files:** `digest-card.tsx` (new), `monitoring-pane.tsx`, `types.ts`, `api.ts`, `digest-card.test.tsx` (new)

### K6. Batch Triage UI

**Severity:** Low | **Effort:** ~4–6 hrs
**Status:** ✅ Resolved (2026-03-13)
**Resolution:** UploadDialog enhanced with batch mode (≥3 files). Shows expanded file list with sizes, batch header with file count, "Start Batch" button. Progress counter in KnowledgePane shows "Uploaded X of N…" during parallel upload. 8 new tests.
**Files:** `upload-dialog.tsx`, `knowledge-pane.tsx`, `upload-dialog.test.tsx` (new)

### K7. Multi-Stage MCP Dockerfile

**Severity:** Low | **Effort:** ~2–3 hrs
**Status:** ✅ Resolved (Phase 40, 2026-03-16)
**Notes:** Minor image size savings. Current single-stage image works but is larger than necessary. **Resolution:** Converted to 3-stage Dockerfile (builder, models, runtime). ~200MB image reduction.

---

## L. Phase 39 — Privacy Hardening

### L1. CORS Wildcard Default

**Severity:** Medium (security)
**Status:** ✅ Resolved (Phase 39, 2026-03-14)

**Problem:** `CORS_ORIGINS` defaulted to `*`, allowing any origin to make credentialed requests to the MCP API. While acceptable for local-only use, this was inconsistent with the project's privacy-first posture and risky if the API was exposed on a LAN.

**Resolution:** Changed `CORS_ORIGINS` default from `*` to `http://localhost:3000,http://localhost:5173` (React GUI production + Vite dev). Users can override via env var. Existing `CORS_ORIGINS=*` in `.env` files continue to work.

**Files:** `src/mcp/main.py`, `src/mcp/config/settings.py`

### L2. Service Ports Bound to 0.0.0.0

**Severity:** Medium (security)
**Status:** ✅ Resolved (Phase 39, 2026-03-14)

**Problem:** All Docker Compose port mappings used `0.0.0.0` (implicit default), exposing services (MCP 8888, Neo4j 7474/7687, ChromaDB 8001, Redis 6379) to the entire LAN. Any device on the network could connect directly to databases.

**Resolution:** Added `CERID_BIND_ADDR` env var (default `127.0.0.1`). All `docker-compose.yml` port mappings now use `${CERID_BIND_ADDR:-127.0.0.1}:port:port`. Users who need LAN access can set `CERID_BIND_ADDR=0.0.0.0`.

**Files:** `src/mcp/docker-compose.yml`, `src/web/docker-compose.yml`, `stacks/infrastructure/docker-compose.yml`, `stacks/bifrost/docker-compose.yml`, `.env.example`

### L3. Email Header PII Exposure

**Severity:** Medium (privacy)
**Status:** ✅ Resolved (Phase 39, 2026-03-14)

**Problem:** The email parser stored full sender/recipient email addresses and display names in ChromaDB metadata and Neo4j. Ingesting personal email archives exposed PII in the knowledge base that could surface in RAG context injections.

**Resolution:** Added `CERID_ANONYMIZE_EMAIL_HEADERS` env var (default `true`). When enabled, the email parser hashes email addresses with SHA-256 (first 8 chars) and strips display names before storing metadata. Existing ingested emails are not retroactively anonymized.

**Files:** `src/mcp/parsers/email.py`, `src/mcp/config/settings.py`

### L4. Redis Audit Log No TTL

**Severity:** Low (privacy)
**Status:** ✅ Resolved (Phase 39, 2026-03-14)

**Problem:** The Redis ingest audit log (`ingest:audit:*` keys) accumulated indefinitely with no expiration. Over time, this created an unbounded record of every file ingested, including filenames and paths — a privacy concern for users who expect data minimization.

**Resolution:** Added 30-day TTL to all `ingest:audit:*` keys using `EXPIRE` after each write. Configurable via `CERID_AUDIT_TTL_DAYS` env var.

**Files:** `src/mcp/services/ingestion.py`, `src/mcp/config/settings.py`

### L5. Sync Directory Unencrypted

**Severity:** Medium (privacy)
**Status:** ✅ Resolved (Phase 39, 2026-03-14)

**Problem:** The cloud sync directory (`~/cerid-archive/sync/`) stored user state files (conversations, settings, preferences) in plaintext JSON. When synced via Dropbox, this meant sensitive data was readable by the cloud provider and anyone with Dropbox access.

**Resolution:** Added at-rest encryption for sync directory files using Fernet (symmetric, from `cryptography` library). Auto-enabled when `CERID_ENCRYPTION_KEY` is set. Reuses existing `utils/encryption.py` infrastructure. Files are encrypted before write and decrypted on read, transparent to the rest of the application.

**Files:** `src/mcp/sync/user_state.py`, `src/mcp/utils/encryption.py`, `src/mcp/config/settings.py`

### L6. KB Injection Not Transparent to User

**Severity:** Low (UX)
**Status:** ✅ Resolved (Phase 39, 2026-03-14)

**Problem:** When the KB context injection system added knowledge base content to a chat prompt, the user had no indication that their query was being augmented. This made it difficult to understand why the model's response referenced specific documents.

**Resolution:** Added a transparency indicator in the chat UI that shows when KB context has been injected, including the number of chunks and source documents. Visible as a small badge on messages that received KB augmentation.

**Files:** `src/web/src/components/chat/chat-message.tsx`, `src/web/src/contexts/KBInjectionContext.tsx`

### L7. Marketing Privacy Claims Inaccurate

**Severity:** Medium (docs)
**Status:** ✅ Resolved (Phase 39, 2026-03-14)

**Problem:** The marketing site (cerid.ai) and CLAUDE.md claimed "all data stays local" and "never leaves your machine," but Phase 38D added cloud sync via Dropbox, which uploads user state to a third-party service. The privacy claims had drifted from the actual data flow.

**Resolution:** Updated marketing copy and CLAUDE.md to accurately describe the data flow: local-first with optional encrypted cloud sync. Added nuance about what goes where — LLM API calls are external, sync is opt-in and encrypted, raw knowledge base stays local.

**Files:** `packages/marketing/src/app/page.tsx`, `CLAUDE.md`

---

## M. Phase 39B — Rate Limiting & Performance

### M1. Semantic Cache Silently Inactive

**Severity:** Low (performance)
**Status:** ✅ Resolved (Phase 40, 2026-03-16)

**Problem:** `ENABLE_SEMANTIC_CACHE=true` is set in `src/mcp/docker-compose.yml` but the semantic cache never activates. The cache (`utils/semantic_cache.py`) requires a client-side embedding function to compute query embeddings for HNSW similarity matching. The default embedding model (`all-MiniLM-L6-v2`) runs server-side inside ChromaDB — `get_embedding_function()` in `deps.py` returns `None` when the server-side default is in use, so no query embedding is ever computed and the HNSW index is never populated or consulted.

**Impact:** All `agent_query()` calls pay full retrieval pipeline cost even when semantically identical queries repeat (e.g., the same market event phrased slightly differently across 5 trading sessions). Only the exact-match Redis cache (`utils/query_cache.py`) is active.

**Root cause:** `_EmbeddingAwareClient` in `deps.py` uses `get_embedding_function()` which returns `None` for `all-MiniLM-L6-v2` (ChromaDB's built-in server-side model). `semantic_cache.py` short-circuits with `if embed_fn is None: return None` before any lookup. The HNSW index is always empty.

**Fix required:**
1. Migrate to a client-side ONNX embedding model (`Snowflake/snowflake-arctic-embed-m-v1.5` at 768d recommended)
2. Update `SEMANTIC_CACHE_DIM` to match the new model's output dimension
3. Re-ingest full KB (existing vectors use incompatible server-side embeddings at different dimensions)
4. Bake embedding model ONNX into Dockerfile alongside the reranker

**Full implementation plan:** `tasks/todo.md` → "Task: Activate Semantic Cache"

**Files:** `src/mcp/deps.py`, `src/mcp/utils/semantic_cache.py`, `src/mcp/utils/embeddings.py`, `src/mcp/config/features.py`, `src/mcp/Dockerfile`

**Resolution:** Switched to Snowflake Arctic Embed M v1.5 (client-side ONNX, 768d). Updated SEMANTIC_CACHE_DIM default to 768. Multi-stage Dockerfile bakes model at build time. Requires destructive KB re-ingest.

---

## N. Phase 41 — SDK Hardening & Multi-Agent Extensibility

### N1. SDK Typed Response Models

**Severity:** Medium
**Status:** Resolved (Phase 41, 2026-03-21)

**Resolution:** SDK endpoints now have typed Pydantic response models in `models/sdk.py`. All `/sdk/v1/` endpoints return structured, validated responses instead of raw dicts.

**Files:** `src/mcp/models/sdk.py`, `src/mcp/routers/sdk.py`

### N2. Consumer Domain Access Control

**Severity:** Medium
**Status:** Resolved (Phase 41, 2026-03-21)

**Resolution:** Added `CONSUMER_REGISTRY` to `config/settings.py` with per-consumer `allowed_domains` and `strict_domains` fields. Consumers can only query KB domains explicitly listed in their `allowed_domains`. When `strict_domains: True`, cross-domain affinity bleed is disabled for that consumer. Personal data (personal, conversations) is never accessible to non-GUI consumers unless explicitly configured.

**Files:** `src/mcp/config/settings.py`, `src/mcp/middleware/rate_limit.py`, `src/mcp/routers/sdk.py`

### N3. Trading SDK Feature Flag Gating

**Severity:** Medium
**Status:** Resolved (Phase 41, 2026-03-21)

**Resolution:** All 5 trading SDK endpoints (`/sdk/v1/trading/*`) and 5 MCP tools (`pkb_trading_*`) are gated by `CERID_TRADING_ENABLED` feature flag. Endpoints return 404 when disabled. Default is `false` (backward-compatible).

**Files:** `src/mcp/routers/sdk.py`, `src/mcp/tools.py`, `src/mcp/config/settings.py`

### N4. MCP Tools outputSchema

**Severity:** Low
**Status:** Resolved (Phase 41, 2026-03-21)

**Resolution:** All 23 MCP tools now include `outputSchema` definitions alongside existing `inputSchema`. Enables better client-side validation and documentation generation.

**Files:** `src/mcp/tools.py`

### N5. SDK Test Suite

**Severity:** Medium
**Status:** Resolved (Phase 41, 2026-03-21)

**Resolution:** Created `tests/test_router_sdk.py` with tests covering all SDK endpoints, feature flag gating, domain access control, and rate limiting per consumer.

**Files:** `src/mcp/tests/test_router_sdk.py`

### N6. Integration Guide

**Severity:** Low (docs)
**Status:** Resolved (Phase 41, 2026-03-21)

**Resolution:** Created `docs/INTEGRATION_GUIDE.md` with a canonical 13-step checklist for adding new cerid-series agent integrations. Covers feature flags, domain setup, consumer registration, endpoints, MCP tools, proxy routes, scheduler jobs, tests, and documentation. Includes domain segregation rules, client authentication, and the trading-agent reference implementation.

**Files:** `docs/INTEGRATION_GUIDE.md`, `CLAUDE.md`, `docs/DEPENDENCY_COUPLING.md`

---

## Priority Order

### Open Items (1)

K4 (plugin management UI) — Low severity, no backend plugin API exists yet

### Resolved (100 items)

**Phase 40** (6 items): M1 (semantic cache), J1 (verification OOM), K1 (Codecov), K2 (license scanning), K3 (ReDoS audit), K7 (multi-stage Dockerfile)
**Phase 39** (7 items): L1 (CORS wildcard), L2 (port binding), L3 (email PII), L4 (audit TTL), L5 (sync encryption), L6 (KB injection transparency), L7 (marketing claims)
**Phase 38D+** (3 items): D4 (temporal claims uncertain), D5 (auto router real-time queries), D6 (Llama fallback retry)
**Phase 38D** (3 items): D3 (model router auto mode), K5 (digest view), K6 (batch triage UI)
**Phase 41** (6 items resolved): SDK hardening & multi-agent extensibility — typed response models (`models/sdk.py`), consumer domain access control (`CONSUMER_REGISTRY` with `allowed_domains`/`strict_domains`), trading endpoints gated by `CERID_TRADING_ENABLED`, MCP `outputSchema` on all 23 tools, SDK test suite (`test_router_sdk.py`), integration guide (`docs/INTEGRATION_GUIDE.md`)
**Phase 30** (0 new issues): Codebase audit & cleanup — no new issues filed; structural debt reduced
**Phase 29** (1 item): V21 (advanced response formatting + inline verification)
**Phase 26** (14 items): V1a, V2, V4, V5, V7, V8, V9, V10, V11, V12, V15, V16, V17, V18
**Phase 28** (9 items): V1b, V3, V4, V6, V13, V14, V19, V20, V22
**Phases 10A–25**: All items from sections A–H. See [COMPLETED_PHASES.md](COMPLETED_PHASES.md) for full history.
