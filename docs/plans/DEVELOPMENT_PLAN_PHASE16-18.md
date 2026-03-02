# Comprehensive Development Plan — Post-Phase 15H Holistic Audit

## Context

Phase 15H is complete. All 16 phases (0–15) are done: 795+ tests, full React GUI, 5 backend agents, 17 MCP tools, streaming verification, accuracy analytics, user feedback. A holistic audit was performed covering all project documentation, code quality (backend + frontend), dependencies, Docker, CI/CD, infrastructure, knowledge sync, artifact quality, and tag/keyword effectiveness. This plan consolidates **130+ findings** plus new feature requirements into actionable work for future LLM sessions.

**Current state:** Phase 16A–F complete. 811+ Python tests (125 hallucination), 111 frontend tests. Security hardened, dead code removed, code quality improved, dependencies tightened, backend features wired to GUI. Next: 16G (Content Experience & Testing).

---

## Audit Summary

| Area | Findings | Critical | High | Medium | Low |
|------|----------|----------|------|--------|-----|
| Backend Python | 31 | 0 | 1 | 9 | 21 |
| Frontend TypeScript | 47 | 0 | 0 | 9 | 38 |
| Dependencies & Infra | 52 | 4 | 4 | 15 | 29 |
| Sync & Knowledge Mgmt | 20+ | 0 | 5 | 8 | 7+ |
| Artifact Quality (tags/summaries) | 10+ | 0 | 2 | 5 | 3+ |
| **Total** | **160+** | **4** | **12** | **46** | **98+** |

---

## Phase 16A: Security & Infrastructure Hardening

**Priority: Critical/High — address before any production deployment**

### 16A.1 — Image Pinning (Critical)
- Pin `maximhq/bifrost:latest` to specific version in `stacks/bifrost/docker-compose.yml:7`
- Pin `ghcr.io/danny-avila/librechat-dev:latest` to specific SHA or tag in `stacks/librechat/docker-compose.yml:11`
- **Why:** `latest` tag defeats reproducibility; builds can break silently

### 16A.2 — Credential Hardening (Critical/High)
- `stacks/librechat/docker-compose.yml:97-99` — PostgreSQL creds (`myuser`/`mypassword`) hardcoded. Move to `.env` vars
- `stacks/infrastructure/docker-compose.yml:27` — Redis has no `requirepass`. Add `REDIS_PASSWORD` env var
- `stacks/librechat/docker-compose.yml:24` — MongoDB runs `--noauth`. Add user/password for production
- `stacks/librechat/docker-compose.yml:35` — Meilisearch `MEILI_MASTER_KEY` is empty. Require from `.env`

### 16A.3 — CI Security Gaps (Medium)
- Add `npm audit --audit-level=high` step to frontend CI job (`.github/workflows/ci.yml`)
- Add secret detection (e.g., `truffleHog` or `detect-secrets`) to CI pipeline
- Review 10 ignored Trivy CVEs — 3 are HIGH severity (glibc, libxml2). Set remediation SLA

### 16A.4 — Runtime Config (Medium)
- `src/web/Dockerfile:9-10` — `VITE_MCP_URL=http://localhost:8888` hardcoded at build time. Generate `window.__ENV__` at container startup via nginx init script for runtime configurability
- Add startup validation for `OPENROUTER_API_KEY` presence in `src/mcp/main.py`
- Validate spaCy model version matches installed version at startup

---

## Phase 16B: Dead Code & API Cleanup

**Priority: High hygiene value, low effort (~1-2 hours)**

### 16B.1 — Remove Dead Frontend API Functions
**File:** `src/web/src/lib/api.ts`
- Remove `checkHallucinations()` — replaced by `streamVerification()` in 15H.1
- Remove `fetchCollections()` — unused, no component calls it
- Remove `fetchSupportedExtensions()` — unused, no component calls it
- Remove `fetchTags()` — unused, tag data comes via artifact queries
- Remove `mergeTags()` — unused
- Remove `updateArtifactTaxonomy()` — unused

### 16B.2 — Remove Dead Backend Code
- `query_agent.py:200-245` — Remove unused `seen_ids` variable (set created but never read after check)
- `routers/agents.py:123-127` — Move inline import `validate_file_path` to module-level
- `hallucination.py:186` — Remove inline `import asyncio` (already imported at top)
- `curator.py:162-175` — Inline `_score_distribution()` helper (called only once)

