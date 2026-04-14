# Cerid AI — Implementation Plan v2

**Date:** 2026-04-04
**Source:** `FINAL_PUNCH_LIST_V2_2026-04-04.md`, `ROOT_CAUSE_ANALYSIS_2026-04-04.md`
**Commit baseline:** `e15cacc` (HEAD as of 2026-04-04)
**Repo:** `Cerid-AI/cerid-ai-internal` (private) — sync to `cerid-ai` (public) after internal clears

---

## Section 0: Conventions

### Workflow

1. **Internal repo first.** All work on `~/Develop/cerid-ai`. Public repo is a distribution target.
2. **AI slop audit** after every 2 work packages — automated + manual:
   ```bash
   # Placeholder detection
   grep -rn "TODO\|FIXME\|HACK\|XXX\|placeholder\|not implemented\|lorem ipsum" \
     --include="*.py" --include="*.ts" --include="*.tsx" src/

   # Console noise
   grep -rn "console\.log\|console\.warn\|console\.error\|print(" \
     --include="*.ts" --include="*.tsx" --include="*.py" src/ \
     | grep -v "logger\.\|logging\.\|test_\|\.test\.\|__test__"

   # Unused imports (Python)
   ruff check src/mcp/ --select F401

   # Unused imports (TypeScript)
   cd src/web && npx tsc --noEmit 2>&1 | grep "declared but"

   # Dead branches — any if(false), if(0), unreachable code
   grep -rn "if False\|if 0\|# dead\|// dead\|UNREACHABLE" \
     --include="*.py" --include="*.ts" --include="*.tsx" src/
   ```
3. **CI clear + visual inspection checkpoint** after each section completes:
   ```bash
   # CI equivalent
   cd src/mcp && ruff check . && mypy . && python -m pytest tests/ -x -q
   cd src/web && npx tsc --noEmit && npx vitest run && npx vite build
   # Visual: load http://localhost:3000 and walk through affected pages
   ```
4. **Public repo sync** after internal clears all checks:
   ```bash
   cd ~/Develop/cerid-ai-public
   git fetch origin && git checkout main
   # Cherry-pick or copy non-BSL files from internal
   # Verify no Pro content: grep -r "BSL-1.1" --include="*.py" --include="*.json" src/ plugins/
   git push origin main
   ```

### Best Practices Mandated for This Plan

| Pattern | When | Why |
|---------|------|-----|
| React Context/Provider for deep prop chains | S1 wiring (ActivityContext, EnrichmentContext) | Avoids prop-drilling 5+ levels; single source of truth |
| Custom hooks for shared logic | Chat enrichment, verification orchestration | Extract `useEnrichment()`, `useVerificationSelect()` hooks |
| FastAPI Pydantic v2 response models | All new/modified endpoints | 50x faster validation vs v1; typed responses prevent drift |
| SSE error events with `Last-Event-ID` reconnection | Verification streaming (S2.4) | `retry:` field + `id:` on each event; client reconnects at last ID |
| Docker virtiofs retry with exponential backoff | File I/O crossing Docker mount boundary (S4) | 3 attempts, 500ms/1s/2s backoff; catches `OSError(errno=35)` |
| ChromaDB HNSW rebuild via `REBUILD_HNSWLIB=1` env var | Post-migration or CPU SIMD mismatch | Forces HNSW index rebuild with correct SIMD instructions for host CPU |
| React compound components for KB console | Artifact card expanded view, config bar | `<ArtifactCard.Expanded>`, `<ArtifactCard.Actions>` slots |

---

## Section 1: Systemic Fixes (S1–S4)

> Fix these FIRST. Each resolves multiple downstream symptoms.

---

### S1. Parent-Child Wiring Audit

**Problem:** WP1-WP13 built leaf components without wiring props from parents. Features exist in source but are unreachable.

#### WP-S1.1: Wire `onEnrich` from chat panel to MessageBubble

| Item | Detail |
|------|--------|
| **File** | `src/web/src/components/chat/message-bubble.tsx` |
| **Prop definition** | Line 356: `onEnrich?: (messageId: string, content: string) => void` |
| **Conditional guard** | Line 563: `{onEnrich && (` — button only renders when prop is passed |
| **Button** | Line 572: `onClick={() => onEnrich(message.id, message.content)` |
| **Bug** | Zero call sites for `onEnrich=` or `handleEnrich` anywhere outside `message-bubble.tsx`. Grep confirms: **NOT WIRED**. |
| **Parent renderer** | `src/web/src/components/chat/chat-messages.tsx` — renders `<MessageBubble>` at ~line 147 |

**Fix:**
1. Create `handleEnrich` callback in the chat panel (or `chat-messages.tsx` parent):
   ```tsx
   // src/web/src/hooks/use-enrichment.ts (new custom hook)
   export function useEnrichment() {
     const enrich = useCallback(async (messageId: string, content: string) => {
       const res = await fetch(`${MCP_URL}/agent/enrich`, {
         method: "POST",
         headers: { "Content-Type": "application/json", "X-Client-ID": "gui" },
         body: JSON.stringify({ message_id: messageId, content }),
       });
       if (!res.ok) throw new Error("Enrichment failed");
       return res.json();
     }, []);
     return { enrich };
   }
   ```
2. Pass `onEnrich={enrich}` to every assistant `<MessageBubble>` in `chat-messages.tsx:147`
3. Create backend endpoint `POST /agent/enrich` in `src/mcp/routers/agents.py` — calls `DataSourceManager.query_all()` with the message content, returns enrichment results
4. **Pydantic response model:** `EnrichResponse(results: list[EnrichResult], source_count: int)`

#### WP-S1.2: Verify `onSelectForVerification` threading

| Item | Detail |
|------|--------|
| **File** | `src/web/src/components/chat/message-bubble.tsx:359` |
| **Prop** | `onSelectForVerification?: () => void` |
| **Usage** | Line 640: `onClick={onSelectForVerification ?? onToggleMarkup}` |
| **Parent** | `chat-messages.tsx:147` — `onSelectForVerification={` IS passed |

**Status:** Already wired in `chat-messages.tsx:147`. Verify the callback actually triggers verification — trace from parent to `use-verification-orchestrator.ts`. If the callback is a no-op stub, implement the body.

#### WP-S1.3: Artifact card expanded view content

