# Cerid AI — Root Cause Analysis of Beta Test Clusters

**Date:** 2026-04-04
**Method:** Source code trace analysis across frontend + backend codebases
**Scope:** 6 failure clusters from 2026-04-04 beta test

---

## Cluster 1: Provider Detection Failures

### Symptoms
1. Test buttons don't work on Keys page
2. Review & Apply only shows OpenRouter + OpenAI
3. Settings shows providers as offline

### Root Cause: THREE SEPARATE BUGS, not one

**Bug 1A: Test buttons — `onEnrich` pattern reveals a wiring gap**

The `ApiKeyInput` component (`api-key-input.tsx:39-76`) has a correct `handleTest` implementation that calls `validateProviderKey()`. The button is wired (`onClick={handleTest}`, line 138), and the backend endpoint `POST /setup/validate-key` (setup.py:424-437) is functional — it delegates to `config/providers.py:validate_provider_key()` which makes real HTTP calls to provider APIs.

**The actual bug:** The Test button is `disabled={!value.trim() || status === "checking"}` (line 139). For **preconfigured** keys (detected from `.env`), `value` is initialized as `""` (line 33: `const [value, setValue] = useState("")`) and the preconfigured flag just sets `status` to `"valid"`. The input is empty, so `!value.trim()` is `true`, which means **the Test button is permanently disabled for pre-detected keys**. The user sees a green check from the preconfigured state but cannot actually test the key because the input is empty.

The **see/hide button** (`setVisible(!visible)`, line 122) toggles between `type="text"` and `type="password"` on the Input. This should work. If it doesn't, the issue is likely that for preconfigured keys, `value` is `""` — toggling visibility on an empty input shows nothing. The user may think it's broken because there's nothing to see.

**Fix:** When `preconfigured=true`, either pre-populate `value` with a masked placeholder that enables the Test button, or add a separate "Re-test" button that validates the `.env` key directly without requiring user input.

**Bug 1B: Review & Apply — Only OpenRouter + OpenAI detected**

`detect_provider_status()` in `setup.py:150-164` correctly maps all 4 providers and strips quotes. The `_KEY_TO_PROVIDER` map (line 134-139) includes all 4: `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`.

The issue is in how the **frontend consumes** this data. Looking at `setup-wizard.tsx:508-595`, the wizard renders `ApiKeyInput` with `preconfigured={state.keys.openrouter.key === "(configured)"}`. The wizard state is populated from `fetchSetupStatus()` which returns `provider_status` and `configured_providers`.

**Critical finding:** The wizard hydration code (which I found referenced at the grep result lines 508, 579, 587, 595) checks `state.keys.<provider>.key === "(configured)"`. This state is set via `SET_KEY` dispatch actions. The question is: **does the wizard dispatch `SET_KEY` for providers found in `provider_status`?**

Tracing the flow: `fetchSetupStatus()` returns `configured_providers: ["openrouter", "openai", "anthropic", "xai"]` and `provider_status: {openrouter: {configured: true, ...}, ...}`. But the **reducer** that processes this is key. If the wizard only dispatches `SET_KEY` for providers in `configured_providers` (a list), and uses the provider name as the key, but the reducer expects a different key format, or if there's a conditional that only processes the first 2 providers... that's the bug.

**Most likely root cause:** The wizard hydration code reads `configured_providers` (the list from `_configured_providers()`) but there may be a subtle bug where it only processes providers that match `_REQUIRED_KEYS` (which is just `["OPENROUTER_API_KEY"]`). The `_OPTIONAL_KEYS` are `["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY"]`. Check if `SetupStatus.optional_keys` is being used for display filtering — it returns the **variable names** (e.g., `"OPENAI_API_KEY"`) not the **provider IDs** (e.g., `"openai"`), which could cause a mapping mismatch.

**Bug 1C: Settings providers offline — `get_configured_providers()` vs `detect_provider_status()`**