### 16B.3 — Remove Unused Dependencies
**Python (`src/mcp/requirements.txt`):**
- Verify `python-multipart` usage — likely unused (FastAPI multipart is built-in via starlette)
- Verify `spacy` usage — grep for `nlp.` in src/mcp/. If unused, remove (~90 MB Docker savings)
- Verify `pandas` usage — if only CSV parsing, replace with stdlib `csv` module

**Node (`src/web/package.json`):**
- Verify `@tanstack/react-query` — grep for `useQuery`/`useMutation`. If unused, remove
- Remove `tw-animate-css` if not imported anywhere
- Move `shadcn` CLI to devDependencies or remove (scaffolding-only tool)

---

## Phase 16C: Backend Code Quality

**Priority: Medium — improves maintainability and performance**

### 16C.1 — DRY Refactoring (Medium severity, 9 items)

**`src/mcp/agents/query_agent.py`:**
1. **Lines 176-201** — Extract `_format_chunk()` helper to eliminate 60 lines of duplicated ChromaDB result → dict formatting (used for both vector and BM25 results)
2. **Lines 118-143** — Extract `_extract_query_terms()` helper for stopword filtering (reused 3+ times across file)
3. **Lines 200-245** — Combine vector + BM25 fetch logic into single `_fetch_and_score_chunk()` helper
4. **Lines 260-262** — Replace manual `extend()` loop with `itertools.chain.from_iterable()`

**`src/mcp/agents/triage.py`:**
5. **Lines 156-164** — Extract `_merge_metadata()` helper for repeated metadata merging pattern (4 occurrences)

**`src/mcp/agents/audit.py`:**
6. **Lines 180-188** — Simplify 4 Counter + loop patterns to `Counter(e.get("domain") for e in entries)` directly
7. **Lines 56-62** — Replace `defaultdict(lambda: defaultdict(int))` with `defaultdict(Counter)`
8. **Lines 217-269** — Replace manual Redis `scan()` loop with `redis_client.scan_iter()`

**`src/mcp/agents/maintenance.py`:**
9. **Lines 143-155** — Distinguish retriable vs. unrecoverable errors in batch purge loop (currently catches all `Exception`)

### 16C.2 — Efficiency Improvements (Medium severity, 6 items)

1. **`services/ingestion.py:50-68`** (HIGH) — Remove redundant `_check_duplicate()` pre-check query. Neo4j UNIQUE CONSTRAINT already exists — rely on constraint violation instead of O(n) extra round-trips during batch ingest
2. **`services/ingestion.py:154-183`** — Replace 2-query re-ingest path (fetch + update) with single `MERGE ... ON MATCH SET` statement
3. **`curator.py:290-301`** — Push artifact filtering into `list_artifacts()` Neo4j query instead of fetching all then filtering post-hoc
4. **`bm25.py:133-143`** — Defer BM25 index rebuild to batch updates (every 100 adds or on-demand) instead of rebuilding on every `add_documents()` call
5. **`curator.py:414-439`** — Replace sequential synopsis generation + `asyncio.sleep()` with `asyncio.gather()` using `AsyncLimiter` for rate limiting
6. **`query_agent.py:299-312`** — Batch graph expansion with initial query results before assembly, not after

### 16C.3 — Error Handling Improvements (Medium severity, 3 items)

1. **`audit.py:237-239`** — Log Redis scan failure details instead of silently returning empty result. Include `error_detail` in response
2. **`maintenance.py:145-170`** — Distinguish retriable (network) vs. unrecoverable errors in batch purge. Use specific exception types
3. **`rectify.py:199`** — Track partial failures during chunk deletion. Return summary of succeeded vs. failed operations

### 16C.4 — Type Safety (2 items)

1. **`query_agent.py:151-155`** — Use consistent `get_chroma()` factory from `deps` module instead of ad-hoc `chromadb.HttpClient()` creation when `chroma_client is None`
2. **`curator.py:302-306`** — Define `SynopsisModelConfig = TypedDict(...)` for stringly-typed `model_info` dict access

### 16C.5 — AI Slop Cleanup (5 items)

1. **`audit.py:203`** — Move `from utils.cache import ...` import from inside function to module-level; remove `# noqa: E402`
2. **`hallucination.py:21-30`** — Move `DEFAULT_THRESHOLD` and `UNVERIFIED_THRESHOLD` constants to `config/settings.py`
3. **`query_agent.py:95-115`** — Remove line-by-line comments in slot allocation loop; variable names are sufficient
4. **`curator.py:68-80`** — Simplify 3-line docstring to one-liner for `score_keywords()` (8-line function)
5. **`memory.py:24`** — Remove redundant `# skip trivially short responses` comment on `MIN_RESPONSE_LENGTH = 100`