| Item | Detail |
|------|--------|
| **File** | `src/web/src/components/kb/artifact-card.tsx` |
| **State** | Line 65: `const [expanded, setExpanded] = useState(false)` |
| **Toggle** | Line 129: `setExpanded((prev) => !prev)` |
| **Current behavior** | Line 252: `!expanded && "line-clamp-2"` — only removes text clamping |

**Fix:** Add expanded view content using compound component pattern:
```tsx
// When expanded, render BELOW the summary:
{expanded && (
  <div className="mt-3 space-y-2 border-t pt-3">
    {/* Keyword tags */}
    <div className="flex flex-wrap gap-1">
      {result.keywords?.map(k => <Badge key={k} variant="secondary">{k}</Badge>)}
    </div>
    {/* Metadata row */}
    <div className="flex gap-4 text-xs text-muted-foreground">
      <span>Source: {result.source_type}</span>
      <span>Chunks: {result.chunk_count}</span>
      <span>Ingested: {formatDate(result.ingested_at)}</span>
      <span>Retrievals: {result.retrieval_count ?? 0}</span>
    </div>
    {/* Quality breakdown (6 dimensions) */}
    {result.quality_scores && <QualityBreakdown scores={result.quality_scores} />}
  </div>
)}
```
Increase expanded card max-height to `~2x` collapsed. Animate with `transition-all duration-200`.

#### WP-S1.4: Console activity LED in sidebar

| Item | Detail |
|------|--------|
| **File** | `src/web/src/components/layout/sidebar.tsx` |
| **Current state** | Line 37: `{ pane: "knowledge", icon: Database, label: "Knowledge" }` — no LED, no pulse, no activity tracking |

**Fix:**
1. Create `src/web/src/contexts/ActivityContext.tsx`:
   ```tsx
   // Tracks last-activity timestamp per pane
   // Exposes: recordActivity(pane), hasRecentActivity(pane) -> boolean (within 10s)
   ```
2. Fire `recordActivity("knowledge")` on: query completion, ingestion success, verification result
3. In `sidebar.tsx`, render LED dot next to pane label:
   ```tsx
   {hasRecentActivity(pane.pane) && (
     <span className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
   )}
   ```

#### WP-S1.5: ConsoleConfigBar sticky positioning

| Item | Detail |
|------|--------|
| **File** | `src/web/src/components/kb/knowledge-console.tsx` |
| **Definition** | Line 236: `function ConsoleConfigBar({` |
| **Render** | Line 358: `<ConsoleConfigBar ragMode={ragMode} onRagModeChange={onRagModeChange} />` |
| **Bug** | Not `sticky`; only shows RAG mode selector |

**Fix:**
1. Add `className="sticky top-0 z-10 bg-background/95 backdrop-blur"` to ConfigBar container
2. Add injection threshold display (read from settings context)
3. Add settings gear icon linking to Settings > Pipeline tab

#### WP-S1.6: CustomApiDialog trigger

| Item | Detail |
|------|--------|
| **File** | `src/web/src/components/kb/custom-api-dialog.tsx:29` |
| **Status** | Component exists but has **zero import sites** — never rendered |

**Fix:** Import and render in the External Sources section of `knowledge-console.tsx`:
```tsx
import { CustomApiDialog } from "./custom-api-dialog"
// Add "Add Custom API" button in external sources section
<Button variant="outline" size="sm" onClick={() => setCustomApiOpen(true)}>
  <Plus className="h-3 w-3 mr-1" /> Add Custom API
</Button>
<CustomApiDialog open={customApiOpen} onClose={() => setCustomApiOpen(false)} onSave={handleAddSource} />
```

#### WP-S1.7: Verify wizard step definitions

**Files to check:**
- `src/web/src/components/setup/setup-wizard.tsx` — step array definition
- `src/web/src/components/setup/optional-features-step.tsx` — verify imported and in step array
- `src/web/src/components/setup/custom-provider-input.tsx` — verify rendered in API Keys step

**Action:** Read step array, confirm all 8 steps present. If `optional-features-step` or `custom-provider-input` are missing from the step array / render tree, add them.

#### WP-S1.8: Health dashboard fix actions + verification re-test

| Item | Detail |
|------|--------|
| **File** | `src/web/src/components/setup/health-dashboard.tsx` |
| **Line** | 24: services map with `verification_pipeline` entry |
| **Missing** | No "Re-check" button for verification; no `POST /setup/retest-verification` endpoint |

**Fix:**
1. Add "Re-check" button next to verification_pipeline status
2. Backend: `POST /setup/retest-verification` endpoint (see S2.2)
3. Each offline service row: show copy-to-clipboard Docker restart command

**Acceptance for S1:** Every component in this table has a Vitest test verifying the prop is consumed. Screenshot proof that each feature is reachable from the UI.

---

### S2. LLM Dependency Cascade

**Problem:** Verification self-test, metadata extraction, and claim extraction all require a working LLM. During first setup, no LLM is available — cascading failures persist for 24 hours.

#### WP-S2.1: Re-run self-test after keys are configured

| Item | Detail |
|------|--------|
| **File** | `src/mcp/routers/setup.py` |
| **Endpoint** | Line 481: `async def configure(req: ConfigureRequest)` |
| **Change** | After successful configure (env vars written), add: |

```python
# At the end of configure(), after env update succeeds:
try:
    from agents.hallucination.startup_self_test import run_verification_self_test
    from deps import get_redis
    _redis = get_redis()
    asyncio.create_task(run_verification_self_test(_redis))
except Exception:
    pass  # Non-blocking — next health check will pick it up
```

#### WP-S2.2: Manual re-test endpoint

| Item | Detail |
|------|--------|
| **File** | `src/mcp/routers/setup.py` |
| **New endpoint** | `POST /setup/retest-verification` |

```python
@router.post("/setup/retest-verification")
async def retest_verification():
    """Re-run verification self-test (called from health dashboard)."""
    from agents.hallucination.startup_self_test import run_verification_self_test
    from deps import get_redis
    result = await run_verification_self_test(get_redis())
    return result
```

**Response model:** `SelfTestResult(status: str, claims_found: int, method: str, timestamp: str)`

#### WP-S2.3: Fix heuristic claim extractor

| Item | Detail |
|------|--------|
| **File** | `src/mcp/agents/hallucination/patterns.py` |
| **`FACTUAL_PATTERNS`** | Line 67 |
| **`STRONG_FACTUAL_PATTERNS`** | Line 103 |

