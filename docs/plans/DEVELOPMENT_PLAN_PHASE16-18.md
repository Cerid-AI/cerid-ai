# Comprehensive Development Plan — Phases 17-21

## Context

Phase 16A-H is complete. All phases (0-16) are done: 811+ Python tests, 130+ frontend tests, full React GUI, 8 backend agents, 17 MCP tools, streaming verification, accuracy analytics, user feedback. This plan consolidates all open/deferred items, new requirements (iPad compatibility, LAN access, demo deployment, expert orchestration/validation), and previously planned work (smart tags, knowledge sync) into a prioritized roadmap. Research was conducted on production RAG best practices, Caddy reverse proxy, and Cloudflare Tunnels.

**Current state:** Phase 16A-H complete. 5-step Docker Compose startup. CI/CD: 6-job pipeline. Next: Phase 17 (iPad & Responsive Touch UX).

---

## Open Items Carried Forward

| ID | Description | Original Phase | Carried To |
|----|-------------|----------------|------------|
| D2 | Conversation fork/branch UI | 16G | Deferred |
| -- | Frontend component tests (40+ untested) | 16G | Deferred |
| -- | CHANGELOG.md creation | 16H | Deferred |
| -- | Env var naming standardization doc | 16H | Deferred |
| -- | mypy/pyright type checking | 16E | Deferred |

---

## Phase 17: iPad & Responsive Touch UX

**Priority: Critical — iPad is the primary mobile access path; current UI is broken on touch devices.**
**Effort: 12-17 hours**
**Dependencies: None (standalone frontend work).**

### Current Gaps (from codebase exploration)

- **4 `group-hover:opacity-100` patterns** hide critical buttons (copy, delete conversation, add sub-category) on touch devices where hover does not exist
- **Only 18 responsive class occurrences** across 9 files (most layout is JS-controlled with hardcoded pixel breakpoints)
- **2 hardcoded breakpoints:** 768px in `app-layout.tsx:15` and 1024px in `chat-panel.tsx:63`
- **Zero `@media (hover: none)` rules** — no touch detection at all
- **40 `hover:` class usages** across 18 files — most are fine for tap-feedback but some guard visibility
- **Tooltips on collapsed sidebar** use Radix Tooltip which requires hover trigger

### 17A: Touch-Visibility Fixes (4-6 hrs)

Fix all hover-dependent interactive elements so they are visible/accessible on touch devices.

#### 17A.1 — Global Touch CSS
**File:** `src/web/src/index.css`
- Add `@media (hover: none)` block to override `group-hover:opacity-100` patterns on touch devices
- Override `.opacity-0.transition-opacity` patterns used for progressive disclosure
- Add `.touch-visible` utility class for opt-in always-visible behavior

#### 17A.2 — Message Copy Button
**File:** `src/web/src/components/chat/message-bubble.tsx`
- **Line 37:** Code block copy button uses `opacity-0 group-hover:opacity-100` — invisible on iPad
- **Line 171:** Message copy button uses same pattern
- Fix: Add touch-device CSS override from 17A.1

#### 17A.3 — Conversation Delete Button
**File:** `src/web/src/components/chat/conversation-list.tsx`
- **Line 46:** Trash icon uses `opacity-0 group-hover:opacity-100` — impossible to delete conversations on iPad
- Fix: Same approach as 17A.2

#### 17A.4 — Taxonomy Tree Add Sub-Category Button
**File:** `src/web/src/components/kb/taxonomy-tree.tsx`
- **Line 232:** Plus button for adding sub-categories uses `opacity-0 group-hover:opacity-100`
- Fix: Same approach as 17A.2

#### 17A.5 — Split Pane Separator Feedback
**File:** `src/web/src/components/layout/split-pane.tsx`
- **Line 23:** Separator uses `hover:bg-primary/20` for visual resize feedback
- React Resizable Panels supports touch natively, but separator needs visible grab handle on touch
- Fix: Add wider touch target (min 44px for Apple HIG) and visible dots/lines indicator via `@media (pointer: coarse)`

**Verification:** Open React GUI on iPad Safari. Tap message bubbles — copy button must be visible. Navigate to Knowledge pane, expand taxonomy — add sub-category button must be visible. Resize split pane by touch-dragging separator.

### 17B: Tablet Layout Optimization (6-8 hrs)

