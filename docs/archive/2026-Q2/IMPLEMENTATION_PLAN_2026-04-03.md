# Cerid AI Beta Test — Implementation Plan

**Date:** 2026-04-03
**Source:** `docs/BETA_TEST_SPEC_2026-04-03.md` (50 items, 3 phases)
**Open Questions:** Resolved via `/tmp/open-questions-research.md`
**Codebase:** cerid-ai v0.81 (post-audit, post-sync)

---

## Approved Decisions (Open Questions)

| # | Question | Decision |
|---|----------|----------|
| Q1 | Bifrost visibility | Hide from community tier, run silently as fallback. Keep in Docker startup. |
| Q2 | Verification cost tooltip | "Claims will try to first verify against your KB at no cost. External verification uses efficient models. Expert mode uses premium models which vary widely but are less token-efficient." |
| Q3 | Custom API auth | API key only — Bearer header, custom header, or query param. No OAuth. |
| Q4 | KB quality scoring | Domain-adaptive replacement + user star/evergreen pinning. Starred items stay evergreen but don't recommend out of context. |
| Q5 | External results | Keep ephemeral default. Add "Save to KB" buttons. Smart suggestion after 3+ identical retrievals. |

---

## Dependency Map

```
Phase 1 (P0) — Critical path, blocks beta restart
═══════════════════════════════════════════════════
WP1 [PDF Drag-Drop & Ingestion] ──┐
WP2 [Test Buttons & Provider Detection] ──┤── WP5 [Review & Apply Fix]
WP3 [Remove Dev Tier Switch] │
WP4 [KB Quality Scoring & Preview] ──────┘

Phase 2 (P1) — Usability & polish
═══════════════════════════════════════════════════
WP5 ──► WP6 [Wizard Structure Overhaul] ──► WP8 [Chat Tooltips & UX]
         WP7 [Custom LLM & Credits]
         WP9 [KB Tab Improvements]
         WP10 [Settings Page Polish]

Phase 3 (P2) — Backlog
═══════════════════════════════════════════════════
WP11 [External Enrichment & Save-to-KB]
WP12 [Console LED & Panel Consistency]
WP13 [Custom API Wizard]
```

---

## Work Package Summary

| WP | Name | Items | Est. Hours | Phase |
|----|------|-------|------------|-------|
| 1 | PDF Drag-Drop & Ingestion Speed | 19, 20, 21 | 6-8h | P0 |
| 2 | Test Buttons & Provider Detection | 4, 45, 10 | 4-5h | P0 |
| 3 | Remove Dev Tier Switch | 32 | 0.5h | P0 |
| 4 | KB Quality Scoring & Preview | 35, 36 | 5-7h | P0 |
| 5 | Review & Apply + Domains Removal | 9, 11 | 3-4h | P0 |
| 6 | Wizard Structure Overhaul | 12, 13, 14, 15, 16, 17, 18 | 8-10h | P1 |
| 7 | Custom LLM & Credits UI | 5, 6, 7, 8 | 6-8h | P1 |
| 8 | Chat Tooltips & UX | 22, 23, 24, 25, 26, 27, 28, 33 | 6-8h | P1 |
| 9 | KB Tab Improvements | 37, 38, 39, 40, 41, 44 | 5-6h | P1 |
| 10 | Settings Page Polish | 46, 47, 48, 49, 50 | 4-5h | P1 |
| 11 | External Enrichment & Save-to-KB | 31, 42 | 8-10h | P2 |
| 12 | Console LED & Panel Consistency | 29, 30, 34 | 3-4h | P2 |
| 13 | Custom API Wizard | 43 | 6-8h | P2 |
| **Total** | | **50 items** | **65-83h** | |

---

## Phase 1 — P0 (Critical Path)

### WP1: PDF Drag-Drop & Ingestion Speed

**Items:** 19 (CRITICAL), 20 (CRITICAL), 21 (HIGH)
**Estimate:** 6-8 hours
**Dependencies:** None (can start immediately)

#### Item 19: Fix PDF drag-drop activating Adobe Acrobat

**Root Cause:** The `useDragDrop` hook (`src/web/src/hooks/use-drag-drop.ts:26-62`) correctly calls `e.preventDefault()` on drop, but the `onDragOver` handler (line 50) only prevents default — it doesn't call `e.stopPropagation()`. On macOS, if the browser loses focus during drag, the OS file handler (Adobe Acrobat for `.pdf`) intercepts the drop.

Additionally, `first-document-step.tsx:144-152` spreads drag handlers onto a `<div>`, but the hidden `<input type="file">` at line 157-166 with `accept=".pdf,.txt,.md,.docx"` may also be intercepting the event.

**Files to modify:**
- `src/web/src/hooks/use-drag-drop.ts` — Lines 46-52
- `src/web/src/components/setup/first-document-step.tsx` — Lines 144-166

**Changes:**
1. In `use-drag-drop.ts`, add `e.stopPropagation()` to ALL four handlers (`onDragEnter`, `onDragLeave`, `onDragOver`, `onDrop`)
2. Add `e.dataTransfer.dropEffect = 'copy'` in `onDragOver` to signal the browser this is a valid drop target
3. In `first-document-step.tsx`, ensure the file input is `pointer-events-none` and `tabIndex={-1}` so it doesn't intercept drag events
4. Add a full-viewport invisible overlay during drag (`position: fixed; inset: 0; z-index: 50`) that captures the drop, preventing OS interception

**Test:** Drag a PDF from Finder onto the wizard drop zone. Should NOT open Adobe Acrobat. File should appear in the ingestion queue.

#### Item 20: Fix PDF query failure after ingestion

**Root Cause Analysis:** The wizard's `handleIngestFile` (`first-document-step.tsx:43-60`) calls `uploadFile(file, {categorizeMode: "smart"})` which POSTs to `/upload` (`routers/upload.py:28-127`). This calls `ingest_content()` (`services/ingestion.py:309-687`). The query then calls `queryKB()` which hits `/agent/query`.

The likely failure path: ingestion writes to ChromaDB and Neo4j, but the query fires before ChromaDB has flushed the write. ChromaDB 0.5.x `collection.add()` is eventually consistent — the embeddings may not be queryable for 100-500ms after the write returns.

Also: the `categorizeMode: "smart"` triggers `ai_categorize()` which makes an LLM call. If no LLM is available (OpenRouter key not yet applied, Ollama not running), categorization fails silently and the document may be stored with wrong domain metadata, causing query misrouting.

**Files to modify:**
- `src/web/src/components/setup/first-document-step.tsx` — Lines 96-115
- `src/mcp/services/ingestion.py` — After ChromaDB write (around line 502-511)
- `src/mcp/routers/upload.py` — Lines 94-108