**Add patterns:**
```python
# In FACTUAL_PATTERNS (line 67):
re.compile(r"\b\w+\s+is\s+a(?:n)?\s+\w+", re.IGNORECASE),  # "X is a/an Y"
re.compile(r"\b(?:was|were)\s+(?:created|founded|developed|invented|built|designed)\s+by\b", re.IGNORECASE),
re.compile(r"\b(?:was|were)\s+(?:born|established|released|published|introduced)\s+in\b", re.IGNORECASE),

# In STRONG_FACTUAL_PATTERNS (line 103):
re.compile(r"\b(?:created|founded|developed|invented)\s+by\s+[A-Z]", re.IGNORECASE),
```

**Verify:** Self-test response `"Python is a programming language created by Guido van Rossum"` must extract >= 1 claim via heuristic path.

#### WP-S2.4: SSE error events for all exception paths

| Item | Detail |
|------|--------|
| **File** | `src/mcp/agents/hallucination/streaming.py` |
| **Function** | Line 239: `async def verify_response_streaming(` |
| **Current behavior** | Unhandled exceptions kill the generator; client sees "stream interrupted" |

**Fix:** Wrap the generator body in try/except:
```python
async def verify_response_streaming(...):
    event_id = 0
    try:
        # ... existing generator body ...
        # Add to each yield:
        event_id += 1
        yield f"id: {event_id}\nretry: 3000\nevent: claim\ndata: {json.dumps(payload)}\n\n"
    except Exception as exc:
        event_id += 1
        yield f"id: {event_id}\nevent: error\ndata: {json.dumps({'message': str(exc), 'recoverable': False})}\n\n"
    finally:
        event_id += 1
        yield f"id: {event_id}\nevent: done\ndata: {json.dumps({'total': event_id})}\n\n"
```

**Frontend** (`use-verification-stream.ts`): Handle `event: error` — display message, stop spinner. Handle reconnection via `Last-Event-ID` header.

#### WP-S2.5: Skip LLM metadata extraction in wizard context

| Item | Detail |
|------|--------|
| **Backend file** | `src/mcp/routers/upload.py` |
| **Current** | Line 35: `skip_quality: bool = Query(False)` |
| **Add** | `skip_metadata: bool = Query(False)` parameter |
| **Ingestion file** | `src/mcp/services/ingestion.py` |
| **Metadata call** | Line 718: `meta = extract_metadata(text, filename, domain)` |

**Fix:**
1. `upload.py:35` — add `skip_metadata: bool = Query(False)`
2. Pass through to `ingest_content()` / `ingest_file()`
3. In `ingestion.py`, when `skip_metadata=True`:
   ```python
   if skip_metadata:
       meta = {"summary": text[:200], "keywords": filename.split(".")[0].split("_")}
   else:
       meta = extract_metadata(text, filename, domain)
   ```
4. Frontend wizard upload: pass `?skip_quality=true&skip_metadata=true`

**Impact:** Saves 3-5s per ingestion during wizard flow.

#### WP-S2.6: Reduce self-test TTL

| Item | Detail |
|------|--------|
| **File** | `src/mcp/agents/hallucination/startup_self_test.py` |
| **Line** | 82: `redis_client.set(_SELF_TEST_KEY, json.dumps(result), ex=86400)` |
| **Change** | `ex=86400` → `ex=3600` (24h → 1h) |

#### WP-S2.7: Show "LLM required" instead of "offline"

| Item | Detail |
|------|--------|
| **File** | `src/web/src/components/setup/health-dashboard.tsx` |
| **Line** | 24: `verification_pipeline` entry |

**Fix:** When `verification_pipeline` status is `"unavailable"` AND zero providers are configured, display:
```
"Requires API key — configure a provider first"
```
instead of generic "Offline".

**Acceptance for S2:** Complete wizard → configure OpenRouter key → verification shows "healthy" within 10s. "Verify last response" extracts 3+ claims from a factual response.

---

### AI Slop Audit Checkpoint #1 (after S1 + S2)

Run the slop audit script from Section 0. Fix any:
- `TODO`/`FIXME` comments in newly touched files
- `console.log` statements not behind `__DEV__` guard
- Unused imports flagged by `ruff` or `tsc`
- Dead branches from old WP implementations

---

### S3. Unify Provider Detection

**Problem:** Three parallel provider detection code paths disagree on quote handling and return format.

**Current code paths:**
1. `src/mcp/routers/setup.py:150` — `detect_provider_status()` — strips quotes ✓
2. `src/mcp/config/providers.py:172` — `get_configured_providers()` — does NOT strip quotes ✗ (line 194: bare `os.getenv`)
3. `src/mcp/routers/providers.py` — `list_providers()` at GET `/providers` — strips quotes ✓ via separate logic

#### WP-S3.1: Shared quote stripping in `get_configured_providers()`

| Item | Detail |
|------|--------|
| **File** | `src/mcp/config/providers.py` |
| **Line** | 194: `api_key = os.getenv(env_var, "")` |
| **Fix** | `api_key = os.getenv(env_var, "").strip().strip('"').strip("'")` |

#### WP-S3.2: Canonical provider detection utility

**Extract shared logic to `src/mcp/utils/provider_detection.py` (new file):**

```python
"""Canonical provider detection — the ONE way to check provider status."""
import os

_PROVIDER_ENV_VARS = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
}

def _clean_key(env_var: str) -> str:
    """Strip quotes and whitespace from env var value."""
    return os.getenv(env_var, "").strip().strip('"').strip("'")

def detect_all_providers() -> dict[str, dict]:
    """Single source of truth for provider status."""
    result = {}
    for provider_id, env_var in _PROVIDER_ENV_VARS.items():
        key = _clean_key(env_var)
        result[provider_id] = {
            "configured": bool(key),
            "key_env_var": env_var,
            "key_present": bool(key),
            "key_preview": f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "***",
        }
    return result
```

Make `setup.py:detect_provider_status()`, `providers.py:get_configured_providers()`, and `providers.py:list_providers()` all delegate to this utility.

#### WP-S3.3: Fix wizard hydration for ALL providers

| Item | Detail |
|------|--------|
| **File** | `src/web/src/components/setup/setup-wizard.tsx` |
| **Hydration code** | Line 264-285: `fetchSetupStatus()` → dispatch `SET_KEY` |
| **Current** | Line 267: uses `provider_status` map, line 271: dispatches for each provider where `configured: true` |