#### 17B.1 — Responsive Sidebar
**File:** `src/web/src/components/layout/app-layout.tsx`
- **Line 15:** `window.innerWidth < 768` — iPad portrait is 768px exactly (edge case)
- Fix: Change to 1024px threshold. Sidebar should auto-collapse on both iPad orientations

**File:** `src/web/src/components/layout/sidebar.tsx`
- When collapsed on touch devices, tooltips (line 96: `TooltipContent side="right"`) won't show
- Replace with labels below icons (like a mobile tab bar) or use Radix Popover triggered by tap

#### 17B.2 — KB Pane Responsive Behavior
**File:** `src/web/src/components/chat/chat-panel.tsx`
- **Line 63:** `window.innerWidth >= 1024` controls KB pane visibility. On iPad, KB pane is never shown by default
- Fix: On tablet (768-1024px), show KB as a bottom drawer/sheet (Radix Sheet). Keep toggle button functional — tap to open drawer overlay
- For landscape iPad (1024px+), keep current split-pane behavior

#### 17B.3 — Chat Toolbar Touch Optimization
**File:** `src/web/src/components/chat/chat-panel.tsx`
- **Lines 281-369:** Toolbar has 6 icon buttons + model select in a row. On iPad portrait this will be cramped
- Fix: Group toolbar buttons into an overflow menu (ellipsis) when width < 768px. Show only essential buttons (new chat, KB toggle, model select) inline

#### 17B.4 — Touch-Friendly Targets
**Files:** Multiple components
- Apple HIG requires 44x44pt minimum touch targets
- `Button size="xs"` (in `artifact-card.tsx` lines 177-213) produces ~24px buttons — too small
- Fix: On touch devices, ensure minimum tap target of 44px via `@media (pointer: coarse)` padding increase
- Target files: `artifact-card.tsx`, `taxonomy-tree.tsx`, `conversation-list.tsx`

**Verification:** iPad Safari portrait: sidebar collapsed with identifiable icons. Chat toolbar not overflowing. KB pane opens as drawer from bottom. All buttons tappable without mis-taps.

### 17C: iPad-Specific Polish (2-3 hrs)

#### 17C.1 — Safe Area Insets
**File:** `src/web/src/index.css`
- Add `env(safe-area-inset-*)` padding for iPads with rounded corners
- Apply to status bar and chat input areas

#### 17C.2 — Input Zoom Prevention
**File:** `src/web/src/index.css`
- iOS Safari zooms on input focus when font-size < 16px
- Chat input and taxonomy inline inputs use `text-sm` (14px) and `text-[11px]` — both trigger zoom
- Fix: Set minimum 16px on focus for iOS via `@supports (-webkit-touch-callout: none)`

#### 17C.3 — Orientation Change Handling
**File:** `src/web/src/components/layout/app-layout.tsx`
- Current `matchMedia` handles width changes but orientation change on iPad may not trigger consistently
- Fix: Add `orientationchange` event listener as backup

**Verification:** Rotate iPad between portrait and landscape. UI adapts: sidebar collapses/expands, KB pane switches between drawer and side panel. No input zoom. Content respects safe area insets.

---

## Phase 18: Network Access & Demo Deployment

**Priority: High — enables iPad access from LAN and sharing demos externally.**
**Effort: 8-11 hours**
**Dependencies: Phase 17 (responsive UI should work before remote access).**

### 18A: LAN Hostname Configuration (2-3 hrs)

#### 18A.1 — Dynamic Host Detection
**File:** `scripts/start-cerid.sh`
- Add LAN IP detection after stack startup (`ipconfig getifaddr en0` on macOS, `hostname -I` on Linux)
- Display LAN access URLs alongside localhost URLs in the "Access URLs" section
- Add `CERID_HOST` env var support: if set, use it; if not, auto-detect LAN IP

#### 18A.2 — Runtime URL Injection
**File:** `src/web/docker-entrypoint.sh`
- Already supports `VITE_MCP_URL` override at container startup
- Change default to use `CERID_HOST` env var: `http://${CERID_HOST:-localhost}:8888`
- Same for `VITE_BIFROST_URL`: when `CERID_HOST` is set, use direct URL instead of nginx proxy path

**File:** `src/web/docker-compose.yml`
- Add `environment:` section passing through `CERID_HOST`, `VITE_MCP_URL`, `VITE_BIFROST_URL`