---

## Phase 16D: Frontend Code Quality

**Priority: Medium — improves consistency and performance**

### 16D.1 — Extract Shared Utilities (Medium severity, 4 items)

1. **localStorage helpers** — `readBool()` is defined identically in `use-settings.ts`, `sidebar.tsx`, and `settings-pane.tsx`. Extract to `src/web/src/lib/storage.ts`
2. **Cost calculation** — `inputCost = (tokens * costPer1M) / 1_000_000` repeated in `chat-dashboard.tsx` and `use-live-metrics`. Extract to `src/web/src/lib/utils.ts`
3. **Accuracy color logic** — `accuracy >= 80 ? green : accuracy >= 50 ? yellow : red` repeated 3x in `verification-status-bar.tsx` and `accuracy-dashboard.tsx`. Extract to shared `getAccuracyColor()` function
4. **OCR text cleaning** — Chain of 4 `.replace()` calls in `artifact-card.tsx:37-43`. Extract to `cleanOCRText()` utility

### 16D.2 — Extract Shared Components (Low severity, 4 items)

1. **`<TagPills>`** — Tag rendering (`.tags.slice(0, 4).map(...)`) repeated in `artifact-card.tsx` and `knowledge-pane.tsx`
2. **`<LoadingDots>`** — Bouncing dot animation hardcoded in `message-bubble.tsx:110-116`; similar in other places
3. **`<SettingsSection>`** — Card wrapper pattern repeated 7x in `settings-pane.tsx:150-208`
4. **Status colors** — `STATUS_COLORS` dict in `hallucination-panel.tsx:13-19` duplicated conceptually in `verification-status-bar.tsx`. Centralize to `src/web/src/lib/constants.ts`

### 16D.3 — React Anti-patterns (Medium severity, 4 items)

1. **`chat-panel.tsx:196-241`** — `handleSend` callback is 46 lines combining user message, injection, auto-inject, system prompt, and API call. Extract injection logic to `combineKBContext()` helper
2. **`knowledge-pane.tsx:101`** — Unnecessary `activeSearch` state variable; can be derived from `searchInput` debounce
3. **`message-bubble.tsx:16-17`** — Module-level mutation `oneDarkStyle` via dynamic import. Use React lazy loading pattern instead
4. **`settings-pane.tsx:99`** — `setSettings({ ...settings, ...update } as ServerSettings)` type assertion. Define `PartialServerSettings` type

### 16D.4 — Performance (Medium severity, 3 items)

1. **`knowledge-pane.tsx:308-320`** — Array `.map()` key uses `${artifact_id}-${chunk_index}-${i}` — the index `i` makes keys unstable. Remove `i`
2. **`use-verification-stream.ts:110-135`** — Multiple `setClaims()` calls in SSE parsing loop causes 7+ re-renders per batch. Accumulate updates in temp array, then single `setClaims()`
3. **`accuracy-dashboard.tsx:38-39`** — `Object.entries(verification.by_model).sort(...)` not memoized; recalculates on every render. Wrap in `useMemo`

### 16D.5 — Type Safety (Medium severity, 3 items)

1. **`api.ts:76`** — Unsafe `JSON.parse` in tags normalization. Create typed `parseTags(tags: unknown): string[]` function
2. **`api.ts:422-423`** — Unsafe error object parsing loses original error. Create `ApiError` type
3. **`settings-pane.tsx:99`** — Type assertion `as ServerSettings` hides potential missing fields

---

## Phase 16E: Dependency & Docker Optimization

**Priority: Medium — reduces image size, improves build reliability**

### 16E.1 — Python Dependency Tightening
- Narrow `langgraph>=0.2.0,<0.4` to `>=0.2.0,<0.3`
- Narrow `langchain-core>=0.3.0,<0.4` to `>=0.3.0,<0.3.1`
- Verify `langchain-community` necessity; remove if only legacy compat
- Extract spaCy model version to `ARG` in Dockerfile for flexibility

### 16E.2 — Docker Improvements
- Add `--no-install-recommends` to `apt-get install` in MCP Dockerfile
- Consider multi-stage build for MCP Dockerfile (separate build/pip-compile from runtime)
- Fix web Dockerfile `apk upgrade` defeating layer cache — move to base image or `--cache-mount`
- Add explicit `CHROMA_ANONYMIZED_TELEMETRY=false` and `LOG_LEVEL=warn` to ChromaDB config