**Verify:** The hydration loop at line 267-271 iterates ALL keys in `provider_status` (not just `configured_providers` list). The current code looks correct — `const ps = status.provider_status` then iterates `Object.entries(ps)`. If `provider_status` contains all 4 providers, hydration should work. Bug may be that the backend `SetupStatus` response doesn't include all 4 providers in `provider_status` when only some have keys.

**Action:** Trace `GET /setup/status` response shape. Ensure `provider_status` always includes all 4 providers (with `configured: false` for missing ones), not just configured ones. Fix in `setup.py` if `detect_provider_status()` only returns configured providers.

#### WP-S3.4: Fix ApiKeyInput for preconfigured keys

| Item | Detail |
|------|--------|
| **File** | `src/web/src/components/setup/api-key-input.tsx` |
| **Init** | Line 33: `const [value, setValue] = useState("")` |
| **Disable** | Line 139: `disabled={!value.trim() || status === "checking"}` |
| **Bug** | Preconfigured keys: `value` is `""`, so `!value.trim()` is `true` → Test button permanently disabled |

**Fix:**
```tsx
// Line 33: Don't use empty string for preconfigured
const [value, setValue] = useState(preconfigured ? "(from .env)" : "")

// Line 139: Allow testing preconfigured keys
disabled={(preconfigured ? false : !value.trim()) || status === "checking"}

// Line 156: Already has preconfigured label display
```

#### WP-S3.5: Backend validate env key directly

| Item | Detail |
|------|--------|
| **File** | `src/mcp/routers/setup.py` |
| **Function** | `validate_key()` endpoint (~line 424-437) |

**Fix:** If `api_key` is empty but provider has an env var set, validate the env var value:
```python
if not req.api_key and req.provider in _KEY_TO_PROVIDER.values():
    env_var = next(k for k, v in _KEY_TO_PROVIDER.items() if v == req.provider)
    env_key = os.getenv(env_var, "").strip().strip('"').strip("'")
    if env_key:
        req.api_key = env_key
```

**Acceptance for S3:** All 4 keys in `.env` → `GET /setup/status` returns all 4 in `configured_providers` → `GET /providers` returns all 4 with `key_set: true` → Wizard shows all 4 preconfigured → Settings shows all 4 online.

---

### S4. macOS Docker virtiofs Handling

**Problem:** `Errno 35` (EDEADLK) affects sync, ingestion, and file watching on macOS Docker Desktop.

#### WP-S4.1: Catch Errno 35 in sync status

| Item | Detail |
|------|--------|
| **File** | `src/mcp/sync/status.py` |
| **Function** | Line 30: `compare_status()` |

**Fix:**
```python
import errno

def compare_status(...):
    try:
        # ... existing file operations ...
    except OSError as exc:
        if exc.errno in (errno.EDEADLK, 35):  # EDEADLK / resource deadlock
            return {
                "status": "busy",
                "message": "File system busy — common with Docker on macOS. Try again in a few seconds.",
                "retry_after": 3,
            }
        raise
```

#### WP-S4.2: Retry with exponential backoff for file reads

| Item | Detail |
|------|--------|
| **File** | `src/mcp/services/ingestion.py` |
| **Call site** | `parse_file()` usage (called from `ingest_file()`) |

**Fix:** Create `src/mcp/utils/virtiofs_retry.py`:
```python
"""Retry decorator for macOS Docker virtiofs Errno 35."""
import errno, time, functools, logging

logger = logging.getLogger("ai-companion.virtiofs")

def virtiofs_retry(attempts: int = 3, base_delay: float = 0.5):
    """Retry file operations that may hit virtiofs EDEADLK."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except OSError as exc:
                    if exc.errno not in (errno.EDEADLK, 35) or attempt == attempts - 1:
                        raise
                    delay = base_delay * (2 ** attempt)  # 0.5s, 1s, 2s
                    logger.warning(f"virtiofs Errno 35, retry {attempt+1}/{attempts} in {delay}s")
                    time.sleep(delay)
        return wrapper
    return decorator
```

Wrap `parse_file()` call in `ingestion.py` with `@virtiofs_retry()`.

#### WP-S4.3: Document virtiofs limitation

| Item | Detail |
|------|--------|
| **File** | `docs/OPERATIONS.md` |
| **Add section** | "macOS Docker virtiofs known issue" |

Content: Errno 35 explanation, workarounds (avoid concurrent access from Dropbox + watcher + uploads), auto-retry behavior.

#### WP-S4.4: Unify watched folders systems

| Item | Detail |
|------|--------|
| **File** | `src/mcp/routers/setup.py` |
| **Lines** | 502-503: `updates["WATCH_FOLDER"] = req.archive_path` |
| **Watched folders API** | `src/mcp/routers/watched_folders.py:37` — prefix `/watched-folders` |

**Fix:** After setting `WATCH_FOLDER` env var, also create a watched folder entry:
```python
# In configure(), after setting WATCH_FOLDER (line 503):
if req.archive_path:
    updates["WATCH_FOLDER"] = req.archive_path
    # Also register with watched folders API
    try:
        from routers.watched_folders import _register_folder
        await _register_folder(req.archive_path, label="Archive", auto_ingest=True)
    except Exception:
        logger.warning("Failed to register archive as watched folder")
```

#### WP-S4.5: Frontend friendly error for Errno 35

**Files:** Any component displaying sync status.

**Fix:** Catch error messages containing `"Errno 35"` or `"Resource deadlock"` and display: `"File system busy — retrying..."` with auto-retry after 3 seconds.

**Acceptance for S4:** Sync status page never shows raw `[Errno 35]`. Archive folder configured in wizard appears in watched folders UI.

---

### AI Slop Audit Checkpoint #2 (after S3 + S4)

Run full slop audit. CI clear. Visual inspection of: wizard flow, settings providers page, sync status page.

---

## Section 2: Individual Fixes by Subsystem

> Items that need their own fix AFTER systemic fixes. Each group ends with CI + visual check.

---

### Group A: Setup Wizard