#### 18A.3 — CORS Verification
**File:** `src/mcp/main.py`
- Current: `CORS_ORIGINS` defaults to `*` — already permissive
- Verify MCP responds to requests from `http://192.168.x.x:3000` origin
- Check Bifrost CORS in `stacks/bifrost/config.yaml`

#### 18A.4 — Documentation
**File:** `docs/OPERATIONS.md`
- Add "LAN Access" section: set `CERID_HOST=192.168.x.x` in `.env`, rebuild web container, access from iPad

**Verification:** Set `CERID_HOST=<lan-ip>` in `.env`. Run `start-cerid.sh --build`. LAN IP URLs shown in output. Open `http://<lan-ip>:3000` on iPad. Chat works. KB queries return results. No CORS errors.

### 18B: Caddy Reverse Proxy — Local HTTPS (3-4 hrs)

#### 18B.1 — Caddy Docker Service
**Files:** New `stacks/gateway/docker-compose.yml`, new `stacks/gateway/Caddyfile`
- Caddy container on the `llm-network` bridge
- Routes: `/` → `cerid-web:80`, `/api/mcp/` → `ai-companion-mcp:8888`, `/api/bifrost/` → `bifrost:8080`
- `tls internal` directive for auto-generated self-signed certificates
- Listens on ports 80 (redirect) and 443
- Single entry point: `https://cerid.local` or `https://192.168.x.x`

#### 18B.2 — mDNS / Local DNS
- macOS already broadcasts `.local` hostnames via Bonjour — iPad on same network can resolve `Justins-Mac-Pro.local`
- Document: trusting Caddy's self-signed cert on iPad (Settings → install profile)

#### 18B.3 — Startup Script Integration
**File:** `scripts/start-cerid.sh`
- Add optional `[6/6] Starting Gateway (Caddy)...` step
- Only start if `CERID_GATEWAY=true` env var is set
- Display `https://` URLs when gateway is active

**Verification:** Set `CERID_GATEWAY=true`. Run `start-cerid.sh --build`. Navigate to `https://<hostname>.local` on iPad. Self-signed cert warning once. After accepting, full HTTPS access works.

### 18C: Cloudflare Tunnel — Public Demos (3-4 hrs)

#### 18C.1 — Cloudflare Tunnel Container
**Files:** New `stacks/tunnel/docker-compose.yml`, new `stacks/tunnel/config.yml`
- `cloudflare/cloudflared` container on `llm-network`
- `CLOUDFLARE_TUNNEL_TOKEN` env var in `.env`
- Routes configured for cerid-web and MCP API

#### 18C.2 — Access Policy
- Email-based one-time-pin authentication via Cloudflare Access
- Zero-trust: no port forwarding, no public IP exposure
- Automatic HTTPS with real Cloudflare certificates

#### 18C.3 — Startup Script Integration
**File:** `scripts/start-cerid.sh`
- Add optional tunnel step (only if `CLOUDFLARE_TUNNEL_TOKEN` is set)
- Display public URL when tunnel is active

#### 18C.4 — Documentation
**File:** `docs/OPERATIONS.md`
- Add "Public Demo Access" section: Cloudflare Tunnel setup, token creation, access policies

**Verification:** Set `CLOUDFLARE_TUNNEL_TOKEN` in `.env`. Run `start-cerid.sh`. Navigate to public URL from external device. Cloudflare Access gate appears. After email OTP, full access works.

---

## Phase 19: Expert Orchestration & Validation

**Priority: Medium-High — addresses reliability, chunking quality, and evaluation gaps that directly impact RAG accuracy.**
**Effort: 17-24 hours**
**Dependencies: None (backend-only work, can parallelize with Phase 17/18).**

Research basis: Production RAG best practices from Orkes, RAGAS, Meilisearch, and Stanford AI Lab findings on chunking quality and hallucination reduction.

### 19A: Circuit Breakers & Resilience (4-6 hrs)

#### 19A.1 — Circuit Breaker Utility
**File:** New `src/mcp/utils/circuit_breaker.py`
- `CircuitBreaker(failure_threshold=3, recovery_timeout=60)`
- States: closed (normal), open (fail-fast), half-open (probe)
- Async-compatible for use in agent code