In `config/providers.py:172-210`, `get_configured_providers()` checks `os.getenv(env_var, "")` but **does NOT strip quotes** the same way. Line 194: `api_key = os.getenv(env_var, "")` — bare `getenv` without `.strip().strip('"')`. If the `.env` file has `ANTHROPIC_API_KEY="sk-ant-..."` (with quotes), this function gets the quoted value and considers it "set" (it's not empty), but the actual API calls may fail because the key has literal `"` characters.

Meanwhile, `detect_provider_status()` in `setup.py:158` does `.strip().strip('"').strip("'")`. **The two functions disagree on what "configured" means.** The settings page likely calls the `/providers` endpoint which uses `get_configured_providers()` from `config/providers.py` — the version WITHOUT quote stripping for the key_set check.

**Wait — actually looking more carefully at providers.py:68:** `api_key = os.getenv(env_var, "").strip().strip('"').strip("'")`. The `list_providers` endpoint DOES strip quotes. But `get_configured_providers()` at line 194 does NOT. So `/providers/configured` (line 91-95) returns results without quote stripping, while `/providers` (line 62-88) does strip quotes. If the Settings page calls `/providers/configured`, it gets the unstripped version.

### Additional Issues Discovered
- **Three different provider detection code paths** remain: `setup.py:detect_provider_status()`, `providers.py:list_providers()`, and `providers.py:get_configured_providers()`. The WP2 implementation added `detect_provider_status()` as a canonical function but didn't make the other two use it.
- The `providers.py:get_configured_providers()` at line 194 is the **only** path that doesn't strip quotes, and it's the one used by `/providers/configured`.

### Proper Fix
1. Make `get_configured_providers()` delegate to a shared quote-stripping utility (or call `detect_provider_status()` directly)
2. Frontend: pre-populate ApiKeyInput value for preconfigured keys with masked value, and enable the Test button
3. Verify wizard hydration dispatches `SET_KEY` for ALL providers in `provider_status`, not just those in `configured_providers`

---

## Cluster 2: Verification Pipeline Broken

### Symptoms
1. Verification shows offline in health check
2. "No factual claims to verify" when claims exist
3. "Stream interrupted" on verification dashboard
4. Selecting previous responses doesn't work

### Root Cause: CASCADING FAILURE from self-test + LLM dependency

**Bug 2A: Verification shows offline**

The health endpoint (`setup.py:400-409`) reads the self-test result from Redis:
```python
vp_result = get_self_test_status_sync(_redis)
vp_status = "healthy" if vp_result and vp_result.get("status") == "pass" else "unavailable"
```

`run_verification_self_test()` (startup_self_test.py:38-86) calls `extract_claims()` with a synthetic response. `extract_claims()` (extraction.py) attempts LLM-based extraction first (`_extract_claims_llm`), which calls `call_llm()` or `call_internal_llm()`.

**The critical issue:** The self-test runs at **startup** — before API keys are fully propagated to the environment. In Docker, the MCP container starts, runs the self-test, and the self-test tries to make an LLM call. If OpenRouter isn't available yet (or the key hasn't been applied via the wizard), `extract_claims()` falls back to heuristic extraction. But the heuristic extractor may not find 1+ claims in the test response (it's three simple factual sentences — the heuristic checks for `STRONG_FACTUAL_PATTERNS` and `FACTUAL_PATTERNS` regex patterns). If heuristic extraction returns 0 claims, `status` is set to `"fail"`.

The Redis key `cerid:verification:self_test:last_result` is set with TTL 86400 (24 hours). So a failed self-test at startup **persists as "unavailable" for 24 hours** even after API keys are configured.

**Bug 2B: "No factual claims to verify"**

The verification stream (`use-verification-stream.ts`) sends `responseText` to `POST /agent/verify-stream`. The backend calls `extract_claims(response_text, user_query=...)`.

Looking at the extraction code: `extract_claims()` tries LLM extraction first. If the LLM is unavailable (circuit breaker tripped, timeout, etc.), it falls back to heuristic extraction. The heuristic extracts sentences matching `FACTUAL_PATTERNS` regex (patterns.py).

**Key issue:** If the response is a short conversational answer (e.g., "Here's what I found about X..."), the heuristic may genuinely find no claims. But the user sees factual content and expects claims to be extracted. The gap is: **LLM extraction works much better than heuristic, but LLM extraction requires a working LLM provider.** If Bifrost is down and OpenRouter has issues, extraction degrades to heuristic, which is extremely conservative.

Also: the `verify_response_streaming()` function receives the response text. If the text is empty or very short, it returns early with "no claims." Check whether the frontend is passing the full `responseText` or a truncated version.

**Bug 2C: "Stream interrupted"**

The frontend uses `fetch()` with `ReadableStream` to read SSE events from `POST /agent/verify-stream` (use-verification-stream.ts:78+). The stream can break if:
1. The backend SSE connection times out (nginx proxy timeout default: 60s)
2. Docker container restarts during verification
3. An unhandled exception in `verify_response_streaming()` causes the generator to exit without sending a `done` event

The most likely cause: the verification endpoint tries to make LLM calls for each claim. If the LLM provider is flaky or the circuit breaker trips mid-stream, the generator throws an exception that isn't caught as an SSE error event, causing the connection to drop. The frontend sees this as "stream interrupted."

**Bug 2D: Selecting previous responses doesn't work**

This is a **regression**. The `onSelectForVerification` prop on `MessageBubble` (line 356 in message-bubble.tsx) needs to be wired from the parent chat panel. If the parent component doesn't pass this prop, clicking a previous message for verification does nothing. The WP8 changes to the chat toolbar may have disrupted the prop threading.

### Additional Issues Discovered
- The self-test result persists for 24h. There's no mechanism to re-run the self-test after initial configuration. The "Check now" button in the health dashboard would need to trigger `run_verification_self_test()` again.
- The verification pipeline is entirely dependent on LLM availability. If no LLM is available (common during first setup), ALL verification features appear broken.
- The heuristic claim extractor is too conservative as a fallback. It likely misses claims like "Python was created by Guido van Rossum" because the regex patterns don't match that sentence structure.

### Proper Fix
1. Re-run self-test after API keys are configured (add a `POST /setup/retest-verification` endpoint)
2. Add a manual "Re-check" button in the health dashboard that triggers re-test
3. Add SSE error events for all exception paths in `verify_response_streaming()`
4. Verify `onSelectForVerification` is threaded from the chat panel to `MessageBubble`
5. Improve heuristic extractor to catch more claim patterns (or always return the self-test as "pass" if heuristic found >=1 claim from the test response)

---

## Cluster 3: PDF/Ingestion Failures

### Symptoms
1. ~25 seconds for 2-page PDF (target: <5s)
2. Query fails after ingestion
3. DOCX import fails silently

### Root Cause: MULTIPLE BOTTLENECKS + macOS Docker virtiofs issue

**Bug 3A: Ingestion speed — 25 seconds**

The ingestion pipeline (`services/ingestion.py`) does these steps:
1. `parse_file()` — PDF parsing via pdfplumber (line 703)
2. `ai_categorize()` — LLM call for domain classification
3. `extract_metadata()` — LLM call for summary/keywords
4. `chunk_text()` — text splitting
5. `collection.add()` — ChromaDB embedding + storage
6. `graph.create_artifact()` — Neo4j metadata

WP1 added `skip_quality=True` for wizard ingestion (ingestion.py:314,528) and `categorize_mode` passthrough (line 707-722). But **the main bottleneck was never addressed:**

The `extract_metadata()` call makes an LLM API call to generate a summary and keywords. This takes 2-5 seconds. The `ai_categorize()` call takes another 2-5 seconds. Even if `categorize_mode="manual"` skips AI categorization, `extract_metadata()` is still called and makes its own LLM call.

Additionally, the **embedding step** (`collection.add()`) generates embeddings for all chunks. ChromaDB's default embedding function uses the `all-MiniLM-L6-v2` model (or whatever is configured). If running in Docker on macOS with no GPU, embedding generation for even a few chunks takes 5-15 seconds.

**Key discovery:** Looking at the upload router (`upload.py:35`), there's `skip_quality: bool = Query(False)`. The wizard frontend would need to pass `?skip_quality=true` in the query string. But the frontend calls `uploadFile()` which likely doesn't pass this parameter — need to verify the frontend upload function.

The 25-second timing breakdown is likely:
- PDF parsing: 0.5-1s
- Metadata extraction (LLM): 3-5s
- Embedding generation: 10-15s (CPU, no GPU)
- ChromaDB write: 0.5-1s
- Neo4j write: 0.5-1s

The embedding step is the real killer on macOS Docker.

**Bug 3B: Query fails after ingestion**

The WP1 implementation added a read-back check (`ingestion.py:521-525`):
```python
try:
    collection.get(ids=[chunk_ids[0]])
except (ValueError, KeyError, IndexError):
    pass  # Non-critical — query retry handles eventual consistency
```

This is a **no-op**: the read-back catch silently passes on failure. It confirms nothing. ChromaDB 0.5.x uses eventual consistency — `collection.add()` returns before embeddings are indexed. The `collection.get()` by ID may succeed (the document is stored) but the **embedding index** isn't ready for similarity search yet.

The query path uses `collection.query()` which does similarity search, not ID lookup. The documents won't appear in similarity search until the embedding index is rebuilt, which can take 1-5 seconds after the write.

**The wizard's `handleQuery` does not implement retry logic.** The implementation plan specified "add retry logic: if first query returns 0 results, wait 2s and retry once" but this was either not implemented or not effective.

**Bug 3C: DOCX import fails silently**

The DOCX parser (`parsers/office.py:17-50`) uses `python-docx` (requirement confirmed at `requirements.txt:28`). The parser catches exceptions and raises `ValueError` with a descriptive message. However, `python-docx` can only parse `.docx` files (Office Open XML format).

**Critical finding:** Old-style `.doc` files (binary format, pre-2007) are NOT supported by `python-docx`. If the user has a file with `.docx` extension but it's actually a `.doc` file (common with email attachments), or if the file is a `.docx` with macros (`.docm`), `python-docx` will throw an exception.

The parse error should propagate up to the upload endpoint and return an HTTP error. But looking at the upload flow: `routers/upload.py` catches errors from `ingest_content()` but the error may be caught at a higher level and returned as a generic "ingestion failed" without the actual error message reaching the frontend.

**Additional finding:** RTF is handled by `parsers/ebook.py:121` via `@register_parser([".rtf"])`. This is a separate parser that likely uses `striprtf` or similar. RTF is simpler to parse, explaining why it works when DOCX doesn't.

The **missing file types** that will silently fail:
- `.doc` (old Word binary format) — no parser registered
- `.docm` (Word with macros) — no parser registered
- `.pptx`, `.ppt` — no parser registered
- `.odt` (OpenDocument) — no parser registered
- `.pages` (Apple Pages) — no parser registered

### Additional Issues Discovered
- The embedding bottleneck is fundamental to macOS Docker performance. The only real fix is either (a) using a pre-computed embedding API (OpenAI embeddings, etc.) or (b) using a lighter embedding model, or (c) running embedding on the host GPU via Ollama.
- The `skip_quality` flag is available but may not be passed from the wizard frontend.
- The DOCX parser error message (`"Failed to read DOCX"`) is descriptive, but the error propagation path to the frontend may swallow it.

### Proper Fix
1. **Speed:** For wizard context, skip `extract_metadata()` LLM call too (not just quality scoring). Use a lightweight local summary (first 200 chars) instead. This saves 3-5s.
2. **Speed:** Consider using OpenAI/OpenRouter embedding API instead of local embedding model in Docker. This moves the 10-15s CPU bottleneck to a <1s API call.
3. **Query reliability:** Add actual retry logic in the wizard frontend: poll `GET /artifacts?sort=recent&limit=1` every 500ms for up to 5s, then query once the artifact appears.
4. **DOCX:** Surface the actual parser error message in the frontend. Add a help message: "If import fails, try converting to PDF or RTF first."
5. **File type coverage:** Register parsers for `.doc`, `.pptx`, `.odt` using appropriate libraries, or show clear "unsupported format" messages.

---

## Cluster 4: Frontend Components Not Rendering

### Symptoms
1. KB card expansion not working
2. Chat enrichment buttons not visible
3. Console LED not visible
4. Knowledge Console config panel missing

### Root Cause: CODE EXISTS but is NOT WIRED to parent components

**Bug 4A: KB card expansion**

The `artifact-card.tsx` has expansion state (`line 65: const [expanded, setExpanded] = useState(false)`) and toggle logic (`line 129: setExpanded((prev) => !prev)`). The expanded state controls content display (`line 252: !expanded && "line-clamp-2"`).

The code exists and should work. However, the toggle is on the card container click (line 129). If the card has interactive child elements (buttons, links) that stop propagation, the click never reaches the container. Or the expanded content may be present but visually identical to collapsed (the `line-clamp-2` class is only on the content div, so if the content is very short, expanding looks the same).

**More likely:** The issue is that expansion only removes the `line-clamp-2` class on the summary text. The implementation plan specified "Increase expanded height to ~2x current" and "show: full summary, all keywords as tags, source metadata, quality breakdown, chunk list." These additional data elements were likely **never added** — the expand just removes text clamping, which is barely noticeable for short summaries. The user expects a dramatically different expanded view.

**Bug 4B: Chat enrichment buttons not visible**

The `MessageBubble` component (`message-bubble.tsx:563-580`) renders the Globe enrichment button **only when `onEnrich` prop is passed** (`{onEnrich && (...)}` at line 563).

**`onEnrich` is NEVER passed from the parent.** Grepping for `onEnrich=` and `handleEnrich` across the entire frontend returns **zero results** outside of `message-bubble.tsx` itself. WP11 added the button to `MessageBubble` but **never wired the `onEnrich` callback from the chat panel component.** The button exists in the component code but is never rendered because the conditional guard `{onEnrich && ...}` always evaluates to false.

**Bug 4C: Console LED not visible**

Grepping for `pulse`, `led`, `activityLed`, `unreadCount` in `sidebar.tsx` returns NO matches. WP12 (`bcf3e85`) was supposed to add a dot indicator next to "Console" in the sidebar, but the sidebar code only has basic pane definitions:
```
{ pane: "knowledge", icon: Database, label: "Knowledge" }
```
No LED component, no animation CSS, no activity state tracking. The LED was either **never implemented** or was added to a different component that isn't rendered in the sidebar.

**Bug 4D: Knowledge Console config panel**

`ConsoleConfigBar` exists in `knowledge-console.tsx:236` and is rendered at line 358:
```tsx
<ConsoleConfigBar ragMode={ragMode} onRagModeChange={onRagModeChange} />
```

This should be visible. The beta tester reports "missing fixed config/interface options at top." This could mean:
1. The `ConsoleConfigBar` only shows the RAG mode selector (which was made read-only by WP12), and the user expects more controls (injection threshold, settings gear, etc.)
2. The config bar may be scrolled out of view if the knowledge panel content pushes it off-screen
3. The "fixed" in "fixed config" means the user wants it to be `position: sticky` at the top, but it scrolls with the content

### Additional Issues Discovered
- **Pattern:** Multiple WP implementations added component code to leaf components but never wired the props from parent components. This suggests the implementations were done bottom-up (build the component) without top-down integration (wire it into the app).
- The enrichment button backend endpoint (`POST /agent/enrich`) was likely also never created (WP11 was a P2 backlog item that may have been partially implemented).
- The Console LED requires a cross-component event bus (activity events from queries, ingestion, verification piped to the sidebar). This is architecturally non-trivial and was likely stubbed out.

### Proper Fix
1. **Enrichment:** Create the `handleEnrich` callback in the chat panel, pass it as `onEnrich` to `MessageBubble`. Implement the backend `POST /agent/enrich` endpoint.
2. **KB cards:** Add expanded view content (keyword tags, metadata, quality breakdown) — not just text unclamping.
3. **Console LED:** Implement activity event tracking (React Context or lightweight event emitter), add the LED dot to sidebar pane items.
4. **Config bar:** Make it `sticky top-0` with a z-index so it doesn't scroll away.

---

## Cluster 5: File Type Support

### Symptoms
1. DOCX fails, RTF works, PDF slow

### Root Cause: Parser registry gaps + dependency issues

**Registry analysis:**

```
REGISTERED PARSERS (from parsers/ grep):
  .pdf    → parsers/pdf.py (pdfplumber)
  .epub   → parsers/ebook.py
  .rtf    → parsers/ebook.py
  .py     → parsers/code_ast.py
  .js/.ts/.jsx/.tsx → parsers/code_ast.py
  .eml    → parsers/email.py
  .mbox   → parsers/email.py
  .docx   → parsers/office.py (python-docx)
  .xlsx   → parsers/office.py (openpyxl)
  .csv/.tsv → parsers/structured.py
  .html/.htm → parsers/structured.py
  .md/.markdown → parsers/structured.py
  .json/.yaml/.yml/.xml/.toml → parsers/structured.py (line 174)
  .txt    → (likely in structured.py)
```

**MISSING file types that users will try:**
- `.doc` (old Word) — **NOT REGISTERED** — will throw "Unsupported file type"
- `.pptx` / `.ppt` (PowerPoint) — NOT REGISTERED
- `.odt` (OpenDocument Text) — NOT REGISTERED
- `.ods` (OpenDocument Spreadsheet) — NOT REGISTERED
- `.pages` (Apple Pages) — NOT REGISTERED
- `.numbers` (Apple Numbers) — NOT REGISTERED
- `.docm` (Word with macros) — NOT REGISTERED
- `.xlsm` (Excel with macros) — NOT REGISTERED
- `.msg` (Outlook email) — NOT REGISTERED
- `.log` — NOT REGISTERED (common for developers)

**DOCX failure analysis:**

The `python-docx` library (requirements.txt:28, `>=0.8,<2`) is a pure-Python library that should work in Docker. The parser code (`office.py:17-50`) is straightforward. The most likely failure mode:

1. **File corruption during upload:** The upload endpoint receives the file as multipart form data. If the file isn't fully written to disk before parsing starts, `python-docx` will fail.
2. **Memory issue:** Large DOCX files with embedded images can be very memory-heavy.
3. **Password-protected DOCX:** `python-docx` cannot open encrypted/password-protected files.
4. **Macro-enabled files:** `.docm` files won't match the `.docx` parser registration.

The error should propagate with a clear message ("Failed to read DOCX: ..."), but the frontend may not be displaying the error detail from the API response.

### Additional Issues Discovered
- **No `.log` or `.txt` with auto-detection**: Text files are likely handled but the registry needs explicit `.log`, `.cfg`, `.ini`, `.env` extensions.
- **No image OCR**: Image files (.png, .jpg) require the OCR plugin which is BSL-1.1 (Pro tier). Community users get no image support.
- **Parser errors are not user-friendly**: The ValueError messages include technical details but the frontend likely shows a generic "Import failed" message.

### Proper Fix
1. Add `.doc` support via `antiword` or `textract` library (or show clear "Please convert to .docx" message)
2. Add `.pptx` support via `python-pptx` library
3. Add `.log`, `.cfg`, `.ini`, `.env` extensions to the text/markdown parser
4. Surface actual parser error messages in the frontend upload response
5. Add a file type validation check in the frontend upload flow that warns before uploading unsupported types

---

## Cluster 6: Data Persistence / Sync

### Symptoms
1. Sync status shows "[Errno 35] Resource deadlock avoided"
2. Archive folder not shown as watched folder
3. Chat queries creating separate KB items

### Root Cause: macOS Docker virtiofs + architectural gap

**Bug 6A: [Errno 35] — macOS Docker virtiofs known issue**

This is a **known, documented** issue in the codebase. `scripts/scan_ingest.py:101-108` explicitly states:

> "This avoids macOS Docker virtiofs Errno 35 issues with /ingest_file, which reads files inside the container via the bind mount."
> "On macOS Docker, /ingest_file crashes the container due to virtiofs Errno 35."

`Errno 35` is `EAGAIN` / "Resource temporarily unavailable" on macOS. The virtiofs filesystem used by Docker Desktop for Mac has known issues with concurrent file access across the host/container boundary. When the sync system tries to read files from the mounted archive directory while another process (the file watcher, Dropbox, or the host) is accessing the same file, virtiofs returns `EDEADLK` (Resource deadlock avoided).

The sync status endpoint (`routers/sync.py:99-114`) calls `compare_status()` which reads from the sync directory. If the sync directory is on a virtiofs mount, concurrent access from the watcher script or Dropbox triggers the deadlock.

**This is an OS/Docker-level issue, not a Cerid bug.** But it should be handled gracefully instead of showing the raw error.

**Bug 6B: Archive folder not shown as watched folder**

The wizard's Storage & Archive step saves the archive path via `ConfigureRequest.archive_path` → `_update_env_file({"WATCH_FOLDER": req.archive_path})` (setup.py:502-503). This sets the `WATCH_FOLDER` env var.

But the **watched folders API** (`routers/watched_folders.py`) manages folders in a separate Redis-backed store. Setting `WATCH_FOLDER` env var only configures the legacy `scripts/watch_ingest.py` watcher script. The new watched folders system requires an explicit `POST /watched-folders` API call to register the folder.

**The wizard never creates a watched folder entry.** It only sets the env var. The watched folders UI reads from the API, which returns nothing because no folder was registered via the API.

**Bug 6C: Chat queries creating separate KB items**

This is **by design**, not a bug, but it's a UX gap. Each query response that gets saved to KB (via the feedback loop or manual save) creates a new artifact in Neo4j/ChromaDB. There's no concept of "conversation artifact" that groups multiple Q&A exchanges into one item.

The ingestion pipeline (`services/ingestion.py`) creates one artifact per `ingest_content()` call. The chat system calls `ingest_content()` per response, not per conversation. Implementing conversation-level grouping would require:
1. A new artifact type ("conversation") in Neo4j
2. Appending to existing artifacts instead of creating new ones
3. Conversation boundary detection

### Additional Issues Discovered
- The legacy `WATCH_FOLDER` env var and the new watched folders API are **parallel systems that don't talk to each other**. This will confuse users who set up archive watching via the wizard and see nothing in the watched folders UI.
- The virtiofs Errno 35 will also affect file ingestion from the archive directory, not just sync. Any file operation that crosses the Docker mount boundary is at risk.
- Dropbox sync + Docker mount + file watcher creates a triple-contention scenario. All three are trying to access files in `~/cerid-archive/` simultaneously.

### Proper Fix
1. **Errno 35:** Catch `OSError` with errno 35 in `sync/status.py:compare_status()` and return a user-friendly message: "Sync status unavailable — file system busy. This is common with Docker on macOS."
2. **Archive folder:** After setting `WATCH_FOLDER` env var, also call the watched folders API to register the folder. Or unify the two systems.
3. **Chat KB items:** Add a `conversation_id` field to artifacts, allow grouping/merging in the KB UI.

---

## Summary: Systemic Issues

### Pattern 1: Implementation without Integration
WP11, WP12, and parts of WP9 added component code without wiring it to parent components. The enrichment button, console LED, and expanded card views all exist in source but are never rendered because props aren't passed or events aren't connected. **This suggests a bottom-up implementation approach without top-down integration testing.**

### Pattern 2: LLM Dependency Cascade
The verification pipeline, metadata extraction, AI categorization, and quality scoring ALL depend on a working LLM provider. During first setup (the wizard flow), no LLM is available yet. This causes cascading failures: verification self-test fails → health shows offline → ingestion is slow (waits for LLM timeout) → query fails (metadata not extracted properly).

**Fix:** All LLM-dependent features need graceful degradation during setup. Use heuristics/defaults when no LLM is available, and re-test when keys are configured.

### Pattern 3: Parallel Detection Systems
Provider detection has 3 code paths. Watched folders has 2 systems (env var + API). Health has multiple check mechanisms. Each was added in a different sprint without consolidating with existing code. **Fix:** Establish single canonical functions and deprecate the alternatives.

### Pattern 4: macOS Docker virtiofs
The `Errno 35` issue affects ingestion, sync, and file watching. The codebase already has workarounds in `scan_ingest.py` but they aren't applied consistently across all file-touching code paths.

---

*Analysis generated 2026-04-04 from source code trace of `~/Develop/cerid-ai` at HEAD (`e15cacc`).*
