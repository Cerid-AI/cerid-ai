# Cerid AI — Final Comprehensive Punch List v2

**Date:** 2026-04-04
**Inputs:** Aggregated Punch List, Root Cause Analysis (6 clusters), Beta Test #2 findings
**Scope:** 5 sections — Systemic Fixes, Individual Issues, Wiring Checks, Multi-OS Compatibility, Ollama Architecture

---

## Section 1: Systemic Fixes

> Fix these FIRST. Each resolves multiple symptoms across the product.

---

### S1. Parent-Child Wiring Audit

**Problem:** WP1-WP13 implemented leaf components bottom-up without wiring props from parent components. Multiple features exist in source but are unreachable from the UI.

**Checklist — verify every item has: (a) prop defined, (b) parent passes prop, (c) handler exists, (d) feature reachable from UI:**

| Component | Prop | Parent | Status | Fix |
|-----------|------|--------|--------|-----|
| `message-bubble.tsx` | `onEnrich` | Chat panel (message list renderer) | **NOT WIRED** — grep returns zero call sites for `onEnrich=` or `handleEnrich` | Create `handleEnrich` in chat panel, pass to every assistant `MessageBubble` |
| `message-bubble.tsx` | `onSelectForVerification` | Chat panel | **VERIFY** — may have been disrupted by WP8 toolbar changes | Confirm prop is threaded from parent; test click-to-verify on previous messages |
| `artifact-card.tsx` | `expanded` state | Self-contained (internal `useState`) | **PARTIAL** — expand toggles `line-clamp-2` only, no additional content rendered | Add expanded view: keyword tags, metadata row, quality breakdown, chunk list |
| `sidebar.tsx` | Console activity LED | N/A — requires event bus | **NOT IMPLEMENTED** — no pulse/led/activity code in sidebar | Create `ActivityContext` (or lightweight emitter), add LED dot to sidebar pane items |
| `knowledge-console.tsx` | `ConsoleConfigBar` | Self-rendered at line 358 | **WORKS but insufficient** — only shows RAG mode selector | Add injection threshold display, settings gear, make `sticky top-0 z-10` |
| `custom-api-dialog.tsx` | Dialog trigger | Knowledge console external section | **VERIFY** — dialog was created in WP13 | Confirm "Add Custom API" button exists and opens dialog |
| `optional-features-step.tsx` | Wizard step slot | `setup-wizard.tsx` step definitions | **VERIFY** — WP6 should have updated step array | Confirm step appears in wizard navigation, step count is correct |
| `custom-provider-input.tsx` | Rendered in API Keys step | `setup-wizard.tsx` API Keys section | **VERIFY** — WP7 should render this below standard providers | Confirm "Add Custom Provider" expandable section is visible |
| `health-dashboard.tsx` | Fix action buttons | Self-contained | **VERIFY** — WP6 added fix actions | Confirm each offline service shows a "Fix" button with copy-to-clipboard command |
| `health-dashboard.tsx` | Verification re-test | Needs backend endpoint | **NOT IMPLEMENTED** — no re-test trigger exists | Add "Re-check" button → `POST /setup/retest-verification` |

**Acceptance:** For each row, screenshot showing the feature is reachable from the UI. Automated: add a Vitest test per component verifying prop types are used.

---

### S2. LLM Dependency Cascade

**Problem:** Verification self-test, metadata extraction, AI categorization, and claim extraction all require a working LLM provider. During first setup, no LLM is available, causing cascading failures that persist for 24 hours.

**Fix items:**

| # | Fix | File(s) | Detail |
|---|-----|---------|--------|
| S2.1 | Re-run verification self-test after keys are configured | `routers/setup.py` | Add to `POST /setup/configure` success path: `await run_verification_self_test(get_redis())`. This overwrites the failed startup result. |
| S2.2 | Add manual "Re-check" endpoint | `routers/setup.py` | `POST /setup/retest-verification` → calls `run_verification_self_test()`, returns result. Wire to health dashboard button. |
| S2.3 | Fix heuristic claim extractor | `agents/hallucination/extraction.py` | The self-test response "Python is a programming language created by Guido van Rossum" must extract >=1 claim via heuristic. Add a pattern for `"X is a/an Y"` sentences. Also add `"X was created/founded/developed by Y"` pattern. |
| S2.4 | SSE error events for all exception paths | `agents/hallucination/streaming.py` | Wrap the `verify_response_streaming()` generator in try/except. On exception, yield `{"event": "error", "data": {"message": str(exc)}}` before returning. Prevents "stream interrupted." |
| S2.5 | Skip LLM metadata extraction in wizard context | `services/ingestion.py`, `routers/upload.py` | Add `skip_metadata: bool = Query(False)` to upload endpoint. When true, use first 200 chars as summary, filename words as keywords. Skip `extract_metadata()` entirely. Frontend wizard passes `?skip_metadata=true&skip_quality=true`. |
| S2.6 | Reduce self-test TTL to 1 hour | `agents/hallucination/startup_self_test.py:82` | Change `ex=86400` to `ex=3600`. Failed results expire faster, allowing natural recovery. |
| S2.7 | Show "LLM required" message instead of "offline" | `health-dashboard.tsx` | When verification_pipeline status is "unavailable" AND no providers are configured, show "Requires API key — configure a provider first" instead of generic "Offline". |