#### 19A.2 — Bifrost Reranking Fallback
**File:** `src/mcp/agents/query_agent.py`
- **Line 431:** If Bifrost is down, reranking waits full timeout before fallback
- Wrap in circuit breaker: repeated failures trigger immediate fallback to embedding sort
- Log fallback frequency for monitoring

#### 19A.3 — External Verification Resilience
**File:** `src/mcp/agents/hallucination.py`
- **Lines 451, 959:** External verification calls
- Circuit breaker: 3 failures in 60s → mark pending claims as "unverified" immediately
- Complements existing semaphore (`_ext_verify_semaphore`) with failure-rate protection

#### 19A.4 — Exponential Backoff
**File:** `src/mcp/deps.py`
- **Lines 31-44:** Current `_retry()` uses linear backoff (fixed `delay=2.0`)
- Replace with: `delay * (2 ** attempt) + random(0, 1)` (exponential + jitter)
- Apply to all agent httpx calls

#### 19A.5 — Curator & Memory Agent Resilience
**Files:** `src/mcp/agents/curator.py`, `src/mcp/agents/memory.py`
- Both make Bifrost calls without circuit breaker protection
- Wrap in shared circuit breaker instance

**Verification:** Stop Bifrost container. Make query via React GUI. Query returns results (embedding-only) within 2s instead of hanging for timeout. Verification marks claims as "unverified" quickly. Restart Bifrost. Circuit breaker recovers within 60s.

### 19B: Distributed Tracing (3-4 hrs)

#### 19B.1 — Request ID Propagation
**File:** `src/mcp/middleware/request_id.py`
- Already generates X-Request-ID. Ensure all outbound httpx calls include it
- Create `get_request_id()` utility via `contextvars`

#### 19B.2 — Agent Trace Logging
**Files:** All agents in `src/mcp/agents/`
- Add request_id to all logger calls in agent execution paths
- Include in Redis `log_event()` audit entries

#### 19B.3 — Frontend Correlation
**File:** `src/web/src/lib/api.ts`
- Generate X-Request-ID header in frontend API calls

**Verification:** Make a query. Check MCP logs. Same request ID appears in middleware, agent, and Bifrost call logs.

### 19C: Chunking Quality Improvements (4-6 hrs)

Research finding: "80% of RAG failures trace back to chunking decisions" — CDC policy RAG study, 2025.

#### 19C.1 — Semantic Chunking
**File:** `src/mcp/utils/chunker.py`
- Current: pure token-count chunking with fixed overlap (lines 22-59)
- Add `chunk_text_semantic()`:
  1. Split on paragraph boundaries (`\n\n`) first
  2. Group paragraphs into chunks up to `max_tokens`
  3. Preserve sentence boundaries (never split mid-sentence)
  4. Fall back to token chunking for massive paragraphs
- `CHUNKING_MODE` env var: `"token"` (current) or `"semantic"` (new default)

#### 19C.2 — Contextual Headers
**File:** `src/mcp/utils/chunker.py`
- Prepend document context to each chunk: `"Source: {filename} | Domain: {domain} | Section: {heading}"`
- Extract section headings from markdown (`#` lines), HTML (`<h1>`-`<h6>`), PDF (pdfplumber font-size heuristic)

#### 19C.3 — Structure-Aware PDF Chunking
**File:** `src/mcp/parsers/pdf.py`
- Keep tables with surrounding paragraph context (paragraph before table describes its content)
- Pass section heading context through to the chunker

**Verification:** Ingest a multi-section PDF. Verify chunks respect paragraph boundaries. Verify each chunk has contextual header. Run eval harness — NDCG should not decrease.

### 19D: Evaluation Enhancement (3-4 hrs)

#### 19D.1 — Latency Metrics
**File:** `src/mcp/eval/harness.py`
- Add `latency_ms` to `EvalResult`. Track wall-clock time per query
- Report P50, P95, P99 in aggregate

#### 19D.2 — Per-Domain Breakdowns
**File:** `src/mcp/eval/harness.py`
- Group results by domain. Identify domains with lower retrieval quality

#### 19D.3 — Cost Tracking
**File:** `src/mcp/eval/harness.py`
- Track token usage for LLM reranking. Report cost-per-query per pipeline config