| # | Issue | Fix | Files |
|---|-------|-----|-------|
| Beta #2 | Storage/Archive needs location selector | Replace text input with `<input type="file" webkitdirectory>` polyfill (web) or `dialog.showOpenDialog` (Electron). Add explanatory text about Dropbox/iCloud sync. Add DB config section (Neo4j password, Redis password). | `src/web/src/components/setup/storage-step.tsx` |
| Beta #3 | Ollama wizard hardware-aware recommendations | See **Section 5** for full spec. Call `GET /providers/ollama/recommendations`, display hardware card, show compatible models with badge, explain expected speed. | `src/web/src/components/setup/ollama-step.tsx`, `src/mcp/routers/providers.py:185` |
| Beta #4 | Review & Apply: errors need fix actions | Add "Fix" action per error row: missing keys → provider signup link, failed validation → specific error + retry button. | `src/web/src/components/setup/review-apply-step.tsx` |
| Beta #6 | Try It Out: 25s parse, query fails | Add query retry in `first-document-step.tsx`: poll `GET /artifacts?sort=ingested_at&limit=1` every 500ms for 5s. Enable query input only after artifact confirmed. Frontend passes `?skip_quality=true&skip_metadata=true`. | `src/web/src/components/setup/first-document-step.tsx`, `src/mcp/routers/upload.py:35` |

**CI + visual check:** Walk through all 8 wizard steps. Every button, every transition.

---

### Group B: Chat

| # | Issue | Fix | Files |
|---|-------|-----|-------|
| Beta #9 | Expert verification "premium" badge incorrect | Find Badge component in verification submenu. Remove "premium" label — expert mode is user choice, not tier-gated. | `src/web/src/components/chat/` (verification toolbar section) |
| Beta #12 | Sub-menus need formatting/tooltip audit | Audit all popover/dropdown sub-menus in chat toolbar. Consistent padding, font sizes, dividers. Every interactive element gets a tooltip. | `src/web/src/components/chat/chat-toolbar.tsx` and sub-menus |
| Beta #21 | Chat names should be editable | Add inline-editable title in chat header. Click → `<input>` with current name. Enter/blur → `PATCH /conversations/{id}`. | `src/web/src/components/chat/chat-header.tsx`, `src/mcp/routers/conversations.py` |
| Beta #13 | No trash/archive on chat history | Add mouseover action buttons to conversation list items: archive, delete (with confirmation). Backend: add `archived` boolean to conversation model. | `src/web/src/components/chat/conversation-list.tsx`, `src/mcp/models/` |

**CI + visual check:** Send a message, verify streaming, check toolbar sub-menus, edit a chat name.

---

### Group C: Knowledge Base

| # | Issue | Fix | Files |
|---|-------|-----|-------|
| Beta #17 | KB card expansion (content) | Add keyword tags, metadata row, quality breakdown, chunk list in expanded view (see S1.3). | `src/web/src/components/kb/artifact-card.tsx:65+` |
| Beta #18 | KB cards: replace/re-generate buttons | Add to artifact-card action row: "Replace file" → file picker → re-ingest to same ID. "Re-generate synopsis" → `POST /artifacts/{id}/regenerate-synopsis`. | `artifact-card.tsx`, new backend endpoint |
| Beta #20 | KB title editable in card | Add inline-editable title. Click → input. Save → `PATCH /artifacts/{id} {title: newTitle}`. | `artifact-card.tsx` |
| Beta #22 | DOCX import fails silently | (a) Surface parser error in frontend upload response — ensure `upload.py` error handler includes exception message. (b) Add `.doc` → "Unsupported: please convert to .docx" in `parsers/registry.py:30` (`parse_file()`). (c) Frontend: display specific error message. | `src/mcp/routers/upload.py`, `src/mcp/parsers/registry.py:30`, `src/mcp/parsers/office.py:16` |
| Beta #23 | Recent imports: max 4 expanded | Default collapsed, max 4 visible, "Show N more" link. Scrollable container with max-height. | KB panel component |
| Beta #24 | Sort options above artifacts | Move sort row (quality, date, name, relevance) directly above artifact list, below upload actions. | KB panel component |
| Beta #26 | Auto-generate synopsis on import | In `services/ingestion.py:718`, ensure `extract_metadata()` always generates summary. If LLM fails, use first 200 chars as fallback. Surface synopsis status in frontend. | `src/mcp/services/ingestion.py:718` |
| Orig #42 | External search returns no results | Debug `DataSourceManager.query_all()`: check enabled sources, circuit breaker state, add logging. Show "No data sources enabled" if all disabled, with settings link. | `src/mcp/utils/data_sources/`, KB console external section |
| Beta #32 | External sources inconsistent | Unify data source display across KB Console and Settings. Show all configured sources with status. Show archive as "watched folder" data source. | KB console, settings panel |

**CI + visual check:** Upload PDF, DOCX, CSV. Expand artifact card. Check external sources section. Verify sort controls.

---

### Group D: Settings

| # | Issue | Fix | Files |
|---|-------|-----|-------|
| Beta #25 | Health tab layout/display | Redesign: group into Infrastructure/AI Pipeline/Optional. Card layout with status indicator + last-checked + expandable detail. Auto-refresh 30s. | `health-dashboard.tsx` or new `settings/health-tab.tsx` |
| Beta #28 | Platform capabilities by tier | In System tab: Core/Pro/Enterprise columns. Each capability: icon + status (active/available/locked). Mouseover: description + unlock tier. | `src/web/src/components/settings/system-tab.tsx` |
| Beta #30 | Unclear editable vs read-only | Editable: hover highlight + cursor-pointer. Read-only: lock icon + muted text + cursor-default. "(read-only)" label on computed values. | Settings components |
| Beta #31 | Pipeline stages not explained | Info icon per pipeline stage toggle. Tooltip: what it does, when it runs, what disabling means. | Settings pipeline tab |
| Beta #33 | Feedback loop purpose unclear (P2) | Research and document first. Current: saves assistant responses to KB. Proposed: opt-in per conversation with "This response will be saved to KB" indicator. | Design doc first, then implementation |
| Beta #34 | Non-binary settings need recommended configs | Every slider/dropdown: "Recommended" indicator. Chunk size: "Recommended: 400-512" highlighted. Injection threshold: "Recommended: Standard" with star. | Settings components |

**CI + visual check:** Settings page all tabs. Check editable vs read-only distinction. Verify health auto-refresh.

---

### Group E: Model Management (P2)

| # | Issue | Fix | Files |
|---|-------|-----|-------|
| Beta #35 | 350 models, no management UX | Virtual scrolling for 350+ list. Search/filter by name, provider, capability. Sort. "Add model" for custom IDs. Installed vs available for Ollama. Per-model context length + pricing. | New `src/web/src/components/settings/model-management.tsx` |
| Beta #15 | Chinese models via OpenRouter | Policy: USG compliance = bundled/default models only. OpenRouter passthrough allows any model. Don't filter OpenRouter list. Add disclaimer on Chinese-origin models. | `src/mcp/utils/model_registry.py`, model selector UI |