**Changes:**
1. In `first-document-step.tsx`, add a 1-second delay between ingestion success and enabling the query input (simple `setTimeout` before setting phase to "chat")
2. In `first-document-step.tsx`, force `categorizeMode: "manual"` with `domain: "general"` in the wizard context — skip AI categorization during setup (it's unreliable before providers are configured)
3. In `ingestion.py`, after the ChromaDB `collection.add()` call, add an explicit `collection.get(ids=[chunk_ids[0]])` read-back to confirm write flushed
4. In the query callback (`handleQuery`), add retry logic: if first query returns 0 results, wait 2s and retry once

**Test:** Upload a 2-page PDF in the wizard → wait for ingestion → type "What is this about?" → should return relevant chunks.

#### Item 21: Optimize ingestion to <5s for 2-page PDF

**Current Bottlenecks (from `services/ingestion.py`):**
1. File parsing: `parse_file()` via thread pool — 100ms-2s (line 703)
2. AI categorization: `ai_categorize()` — 2-5s if LLM call needed
3. Embedding generation: `embed()` call for each chunk — 500ms-2s
4. ChromaDB write: `collection.add()` — 100-500ms
5. Neo4j artifact creation: `graph.create_artifact()` — 200-500ms
6. BM25 indexing: background, non-blocking
7. Quality scoring: synchronous, <50ms

Total for 2-page PDF: ~5-10s (dominated by AI categorization + embedding)

**Files to modify:**
- `src/web/src/components/setup/first-document-step.tsx` — Lines 43-60
- `src/mcp/routers/upload.py` — Lines 78-108
- `src/mcp/services/ingestion.py` — Lines 309-400 (early path)

**Changes:**
1. **Skip AI categorization in wizard context:** Pass `categorize_mode=manual` + `domain=general` from the wizard upload. This eliminates the 2-5s LLM call. (The user hasn't even configured domains yet.)
2. **Parallel Neo4j + ChromaDB writes:** Currently sequential. Wrap both in `asyncio.gather()` — saves ~200-500ms
3. **Skip quality scoring on wizard ingest:** Quality score for a fresh document in the wizard is meaningless. Pass `skip_quality=True` flag through to `ingest_content()` and default to 0.5
4. **Frontend progress:** Show real-time progress messages ("Parsing..." → "Embedding..." → "Storing...") using SSE or polling the ingestion status endpoint

**Target:** 2-page PDF should complete in 2-3s (parse: 200ms, embed: 1-1.5s, store: 500ms).

**Test:** Time the ingestion of a 2-page PDF in the wizard. Should complete in under 5 seconds. Verify with `console.time()`/`console.timeEnd()` in the frontend.

**Acceptance Criteria (WP1):**
- [ ] PDF drag-drop works on macOS without triggering Adobe Acrobat
- [ ] Ingested PDF is queryable within 3 seconds of upload completion
- [ ] 2-page PDF ingests in <5 seconds in the wizard
- [ ] Error states show clear messages (not just silent failures)

---

### WP2: Test Buttons & Provider Detection

**Items:** 4 (HIGH), 45 (HIGH), 10
**Estimate:** 4-5 hours
**Dependencies:** None (can start immediately, parallel with WP1)

#### Item 4: Fix Test buttons — hit backend validation with spinner

**Current State:** `ApiKeyInput` component (`src/web/src/components/setup/api-key-input.tsx:24-148`) already has a `handleTest()` callback (lines 38-57) that calls `validateProviderKey(provider, value.trim())`. This hits `POST /providers/{name}/validate` (`routers/providers.py:580-596`).

**Issue:** The test may be failing silently if the backend isn't running during wizard setup, or if the validation endpoint has connectivity issues.

**Files to modify:**
- `src/web/src/components/setup/api-key-input.tsx` — Lines 38-57
- `src/mcp/routers/providers.py` — Lines 580-596

**Changes:**
1. In `api-key-input.tsx`, add explicit timeout handling (5s) with user-friendly error: "Backend not responding — is Docker running?"
2. Add retry-once logic on network error (could be Docker startup race)
3. Ensure spinner is visible during test (verify the `checking` status renders correctly — line 116-131)
4. In `providers.py`, validate endpoint should return structured error with `provider_name`, `error_type` (invalid_key vs connection_failed vs timeout), and `suggestion` text
5. Add `AbortController` to cancel in-flight validation if user changes the key input

**Test:** Enter a valid OpenRouter key → click Test → should show spinner for 1-2s → green checkmark. Enter invalid key → red X with "Invalid API key" message.

#### Item 45: Fix three LLM providers showing offline despite keys in .env

**Root Cause:** `_configured_providers()` in `routers/setup.py:141-146` checks `os.environ.get(key)`. In Docker, env vars are loaded from `.env` via `env_file: ../../.env` in `docker-compose.yml`. But the `_KEY_TO_PROVIDER` map (lines 133-138) maps exact env var names to provider IDs.

The likely bug: Docker `env_file` strips quotes from values. If `.env` has `OPENAI_API_KEY="sk-..."` (with quotes), `os.environ.get("OPENAI_API_KEY")` returns `"sk-..."` including the literal quotes. The `strip()` call on line 145 strips whitespace but not quotes.  # pragma: allowlist secret

Alternative cause: The `.env` file may have the keys with a different variable name format, or the keys may be commented out with a space (` #OPENAI_API_KEY=...`).

**Files to modify:**
- `src/mcp/routers/setup.py` — Lines 141-146
- `src/mcp/routers/providers.py` — Lines 62-88 (GET /providers)

**Changes:**
1. In `_configured_providers()`, strip both whitespace AND surrounding quotes from env var values:
   ```python
   val = os.environ.get(key, "").strip().strip('"').strip("'")
   ```
2. Add logging at startup: `logger.info("Configured providers: %s", _configured_providers())` to make detection issues visible
3. In `/providers` endpoint, add a `detection_method` field showing where each key was found (env var name, whether .env file exists, whether value is non-empty)
4. Add a `/providers/diagnose` debug endpoint (dev-only) that returns which env vars are set, their lengths (not values), and whether they pass format validation

**Test:** Start the stack with all 4 keys in `.env`. Hit `GET /setup/status`. All 4 providers should appear in `configured_providers`. Verify in the wizard's Review & Apply step.

#### Item 10: End-to-end key detection consistency

**Root Cause:** Key detection happens in three different places with inconsistent logic:
1. `routers/setup.py:_configured_providers()` — checks `os.environ`
2. `routers/providers.py:62-88` (GET /providers) — checks `PROVIDER_REGISTRY` entries
3. `setup-wizard.tsx:262-285` (fetchSetupStatus) — uses `status.missing_keys` and `status.optional_keys`

**Files to modify:**
- `src/mcp/routers/setup.py` — Lines 124-146
- `src/mcp/routers/providers.py` — Lines 62-88
- `src/web/src/components/setup/setup-wizard.tsx` — Lines 262-285

**Changes:**
1. Create a single canonical function `detect_provider_status()` in `routers/setup.py` that returns a dict of `{provider_id: {configured: bool, key_env_var: str, key_present: bool, key_valid: bool | None}}`
2. Both `/setup/status` and `/providers` endpoints use this same function
3. Frontend `fetchSetupStatus()` returns the unified provider status map
4. Wizard hydration logic uses the same map to pre-populate key states

**Acceptance Criteria (WP2):**
- [ ] Test buttons show spinner → success/error for all 4 providers
- [ ] All providers with keys in .env show as "configured" on the settings page
- [ ] Provider detection is consistent between wizard, settings, and API responses

---

### WP3: Remove Dev Tier Switch

**Item:** 32 (HIGH)
**Estimate:** 0.5 hours
**Dependencies:** None

**Current Location:** `src/web/src/components/layout/sidebar.tsx:300-332` — A Shield icon button that cycles through Core/Pro/Vault tiers. Visible to all users in the sidebar bottom controls.

**Files to modify:**
- `src/web/src/components/layout/sidebar.tsx` — Lines 300-332

**Changes:**
1. Wrap the tier cycle button in a feature gate: only render if `import.meta.env.DEV` is true (Vite dev mode) or if `VITE_SHOW_DEV_TOOLS=true`
2. In production builds, this button is completely absent from the DOM

```tsx
{import.meta.env.DEV && onCycleTier && (
  // existing tier toggle button code
)}
```

**Also check:**
- `src/web/src/components/layout/sidebar.tsx` — Lines 54-60 (`TIER_CONFIG`, `TIER_LABELS`) — Keep these constants (used elsewhere for display), just gate the interactive button

**Test:** Run `npm run build` → open production build → confirm Shield/tier button is NOT visible. Run `npm run dev` → confirm it IS visible.

**Acceptance Criteria (WP3):**
- [ ] Tier switch button hidden in production builds
- [ ] Tier switch still works in development mode
- [ ] No console errors or layout shifts from removal

---

### WP4: KB Quality Scoring & Preview

**Items:** 35 (HIGH), 36 (HIGH)
**Estimate:** 5-7 hours
**Dependencies:** None (can start immediately, parallel with WP1-3)

#### Item 35: Fix KB quality scoring (resume scores Q20, should be higher)

**Root Cause (from research):** The current algorithm in `utils/quality.py:101-139` penalizes documents that:
1. Lack tags and sub_category (completeness score drops to 0.25-0.50)
2. Are older than 30 days (freshness decay: 90-day doc scores 0.125)
3. Have summaries >500 chars (penalized despite being rich content)
4. Have fewer than 5 keywords (linear penalty)

A resume PDF would typically have: long summary (penalized), 3-4 keywords (penalized), no tags (penalized), no sub_category (penalized), and be weeks/months old (penalized). Result: Q20.

**Decision:** Domain-adaptive replacement + user star/evergreen pinning.

**Files to modify:**
- `src/mcp/utils/quality.py` — Complete rewrite of scoring functions (lines 37-139)
- `src/mcp/config/constants.py` — Lines 44-48 (quality tiers), add new constants
- `src/mcp/db/neo4j/artifacts.py` — Add `starred` and `evergreen` fields to artifact schema
- `src/mcp/agents/assembler.py` — Lines 279-373 (`apply_quality_boost`) — update boost formula
- `src/mcp/agents/curator.py` — Update curator recommendations for new scoring
- `src/mcp/routers/artifacts.py` — Add `PATCH /artifacts/{id}/star` and `/evergreen` endpoints
- `src/web/src/components/kb/artifact-card.tsx` — Add star/evergreen toggle buttons

**New Algorithm (`utils/quality.py`):**

```python
def compute_quality_score(
    content: str,          # full text (new: was not available before)
    summary: str,
    keywords: list[str],
    tags: list[str],
    sub_category: str,
    default_sub_category: str,
    ingested_at: str | None = None,
    domain: str = "general",
    source_type: str = "upload",
    retrieval_count: int = 0,
    starred: bool = False,
    evergreen: bool = False,
) -> float:
```

**New Dimensions:**

| Factor | Weight | Implementation |
|--------|--------|----------------|
| Content richness | 25% | Word count (100-5000 optimal), structural elements (headings, lists, code blocks), information density (unique words / total words) |
| Metadata completeness | 20% | Graduated: summary ≥20 chars (+0.25), ≥2 keywords (+0.25), tags present (+0.25), sub_category non-default (+0.25) |
| Domain-adaptive freshness | 15% | Evergreen domains (coding, personal): no decay. Temporal domains (finance, news): 7-day half-life. User-pinned evergreen: no decay regardless of domain |
| Source authority | 15% | upload=1.0, webhook=0.9, clipboard=0.8, external=0.7 |
| Retrieval utility | 15% | `min(1.0, retrieval_count / 10)` — documents retrieved 10+ times score full marks |
| Embedding coherence | 10% | Placeholder: default 0.7 (real implementation requires embedding comparison, defer to P2) |

**New Constants (add to `config/constants.py`):**
```python
# Quality scoring v2
QUALITY_WEIGHT_RICHNESS = 0.25
QUALITY_WEIGHT_METADATA = 0.20
QUALITY_WEIGHT_FRESHNESS = 0.15
QUALITY_WEIGHT_AUTHORITY = 0.15
QUALITY_WEIGHT_UTILITY = 0.15
QUALITY_WEIGHT_COHERENCE = 0.10
QUALITY_MIN_FLOOR = 0.35          # raised from implicit ~0.2
QUALITY_EVERGREEN_DOMAINS = ["coding", "personal", "projects"]
QUALITY_TEMPORAL_HALF_LIFE_DAYS = 7     # was 30
QUALITY_EVERGREEN_HALF_LIFE_DAYS = 365  # effectively no decay
```

**Neo4j Schema Changes:**
- Add `starred: boolean` (default false) to Artifact node
- Add `evergreen: boolean` (default false) to Artifact node
- Add `retrieval_count: integer` (default 0) to Artifact node — increment on each retrieval hit in `query_agent.py`

**Migration:** Run Cypher to add defaults:
```cypher
MATCH (a:Artifact) WHERE a.starred IS NULL SET a.starred = false, a.evergreen = false, a.retrieval_count = 0
```

**Frontend Star/Evergreen Buttons:**
- Add to `artifact-card.tsx` action buttons row (near line 380-392)
- Star icon (filled/outline) toggles via `PATCH /artifacts/{id}/star`
- Evergreen icon (leaf/snowflake) toggles via `PATCH /artifacts/{id}/evergreen`
- Starred items show gold star badge on the card

**Test:** Ingest a resume PDF → check quality score → should be ≥0.50 (was 0.20). Star it → score should not change (starring affects retrieval boost, not raw score). Mark as evergreen → freshness component should not decay.

#### Item 36: Fix "Preview content" failure

**Root Cause:** `artifact-card.tsx:387` calls `onPreview(result.artifact_id)` which triggers `fetchArtifactDetail()` (`lib/api/kb.ts:115-119`) hitting `GET /artifacts/{artifactId}` (`routers/artifacts.py:149-200`).

Failure points:
1. `artifact_id` may be the external format (`external:duckduckgo`) which doesn't exist in Neo4j
2. `chunk_ids` field on the artifact may be malformed JSON (line 160)
3. ChromaDB collection lookup uses the artifact's domain — if domain changed after ingestion, collection doesn't match

**Files to modify:**
- `src/mcp/routers/artifacts.py` — Lines 149-200
- `src/web/src/components/kb/artifact-card.tsx` — Lines 380-392

**Changes:**
1. In `artifacts.py`, add error handling for malformed `chunk_ids` (try/except around JSON parse)
2. Add fallback: if chunk reassembly fails, return the artifact's `summary` field as preview content
3. Handle `external:*` artifact IDs gracefully — return the stored content or "External source — no preview available"
4. In `artifact-card.tsx`, show loading state during preview fetch, and a clear error message on failure
5. Add a preview cache in the frontend (React Query with 5-minute stale time) to avoid repeated fetches

**Test:** Click "Preview content" on a KB artifact → should show full reassembled content. Click on an external source artifact → should show "External source" message, not an error.

**Acceptance Criteria (WP4):**
- [ ] Resume PDF scores ≥0.50 quality (was 0.20)
- [ ] Star/evergreen toggles work on artifact cards
- [ ] Preview content shows document text or clear fallback message
- [ ] No unhandled errors in preview flow
- [ ] Quality scoring v2 passes existing test suite (update test expectations)

**New tests to add:**
- `tests/test_quality_v2.py` — Unit tests for all 6 scoring dimensions
- `tests/test_artifacts.py` — Preview endpoint with malformed chunk_ids, external artifacts

---

### WP5: Review & Apply + Domains Removal

**Items:** 9, 11 (HIGH)
**Estimate:** 3-4 hours
**Dependencies:** WP2 (provider detection fix needed for accurate Review & Apply)

#### Item 9: Fix Review & Apply showing "Not configured" for existing keys

**Root Cause:** `setup-wizard.tsx:262-285` calls `fetchSetupStatus()` and checks `status.optional_keys` to determine which providers are "not configured". But the backend's `_configured_providers()` (WP2 fix) may not be detecting keys correctly.

After WP2's unified `detect_provider_status()` is in place, this item becomes straightforward.

**Files to modify:**
- `src/web/src/components/setup/setup-wizard.tsx` — Lines 262-285

**Changes:**
1. Use the new unified provider status from WP2's `detect_provider_status()`
2. For each provider in the response, if `configured: true`, dispatch `SET_KEY(provider, "(configured)", true)`
3. Show "(from .env)" label next to pre-configured keys instead of "(configured)"
4. If a key is in .env but validation fails, show yellow warning: "Key found but validation failed"

#### Item 11: Remove Domains card from wizard

**Decision:** Domains grow organically from KB content. Pre-selecting domains during setup is misleading.

**Files to modify:**
- `src/web/src/components/setup/kb-config-step.tsx` — Lines 67-95 (remove domain grid)
- `src/web/src/components/setup/setup-wizard.tsx` — Lines 73 (remove domains from WizardState.kbConfig), 337-339 (remove domains from apply payload)

**Changes:**
1. Remove the entire "Domains" section from `kb-config-step.tsx` (lines 67-95)
2. Keep archive path, lightweight mode, and auto-watch settings
3. Default `domains: ["general"]` internally (not shown to user)
4. Remove domains from the Review & Apply summary display (lines 625-635)
5. Update the KB config step title/description to focus on archive path and storage mode
6. Rename the step from "Knowledge Base" to "Storage & Archive" for clarity

**Test:** Complete wizard → no domain selection shown → KB config step shows archive path + storage options only → Review & Apply doesn't mention domains.

**Acceptance Criteria (WP5):**
- [ ] Pre-configured .env keys show correctly in Review & Apply
- [ ] Domains card removed from wizard
- [ ] Apply Configuration succeeds with keys from .env
- [ ] No references to domain selection in wizard flow

---

## Phase 2 — P1 (Usability & Polish)

### WP6: Wizard Structure Overhaul

**Items:** 12 (HIGH), 13, 14, 15, 16, 17, 18
**Estimate:** 8-10 hours
**Dependencies:** WP5 (wizard state changes)

#### Item 12: Create Optional Features wizard card

**New file:** `src/web/src/components/setup/optional-features-step.tsx`

**Content:** Consolidate Ollama, external API configs, and optional service settings into one step.

**Changes:**
1. Create new component with 3 collapsible sections:
   - **Local LLM (Ollama)** — Move content from `ollama-step.tsx` here
   - **External Data Sources** — Toggle DuckDuckGo, Wikipedia, Wolfram Alpha
   - **Optional Services** — Bifrost status (hidden, read-only "Running as fallback")
2. Update `setup-wizard.tsx` step definitions to replace Ollama step with Optional Features
3. Update `TOTAL_STEPS` and step navigation logic
4. Update `skippedSteps` — this step should be skippable

#### Item 13: Remove/hide Bifrost from wizard

**Files to modify:**
- `src/web/src/components/setup/health-dashboard.tsx` — Lines 17-24 (SERVICE_META)

**Changes:**
1. Remove `bifrost` entry from `SERVICE_META` (line 21)
2. Bifrost health is still checked server-side but not displayed in the wizard health grid
3. If Bifrost is down, don't show it as a failed service — it's handled silently

#### Item 14: Reframe "Simple" mode → "Clean & Simple"

**Files to modify:**
- `src/web/src/components/setup/mode-selection-step.tsx` — Lines 42-55

**Changes:**
1. Change title from "Simple" to "Clean & Simple" (line 47)
2. Update description to emphasize the UX: "A clean chat focused on your knowledge — no technical controls visible. Perfect for everyday use. You can switch to Advanced anytime in Settings."

#### Item 15: Mode card reflects configured providers and KB state

**Files to modify:**
- `src/web/src/components/setup/mode-selection-step.tsx` — Lines 31-38

**Changes:**
1. Show configured provider names (not just count): "OpenRouter + Anthropic configured"
2. Show KB status: "0 documents" or "3 documents ingested" (from wizard state)
3. Show Ollama status: "Local LLM: llama3.2:3b" or "Local LLM: not configured"

#### Items 16-18: Service health improvements

**Files to modify:**
- `src/web/src/components/setup/health-dashboard.tsx` — Lines 89-125

**Changes for Item 16:** Move optional services note to Optional Features card (remove from health dashboard).

**Changes for Item 17 (plain language tooltips):**
Add `tooltip` field to `SERVICE_META`:
```typescript
neo4j: { ..., tooltip: "Tracks relationships between your documents — which topics connect to which sources" },
chromadb: { ..., tooltip: "Stores document embeddings for fast semantic search — finds relevant content even when wording differs" },
redis: { ..., tooltip: "Speeds up repeated queries and stores your conversation audit trail" },
mcp: { ..., tooltip: "The brain of Cerid — processes queries, manages your KB, and coordinates all services" },
verification_pipeline: { ..., tooltip: "Fact-checks AI responses against your KB and external sources" },
```

**Changes for Item 18 (actionable fix buttons):**
Add `fixAction` to `SERVICE_META` entries:
- Neo4j offline → "Run `docker compose up neo4j`" (copy-to-clipboard button)
- ChromaDB offline → "Run `docker compose up chromadb`"
- Redis offline → "Run `docker compose up redis`"
- MCP offline → "Check Docker Desktop is running" + link to troubleshooting

**Acceptance Criteria (WP6):**
- [ ] Optional Features card consolidates Ollama + external APIs
- [ ] Bifrost not visible anywhere in wizard
- [ ] Mode selection shows "Clean & Simple" with provider/KB summary
- [ ] Health dashboard has tooltips and fix buttons for each service
- [ ] Wizard step count updated correctly

---

### WP7: Custom LLM & Credits UI

**Items:** 5 (HIGH), 6, 7, 8
**Estimate:** 6-8 hours
**Dependencies:** WP2 (provider detection), WP6 (Optional Features card)

#### Item 5: Add multi-provider / custom LLM option

**Files to create:**
- `src/web/src/components/setup/custom-provider-input.tsx` — New component

**Files to modify:**
- `src/web/src/components/setup/setup-wizard.tsx` — API Keys step (lines 490-570)
- `src/mcp/routers/providers.py` — Add `POST /providers/custom` endpoint
- `src/mcp/routers/setup.py` — Add custom provider to config apply flow

**Changes:**
1. Below the 4 standard provider inputs, add "Add Custom Provider" expandable section
2. Fields: Provider Name, API Base URL, API Key, Model ID (optional)
3. Test button validates connectivity: POST to `{base_url}/models` with bearer token
4. Store in `.env` as `CUSTOM_LLM_URL`, `CUSTOM_LLM_KEY`, `CUSTOM_LLM_MODEL`
5. Backend validates the custom endpoint returns an OpenAI-compatible response

#### Item 6: Add OpenRouter "Add Credits" link

**Files to modify:**
- `src/web/src/components/setup/setup-wizard.tsx` — Lines 522-531 (credits display)

**Changes:**
1. Below the credits balance display, add:
   ```
   [Add Credits →] (link to https://openrouter.ai/credits)
   ```
2. Add subtle disclaimer text: "Credits are purchased through OpenRouter, not Cerid. OpenRouter pricing applies."
3. Style as `text-xs text-muted-foreground` to be informational without being alarming

#### Item 7: Add usage rate explainer

**Files to modify:**
- `src/web/src/components/setup/setup-wizard.tsx` — API Keys step
- `src/web/src/components/setup/api-key-input.tsx` — Below the OpenRouter input

**Changes:**
1. Add info card below OpenRouter key input:
   > "Costs vary by model and provider. A typical query costs $0.001-0.01. Verification adds ~$0.001 per 10 claims. Expert mode uses premium models at 15-50x standard rates."
2. Tooltip on the info icon links to OpenRouter pricing page

#### Item 8: Remove Ollama info box from Keys page

**Files to modify:**
- `src/web/src/components/setup/setup-wizard.tsx` — API Keys step rendering

**Changes:**
1. Remove any Ollama-related info box or mention from the API Keys step
2. Ollama is now covered in the Optional Features card (WP6, Item 12)

**Acceptance Criteria (WP7):**
- [ ] Custom LLM provider can be added with URL + key + test
- [ ] OpenRouter credits link opens in new tab
- [ ] Usage rate explainer visible on API Keys step
- [ ] No Ollama references on API Keys step

---

### WP8: Chat Tooltips & UX

**Items:** 22 (HIGH), 23 (HIGH), 24 (HIGH), 25, 26, 27, 28, 33
**Estimate:** 6-8 hours
**Dependencies:** None (independent of wizard work)

**Note from research:** The toolbar already has tooltips on ALL icons (`chat-toolbar.tsx` uses `ToolbarButtonWithMenu` with `tooltip` prop). The issue is likely that tooltips are too terse or use jargon.

#### Items 22-24: Improve tooltip text quality

**Files to modify:**
- `src/web/src/components/chat/chat-toolbar.tsx` — All tooltip strings

**Changes — replace terse labels with plain-language descriptions:**

| Icon | Current Tooltip | New Tooltip |
|------|----------------|-------------|
| KB toggle | "Show/Hide knowledge context" | "Include relevant documents from your knowledge base in AI responses" |
| Injection threshold (Broad/Standard/Focused/Strict) | Just labels | "Broad: include loosely related docs. Standard: balanced relevance. Focused: only highly relevant. Strict: exact matches only." |
| RAG Mode (Manual/Smart/Custom) | Just labels | "Manual: you control which docs are included. Smart: automatically finds relevant docs + memories + external sources. Custom: fine-tune retrieval weights (Pro)." |
| Verification | "Verification enabled/disabled" | "Fact-check AI responses against your KB and external sources. Toggle to enable automatic verification." |
| Expert verification checkbox | "~15x cost" | Q2 approved text: "Claims will try to first verify against your KB at no cost. External verification uses efficient models. Expert mode uses premium models which vary widely but are less token-efficient." |
| Feedback Loop | "Feedback ON/OFF" | "Save AI responses back to your knowledge base, creating a learning loop that improves future answers" |
| Privacy Mode levels | Level numbers | "Off: normal operation. Level 1: skip saves & sync. Level 2: also skip KB injection. Level 3: also no logging. Level 4: full ephemeral — nothing persisted." |
| Dashboard | "Show/Hide metrics" | "Show token usage, response timing, and retrieval metrics for the current conversation" |
| Routing | "Manual/Recommend/Auto" | "Manual: you pick the model. Recommend: AI suggests optimal model. Auto: AI picks the best model for each query." |

#### Item 25: Consistent markup/rendering across panels

**Files to check:** All panel components that render AI content:
- `src/web/src/components/chat/` — Message bubbles
- `src/web/src/components/kb/` — Preview panels
- `src/web/src/components/memories/` — Memory display

**Changes:**
1. Ensure all panels use the same Markdown renderer component
2. Create shared `<MarkdownContent>` component if one doesn't exist
3. Apply consistent `prose` class styling across all rendered content

#### Item 26: Expert mode cost estimate

**Files to modify:**
- `src/web/src/hooks/use-verification-stream.ts` — Lines 41-45

**Changes:**
1. Replace the scary warning with the approved tooltip text (Q2 decision)
2. Show estimated cost: "~$0.001 per 10 claims (standard) / ~$0.01 per 10 claims (expert)"
3. Display in the verification status bar, not as a modal/warning

#### Item 27: Activate response verification dashboard

**Files to modify:**
- `src/web/src/components/chat/chat-toolbar.tsx` — Verification menu

**Changes:**
1. Add "Open Verification Dashboard" option in the verification popover menu
2. Dashboard shows: claims verified, confidence distribution, cost breakdown, cache hit rate
3. Reuse existing verification stream data from `use-verification-stream.ts`

#### Item 28: Privacy mode visual escalation

**Files to modify:**
- `src/web/src/components/chat/chat-toolbar.tsx` — Lines 235-273

**Changes:**
1. Color the Lock icon by level:
   - Level 0: default muted color (off)
   - Level 1: `text-green-500` (green — minimal privacy)
   - Level 2: `text-yellow-500` (yellow — moderate)
   - Level 3: `text-orange-500` (orange — high)
   - Level 4: `text-red-500` (red — full ephemeral)
2. Add a subtle glow/pulse animation at levels 3-4 to draw attention
3. Tooltip shows current level name and what it means

#### Item 33: Explain "tokens remaining" in metrics dashboard

**Files to modify:**
- `src/web/src/components/chat/chat-dashboard.tsx` — Lines 94-95

**Changes:**
1. Add tooltip to the "Remaining" display:
   > "Tokens remaining in the current context window. When this reaches zero, older messages are summarized to free space. Your knowledge base is not affected."
2. Show the context window size (e.g., "128K context") as a label

**Acceptance Criteria (WP8):**
- [ ] All chat toolbar icons have descriptive plain-language tooltips
- [ ] Injection threshold options have explanations
- [ ] RAG modes have plain-language descriptions
- [ ] Expert verification shows approved cost tooltip (Q2)
- [ ] Privacy mode icons color-coded green→yellow→orange→red
- [ ] "Tokens remaining" has explanatory tooltip

---

### WP9: KB Tab Improvements

**Items:** 37, 38, 39, 40, 41, 44
**Estimate:** 5-6 hours
**Dependencies:** WP4 (quality scoring changes)

#### Item 37: Fix plus icon meaning

**Files to modify:**
- `src/web/src/components/kb/artifact-card.tsx` — Plus icon button

**Changes:**
1. Change icon from `Plus` to `MessageSquarePlus` (Lucide) — clearer "add to conversation" intent
2. Add tooltip: "Add this document's content to the current conversation context"

#### Item 38: Tooltip on chunk count badge

**Files to modify:**
- `src/web/src/components/kb/artifact-card.tsx` — Lines 138-143

**Changes:**
1. Wrap the chunk count badge in a `<Tooltip>`:
   > "4 chunks = 4 searchable segments. Documents are split into chunks for more precise retrieval."

#### Item 39: Make KB cards expandable

**Files to modify:**
- `src/web/src/components/kb/artifact-card.tsx` — Expand/collapse logic (line 127)

**Changes:**
1. On expand, show: full summary, all keywords as tags, source metadata, quality breakdown, chunk list
2. Increase expanded height to ~2x current
3. Add smooth height transition (`transition-all duration-200`)
4. Show quality score breakdown (each dimension) in expanded view

#### Item 40: Make Upload/Import/Duplicates more visually prominent

**Files to modify:**
- `src/web/src/components/kb/kb-context-panel.tsx` — Upload/Import section

**Changes:**
1. Move Upload/Import buttons above the artifact list (currently below or inline)
2. Use `variant="default"` (filled) instead of `variant="outline"` for primary actions
3. Add drag-drop zone at the top of the KB panel
4. Duplicates count as a badge on the filter bar

#### Item 41: Add descriptive tooltips to all interactive elements

**Files to modify:**
- `src/web/src/components/kb/artifact-card.tsx` — All icon buttons
- `src/web/src/components/kb/kb-context-panel.tsx` — All controls

**Changes:**
- Every icon button gets a `<Tooltip>` with plain-language description
- Every toggle gets helper text
- Every badge gets a tooltip explaining what it measures

#### Item 44: External section default expanded, sub-categories collapsed

**Files to modify:**
- `src/web/src/components/kb/knowledge-console.tsx` — Lines 446-460

**Changes:**
1. Set external section `defaultOpen={true}`
2. Set each sub-category (DuckDuckGo, Wikipedia, etc.) `defaultOpen={false}`
3. Add expand/collapse all toggle at the section header

**Acceptance Criteria (WP9):**
- [ ] Plus icon clearly means "add to conversation" with tooltip
- [ ] Chunk count has tooltip explaining segmentation
- [ ] KB cards expand to show full metadata and quality breakdown
- [ ] Upload/Import buttons are prominent above artifact list
- [ ] All interactive elements have tooltips
- [ ] External section expanded by default, sub-sources collapsed

---

### WP10: Settings Page Polish

**Items:** 46, 47, 48, 49, 50
**Estimate:** 4-5 hours
**Dependencies:** None

#### Item 46: Info icons for chunk size/overlap

**Files to modify:**
- `src/web/src/components/settings/essentials-section.tsx` — Lines 157-161

**Changes:**
1. Change from read-only `<Row>` to interactive `<SliderRow>` (from `settings-primitives.tsx`)
2. Add detailed tooltip:
   > "Chunk size: how many tokens per searchable segment (larger = more context per result, fewer results). Overlap: how much adjacent chunks share (higher = better context continuity, more storage). Recommended: 400-512 tokens, 15-25% overlap."

#### Item 47: Tooltips on all non-obvious settings

**Files to modify:**
- `src/web/src/components/settings/pipeline-section.tsx` — Add `info` props to all settings
- `src/web/src/components/settings/system-section.tsx` — Add tooltips to taxonomy, infrastructure sections

**Changes:** Audit every setting across all 5 tabs. Add `info` prop (tooltip) to any setting that uses jargon or isn't self-explanatory. Target: every `Row`, `ToggleRow`, `SliderRow`, and `PipelineToggle` has an `info` prop.

#### Item 48: Fix inconsistent card expand/collapse defaults

**Files to modify:**
- `src/web/src/components/settings/settings-pane.tsx` — Lines 30-57 (section state persistence)

**Changes:**
1. Define sensible defaults: Essentials sections expanded, Pipeline/System collapsed
2. Reset localStorage section state on version bump (add version key to persisted state)
3. Ensure all section headings have consistent chevron direction (ChevronRight when collapsed, ChevronDown when expanded)

#### Item 49: Show domain tags on API mouseover

**Files to modify:**
- `src/web/src/components/kb/knowledge-console.tsx` — External section data source items

**Changes:**
1. On hover/focus of a data source entry, show which domains it's associated with
2. Use the `domains` field from `DataSource.domains` (backend: `base.py:36`)
3. Display as small tag pills below the source name

#### Item 50: Fix click affordance

**Files to modify:**
- `src/web/src/components/settings/settings-primitives.tsx` — `Row` component (lines 82-92)
- `src/web/src/components/settings/plugins-section.tsx` — Card header (line 121)

**Changes:**
1. `Row` component: remove any hover effects that suggest interactivity. Apply `cursor-default` explicitly
2. Interactive elements: ensure `cursor-pointer`, hover background change, and focus ring
3. Plugin card: add visual separator between "click to expand" area and the enable/disable switch
4. Audit all elements with `cursor-pointer` — remove from non-interactive elements

**Acceptance Criteria (WP10):**
- [ ] Chunk size/overlap has explanatory tooltip with recommended values
- [ ] Every non-obvious setting has a tooltip across all tabs
- [ ] Section expand/collapse defaults are consistent and sensible
- [ ] Domain tags visible on data source hover
- [ ] Only interactive elements look clickable

---

## Phase 3 — P2 (Backlog)

### WP11: External Enrichment & Save-to-KB

**Items:** 31, 42
**Estimate:** 8-10 hours
**Dependencies:** WP4 (quality scoring), WP9 (KB UI)

#### Item 31: Add external enrichment button on chat bubbles

**Files to modify:**
- `src/web/src/components/chat/message-bubble.tsx` (or equivalent)
- `src/mcp/routers/query.py` — Add `POST /agent/enrich` endpoint

**Changes:**
1. Add a small "Enrich" button (Globe icon) on assistant message bubbles
2. On click, triggers external data source query for the message content
3. Results displayed in a side panel or inline expansion
4. Each result has a "Save to KB" button (Q5 decision)
5. After 3+ identical external retrievals, show smart suggestion: "This source keeps coming up — save it to your KB?"

#### Item 42: Fix external search + manual API routing

**Files to modify:**
- `src/mcp/utils/data_sources/` — Debug `registry.query_all()` flow
- `src/mcp/agents/query_agent.py` — Lines 420-439

**Changes:**
1. Debug why external search returns no results — likely circuit breaker tripped or no sources enabled
2. Add manual API routing: allow user to specify which data source to query via dropdown
3. Show data source status in the KB console external section (connected/disconnected/error)

**Acceptance Criteria (WP11):**
- [ ] "Enrich" button on chat bubbles triggers external search
- [ ] "Save to KB" buttons on external results
- [ ] Smart suggestion after 3+ identical retrievals
- [ ] External search returns results from enabled data sources

---

### WP12: Console LED & Panel Consistency

**Items:** 29, 30, 34
**Estimate:** 3-4 hours
**Dependencies:** None

#### Item 29: Knowledge console — fix duplicate mode selector

**Files to modify:**
- `src/web/src/components/kb/knowledge-console.tsx` — Lines 282-304

**Changes:**
1. Audit where RAG mode selector appears (toolbar AND console)
2. Keep the toolbar selector as the primary control
3. Remove or convert the console selector to a read-only display that syncs with toolbar

#### Item 30: Knowledge console — consistent settings button

**Files to modify:**
- `src/web/src/components/kb/knowledge-console.tsx` — Lines 309-341

**Changes:**
1. Ensure settings gear icon appears in every panel header (KB, Memories, External)
2. All panels use the same settings popover pattern
3. Settings options are panel-specific but UI pattern is identical

#### Item 34: Console activity LED

**Files to modify:**
- `src/web/src/components/layout/sidebar.tsx` — Console section

**Changes:**
1. Add a small dot indicator next to "Console" in the sidebar
2. Dot flashes green on new activity (new query, new ingestion, new verification)
3. Stops flashing after user opens the console panel
4. Uses a simple CSS animation, state from a lightweight event bus or context

**Acceptance Criteria (WP12):**
- [ ] Single authoritative RAG mode selector (toolbar)
- [ ] Consistent settings button across all console panels
- [ ] Activity LED flashes on new console events

---

### WP13: Custom API Wizard

**Item:** 43
**Estimate:** 6-8 hours
**Dependencies:** WP7 (custom LLM foundation)

**Decision:** API key auth only (Q3: Bearer, custom header, query param).

**Files to create:**
- `src/web/src/components/kb/custom-api-dialog.tsx` — Modal for adding custom API
- `src/mcp/utils/data_sources/custom.py` — Custom API data source implementation
- `src/mcp/models/custom_api.py` — Pydantic model for custom API config

**Files to modify:**
- `src/web/src/components/kb/knowledge-console.tsx` — External section
- `src/mcp/routers/data_sources.py` — Add CRUD endpoints for custom APIs
- `src/mcp/utils/data_sources/base.py` — Extend auth model

**Changes:**
1. **Backend DataSource extension:**
   ```python
   class CustomApiSource(DataSource):
       base_url: str
       auth_type: Literal["bearer", "custom_header", "query_param"]
       auth_key: str  # header name or query param name
       auth_value: str  # the actual API key
       response_path: str  # JSONPath to extract results (e.g., "data.results")
       result_title_field: str  # field name for result title
       result_content_field: str  # field name for result content
   ```

2. **New endpoints:**
   - `POST /data-sources/custom` — Create custom API source
   - `PUT /data-sources/custom/{id}` — Update
   - `DELETE /data-sources/custom/{id}` — Remove
   - `POST /data-sources/custom/{id}/test` — Test connectivity

3. **Frontend dialog:**
   - Name, Base URL, Auth Type (dropdown: Bearer/Custom Header/Query Param)
   - Auth Key Name (for custom header/query param), API Key Value
   - Response mapping: JSONPath for title and content fields
   - Test button with live response preview
   - Stored in Redis: `cerid:custom_api:{id}`

4. **Storage:** Redis with `cerid:custom_api:{uuid}` keys. Config loaded on startup and registered with `DataSourceRegistry`.

**Acceptance Criteria (WP13):**
- [ ] User can add a custom API with URL + key + response mapping
- [ ] Three auth modes work: Bearer, custom header, query param
- [ ] Test button validates connectivity and shows sample response
- [ ] Custom API appears in external data sources and can be queried

---

## Risk Assessment

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| **PDF drag-drop fix is OS-specific** | HIGH | MEDIUM | Test on macOS + Chrome/Firefox/Safari. The OS intercept behavior varies by browser. May need browser-specific workarounds. |
| **Quality scoring v2 changes test expectations** | MEDIUM | HIGH | All tests referencing quality scores will need updated expectations. Run full test suite before and after. |
| **ChromaDB write consistency** | HIGH | MEDIUM | The 1s delay + read-back approach is a workaround. Long-term fix: ChromaDB 0.6 has sync writes. |
| **Provider detection env var quoting** | MEDIUM | HIGH | The `.strip('"')` fix is simple but may mask other issues. Add comprehensive logging. |
| **Neo4j schema migration** | LOW | LOW | Adding nullable fields (starred, evergreen, retrieval_count) is backward-compatible. |
| **Custom LLM endpoint compatibility** | MEDIUM | MEDIUM | Not all LLM APIs are OpenAI-compatible. Test against common alternatives (Azure, Mistral, Together). |
| **Wizard step count change** | LOW | MEDIUM | Changing total steps affects progress indicator, skip logic, and localStorage. Need coordinated update. |
| **Frontend bundle size** | LOW | LOW | New tooltip text and components add ~5-10KB. Well within 800KB budget. |

---

## Execution Schedule

```
Week 1 (P0):
├─ Day 1-2: WP1 (PDF fixes) + WP2 (provider detection) — PARALLEL
├─ Day 2:   WP3 (dev tier switch) — 30 min
├─ Day 2-3: WP4 (quality scoring + preview) — PARALLEL with WP1/2
└─ Day 3:   WP5 (Review & Apply) — after WP2 complete

Week 2-3 (P1):
├─ Day 4-5: WP6 (wizard structure) + WP8 (chat tooltips) — PARALLEL
├─ Day 5-6: WP7 (custom LLM + credits)
├─ Day 6-7: WP9 (KB improvements)
└─ Day 7-8: WP10 (settings polish)

Backlog (P2):
├─ WP11: External enrichment (8-10h)
├─ WP12: Console LED + panel consistency (3-4h)
└─ WP13: Custom API wizard (6-8h)
```

---

## Test Strategy

### Before Starting (Baseline)
```bash
# Backend tests
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim \
  bash -c "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ -v --tb=short"

# Frontend tests
cd src/web && npx vitest run

# Record current test counts and pass rates
```

### Per Work Package
Each WP should:
1. Run the relevant test subset before changes (baseline)
2. Add new tests for new behavior
3. Update existing test expectations for changed behavior
4. Run full suite after changes
5. Manual verification in the browser

### Integration Test (End of P0)
1. Fresh Docker build: `./scripts/start-cerid.sh --build`
2. Complete wizard flow end-to-end with a real PDF
3. Verify all providers detected correctly
4. Query the ingested PDF successfully
5. Check quality score is reasonable (≥0.50)
6. Verify no dev tier switch in production build

### Integration Test (End of P1)
1. Complete wizard with custom LLM provider
2. All tooltips visible and readable
3. KB cards expand/collapse correctly
4. Settings page shows all providers online
5. Privacy mode colors correct
6. External section layout correct

---

## Files Changed Summary

### New Files
| File | WP | Purpose |
|------|-----|---------|
| `src/web/src/components/setup/optional-features-step.tsx` | WP6 | Consolidated optional features wizard step |
| `src/web/src/components/setup/custom-provider-input.tsx` | WP7 | Custom LLM provider input component |
| `src/web/src/components/kb/custom-api-dialog.tsx` | WP13 | Custom API configuration dialog |
| `src/mcp/utils/data_sources/custom.py` | WP13 | Custom API data source implementation |
| `src/mcp/models/custom_api.py` | WP13 | Pydantic model for custom API config |
| `tests/test_quality_v2.py` | WP4 | Quality scoring v2 unit tests |

### Modified Files (by frequency)
| File | WPs | Change Count |
|------|-----|-------------|
| `src/web/src/components/setup/setup-wizard.tsx` | 2,5,6,7,8 | 5 |
| `src/web/src/components/chat/chat-toolbar.tsx` | 8 | 1 (but many lines) |
| `src/web/src/components/kb/artifact-card.tsx` | 4,9 | 2 |
| `src/web/src/components/kb/knowledge-console.tsx` | 9,12,13 | 3 |
| `src/web/src/components/settings/essentials-section.tsx` | 10 | 1 |
| `src/web/src/components/setup/health-dashboard.tsx` | 6 | 1 |
| `src/web/src/components/setup/mode-selection-step.tsx` | 6 | 1 |
| `src/web/src/components/setup/kb-config-step.tsx` | 5 | 1 |
| `src/web/src/components/setup/first-document-step.tsx` | 1 | 1 |
| `src/web/src/components/layout/sidebar.tsx` | 3 | 1 |
| `src/web/src/hooks/use-drag-drop.ts` | 1 | 1 |
| `src/web/src/hooks/use-verification-stream.ts` | 8 | 1 |
| `src/web/src/lib/api/kb.ts` | 1,4 | 2 |
| `src/mcp/utils/quality.py` | 4 | 1 (full rewrite) |
| `src/mcp/config/constants.py` | 4 | 1 |
| `src/mcp/routers/setup.py` | 2,5 | 2 |
| `src/mcp/routers/providers.py` | 2,7 | 2 |
| `src/mcp/routers/artifacts.py` | 4 | 1 |
| `src/mcp/services/ingestion.py` | 1 | 1 |
| `src/mcp/routers/upload.py` | 1 | 1 |
| `src/mcp/agents/assembler.py` | 4 | 1 |
| `src/mcp/agents/curator.py` | 4 | 1 |
| `src/mcp/db/neo4j/artifacts.py` | 4 | 1 |
| `src/mcp/routers/data_sources.py` | 13 | 1 |
| `src/mcp/utils/data_sources/base.py` | 13 | 1 |

---

*Plan generated 2026-04-03 from codebase analysis of `~/Develop/cerid-ai` at HEAD.*
*Open questions resolved per approved decisions above.*