#### 19D.4 — A/B Comparison
**File:** `src/mcp/eval/harness.py`
- `compare_pipelines()` — run same benchmark on two configs
- Report per-metric improvement/regression with percentage change
- Paired t-test for statistical significance (n >= 30)

**Verification:** Run eval with `token` vs `semantic` chunking. Report shows per-domain NDCG, latency distribution, cost comparison.

### 19E: Adaptive Quality Feedback (3-4 hrs)

#### 19E.1 — Click-Through Signal
**File:** `src/web/src/hooks/use-kb-context.ts`
- When user clicks "Inject" on an artifact, log positive signal
- New endpoint: `POST /artifacts/{id}/feedback` with `signal: "inject" | "dismiss"`

#### 19E.2 — Quality Score Update
**File:** `src/mcp/agents/curator.py`
- Positive feedback: bump `quality_score` by 0.02 per injection
- Negative feedback (dismiss): decrease by 0.01. Clamp to [0.0, 1.0]

#### 19E.3 — Retrieval Feedback Logging
**File:** `src/mcp/agents/query_agent.py`
- Log which results were injected vs ignored. Future: auto-tune retrieval weights

**Verification:** Inject an artifact 5 times. Quality score increases. It ranks higher in subsequent queries.

---

## Phase 20: Smart Tags & Artifact Quality

**Priority: Medium — improves knowledge discovery and browsing.**
**Effort: 6-9 hours**
**Dependencies: None.**

*Renumbered from previous Phase 17. Content preserved.*

### 20A: Smart Tag System (4-6 hrs)

#### 20A.1 — Tag Vocabulary Definition
**File:** `src/mcp/config/taxonomy.py`
- Extend `TAXONOMY[domain]` to include `"tags"` list per domain — controlled vocabulary
- Example: `"coding": {"tags": ["python", "javascript", "api", "testing", "devops", "database", "frontend", "backend", "cli", "documentation"]}`
- Support custom vocabularies via `CERID_CUSTOM_TAGS` env var (JSON)

#### 20A.2 — Taxonomy-Constrained Tag Generation
**File:** `src/mcp/utils/metadata.py`
- Rewrite AI categorization prompt to include allowed tag vocabulary per domain
- Validate AI-returned tags against vocabulary; accept exact matches, flag new suggestions
- Normalize: lowercase, strip, hyphenate, deduplicate
- Store `tag_source: "vocabulary" | "ai_suggested" | "user"` for provenance

#### 20A.3 — Tag Quality Scoring Upgrade
**File:** `src/mcp/agents/curator.py`
- Replace count-based scoring (lines 49-60) with quality-based:
  - Vocabulary match (+weight), domain relevance (+weight), generic tag penalty (-weight), diversity bonus (+weight)

#### 20A.4 — Tag Typeahead UI
**File:** New `src/web/src/components/kb/tag-editor.tsx`
- Typeahead from controlled vocabulary when editing tags
- Group by domain/sub_category, show usage counts

#### 20A.5 — Tag Analytics & Discovery
**File:** `src/web/src/components/kb/knowledge-pane.tsx`
- Tag cloud/frequency visualization
- Orphaned tag detection (< 2 uses)
- Tag merge suggestions (detect "python3" and "python" as duplicates)

#### 20A.6 — Batch Re-tagging
- Endpoint to re-run smart tagging on existing artifacts per domain
- Progress tracking for large batches

### 20B: Artifact Summary Quality (2-3 hrs)

#### 20B.1 — "What Is This" Summary Prompt
**File:** `src/mcp/utils/metadata.py`
- Rewrite summary instruction: `"Write a single-sentence answer to 'What is this?'"`
- Increase input snippet from 1,500 to 3,000 chars

#### 20B.2 — Synopsis Improvement
**File:** `src/mcp/agents/curator.py`
- Update curator synopsis prompt to match "what is this" format

#### 20B.3 — Summary Display Enhancement
**File:** `src/web/src/components/kb/artifact-card.tsx`
- Display full summary (short by design), inline edit, AI vs user visual distinction

#### 20B.4 — Batch Summary Regeneration
- Extend curator to regenerate summaries for truncated/poor summaries
- Detection: summary < 50 chars, no sentence-ending punctuation, starts with "This document"

**Verification:** Ingest test file → tags from controlled vocabulary → summary reads as "what is this" answer → typeahead shows vocabulary suggestions.

---