**CI + visual check:** Model management panel loads, search works, virtual scrolling handles 350+ items.

---

## Section 3: Subsystem Wiring Checks

> Run AFTER all Section 1 + 2 fixes. Each check is a manual QA script.

---

### 3.1 Setup Wizard Flow

```bash
# Automated pre-check
curl -s http://localhost:8888/setup/status | jq '.provider_status | keys'
# Expect: ["anthropic", "openai", "openrouter", "xai"]
```

**Manual test script:**
```
Step 0 (Welcome):
  [ ] System check: RAM, Docker status, Ollama status correct
  [ ] "Get Started" advances to Step 1

Step 1 (API Keys):
  [ ] All 4 provider inputs visible
  [ ] Pre-configured keys show "(from .env)" label
  [ ] Test button works on pre-configured keys (re-test flow)
  [ ] Test button works on manually entered keys
  [ ] See/hide toggle works
  [ ] "Custom Provider" expandable section visible
  [ ] Credits balance shown for OpenRouter
  [ ] Back/Next buttons work

Step 2 (Storage & Archive):
  [ ] Archive path input with default ~/cerid-archive
  [ ] Lightweight mode toggle works
  [ ] Auto-watch toggle works

Step 3 (Ollama):
  [ ] Hardware detection shown
  [ ] Model recommendations with compatible/incompatible badges
  [ ] Download/Pull button works
  [ ] Enable toggle works

Step 4 (Review & Apply):
  [ ] ALL configured providers shown as "Ready"
  [ ] "Apply Configuration" → success → auto-advance
  [ ] Error states show fix actions

Step 5 (Service Health):
  [ ] All core services show status
  [ ] Verification pipeline correct (not always "offline")
  [ ] "Re-check" button for verification pipeline

Step 6 (Try It Out):
  [ ] PDF drag-drop works
  [ ] Ingestion < 10 seconds for 2-page PDF
  [ ] Query returns relevant results after ingestion
  [ ] DOCX: works OR shows clear error

Step 7 (Choose Mode):
  [ ] Both mode options shown
  [ ] Selection persists to Settings
```

### 3.2 Chat Pipeline

```
  [ ] Send message → loading indicator → response streams
  [ ] Model indicator shows which model was used
  [ ] KB context injected (RAG enabled) — injection badge visible
  [ ] Verification toggle → claims extracted → status bar shown
  [ ] "Verify last response" works on assistant messages
  [ ] Selecting previous message for verification works
  [ ] Expert verification mode works
  [ ] Privacy mode levels 0-4 change Lock icon color
  [ ] Enrichment button visible on assistant messages
  [ ] Dashboard metrics update after message
```

### 3.3 Knowledge Base Pipeline

```
  [ ] Upload button → file picker opens
  [ ] Drag-drop → file lands in upload zone
  [ ] PDF parses correctly, shows chunk count
  [ ] DOCX: parses OR shows specific error message
  [ ] XLSX, CSV, RTF, MD, TXT, .py/.js: parse correctly
  [ ] Unsupported (.doc, .pptx): "Unsupported format" message
  [ ] Quality score shown on card (>= 0.35)
  [ ] Star/Evergreen toggle works
  [ ] Expand shows metadata/quality/chunks
  [ ] Query retrieves recently uploaded document
```

### 3.4 External API Pipeline

```
  [ ] External section visible in Knowledge Console
  [ ] At least one external source enabled in Settings
  [ ] External query returns results (DuckDuckGo/Wikipedia)
  [ ] Custom API dialog opens and creates new source
  [ ] Custom API test validates connectivity
```

### 3.5 Settings Persistence

```
  [ ] Change toggle → save → refresh → persists
  [ ] Change slider value → refresh → persists
  [ ] Change injection threshold → refresh → persists
  [ ] Section expand/collapse persists
  [ ] Tier display correct
```

### 3.6 Health Monitoring

```
  [ ] Health page shows all services
  [ ] Stop container → health shows "unavailable"
  [ ] Restart → health shows "healthy" within 30s
  [ ] Degradation banner when critical service down
  [ ] "Check now" triggers immediate refresh
```

### 3.7 Memory System

```
  [ ] Memories tab shows extracted memories
  [ ] Memory search works
  [ ] Memory edit/delete works
  [ ] Memories recalled in relevant conversations
```

### 3.8 Analytics Pipeline

```
  [ ] Dashboard shows metrics (tokens, timing)
  [ ] Metrics update after chat interaction
  [ ] Time-series graphs render correctly
  [ ] Cost breakdown per-model
```

---

## Section 4: Multi-OS Compatibility

---

### 4.1 macOS (Primary — ARM + Intel)

| Check | ARM (M1/M2/M3) | Intel |
|-------|----------------|-------|
| Docker Compose v2 | Docker Desktop 4.x | Docker Desktop 4.x |
| Volume mounts | virtiofs Errno 35 under concurrent access — handled by S4 | Same |
| File watcher | macOS uses `kqueue`; Docker sees `inotify` via virtiofs — latency possible | Same |
| GPU passthrough | No GPU passthrough in Docker Desktop. Metal not accessible from Linux containers | No GPU in Docker |
| Ollama | HOST install with Metal acceleration. Container → `host.docker.internal:11434` | HOST install, CPU-only |
| RAM detection | `HOST_MEMORY_GB` env var needed (Docker reports VM memory) | Same |

**Docker Compose adjustments:**
```yaml
# docker-compose.yml line 183 — current:
OLLAMA_URL=${OLLAMA_URL:-http://cerid-ollama:11434}
# Should be: (auto-detect in start script)
# macOS: OLLAMA_URL=http://host.docker.internal:11434
# Linux: OLLAMA_URL=http://host.docker.internal:11434 (if Docker Desktop) or http://localhost:11434 (if Docker Engine)
```

**Action items:**
1. `start-cerid.sh`: detect OS, set `OLLAMA_URL` appropriately before `docker compose up`
2. Test virtiofs retry logic (S4.2) on ARM and Intel Macs
3. Set `HOST_MEMORY_GB` via `$(sysctl -n hw.memsize | awk '{print int($1/1024/1024/1024)}')`
4. Document: GPU inference only available via HOST Ollama (Metal) — not Docker

### 4.2 Linux (x86_64 + ARM64)