**Acceptance:** Complete wizard → configure OpenRouter key → verification pipeline shows "healthy" within 10 seconds. "Verify last response" extracts 3+ claims from a factual response.

---

### S3. Unify Provider Detection

**Problem:** Three parallel provider detection code paths disagree on quote handling and return format.

**Current state:**
1. `setup.py:detect_provider_status()` — strips quotes ✓, returns `{provider_id: {configured, key_env_var, key_present}}`
2. `providers.py:list_providers()` (GET /providers) — strips quotes ✓, returns `ProviderListResponse`
3. `providers.py:get_configured_providers()` (GET /providers/configured) — does NOT strip quotes ✗, returns list of dicts

**Fix items:**

| # | Fix | File(s) | Detail |
|---|-----|---------|--------|
| S3.1 | Make `get_configured_providers()` use shared quote stripping | `config/providers.py:194` | Change `api_key = os.getenv(env_var, "")` to `api_key = os.getenv(env_var, "").strip().strip('"').strip("'")` |
| S3.2 | Make `list_providers()` delegate to canonical function | `routers/providers.py:62-88` | Import `detect_provider_status` from `routers/setup.py` (or extract to shared `utils/provider_detection.py`). Use its output to populate `key_set` field. |
| S3.3 | Fix wizard hydration for ALL providers | `setup-wizard.tsx` | In the effect that calls `fetchSetupStatus()`, iterate over `provider_status` (not `configured_providers`) and dispatch `SET_KEY(provider, "(configured)", true)` for every provider where `configured: true`. |
| S3.4 | Fix ApiKeyInput for preconfigured keys | `api-key-input.tsx` | When `preconfigured=true` and `value` is empty: (a) show "(from .env)" label, (b) add a "Re-test" button that calls `validateProviderKey(provider, "")` with a special flag telling the backend to test the env var key, OR (c) set initial `value` to `"(configured)"` and disable the input but enable the Test button to validate the env key. |
| S3.5 | Backend: validate env key directly | `routers/setup.py:validate_key()` | If `api_key` is empty but provider has an env key, use the env key value for validation. This enables testing preconfigured keys without user re-entering them. |

**Acceptance:** Start stack with all 4 keys in `.env` → `GET /setup/status` returns all 4 in `configured_providers` → `GET /providers` returns all 4 with `key_set: true` → `GET /providers/configured` returns all 4 → Wizard shows all 4 as preconfigured → Settings shows all 4 online.

---

### S4. macOS Docker virtiofs Handling

**Problem:** `Errno 35` (EDEADLK / resource deadlock) affects sync, ingestion, and file watching on macOS Docker Desktop. Known issue, documented in `scan_ingest.py`, but not handled consistently.

**Fix items:**

| # | Fix | File(s) | Detail |
|---|-----|---------|--------|
| S4.1 | Catch Errno 35 in sync status | `sync/status.py:compare_status()` | Wrap file operations in try/except for `OSError`. Check `exc.errno == 35` (or `errno.EDEADLK`). Return `{"status": "busy", "message": "File system busy — common with Docker on macOS. Try again in a few seconds."}` |
| S4.2 | Add retry with backoff for file reads | `services/ingestion.py` (parse_file call) | Wrap `parse_file()` in a retry loop: 3 attempts, 500ms backoff. Catches `OSError` with errno 35 and retries. |
| S4.3 | Document virtiofs limitation | `docs/OPERATIONS.md` or `DEVELOPMENT.md` | Add section: "macOS Docker users may see '[Errno 35] Resource deadlock avoided' during concurrent file operations. This is a Docker Desktop virtiofs limitation. Workaround: avoid running Dropbox sync + file watcher + manual uploads simultaneously." |
| S4.4 | Unify watched folders systems | `routers/setup.py:configure()`, `routers/watched_folders.py` | When wizard sets `WATCH_FOLDER` env var, also create a watched folder entry via the API: `POST /watched-folders {path: archive_path, label: "Archive", search_enabled: true}`. Delete the legacy `WATCH_FOLDER` env var approach long-term. |
| S4.5 | Frontend: show friendly error for Errno 35 | `src/web/` (wherever sync status is displayed) | Catch error messages containing "Errno 35" or "Resource deadlock" and display: "File system busy — retrying..." with auto-retry after 3 seconds. |

**Acceptance:** Sync status page never shows raw `[Errno 35]` error. Archive folder configured in wizard appears in watched folders UI.

---

## Section 2: Individual Issue Fixes

> Items that need their own fix AFTER systemic fixes are applied. Grouped by area, ordered by priority.

### Key
- **Resolved by S#** = systemic fix handles this, no additional work needed
- **Needs own fix** = requires targeted implementation

---

### Setup Wizard