## Phase 21: Knowledge Sync & Multi-Computer Parity

**Priority: Medium — current sync is manual-only, no incremental updates, no GUI.**
**Effort: 17-24 hours**
**Dependencies: None.**

*Renumbered from previous Phase 18. Content preserved.*

### 21A: Sync Infrastructure Improvements (6-8 hrs)

#### 21A.1 — Incremental Export
**Files:** `src/mcp/sync/export.py`, `src/mcp/sync/manifest.py`
- Track `last_exported_at` in manifest
- Neo4j: `WHERE a.modified_at > $last_exported` filter
- ChromaDB: track chunk IDs from last export, export delta only

#### 21A.2 — Tombstone Support
**Files:** `src/mcp/sync/export.py`, `src/mcp/sync/import_.py`
- Record tombstones on deletion: `{artifact_id, deleted_at, machine_id}`
- Process tombstones on import. 90-day TTL

#### 21A.3 — Conflict Detection & Resolution
**File:** `src/mcp/sync/import_.py`
- Detect: same artifact_id, different content_hash, both modified since last sync
- Strategies: remote wins (default), local wins, keep both, manual review

#### 21A.4 — Scheduled Sync
**Files:** `src/mcp/main.py`, `src/mcp/config/settings.py`
- `SYNC_AUTO_EXPORT_INTERVAL` env var (APScheduler job)
- Optional: export after each ingestion (`SYNC_EXPORT_ON_INGEST=true`)

#### 21A.5 — Selective Sync
**File:** `src/mcp/sync/export.py`, `scripts/cerid-sync.py`
- CLI: `cerid-sync.py export --domains coding,finance --since 2026-01-01`
- API: `POST /sync/export` with domains and since parameters

### 21B: Sync GUI Integration (3-4 hrs)

#### 21B.1 — Sync Status Dashboard
**File:** New `src/web/src/components/settings/sync-status.tsx`
- Show sync dir path, timestamps, machine ID, per-domain counts vs diff
- Color indicators: green (in sync), yellow (local ahead), red (conflicts)

#### 21B.2 — Sync Actions in GUI
**Files:** `src/web/src/lib/api.ts`, new `src/mcp/routers/sync.py`
- Backend: `GET /sync/status`, `POST /sync/export`, `POST /sync/import`
- Frontend: Export/Import/Status buttons, progress indicator, conflict resolution dialog

#### 21B.3 — Sync History
- Show previous sync events from manifest history
- Per-event: machine, timestamp, counts, conflicts resolved

### 21C: File Attachment & Drag-Drop Ingestion (4-6 hrs)

#### 21C.1 — Drag-and-Drop Zone
**File:** `src/web/src/components/kb/knowledge-pane.tsx` or new `drop-zone.tsx`
- Drag-and-drop overlay on KB pane. Touch-compatible (from Phase 17 work)
- Visual feedback: border highlight, "Drop to ingest" message, file type icon

#### 21C.2 — Upload Progress & Pipeline Status
**File:** New `src/web/src/components/kb/upload-progress.tsx`
- Per-file: Uploading → Parsing → Categorizing → Chunking → Indexing → Done
- Error handling with retry option

#### 21C.3 — Pre-Upload Options Dialog
**File:** New `src/web/src/components/kb/upload-dialog.tsx`
- Domain selection (AI auto-detect or manual from taxonomy tree)
- Tag pre-population from filename/extension
- Categorization mode: smart/manual/pro

#### 21C.4 — Backend Upload Enhancement
**File:** `src/mcp/routers/upload.py`
- Batch upload (multiple files, single request)
- Structured per-file status response

### 21D: Knowledge Storage Options (4-6 hrs)

#### 21D.1 — Storage Mode Configuration
**Files:** `src/mcp/config/settings.py`, `src/mcp/services/ingestion.py`
- **Extract only** (default): Parse → chunk → embed → store metadata
- **Curate files**: Same + copy source file to `~/cerid-archive/{domain}/{sub_category}/`
- `STORAGE_MODE` env var, per-upload override via API

#### 21D.2 — Managed File Archive
**File:** New `src/mcp/services/file_manager.py`
- Copy to managed location, handle naming conflicts (timestamp suffix)
- Store `archive_path` on Neo4j Artifact node
- Track file checksum for integrity