### 16E.3 — CI/CD Hardening
- Add `npm audit --audit-level=high` to frontend CI job
- Add mypy/pyright type checking for Python code
- Increase `--cov-fail-under` from 55% to 70% (actual is 75%)
- Add `--cov-report=xml` for Codecov integration
- Consider adding dependency license scanning (`pip-licenses` or `license-report`)

---

## Phase 16F: Backend Feature Wiring

**Priority: Low-Medium — completes feature set by wiring 8 existing backend endpoints to GUI**

### 16F.1 — Taxonomy CRUD UI
- Wire `POST /taxonomy/domain` — add "Create Domain" button in taxonomy sidebar
- Wire `POST /taxonomy/subcategory` — add "Create Subcategory" button
- Wire `POST /taxonomy/recategorize` — add recategorize action to artifact cards or bulk operations

### 16F.2 — Agent Operations UI
- Wire `POST /agent/triage/batch` — add batch triage button in KB operations
- Wire `POST /agent/memory/archive` — add archive action to memory/conversation UI
- Wire `GET /digest` — add digest view/generation in audit or KB pane

### 16F.3 — Plugin Management UI
- Wire `GET /plugins` + `POST /plugins/{name}/enable|disable` — add plugin management section in settings

### 16F.4 — Settings Wiring
- Wire `enable_model_router` toggle in settings UI to backend persistence

---

## Phase 16G: Content Experience & Testing

**Priority: Low — nice-to-have improvements**

### 16G.1 — Artifact Preview (from original Phase 16, issue E1)
- PDF rendering via pdf.js
- Code syntax highlighting (already have markdown)
- Spreadsheet/table preview
- Email rendering

### 16G.2 — Frontend Component Tests
- 40+ components with 0 tests
- Priority targets: `chat-panel.tsx`, `knowledge-pane.tsx`, `settings-pane.tsx`, `audit-pane.tsx`

### 16G.3 — Conversation Fork/Branch UI (exploratory, from issue D2)

### 16G.4 — Compose Separation (from issue F6)
- Move `cerid-web` out of `src/mcp/docker-compose.yml` into its own compose file

---

## Phase 16H: Documentation Updates

**Priority: Low — keeps docs accurate**

### 16H.1 — Update `docs/ISSUES.md`
- Update status line: "Phase 13 complete" → "Phase 15H complete. 795+ tests."
- Update last-updated date to 2026-03-01
- Mark B1, C1, C2 as resolved in priority list
- Add section H (Unwired Backend Features) and section I (Frontend Cleanup)
- Update priority list at bottom to reflect only open items

### 16H.2 — Update `tasks/todo.md`
- Restructure Phase 16 section with 16A–16H sub-phases from this plan
- Add Phase 17 and 18 forward plan items
- Move completed Phase 15 entries to "Completed Phases" section

### 16H.3 — CHANGELOG.md (nice-to-have)
- Create retroactive changelog from git history

### 16H.4 — Env Var Naming Standardization Doc
- Document prefix policy (e.g., `CERID_*` for app, `LLM_*` for model config)
- Fix `NEO4J_URI` vs `CHROMA_URL` vs `REDIS_URL` inconsistency

---

## Phase 17: Smart Tags & Artifact Quality

**Priority: High — current tag/keyword system produces low-quality, free-form tags that don't support effective taxonomy browsing or user discovery. Summaries are raw text truncations, not useful "what is this" descriptions.**

### Current State (from audit)

**Tags:**
- Generated by AI prompt: "suggest up to 5 descriptive tags (lowercase, hyphenated)"
- Free-form — no validation against taxonomy or controlled vocabulary
- Result: inconsistent tagging across artifacts (e.g., "python" vs "python3" vs "py")
- Local fallback: spaCy NER keyword extraction (named entities, not semantic tags)
- Storage: JSON string on Neo4j Artifact nodes + `TAGGED_WITH` graph edges
- Quality scoring: only counts tags (5+ = 1.0), doesn't assess tag quality

**Summaries:**
- Local mode: raw `text[:200]` truncation — no sentence boundary, no semantic content
- AI mode: "write a 1-sentence summary" in categorization prompt (1,500 char input)
- Curator synopsis: "Write a concise 1-2 sentence synopsis... Do not start with 'This document'"
- Problem: summaries describe content generically, not as a "what is this artifact" statement

**Key files:**
- `src/mcp/utils/metadata.py:147-250` — AI categorization prompt (tags, keywords, summary)
- `src/mcp/agents/curator.py:222-273` — Synopsis generation prompt
- `src/mcp/agents/curator.py:49-60` — Keyword quality scoring (count-based only)
- `src/mcp/config/taxonomy.py:13-44` — TAXONOMY dict (domains + sub_categories, no tag vocabulary)
- `src/web/src/components/kb/artifact-card.tsx` — Tag pill display