| # | Issue | Systemic? | Fix if needed |
|---|-------|-----------|---------------|
| Orig #4 | Test buttons don't work | Resolved by **S3.4, S3.5** | — |
| Orig #9 | Review & Apply only shows 2 providers | Resolved by **S3.3** | — |
| Orig #10 | Key detection inconsistent | Resolved by **S3.1-S3.3** | — |
| Orig #45 | Settings shows providers offline | Resolved by **S3.1** | — |
| Beta #2 | Storage/Archive needs location selector | **Needs own fix** | Replace text input with OS file picker dialog (Electron: `dialog.showOpenDialog`, web: polyfill with `<input type="file" webkitdirectory>`). Add explanatory text about multi-computer sync via Dropbox/iCloud. Add database configuration section (Neo4j password, Redis password). |
| Beta #3 | Ollama wizard needs hardware-aware recommendations | **Needs own fix** | See **Section 5** for full evaluation. Frontend: call `GET /providers/ollama/recommendations`, display hardware info, show compatible models with recommended badge, explain expected experience ("CPU inference: ~10 tokens/sec for 3B model"). |
| Beta #4 | Review & Apply: errors need fix actions | Partially resolved by **S3**, remaining: | Add "Fix" action to each error row in Review & Apply. For missing keys: link to provider signup. For failed validation: show specific error + retry button. |
| Beta #5 | Verification shows offline | Resolved by **S2.1, S2.2, S2.7** | — |
| Beta #6 | Try It Out: 25s parse, query fails | Partially resolved by **S2.5** (speed), remaining: | Add query retry logic in `first-document-step.tsx`: after ingestion success, poll `GET /artifacts?sort=ingested_at&limit=1` every 500ms up to 5 seconds. Only enable query input after artifact confirmed queryable. Consider remote embedding API as option. |

### Chat

| # | Issue | Systemic? | Fix if needed |
|---|-------|-----------|---------------|
| Beta #7 | Enrichment buttons missing | Resolved by **S1** (wiring audit) | — |
| Beta #9 | Expert verification "premium" badge incorrect | **Needs own fix** | Find the Badge component in the verification submenu. Remove "premium" label from expert verification line item. Expert mode is a user choice, not a tier-gated feature. |
| Beta #10 | "No factual claims to verify" | Resolved by **S2.3, S2.4** | — |
| Beta #11 | "Stream interrupted" | Resolved by **S2.4** | — |
| Beta #12 | Sub-menus need formatting/tooltip audit | **Needs own fix** | Audit all popover/dropdown sub-menus across chat toolbar. Ensure consistent padding, font sizes, dividers between sections. Add tooltips to any items missing them. Target: every interactive element in every sub-menu has a tooltip. |
| Beta #16 | Selecting previous responses broken | Resolved by **S1** (wiring audit for `onSelectForVerification`) | — |
| Beta #21 | Chat names should be editable | **Needs own fix** | Add inline-editable title in chat header. On click, toggle to `<input>` with current name. On Enter/blur, call `PATCH /conversations/{id}` with new title. |
| Beta #13 | No trash/archive on chat history | **Needs own fix** | Add mouseover action buttons to conversation list items: archive (moves to "Archived" section), delete (with confirmation). Backend: add `archived` field to conversation model, filter archived from default list. |

### Knowledge Base

| # | Issue | Systemic? | Fix if needed |
|---|-------|-----------|---------------|
| Beta #17 | KB card expansion not working | Resolved by **S1** (wiring audit) — but needs content: | Add expanded view content: all keywords as tags, source type badge, quality score breakdown (6 dimensions), chunk count with list, ingested date, retrieval count. Increase expanded height to ~2x. |
| Beta #18 | KB cards need replace/re-generate buttons | **Needs own fix** | Add to artifact-card action row: (a) "Replace file" button → opens file picker, re-ingests to same artifact_id. (b) "Re-generate synopsis" button → calls `POST /artifacts/{id}/regenerate-synopsis` which re-runs `extract_metadata()`. |
| Beta #19 | Chat queries create separate KB items | **Needs own fix** (P2) | Add `conversation_id` and `source_conversation` fields to Artifact model. When feedback loop saves a response, group by conversation. KB UI shows conversation artifacts as expandable groups. |
| Beta #20 | KB title editable in card | **Needs own fix** | Add inline-editable title in artifact-card header. On click, toggle to input. On save, call `PATCH /artifacts/{id} {title: newTitle}`. |
| Beta #22 | DOCX import fails silently | **Needs own fix** | (a) Surface actual parser error message in frontend upload response — ensure `routers/upload.py` error handler includes the exception message text. (b) Add frontend error display with specific message. (c) Add `.doc` → "Unsupported: please convert to .docx" message in `parsers/registry.py:parse_file()`. |
| Beta #23 | Recent imports: max 4 expanded | **Needs own fix** | Default collapsed, max 4 visible, show "Show N more" link. Add scrollable container with max-height. Add resizable handle (CSS `resize: vertical` or drag handle). |
| Beta #24 | Sort options above artifacts | **Needs own fix** | Move sort row (by quality, date, name, relevance) to directly above the artifact list, below the upload/import actions. |
| Beta #26 | Auto-generate synopsis on import | **Needs own fix** | In `services/ingestion.py`, after successful ingest, if `skip_metadata=false`, ensure `extract_metadata()` always generates a summary field. If it fails (no LLM), use first 200 chars as fallback summary. Surface synopsis generation status in frontend. |
| Beta #27 | "Re-generate all synopses" button | **Needs own fix** (P2) | Add to Health tab: button triggers `POST /artifacts/regenerate-all-synopses` → backend iterates all artifacts missing synopses, queues LLM calls. Show progress indicator. |
| Orig #42 | External search returns no results | **Needs own fix** | Debug `DataSourceRegistry.query_all()`: check if any sources are enabled, check circuit breaker state, add logging. Frontend: show "No data sources enabled" if all sources are disabled, with link to enable in settings. |
| Beta #32 | External sources inconsistent, archive not shown | Partially resolved by **S4.4** (archive folder), remaining: | Unify data source display across Knowledge Console and Settings. Show all configured sources with status (enabled/disabled/error). Show archive folder as a "watched folder" data source. |