| Check | x86_64 | ARM64 |
|-------|--------|-------|
| Docker | Native Docker Engine | Native Docker Engine |
| Volume mounts | Native filesystem — no virtiofs | Native filesystem |
| GPU passthrough | NVIDIA Container Toolkit for CUDA | No NVIDIA on most ARM |
| Ollama | HOST + CUDA if available. `localhost:11434` | Ollama supports ARM64, limited perf |
| ChromaDB embedding | ONNX Runtime with AVX2 | No AVX2 on ARM — use Python embedding fallback |

**Action items:**
1. `OLLAMA_URL=http://localhost:11434` default for Linux (not `host.docker.internal`)
2. Verify `nvidia-smi` detection works inside Docker (needs NVIDIA Container Toolkit)
3. Test ChromaDB embedding on ARM64 — may need `ONNX_DISABLE_AVX2=1` or HNSW rebuild: `REBUILD_HNSWLIB=1`
4. SELinux (Fedora/RHEL): volume mounts may need `:z` suffix — document
5. Use `docker compose` (v2 plugin), not `docker-compose` (v1)

### 4.3 Windows (WSL2 + Docker Desktop)

| Check | WSL2 |
|-------|------|
| Docker | Via WSL2 backend |
| Volume mounts | Cross-filesystem (NTFS → ext4) slow. Use WSL2 filesystem for data dirs |
| GPU passthrough | WSL2 CUDA passthrough works (NVIDIA) |
| Ollama | Install in WSL2, `localhost:11434` |
| Path separators | WSL2 uses POSIX. Windows paths: `/mnt/c/Users/...` |
| Process management | `start-cerid.sh` requires bash (WSL2 provides) |

**Action items:**
1. Add `start-cerid.ps1` PowerShell wrapper (or document WSL2 requirement)
2. Test archive path with `/mnt/c/Users/...` paths
3. Document `.wslconfig` memory recommendation: `memory=12GB` minimum
4. Verify `validate-env.sh` works in WSL2 bash

### 4.4 Cross-Platform Summary

| Priority | Item | Platforms |
|----------|------|-----------|
| HIGH | Platform-aware `OLLAMA_URL` default in `start-cerid.sh` | All |
| HIGH | virtiofs Errno 35 retry (S4) | macOS |
| HIGH | Archive path handling for `/mnt/c/` | Windows |
| MEDIUM | ChromaDB ARM64 embedding + `REBUILD_HNSWLIB=1` support | Linux ARM64 |
| MEDIUM | NVIDIA GPU detection in Ollama recommendations | Linux, Windows |
| MEDIUM | PowerShell startup script | Windows |
| LOW | SELinux `:z` volume mount docs | Fedora/RHEL |
| LOW | `.wslconfig` docs | Windows |

---

## Section 5: Ollama Architecture

---

### 5.1 Architecture Verification

| Check | Status |
|-------|--------|
| HOST OS install (not Docker) | Correct — `ollama-step.tsx` links to ollama.com/download |
| Hardware detection | Implemented at `src/mcp/routers/providers.py:185` (`GET /providers/ollama/recommendations`) |
| Model download → host filesystem | Correct — proxies to Ollama API on host |
| Container → host Ollama | `docker-compose.yml:183` — needs platform-aware default (see S4) |

### 5.2 Hardware Detection Improvements

**Current logic** (`providers.py:278-284`):
```python
if ram_gb >= 32:     recommended = "phi4:14b"
elif ram_gb >= 16:   recommended = "llama3.1:8b"
else:                recommended = "llama3.2:3b"
```

**Problems identified:**
1. **No VRAM detection** — system RAM ≠ GPU VRAM for discrete GPUs
2. **No CPU-only penalty** — 16GB CPU-only should get 3B, not 8B
3. **Apple Silicon unified memory** treated same as discrete — correct but unexplained
4. **No Windows GPU detection** — only checks `sysctl` (macOS) and `nvidia-smi` (Linux)

**Improved recommendation matrix:**

| Profile | RAM | GPU | Recommended | Speed Estimate |
|---------|-----|-----|-------------|---------------|
| M1 8GB | 8 | Metal (unified) | `llama3.2:3b` | ~15 tok/s |
| M1/M2 16GB | 16 | Metal (unified) | `llama3.1:8b` | ~20 tok/s |
| M2/M3 Pro 32GB+ | 32+ | Metal (unified) | `phi4:14b` | ~15 tok/s |
| NVIDIA RTX 3060 (12GB VRAM) | any | CUDA 12GB | `llama3.1:8b` | ~25 tok/s |
| NVIDIA RTX 3090+ (24GB VRAM) | any | CUDA 24GB | `phi4:14b` | ~20 tok/s |
| CPU-only 16GB | 16 | None | `llama3.2:3b` | ~3 tok/s |
| CPU-only 32GB | 32 | None | `llama3.1:8b` | ~5 tok/s |

**Implementation changes to `providers.py:185-300`:**

```python
# Add VRAM detection (Linux/WSL2)
vram_gb: float = 0.0
if gpu_name:
    try:
        vram_raw = await asyncio.get_event_loop().run_in_executor(
            None,
            functools.partial(
                subprocess.check_output,
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                text=True,
            ),
        )
        vram_gb = float(vram_raw.strip()) / 1024  # MB to GB
    except Exception:
        pass

# Apple Silicon: unified memory = total RAM available for model
is_apple_silicon = platform.machine() == "arm64" and sys.platform == "darwin"
effective_memory = ram_gb if is_apple_silicon else (vram_gb if vram_gb > 0 else ram_gb)
has_gpu = bool(gpu_name) or is_apple_silicon

# CPU-only penalty
if not has_gpu and effective_memory >= 16:
    effective_memory *= 0.5  # Halve effective memory for CPU-only

# Recommendation based on effective memory
if effective_memory >= 32:
    recommended = "phi4:14b"
elif effective_memory >= 12:
    recommended = "llama3.1:8b"
else:
    recommended = "llama3.2:3b"
```

### 5.3 Wizard UX Improvements

**File:** `src/web/src/components/setup/ollama-step.tsx`