### 17A: Smart Tag System

**Goal:** Replace free-form tags with taxonomy-aware smart tags that support discovery, hierarchy browsing, and cross-artifact relationships.

#### 17A.1 — Tag Vocabulary Definition
**File:** `src/mcp/config/taxonomy.py`
- Extend `TAXONOMY[domain]` to include `"tags"` list per domain — a controlled vocabulary
- Example: `"coding": {"tags": ["python", "javascript", "api", "testing", "devops", "database", "frontend", "backend", "cli", "documentation"]}`
- Support custom tag vocabularies via `CERID_CUSTOM_TAGS` env var (JSON)
- Tags are hierarchical: domain → sub_category → tags (3-level taxonomy)

#### 17A.2 — Smart Tag Generation Prompt
**File:** `src/mcp/utils/metadata.py:183-192`
- Rewrite AI categorization prompt to include allowed tag vocabulary per domain:
  ```
  Select tags from ONLY these options for the "{domain}" domain:
  {allowed_tags}
  If none of the allowed tags fit, suggest ONE new tag (lowercase, hyphenated).
  ```
- Validate AI-returned tags against vocabulary; accept exact matches, flag new suggestions for review
- Normalize: lowercase, strip, hyphenate, deduplicate
- Store `tag_source: "vocabulary" | "ai_suggested" | "user"` on each tag for provenance

#### 17A.3 — Tag Quality Scoring Upgrade
**File:** `src/mcp/agents/curator.py:49-60`
- Replace count-based scoring with quality-based:
  - Are tags from the controlled vocabulary? (+weight)
  - Do tags match the artifact's domain/sub_category? (+weight)
  - Are tags too generic (e.g., "document", "file")? (-weight)
  - Tag diversity: covers different aspects of the artifact? (+weight)
- Add `score_tag_quality(tags, domain, sub_category)` function

#### 17A.4 — Tag Suggestion & Auto-Complete UI
**File:** `src/web/src/components/kb/` (new or modified components)
- When editing tags on an artifact, show typeahead from controlled vocabulary
- Group suggestions by domain/sub_category
- Visually distinguish vocabulary tags vs. custom tags
- Show tag usage counts for discovery ("python" used in 47 artifacts)

#### 17A.5 — Tag Analytics & Discovery
- Tag cloud / frequency visualization in KB pane sidebar
- Tag co-occurrence analysis (artifacts with "python" often also have "testing")
- Orphaned tag detection (tags used < 2 times)
- Tag merge suggestions (detect "python3" and "python" as duplicates)

#### 17A.6 — Batch Re-tagging
- Wire existing `POST /taxonomy/recategorize` to re-run smart tagging on existing artifacts
- Option: re-tag all artifacts in a domain, or only those with `tag_source != "user"`
- Progress tracking for large batches

### 17B: Artifact Summary Quality

**Goal:** Replace truncated text blurbs with concise "what is this" statements that help users quickly identify what each artifact contains and why it's useful.

#### 17B.1 — "What Is This" Summary Prompt
**File:** `src/mcp/utils/metadata.py:183-192` (AI categorization prompt)
- Rewrite summary instruction:
  ```
  Write a single-sentence answer to "What is this?" that would help someone
  browsing a knowledge base decide if this artifact is relevant.
  Format: "[Type] covering [key topics] for [purpose/audience]."
  Examples: "Python tutorial covering FastAPI authentication with OAuth2 and JWT."
  "Tax worksheet for 2025 quarterly estimated payments with deduction tracking."
  ```
- Increase input snippet from 1,500 to 3,000 chars for better context

#### 17B.2 — Synopsis Improvement Prompt
**File:** `src/mcp/agents/curator.py:235-239`
- Update curator synopsis prompt to match "what is this" format:
  ```
  Answer the question "What is this?" in one sentence.
  Start with the document type (tutorial, reference, notes, report, etc.)
  then describe key topics and purpose. Be specific, not generic.
  Bad: "A document about Python."
  Good: "Python reference for asyncio patterns including task groups, semaphores, and cancellation."
  ```

#### 17B.3 — Summary Display Enhancement
**File:** `src/web/src/components/kb/artifact-card.tsx`
- Display full summary (not truncated) since "what is this" summaries are short by design
- If summary exceeds 2 lines, truncate with "..." and expand on hover
- Visual distinction between AI-generated and user-edited summaries
- Add inline edit capability for summary text (click to edit, save via API)