### Verification

| # | Issue | Systemic? | Fix if needed |
|---|-------|-----------|---------------|
| Orig #27 / Beta #11 | Dashboard "stream interrupted" | Resolved by **S2.4** | — |
| Orig #17 / Beta #5 | Verification offline | Resolved by **S2.1, S2.2, S2.7** | — |

### Settings

| # | Issue | Systemic? | Fix if needed |
|---|-------|-----------|---------------|
| Beta #25 | Health tab layout/display poor | **Needs own fix** | Redesign health tab: group services into categories (Infrastructure, AI Pipeline, Optional). Use card layout with status indicator, last-checked timestamp, and expandable detail. Add auto-refresh every 30 seconds. |
| Beta #28 | Platform capabilities by tier | **Needs own fix** | In System tab, organize capabilities into Core/Pro/Enterprise columns. Each capability shows icon + status (active/available/locked). Mouseover shows description and which tier unlocks it. |
| Beta #30 | Unclear editable vs read-only settings | **Needs own fix** | Add visual distinction: editable settings get subtle hover highlight + cursor-pointer. Read-only settings show lock icon + muted text + `cursor-default`. Add "(read-only)" label to computed/system values. |
| Beta #31 | Pipeline stages not explained | **Needs own fix** | Add info icon to each pipeline stage toggle. Tooltip explains what the stage does, when it runs, and what disabling it means. E.g., "Deduplication: prevents storing duplicate content. Disabling may create duplicate chunks." |
| Beta #33 | Feedback loop purpose unclear | **Needs own fix** (design first) | Research and document: What does the feedback loop save? When? How does it affect future responses? Write a design doc before implementing UX. Current behavior: saves assistant responses to KB. Proposed: make opt-in per conversation with clear "This response will be saved to your KB" indicator. |
| Beta #34 | Non-binary settings need recommended configs | **Needs own fix** | For every slider/dropdown setting, add a "Recommended" indicator. E.g., chunk size slider shows "Recommended: 400-512" range highlighted. Injection threshold dropdown shows "Recommended: Standard" with star icon. |
| Beta #29 | Sync deadlock [Errno 35] | Resolved by **S4.1, S4.5** | — |

### Model Management

| # | Issue | Systemic? | Fix if needed |
|---|-------|-----------|---------------|
| Beta #35 | 350 models, no management UX | **Needs own fix** (P2) | Create model management panel: (a) Virtual scrolling for 350+ model list. (b) Search/filter by name, provider, capability. (c) Sort by name, cost, context length. (d) "Add model" for custom model IDs. (e) Show installed vs available for Ollama models. (f) Model update detection (already has `GET /models/updates` endpoint). (g) Per-model context length and pricing display. |
| Beta #15 | Chinese models via OpenRouter | **Needs own fix** (policy decision) | Decision: USG compliance applies to bundled/default models only. OpenRouter passthrough should allow any model the user has access to. Fix: don't filter the OpenRouter model list. Add disclaimer on Chinese-origin models: "This model is from a non-US provider. Enterprise compliance policies may restrict use." |

---

## Section 3: System-Wide Wiring Checks

> One check per major subsystem. Run AFTER systemic fixes. Each check is a manual QA script.

---

### 3.1 Setup Wizard Flow

**Test every step, every button, every transition:**

```
Step 0 (Welcome):
  [ ] System check shows correct RAM, Docker status, Ollama status
  [ ] "Get Started" button advances to Step 1

Step 1 (API Keys):
  [ ] All 4 provider inputs visible (OpenRouter, OpenAI, Anthropic, xAI)
  [ ] Pre-configured keys show "(from .env)" label
  [ ] Test button works on pre-configured keys (Re-test flow)
  [ ] Test button works on manually entered keys (spinner → success/error)
  [ ] See/hide toggle works on manual keys
  [ ] "Custom Provider" expandable section visible and functional
  [ ] Credits balance shown for OpenRouter
  [ ] Usage rate explainer visible
  [ ] "Add Credits" link opens OpenRouter in new tab
  [ ] No Ollama references on this page
  [ ] Back/Next buttons work

Step 2 (Storage & Archive):
  [ ] Archive path input visible with current default (~/cerid-archive)
  [ ] Lightweight mode toggle works
  [ ] Auto-watch toggle works
  [ ] No domain selection visible
  [ ] Step title is "Storage & Archive"

Step 3 (Ollama):
  [ ] Hardware detection shown (RAM, CPU, GPU)
  [ ] Model recommendations shown with compatible/incompatible badges
  [ ] Download/Pull button works for recommended model
  [ ] Enable toggle works
  [ ] "Not detected" state shows install link

Step 4 (Review & Apply):
  [ ] ALL configured providers shown as "Ready" (not just OpenRouter + OpenAI)
  [ ] Storage path shown
  [ ] Ollama status shown
  [ ] "Apply Configuration" button works → success → auto-advance
  [ ] Error states show specific messages with fix actions

Step 5 (Service Health):
  [ ] All core services show status (Neo4j, ChromaDB, Redis, MCP)
  [ ] Verification pipeline shows correct status (not always "offline")
  [ ] Bifrost NOT shown
  [ ] Each service has tooltip
  [ ] Offline services have fix action button
  [ ] "Re-check" button for verification pipeline

Step 6 (Try It Out):
  [ ] PDF drag-drop works (does NOT open Adobe Acrobat)
  [ ] File picker button works
  [ ] Ingestion completes in <10 seconds for 2-page PDF
  [ ] Query input enabled after ingestion
  [ ] Query returns relevant results
  [ ] DOCX upload works OR shows clear error message

Step 7 (Choose Mode):
  [ ] "Clean & Simple" and "Advanced" options shown
  [ ] Provider/KB summary visible on each mode card
  [ ] Selection persists to Settings
  [ ] "Finish Setup" closes wizard
```