#### 21D.3 — GUI File Management
**File:** `src/web/src/components/kb/artifact-card.tsx`
- "View File" / "Open Folder" actions when `archive_path` exists
- File size and type indicator
- Re-ingest from archive option

**Verification:** Export on Machine A → import on same machine → incremental only. Sync status shows counts. Drag PDF onto KB pane → upload dialog → pipeline progresses → artifact appears.

---

## Deferred / Future

> **Archived 2026-03-13.** Items originally deferred post-Phase 21. Seven completed in
> later phases; seven remain open (tracked in [`docs/ISSUES.md`](../ISSUES.md) § E).

| Item | Effort | Status |
|------|--------|--------|
| D2: Conversation fork/branch UI | 40-60 hrs | ❌ Dropped (Phase 31) |
| Frontend component tests (40+ untested) | 20-30 hrs | ✅ Phase 22 (now 418 tests) |
| CHANGELOG.md | 2-3 hrs | ✅ Phase 22 |
| Env var naming standardization doc | 1-2 hrs | ✅ Phase 22 (`docs/ENV_CONVENTIONS.md`) |
| mypy/pyright type checking | 4-6 hrs | ✅ Phase 22 (mypy in CI) |
| Self-RAG validation loop | 8-12 hrs | ✅ Phase 22 (`agents/self_rag.py`) |
| Codecov XML reports | 1 hr | 🔲 Open — see ISSUES.md E1 |
| Dependency license scanning | 1-2 hrs | 🔲 Open — see ISSUES.md E2 |
| Additional ReDoS regex audit | 2-3 hrs | 🔲 Open — see ISSUES.md E3 |
| Plugin management UI | 4-6 hrs | 🔲 Open — see ISSUES.md E4 |
| Digest view/generation | 3-4 hrs | 🔲 Open — see ISSUES.md E5 |
| Batch triage UI | 4-6 hrs | 🔲 Open — see ISSUES.md E6 |
| MongoDB auth | 2-3 hrs | ✅ Phase 23 (service deprecated Phase 27) |
| Multi-stage MCP Dockerfile | 2-3 hrs | 🔲 Open — see ISSUES.md E7 |

---

## Implementation Order

| Phase | Effort | Priority | Dependencies |
|-------|--------|----------|-------------|
| **17A** Touch Visibility | 4-6 hrs | **Critical** | None |
| **17B** Tablet Layout | 6-8 hrs | **Critical** | 17A |
| **17C** iPad Polish | 2-3 hrs | **High** | 17B |
| **18A** LAN Config | 2-3 hrs | **High** | 17A (test on iPad) |
| **18B** Caddy Gateway | 3-4 hrs | **High** | 18A |
| **18C** Cloudflare Tunnel | 3-4 hrs | Medium | 18A |
| **19A** Circuit Breakers | 4-6 hrs | **High** | None (parallel) |
| **19B** Distributed Tracing | 3-4 hrs | Medium | 19A |
| **19C** Chunking Quality | 4-6 hrs | **High** | None (parallel) |
| **19D** Eval Enhancement | 3-4 hrs | Medium | 19C |
| **19E** Adaptive Quality | 3-4 hrs | Medium | None |
| **20A** Smart Tags | 4-6 hrs | Medium | None |
| **20B** Summary Quality | 2-3 hrs | Medium | 20A |
| **21A** Sync Infrastructure | 6-8 hrs | Medium | None |
| **21B** Sync GUI | 3-4 hrs | Medium | 21A |
| **21C** Drag-Drop | 4-6 hrs | Medium | None |
| **21D** Storage Options | 4-6 hrs | Low-Medium | 21C |

**Total estimated effort:** 65-95 hours across all phases.

---

## Files Modified (by phase)

### Phase 17 (iPad & Touch)
- `src/web/src/index.css` — touch CSS rules, safe area, zoom prevention
- `src/web/src/components/chat/message-bubble.tsx` — copy button touch visibility
- `src/web/src/components/chat/conversation-list.tsx` — delete button touch visibility
- `src/web/src/components/kb/taxonomy-tree.tsx` — add sub-category touch visibility
- `src/web/src/components/layout/split-pane.tsx` — separator touch target
- `src/web/src/components/layout/app-layout.tsx` — breakpoint + orientation
- `src/web/src/components/layout/sidebar.tsx` — collapsed touch-friendly labels
- `src/web/src/components/chat/chat-panel.tsx` — KB drawer, toolbar overflow