| Enhancement | Implementation |
|-------------|---------------|
| **Hardware profile card** | Visual card: "Your Machine: M1 Pro 16GB, Apple Metal GPU". Performance estimate: "~20 tok/s with 8B model." |
| **Model comparison table** | Size, download time estimate (based on typical bandwidth), inference speed per model. Grey out incompatible models: "Needs Xb more RAM." |
| **Download progress bar** | Parse Ollama pull API streaming response. Show bytes downloaded / total. Speed estimate. |
| **Experience explanation** | "With your hardware, the 8B model will respond at ~20 tokens/sec. Used for background tasks (verification, classification) — main chat uses your cloud provider." |
| **System resource impact** | "While running, Ollama uses ~X GB RAM. Your system has Y GB total. This leaves Z GB for other apps." |
| **What happens without Ollama** | "Without Ollama, Cerid uses your cloud provider for all tasks. This works fine but uses more API credits (~$0.001 per verification)." |
| **Semi-automated install** | Detect OS → show command. macOS: `brew install ollama`. Linux: `curl -fsSL https://ollama.ai/install.sh \| sh`. Windows: link to installer. "Detect Ollama" refresh button after install. |

### 5.4 Ollama Action Items

| Priority | Item | Detail |
|----------|------|--------|
| HIGH | Add VRAM detection | `nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits` |
| HIGH | CPU-only penalty | No GPU + not Apple Silicon → halve effective memory for recommendation |
| HIGH | Inference speed estimates | Add `expected_tokens_per_sec` field per model in recommendations response |
| MEDIUM | Download progress bar | Parse Ollama pull API streaming response in `ollama-step.tsx` |
| MEDIUM | Resource impact display | `model_size_gb / total_ram_gb * 100` = % RAM usage |
| MEDIUM | Semi-automated install | OS-specific install command + "Detect Ollama" refresh button |
| MEDIUM | Platform-aware OLLAMA_URL | `start-cerid.sh` sets based on OS detection |
| LOW | Cost comparison | "With Ollama: $0/month. Without: ~$X/month" |
| LOW | Multi-model per stage | Different models for classification vs verification |

---

## Section 6: CI Cleanup and Final Audit

---

### 6.1 Dead Code Scan

```bash
# Python dead code
vulture src/mcp/ --min-confidence 80 --exclude "tests/"

# TypeScript unused exports
npx ts-prune src/web/src/

# Unused dependencies
cd src/mcp && pip-audit --fix --dry-run
cd src/web && npx depcheck
```

### 6.2 Bundle Size Check

```bash
cd src/web && npx vite build
# Check output: main chunk < 800KB (CI limit)
# If over: audit manualChunks in vite.config.ts, check for accidental full-library imports
du -sh dist/assets/*.js | sort -rh | head -10
```

### 6.3 Test Count Verification

```bash
# Python tests
docker run --rm -v "$(pwd)/src/mcp:/work" -w /work python:3.11-slim \
  bash -c "pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest tests/ --co -q | tail -1"
# Expect: 1740+ tests collected

# Frontend tests
cd src/web && npx vitest run --reporter=verbose 2>&1 | tail -5
```

### 6.4 Security Scan

```bash
# Python
cd src/mcp && bandit -r . -ll --exclude ./tests
cd src/mcp && pip-audit -r requirements.lock

# Frontend
cd src/web && npm audit --production

# Docker
hadolint src/mcp/Dockerfile
hadolint src/web/Dockerfile
trivy image cerid-mcp:latest --severity CRITICAL,HIGH
```

### 6.5 Final AI Slop Audit

```bash
# Full scan across all source
grep -rn "TODO\|FIXME\|HACK\|XXX\|placeholder\|not implemented\|lorem ipsum\|console\.log" \
  --include="*.py" --include="*.ts" --include="*.tsx" src/ \
  | grep -v "test_\|\.test\.\|__test__\|logger\.\|logging\." \
  | wc -l
# Target: 0 new slop items introduced by this plan
```

### 6.6 Final Visual Inspection

Walk through every page in the React GUI:

```
[ ] Setup Wizard — all 8 steps
[ ] Chat — send message, verify streaming, toolbar sub-menus
[ ] Knowledge Base — upload, expand cards, external sources
[ ] Monitoring — health dashboard, analytics
[ ] Audit — audit log, memory view
[ ] Memories — list, search, edit
[ ] Settings — all tabs (Essentials, Pipeline, System, Health)
[ ] Degradation banner — stop a service, verify banner appears
[ ] Mobile/tablet — resize to 768px, verify responsive layout
```

### 6.7 CI Pipeline Final Run

All 6 jobs must pass:
```
[ ] lint (ruff)
[ ] typecheck (mypy)
[ ] test (pytest, 60% coverage)
[ ] security (bandit + pip-audit + detect-secrets)
[ ] frontend (tsc + ESLint + Vitest + Vite build + bundle size)
[ ] docker (hadolint + docker build + Trivy)
```

---

## Execution Order Summary

```
Phase 1 (Systemic): S3 → S2 → S1 → S4 → Slop Audit #1 → Slop Audit #2
Phase 2 (Individual): Group A → B → C → D → E → CI check per group
Phase 3 (Wiring): Section 3 checks (all 8 subsystems)
Phase 4 (Platform): Section 4 (macOS ARM, Linux x86_64, WSL2)
Phase 5 (Ollama): Section 5 (hardware detection, wizard UX, progress bar)
Phase 6 (Final): Section 6 (dead code, bundle, tests, security, visual, CI)
```

---

## Key Research Incorporated

| Topic | Finding | Where Applied |
|-------|---------|--------------|
| **ChromaDB HNSW rebuild** | Set `REBUILD_HNSWLIB=1` env var to force HNSW index rebuild with correct CPU SIMD instructions. Critical for ARM64 Linux where AVX2 is unavailable. | Section 4.2 (Linux ARM64 action items) |
| **Pydantic v2 50x faster validation** | v2 `model_validate()` is 50x faster than v1 `parse_obj()`. All new endpoints must use `response_model=` with v2 models. | Section 0 conventions, all new endpoints |
| **React compound components** | `<Card.Header>`, `<Card.Body>`, `<Card.Actions>` pattern for flexible slot-based composition. Avoids prop explosion on artifact cards. | S1.3 (artifact card expanded view) |
| **SSE keepalive + retry field** | `retry: 3000\n` tells browser to reconnect after 3s. `id:` on each event enables `Last-Event-ID` header for resumption. | S2.4 (verification streaming) |
| **Docker virtiofs retry** | Exponential backoff (500ms, 1s, 2s) with `errno.EDEADLK` (35) catch. Documented in `scan_ingest.py:101-108`. | S4.2 (file read retry decorator) |

---

*Implementation plan v2 generated 2026-04-04. Covers systemic fixes, individual issues, wiring checks, multi-OS compatibility, Ollama architecture, and final audit.*