### 3.2 Chat Pipeline

```
Message Send → LLM → Response → Verification → Display:
  [ ] Type message → press Enter → loading indicator shown
  [ ] Response streams in real-time (tokens appear progressively)
  [ ] Model indicator shows which model was used
  [ ] KB context injected (if RAG enabled) — check injection badge
  [ ] Verification toggle works — enable → claims extracted → status bar shown
  [ ] Verification claims render inline (verified/unverified/uncertain badges)
  [ ] "Verify last response" button works on assistant messages
  [ ] Selecting a PREVIOUS message for verification works
  [ ] Expert verification mode works (shows cost tooltip, uses premium model)
  [ ] Privacy mode levels 0-4 change Lock icon color (default → green → yellow → orange → red)
  [ ] Feedback loop toggle works — saved responses appear in KB
  [ ] Enrichment button visible on assistant messages → triggers external search
  [ ] Dashboard metrics update after each message (tokens, timing)
```

### 3.3 Knowledge Base Pipeline

```
Upload → Parse → Chunk → Embed → Store → Query → Retrieve:
  [ ] Upload button works (file picker opens)
  [ ] Drag-drop works (file lands in upload zone, not OS handler)
  [ ] PDF: parses correctly, shows chunk count
  [ ] DOCX: parses correctly OR shows specific error
  [ ] XLSX: parses correctly with table formatting
  [ ] CSV: parses correctly
  [ ] RTF: parses correctly
  [ ] MD: parses correctly
  [ ] TXT: parses correctly
  [ ] .py/.js/.ts: parses correctly with code structure
  [ ] Unsupported type (.doc, .pptx): shows "Unsupported format" message
  [ ] Quality score shown on artifact card (>= 0.35)
  [ ] Star/Evergreen toggle buttons work
  [ ] Preview button shows full document content
  [ ] Expand button shows metadata/quality breakdown
  [ ] Chunk count badge has tooltip
  [ ] Query retrieves recently uploaded document
  [ ] Query returns relevant chunks (not random)
```

### 3.4 External API Pipeline

```
Query → Classify → Route → Fetch → Display → Optional Save:
  [ ] At least one external source enabled in Settings
  [ ] External section visible in Knowledge Console
  [ ] External section expanded by default
  [ ] External query returns results (DuckDuckGo, Wikipedia)
  [ ] Results display with source attribution
  [ ] "Save to KB" button on external results (if implemented)
  [ ] Custom API dialog opens and creates new source
  [ ] Custom API test button validates connectivity
```

### 3.5 Settings Persistence

```
Change → Save → Reload → Verify:
  [ ] Change a toggle → save → refresh page → toggle persists
  [ ] Change chunk size slider → save → refresh → value persists
  [ ] Change injection threshold → refresh → value persists
  [ ] Change RAG mode → refresh → value persists
  [ ] Change privacy level → refresh → value persists
  [ ] Section expand/collapse state persists across page loads
  [ ] Tier display shows correct tier (Community/Pro/Enterprise)
```

### 3.6 Health Monitoring

```
Service Check → Status → Display → Alert:
  [ ] Health page shows all services with correct status
  [ ] Stopping a Docker container → health page shows "unavailable"
  [ ] Restarting container → health page shows "healthy" within 30 seconds
  [ ] Degradation banner appears when critical service is down
  [ ] "Check now" button triggers immediate health refresh
  [ ] Health score (A-F grade) reflects actual service state
```

### 3.7 Memory System

```
Conversation → Extract → Store → Recall:
  [ ] Memories tab shows extracted memories
  [ ] Memory extraction triggers after conversation (if enabled)
  [ ] Memories display with type, confidence, source conversation
  [ ] Memory search works
  [ ] Memory edit works
  [ ] Memory delete works (with confirmation)
  [ ] Memories are recalled in relevant future conversations (RAG injection)
```

### 3.8 Analytics Pipeline