#### 17B.4 — Batch Summary Regeneration
- Extend curator agent to regenerate summaries for artifacts with truncated/poor summaries
- Detection criteria: summary < 50 chars, no sentence-ending punctuation, starts with "This document"
- Already partially implemented in `curator.py:209-219` (`_is_truncated_summary()`)

---

## Phase 18: Knowledge Sync & Multi-Computer Parity

**Priority: High — current sync is manual-only, no incremental updates, no conflict detection, no GUI integration.**

### Current State (from audit)

**What works:**
- Full export of all 4 systems (Neo4j, ChromaDB, BM25, Redis) to JSONL files
- Full import with timestamp-based conflict resolution (remote wins if newer)
- SHA-256 deduplication on import
- Auto-import on startup if local DB is empty
- CLI: `scripts/cerid-sync.py export|import|status`
- Default sync dir: `~/Dropbox/cerid-sync`

**What's missing:**
- No incremental/delta sync (always exports everything)
- No automatic scheduled export (must manually run CLI)
- No GUI integration (no sync status, no trigger buttons)
- No conflict detection for simultaneous edits on different machines
- No tombstones (deleted artifacts don't propagate)
- No selective sync (can't filter by domain or date range)
- No sync history/audit trail (manifest overwritten each time)
- No dry-run mode for preview before import
- No file management (only metadata/embeddings sync, not source files)

**Key files:**
- `src/mcp/sync/export.py` (301 lines) — full export logic
- `src/mcp/sync/import_.py` (579 lines) — merge with conflict resolution
- `src/mcp/sync/manifest.py` (129 lines) — manifest format
- `src/mcp/sync/status.py` (140 lines) — local vs. sync comparison
- `src/mcp/sync/_helpers.py` (103 lines) — constants, JSONL I/O
- `src/mcp/sync_check.py` (76 lines) — auto-import on startup
- `scripts/cerid-sync.py` (532 lines) — CLI commands

### 18A: Sync Infrastructure Improvements

#### 18A.1 — Incremental Export
**Files:** `src/mcp/sync/export.py`, `src/mcp/sync/manifest.py`
- Track `last_exported_at` timestamp in manifest
- Neo4j export: add `WHERE a.modified_at > $last_exported` filter
- ChromaDB export: track chunk IDs from last export, only export new/changed
- BM25: compare file checksums, skip unchanged files
- Manifest: preserve history (append to `manifest_history.jsonl` before overwriting)

#### 18A.2 — Tombstone Support
**Files:** `src/mcp/sync/export.py`, `src/mcp/sync/import_.py`, `src/mcp/db/neo4j/artifacts.py`
- When artifacts are deleted locally, record tombstone: `{artifact_id, deleted_at, machine_id}`
- Export tombstones to `tombstones.jsonl`
- On import, process tombstones: remove matching artifacts from all 4 systems
- Tombstone TTL: 90 days, then purge

#### 18A.3 — Conflict Detection & Resolution
**Files:** `src/mcp/sync/import_.py`
- Detect conflict: same `artifact_id`, different `content_hash`, both modified since last sync
- Resolution strategies (user-selectable):
  - **Remote wins** (current default)
  - **Local wins** (preserve local changes)
  - **Keep both** (create copy with `_conflict_` suffix)
  - **Manual** (flag for user review in GUI)
- Store conflict log: `conflicts.jsonl` with both versions for audit

#### 18A.4 — Scheduled Sync
**Files:** `src/mcp/main.py` (APScheduler integration), `src/mcp/config/settings.py`
- `SYNC_AUTO_EXPORT_INTERVAL` env var (default: `0` = disabled, set to minutes)
- APScheduler job: periodic export to sync dir
- `SYNC_AUTO_IMPORT_ON_CHANGE` env var: watch sync dir for manifest changes, auto-import
- Optional: export immediately after each ingestion (`SYNC_EXPORT_ON_INGEST=true`)

#### 18A.5 — Selective Sync
**Files:** `src/mcp/sync/export.py`, `scripts/cerid-sync.py`
- CLI: `cerid-sync.py export --domains coding,finance --since 2026-01-01`
- API: `POST /sync/export` with `domains` and `since` parameters
- Domain-filtered export creates domain-specific subdirectories

### 18B: Sync GUI Integration

#### 18B.1 — Sync Status Dashboard
**Files:** New component `src/web/src/components/settings/sync-status.tsx`
- Show sync dir path, last export/import timestamps, machine ID
- Table: local counts vs. sync counts vs. diff (from `status.py`)
- Per-domain breakdown
- Color indicators: green (in sync), yellow (local ahead), red (conflicts)

#### 18B.2 — Sync Actions in GUI
**Files:** `src/web/src/lib/api.ts`, `src/mcp/routers/` (new sync router)
- Backend: `GET /sync/status`, `POST /sync/export`, `POST /sync/import`
- Frontend buttons: "Export Now", "Import Now", "Check Status"
- Progress indicator for long-running exports/imports
- Conflict resolution dialog when conflicts detected during import

#### 18B.3 — Sync History
- Show previous sync events (from manifest history)
- Per-event: machine, timestamp, artifacts exported/imported, conflicts resolved

### 18C: File Attachment & Drag-Drop Ingestion

**Goal:** Allow users to drop files directly onto the Knowledge Context pane for immediate ingestion into the pipeline.

#### 18C.1 — Drag-and-Drop Zone
**Files:** `src/web/src/components/kb/knowledge-pane.tsx` (or new `drop-zone.tsx`)
- Add drag-and-drop overlay to the Knowledge Context pane
- Visual feedback: border highlight, "Drop to ingest" message, file type icon
- Accept multiple files at once
- Show file list with size, type, and domain selector before upload
- Supported formats: all 30+ types from `config/taxonomy.py:70-87` (PDF, DOCX, code, email, etc.)

#### 18C.2 — Upload Progress & Pipeline Status
**Files:** `src/web/src/components/kb/` (new `upload-progress.tsx`)
- Per-file progress bar during upload
- Pipeline status: "Uploading → Parsing → Categorizing → Chunking → Indexing → Done"
- Error handling: show parse failures with reason, offer retry
- After successful ingestion, auto-refresh KB results to show new artifact

#### 18C.3 — Pre-Upload Options Dialog
**Files:** New component `src/web/src/components/kb/upload-dialog.tsx`
- Before uploading, show dialog with options:
  - **Domain:** auto-detect (AI) or manual select from taxonomy tree
  - **Tags:** pre-populate from filename/extension, allow user edit
  - **Categorization mode:** smart (AI) / manual / pro (Claude)
  - **Storage mode** (see 18D below): extract-only vs. curate files

#### 18C.4 — Backend Upload Enhancement
**File:** `src/mcp/routers/upload.py`
- Support batch upload (multiple files in single request)
- Return structured response with per-file status
- Add `storage_mode` parameter to upload endpoint
- Stream upload progress via SSE for large files

### 18D: Knowledge Storage Options

**Goal:** Give users the choice between extracting data only (metadata + embeddings) or also curating the original source files in a managed folder structure.

#### 18D.1 — Storage Mode Configuration
**Files:** `src/mcp/config/settings.py`, `src/mcp/services/ingestion.py`
- Two modes per artifact:
  - **Extract only** (default): Parse → chunk → embed → store metadata. Original file not managed.
  - **Curate files**: Same as extract, PLUS copy/move source file to managed archive (`~/cerid-archive/{domain}/{sub_category}/`)
- `STORAGE_MODE` env var: `"extract"` (default) or `"curate"`
- Per-upload override via API parameter

#### 18D.2 — Managed File Archive
**Files:** `src/mcp/services/ingestion.py`, new `src/mcp/services/file_manager.py`
- When curate mode enabled:
  - Copy uploaded file to `{ARCHIVE_PATH}/{domain}/{sub_category}/{filename}`
  - Handle naming conflicts: append timestamp suffix
  - Store `archive_path` on Neo4j Artifact node
  - Track file checksum for integrity verification
- File operations: move, rename, delete (with artifact cascade)

#### 18D.3 — Archive Sync
**Files:** `src/mcp/sync/export.py`, `src/mcp/sync/import_.py`
- When curate mode is used, sync must also handle source files:
  - Option A: Sync metadata only (files managed separately via Dropbox/cloud)
  - Option B: Include file checksums in manifest; verify on import
  - Option C: Full file sync (expensive for large archives — defer to Phase 19+)
- Default: Option A (files in `~/cerid-archive/` synced via Dropbox independently)

#### 18D.4 — GUI File Management
**Files:** `src/web/src/components/kb/artifact-card.tsx`
- If artifact has `archive_path`, show "View File" / "Open Folder" actions
- File size and type indicator
- Option to re-ingest from archive (re-parse, re-chunk, re-embed)

---

## Implementation Order (Suggested)

| Phase | Effort | Priority | Dependencies |
|-------|--------|----------|-------------|
| **16A** Security & Infra | 2-3 hours | **Critical** | None |
| **16B** Dead Code Cleanup | 1-2 hours | High | None |
| **16H** Documentation | 1 hour | High | None |
| **17A** Smart Tags | 4-6 hours | **High** | None |
| **17B** Summary Quality | 2-3 hours | **High** | None |
| **16C** Backend Quality | 3-4 hours | Medium | None |
| **16D** Frontend Quality | 3-4 hours | Medium | None |
| **16E** Deps & Docker | 2-3 hours | Medium | 16B (dep removal) |
| **18A** Sync Infrastructure | 6-8 hours | Medium-High | None |
| **18C** Drag-Drop Ingestion | 4-6 hours | Medium-High | None |
| **18B** Sync GUI | 3-4 hours | Medium | 18A |
| **18D** Storage Options | 4-6 hours | Medium | 18C |
| **16F** Feature Wiring | 4-6 hours | Low-Medium | None |
| **16G** Content & Tests | 8-12 hours | Low | None |

**Total estimated effort:** 50-70 hours across all phases.

---

## Files Modified (by phase — additions to previous list)

### 17A (Smart Tags)
- `src/mcp/config/taxonomy.py` — add `tags` vocabulary per domain
- `src/mcp/utils/metadata.py` — rewrite AI categorization prompt for smart tags
- `src/mcp/agents/curator.py` — upgrade tag quality scoring
- `src/web/src/components/kb/artifact-card.tsx` — enhanced tag display
- `src/web/src/components/kb/knowledge-pane.tsx` — tag analytics/cloud
- `src/web/src/components/kb/tag-editor.tsx` (NEW) — typeahead tag editing

### 17B (Summary Quality)
- `src/mcp/utils/metadata.py` — "what is this" summary prompt
- `src/mcp/agents/curator.py` — improved synopsis prompt
- `src/web/src/components/kb/artifact-card.tsx` — summary display/edit

### 18A (Sync Infrastructure)
- `src/mcp/sync/export.py` — incremental export, tombstones, selective sync
- `src/mcp/sync/import_.py` — conflict detection, tombstone processing
- `src/mcp/sync/manifest.py` — history tracking, last_exported_at
- `src/mcp/sync/_helpers.py` — tombstone constants
- `src/mcp/config/settings.py` — sync scheduling env vars
- `src/mcp/main.py` — APScheduler sync jobs

### 18B (Sync GUI)
- `src/mcp/routers/sync.py` (NEW) — sync API endpoints
- `src/web/src/components/settings/sync-status.tsx` (NEW) — sync dashboard
- `src/web/src/lib/api.ts` — sync API functions

### 18C (Drag-Drop Ingestion)
- `src/web/src/components/kb/drop-zone.tsx` (NEW) — drag-and-drop overlay
- `src/web/src/components/kb/upload-dialog.tsx` (NEW) — pre-upload options
- `src/web/src/components/kb/upload-progress.tsx` (NEW) — pipeline status
- `src/web/src/components/kb/knowledge-pane.tsx` — integrate drop zone
- `src/mcp/routers/upload.py` — batch upload, storage_mode param

### 18D (Storage Options)
- `src/mcp/services/file_manager.py` (NEW) — managed file archive
- `src/mcp/services/ingestion.py` — storage_mode parameter
- `src/mcp/config/settings.py` — STORAGE_MODE env var
- `src/web/src/components/kb/artifact-card.tsx` — file actions

---

## Verification

For each sub-phase:
1. `docker compose -f src/mcp/docker-compose.yml build mcp-server` — 0 Python errors
2. `docker compose -f src/mcp/docker-compose.yml build cerid-web` — 0 TS errors
3. Run `pytest` — all tests pass, coverage ≥ 55%
4. Run `npm run lint && npm run typecheck` in src/web/ — 0 errors
5. Functional: launch full stack with `./scripts/start-cerid.sh --build`, verify all panes load

**Phase-specific verification:**
- **17A:** Ingest a test file → verify tags come from controlled vocabulary → verify tag editing typeahead
- **17B:** Ingest a file → verify summary reads as "what is this" → run curator batch → verify improved summaries
- **18A:** Export on Machine A → modify manifest timestamp → import on same machine → verify incremental only
- **18B:** Open settings → verify sync status panel → click Export → verify progress and completion
- **18C:** Drag PDF onto KB pane → verify upload dialog → verify pipeline progress → verify artifact appears
- **18D:** Upload with curate mode → verify file copied to archive → verify `archive_path` on artifact card