### Phase 18 (Network & Deployment)
- `scripts/start-cerid.sh` — LAN IP detection, gateway/tunnel steps
- `src/web/docker-entrypoint.sh` — CERID_HOST injection
- `src/web/docker-compose.yml` — environment passthrough
- `stacks/gateway/docker-compose.yml` (NEW) — Caddy reverse proxy
- `stacks/gateway/Caddyfile` (NEW) — routing rules
- `stacks/tunnel/docker-compose.yml` (NEW) — Cloudflare tunnel
- `stacks/tunnel/config.yml` (NEW) — tunnel routing
- `docs/OPERATIONS.md` — LAN access + public demo docs

### Phase 19 (Orchestration & Validation)
- `src/mcp/utils/circuit_breaker.py` (NEW) — async circuit breaker
- `src/mcp/agents/query_agent.py` — circuit breaker wrapping
- `src/mcp/agents/hallucination.py` — circuit breaker wrapping
- `src/mcp/agents/curator.py` — circuit breaker + quality feedback
- `src/mcp/agents/memory.py` — circuit breaker wrapping
- `src/mcp/deps.py` — exponential backoff + jitter
- `src/mcp/middleware/request_id.py` — propagation utility
- `src/mcp/utils/chunker.py` — semantic chunking + headers
- `src/mcp/parsers/pdf.py` — structure-aware chunking
- `src/mcp/eval/harness.py` — latency, cost, A/B comparison
- `src/web/src/lib/api.ts` — X-Request-ID header
- `src/web/src/hooks/use-kb-context.ts` — injection feedback signal

### Phase 20 (Smart Tags)
- `src/mcp/config/taxonomy.py` — tag vocabularies per domain
- `src/mcp/utils/metadata.py` — constrained tag generation + summary prompt
- `src/mcp/agents/curator.py` — tag quality scoring + synopsis prompt
- `src/web/src/components/kb/tag-editor.tsx` (NEW) — typeahead
- `src/web/src/components/kb/knowledge-pane.tsx` — tag analytics
- `src/web/src/components/kb/artifact-card.tsx` — summary display

### Phase 21 (Sync & Ingestion)
- `src/mcp/sync/export.py` — incremental, tombstones, selective
- `src/mcp/sync/import_.py` — conflict detection, tombstone processing
- `src/mcp/sync/manifest.py` — history tracking
- `src/mcp/config/settings.py` — sync scheduling vars
- `src/mcp/main.py` — APScheduler sync jobs
- `src/mcp/routers/sync.py` (NEW) — sync API endpoints
- `src/mcp/routers/upload.py` — batch upload
- `src/mcp/services/file_manager.py` (NEW) — managed archive
- `src/mcp/services/ingestion.py` — storage_mode parameter
- `src/web/src/components/settings/sync-status.tsx` (NEW)
- `src/web/src/components/kb/drop-zone.tsx` (NEW)
- `src/web/src/components/kb/upload-dialog.tsx` (NEW)
- `src/web/src/components/kb/upload-progress.tsx` (NEW)
- `src/web/src/lib/api.ts` — sync API functions

---

## Verification (All Phases)

For each sub-phase:
1. `docker compose -f src/mcp/docker-compose.yml build mcp-server` — 0 Python errors
2. `docker compose -f src/web/docker-compose.yml build cerid-web` — 0 TS errors
3. `cd src/web && npx vitest run` — all frontend tests pass
4. Python tests via Docker — all pass, coverage >= 70%
5. Functional: `./scripts/start-cerid.sh --build` — all panes load

**Phase-specific verification:**
- **17:** iPad Safari: copy buttons visible, sidebar collapsed with icons, KB as drawer, no input zoom
- **18:** `CERID_HOST=<lan-ip>` → iPad accesses `http://<ip>:3000`; Caddy: `https://<hostname>.local`; Cloudflare: public URL with OTP
- **19:** Stop Bifrost → query returns in 2s (circuit breaker); multi-section PDF → paragraph-aware chunks; eval shows latency/cost
- **20:** Ingest file → controlled vocabulary tags → "what is this" summary → typeahead works
- **21:** Export/import → incremental only; drag PDF → pipeline completes → artifact appears