```
Event → Record → Aggregate → Display:
  [ ] Dashboard tab shows metrics (token usage, response timing)
  [ ] Metrics update after each chat interaction
  [ ] Time-series graphs render correctly
  [ ] Cost breakdown shows per-model spending
  [ ] Health score computation reflects real data
  [ ] Export/download of metrics works (if available)
```

---

## Section 4: Multi-OS Compatibility Evaluation

---

### 4.1 macOS (Primary Platform)

| Check | ARM (M1/M2/M3) | Intel |
|-------|----------------|-------|
| Docker Compose v2 | ✅ Docker Desktop 4.x | ✅ Docker Desktop 4.x |
| Volume mounts (`~/cerid-archive:/archive`) | ⚠️ virtiofs Errno 35 under concurrent access | ⚠️ Same virtiofs issue |
| File watcher (`watchdog`/`inotify`) | ⚠️ macOS uses `kqueue`, not `inotify`. Docker container sees `inotify` events from virtiofs — may have latency/missed events | Same |
| GPU passthrough | ❌ No GPU passthrough in Docker Desktop. Metal not accessible from Linux containers | ❌ No GPU in Docker |
| Ollama integration | ✅ Runs on HOST with Metal acceleration. Docker container connects via `host.docker.internal:11434` | ⚠️ Runs on HOST, CPU-only (no Metal) |
| Path separators | ✅ POSIX paths | ✅ POSIX paths |
| Process management | ✅ `start-cerid.sh` works | ✅ Works |
| RAM detection | ⚠️ `HOST_MEMORY_GB` env var needed (Docker reports VM memory) | Same |
| Homebrew Python conflict | ⚠️ Python 3.14 on host, 3.11 in Docker — ensure no host-side Python scripts depend on 3.11 features | Same |

**macOS-specific action items:**
1. [ ] Ensure `OLLAMA_URL` defaults to `http://host.docker.internal:11434` in Docker Compose (not `localhost`)
2. [ ] Test virtiofs retry logic (S4.2) on both ARM and Intel Macs
3. [ ] Verify `HOST_MEMORY_GB` is set correctly in docker-compose.yml via `$(sysctl -n hw.memsize | awk '{print int($1/1024/1024/1024)}')`

### 4.2 Linux (x86_64 and ARM64)

| Check | x86_64 (Ubuntu/Debian/Fedora) | ARM64 (Ubuntu Server, Raspberry Pi) |
|-------|-------------------------------|--------------------------------------|
| Docker Compose v2 | ✅ Native Docker Engine | ✅ Native Docker Engine |
| Volume mounts | ✅ Native filesystem, no virtiofs layer | ✅ Native filesystem |
| File watcher | ✅ `inotify` works natively | ✅ `inotify` works natively |
| GPU passthrough | ✅ NVIDIA Container Toolkit for CUDA GPUs | ⚠️ No NVIDIA on ARM. Some ARM GPUs not supported |
| Ollama integration | ✅ Runs on host with CUDA if available. Container connects via `localhost:11434` (host network) or Docker network | ⚠️ Ollama supports ARM64 but performance limited |
| Path separators | ✅ POSIX paths | ✅ POSIX paths |
| RAM detection | ✅ `/proc/meminfo` works directly (no Docker VM layer) | ✅ Same |
| ChromaDB embedding | ✅ Can use ONNX Runtime with AVX2 | ⚠️ ARM: no AVX2. Ensure ONNX runtime has ARM64 build or fall back to Python embedding |

**Linux-specific action items:**
1. [ ] Test with `docker compose` (v2 plugin) not just `docker-compose` (v1 standalone) — v1 is deprecated
2. [ ] Verify `nvidia-smi` detection in `/providers/ollama/recommendations` works inside Docker (needs NVIDIA Container Toolkit)
3. [ ] Test ChromaDB embedding performance on ARM64 — may need `RERANK_ONNX_FILENAME` override
4. [ ] Add `OLLAMA_URL=http://localhost:11434` default for Linux (not `host.docker.internal`)
5. [ ] Test with SELinux enabled (Fedora/RHEL) — volume mounts may need `:z` or `:Z` suffix

### 4.3 Windows (WSL2 + Docker Desktop)

| Check | WSL2 + Docker Desktop | Native Docker Desktop |
|-------|----------------------|----------------------|
| Docker Compose v2 | ✅ Via WSL2 backend | ✅ Via Hyper-V/WSL2 backend |
| Volume mounts | ⚠️ Cross-filesystem mounts (NTFS → ext4 via 9P) are slow. `~/cerid-archive` path needs Windows translation | ⚠️ Same — `/mnt/c/Users/...` paths are slow |
| File watcher | ⚠️ `inotify` works in WSL2 but cross-filesystem events from Windows may be delayed | ⚠️ Same |
| GPU passthrough | ✅ WSL2 supports CUDA passthrough (NVIDIA) | ✅ Same |
| Ollama integration | ✅ Install in WSL2, connects via localhost | ⚠️ If installed on Windows side, needs WSL2 network bridging |
| Path separators | ⚠️ WSL2 uses POSIX, but mounted Windows paths use `/mnt/c/...`. Scripts must handle both | ⚠️ Same |
| Process management | ⚠️ `start-cerid.sh` requires bash (WSL2 provides this, PowerShell does not) | ⚠️ Need PowerShell equivalent or batch file |
| RAM detection | ⚠️ WSL2 has configurable memory limit (`.wslconfig`). Default may be only 50% of host RAM | ⚠️ Same |

**Windows-specific action items:**
1. [ ] Add `start-cerid.ps1` PowerShell script (or document WSL2 requirement clearly)
2. [ ] Test archive path with Windows-style paths (`C:\Users\...` → `/mnt/c/Users/...`)
3. [ ] Document `.wslconfig` memory recommendation: `memory=12GB` minimum
4. [ ] Test Ollama URL: `http://localhost:11434` works in WSL2 for host-installed Ollama
5. [ ] Verify `validate-env.sh` works in WSL2 bash
6. [ ] Test with Docker Desktop WSL2 integration enabled

### 4.4 Cross-Platform Action Items Summary

| Priority | Item | Platforms Affected |
|----------|------|-------------------|
| HIGH | `OLLAMA_URL` default must vary: `host.docker.internal` (macOS/Windows), `localhost` (Linux) | All |
| HIGH | Archive path handling for Windows `/mnt/c/` paths | Windows |
| HIGH | virtiofs Errno 35 retry logic | macOS |
| MEDIUM | ChromaDB ARM64 embedding compatibility | Linux ARM64 |
| MEDIUM | NVIDIA GPU detection in Ollama recommendations endpoint | Linux, Windows |
| MEDIUM | PowerShell startup script | Windows |
| LOW | SELinux volume mount labels | Fedora/RHEL |
| LOW | `.wslconfig` documentation | Windows |

---

## Section 5: Ollama Architecture Evaluation

---

### 5.1 Architecture Verification

| Check | Status | Detail |
|-------|--------|--------|
| Ollama designed to install on HOST OS, not Docker | ✅ **Correct** | `ollama-step.tsx` links to `https://ollama.com/download` for host installation. Docker container connects via URL (`OLLAMA_URL`). No Ollama-in-Docker setup. |
| Hardware detection works | ✅ **Implemented** | `GET /providers/ollama/recommendations` detects RAM (`sysctl`/`/proc/meminfo`), CPU (`machdep.cpu.brand_string`/`lscpu`), GPU (macOS: `platform.machine()` for ARM detection, Linux: `nvidia-smi`). |
| Model download goes to host filesystem | ✅ **Correct** | `pullOllamaModel()` calls `POST /ollama/pull` which proxies to Ollama's API on the host. Ollama stores models in its own directory (`~/.ollama/models` on host). |
| Container connects to host Ollama | ⚠️ **Needs platform-aware default** | Currently defaults to `http://localhost:11434`. On macOS Docker, this should be `http://host.docker.internal:11434`. Auto-detection exists in `routers/providers.py:153-174` (tries both URLs). |

### 5.2 Hardware Profile Recommendations

Current recommendation logic (`providers.py:278-284`):

```python
if ram_gb >= 32:     recommended = "phi4:14b"
elif ram_gb >= 16:   recommended = "llama3.1:8b"
else:                recommended = "llama3.2:3b"
```

**Evaluation per hardware profile:**

| Profile | RAM | GPU | Current Rec | Correct? | Notes |
|---------|-----|-----|-------------|----------|-------|
| M1 8GB | 8 | Metal (unified) | `llama3.2:3b` | ✅ | 3B fits in 8GB. ~15 tokens/sec on Metal. Good for pipeline tasks. |
| M1 16GB | 16 | Metal (unified) | `llama3.1:8b` | ✅ | 8B fits comfortably. ~20 tokens/sec on Metal. |
| M2/M3 Pro 18-36GB | 18-36 | Metal (unified) | `llama3.1:8b` or `phi4:14b` | ⚠️ | M2 Pro 18GB could run 8B but 14B is tight. M3 Pro 36GB handles 14B well. Need VRAM-aware logic, not just total RAM. |
| NVIDIA RTX 3060 12GB VRAM | 16-64 system | CUDA 12GB | `llama3.1:8b` (16GB) or `phi4:14b` (32GB) | ⚠️ | System RAM != VRAM. 12GB VRAM can run 8B quantized but not 14B. Need VRAM detection. |
| NVIDIA RTX 3090 24GB VRAM | 32-128 system | CUDA 24GB | `phi4:14b` | ✅ | 24GB VRAM handles 14B easily. |
| CPU-only (Intel Mac, old Linux) | 8-32 | None | Based on RAM | ⚠️ | CPU inference is 2-5x slower. 8B on CPU is painful (~3 tokens/sec). Should recommend 3B even with 16GB RAM if no GPU. |

**Issues found:**

1. **No VRAM detection.** The endpoint detects GPU name via `nvidia-smi` but doesn't query VRAM. It uses system RAM for recommendations, which is wrong for discrete GPUs. A machine with 64GB RAM but 6GB VRAM should get `llama3.2:3b`, not `phi4:14b`.
2. **No GPU-vs-CPU inference speed communication.** The user doesn't know if they'll get 20 tokens/sec (Metal) or 3 tokens/sec (CPU).
3. **Apple Silicon unified memory not distinguished.** M1/M2/M3 share RAM between CPU and GPU. Total RAM = available for model. This is actually correct for Apple Silicon but should be explained.
4. **No Windows GPU detection.** The hardware detection only checks macOS (`sysctl`) and Linux (`nvidia-smi`). Windows/WSL2 needs `nvidia-smi` from the WSL2 side.

### 5.3 Ollama Wizard UX Gaps

| Gap | Current State | Required |
|-----|--------------|----------|
| Hardware profile display | Shows RAM, CPU, GPU as text | Show as a visual card: "Your Machine: M1 Pro 16GB, Apple Metal GPU". Add performance estimate: "Expected speed: ~20 tokens/sec with 8B model." |
| Model comparison | Lists 3 models with descriptions | Show size, download time estimate, inference speed estimate per model. Mark incompatible models as greyed out with "Needs Xb more RAM." |
| Download progress | Shows "Starting download..." text | Show progress bar with bytes downloaded / total. Ollama's pull API streams progress events — parse and display. |
| Experience explanation | Says "Ollama runs AI models locally for free" | Explain specifically: "With your hardware, the 8B model will respond at ~20 tokens/sec. This is used for background tasks (verification, classification) — your main chat uses your cloud provider." |
| Impact on system resources | Not communicated | Show: "While running, Ollama uses ~X GB of RAM. Your system has X GB total. This leaves X GB for other apps." |
| What happens without Ollama | Says "You can enable it later" | Explain: "Without Ollama, Cerid uses your cloud provider for all tasks including verification and classification. This works fine but uses more API credits (~$0.001 per verification)." |
| Semi-automated install | Links to ollama.com/download | Detect OS → show OS-specific install command. macOS: `brew install ollama`. Linux: `curl -fsSL https://ollama.ai/install.sh | sh`. Windows: link to installer. After install, auto-detect and refresh. |
| Recommended configs table | Single recommended model | Show table with hardware profile → recommended model → expected performance. E.g., "8GB Mac → llama3.2:3b → 15 tok/s → Good for classification." |

### 5.4 Ollama Action Items

| Priority | Item | Detail |
|----------|------|--------|
| HIGH | Add VRAM detection | Linux: parse `nvidia-smi --query-gpu=memory.total --format=csv,noheader`. macOS: unified memory = total RAM (correct as-is but explain). Windows/WSL2: same nvidia-smi. |
| HIGH | CPU-only penalty | If no GPU detected AND system is not Apple Silicon, reduce model recommendation by one tier (e.g., 16GB CPU-only → recommend 3B not 8B). Add note: "CPU inference is slower. Consider a smaller model." |
| HIGH | Inference speed estimates | Add `expected_tokens_per_sec` field to each model in recommendations. Calculate based on hardware profile: Metal=fast, CUDA=fast, CPU=slow. Display in wizard. |
| MEDIUM | Download progress bar | Parse Ollama pull API streaming response. Show progress in `ollama-step.tsx` pull UI. |
| MEDIUM | Resource impact display | Calculate: model size / total RAM = % RAM usage. Display: "This model uses X GB (Y% of your memory)." |
| MEDIUM | Semi-automated install | Detect OS in system check. Show platform-specific install command. Add "Detect Ollama" refresh button after install. |
| MEDIUM | Platform-aware OLLAMA_URL | In docker-compose.yml, add comment and auto-detect: if macOS, use `host.docker.internal:11434`. In `routers/providers.py`, the auto-detection already tries both URLs — verify this works reliably. |
| LOW | Cost comparison | Show "With Ollama: $0/month for pipeline tasks. Without: ~$X/month based on usage." |
| LOW | Multiple model support | Allow configuring different models for different pipeline stages. E.g., 3B for classification, 8B for verification. |

---

## Execution Order

```
Week 1: Systemic Fixes (S1-S4)
  Day 1: S3 (provider detection unification) — unblocks wizard testing
  Day 1: S2 (LLM cascade) — unblocks verification
  Day 2: S1 (wiring audit) — unblocks enrichment, LED, expansion
  Day 2: S4 (virtiofs handling) — unblocks sync
  Day 3: Verification testing — run Section 3.1 (wizard) and 3.2 (chat) checks

Week 2: Individual Fixes (Section 2)
  Day 4: Setup wizard remaining (Beta #2 location selector, Beta #3 Ollama UX)
  Day 5: Chat fixes (Beta #9 badge, #12 sub-menus, #21 editable names)
  Day 5: KB fixes (Beta #17 expansion content, #18 action buttons, #22 DOCX errors)
  Day 6: Settings fixes (Beta #25 health redesign, #28 tier display, #30 edit distinction)
  Day 7: Run Section 3 wiring checks (all 8 subsystems)

Week 3: Platform & Ollama (Sections 4-5)
  Day 8: Ollama wizard UX (hardware display, speed estimates, download progress)
  Day 9: Multi-OS testing (macOS ARM, Linux x86_64, WSL2)
  Day 10: Model management UX (Beta #35) and remaining P2 items

Backlog:
  - Chat queries → conversation grouping (Beta #19)
  - Re-generate all synopses (Beta #27)
  - Feedback loop design (Beta #33)
  - Model management full UX (Beta #35)
```

---

*Final punch list generated 2026-04-04. Incorporates aggregated punch list, root cause analysis, and expanded evaluations.*
