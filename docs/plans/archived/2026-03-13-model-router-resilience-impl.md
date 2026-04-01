# Model Router Resilience — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix three bugs: weak temporal routing in chat model router, no error retry with model fallback on chat stream, and verification stream cancellation on component unmount.

**Architecture:** Three independent fixes in the model routing pipeline. Fix 1 strengthens the client-side model scorer for temporal queries. Fix 2 adds retry-with-fallback in the backend chat proxy. Fix 3 makes verification stream cleanup conditional on stream progress.

**Tech Stack:** TypeScript (React 19, Vitest), Python (FastAPI, httpx, pytest)

---

### Task 1: Expand temporal detection regex and boost web search scoring

**Files:**
- Modify: `src/web/src/lib/types.ts:91` (CURRENT_INFO_RE)
- Modify: `src/web/src/lib/model-router.ts:133` (scoreModelForQuery webSearch bonus)
- Test: `src/web/src/__tests__/model-router.test.ts`

**Step 1: Write the failing tests**

Add to `src/web/src/__tests__/model-router.test.ts` in the `scoreModelForQuery` describe block:

```typescript
it("gives strong web search bonus for 'as of' temporal queries", () => {
  const withSearch: ModelOption = {
    id: "test/search",
    label: "Search",
    provider: "test",
    contextWindow: 128_000,
    effectiveContextWindow: 102_400,
    maxOutputTokens: 4_096,
    inputCostPer1M: 0.15,
    outputCostPer1M: 0.60,
    capabilities: { reasoning: 70, coding: 70, creative: 70, factual: 70, webSearch: true, vision: false, knowledgeCutoff: "2026-03" },
  }
  const noSearch: ModelOption = {
    ...withSearch,
    id: "test/no-search",
    capabilities: { ...withSearch.capabilities!, webSearch: false },
  }
  // "as of" should trigger temporal detection
  const withScore = scoreModelForQuery(withSearch, "as of 2025, what is the population?")
  const withoutScore = scoreModelForQuery(noSearch, "as of 2025, what is the population?")
  expect(withScore - withoutScore).toBeGreaterThanOrEqual(20)
})

it("gives strong web search bonus for 'right now' queries", () => {
  const withSearch: ModelOption = {
    id: "test/search",
    label: "Search",
    provider: "test",
    contextWindow: 128_000,
    effectiveContextWindow: 102_400,
    maxOutputTokens: 4_096,
    inputCostPer1M: 0.15,
    outputCostPer1M: 0.60,
    capabilities: { reasoning: 70, coding: 70, creative: 70, factual: 70, webSearch: true, vision: false, knowledgeCutoff: "2026-03" },
  }
  const noSearch: ModelOption = {
    ...withSearch,
    id: "test/no-search",
    capabilities: { ...withSearch.capabilities!, webSearch: false },
  }
  const withScore = scoreModelForQuery(withSearch, "what's happening right now with AI regulation?")
  const withoutScore = scoreModelForQuery(noSearch, "what's happening right now with AI regulation?")
  expect(withScore - withoutScore).toBeGreaterThanOrEqual(20)
})
```

**Step 2: Run tests to verify they fail**

Run: `cd src/web && npx vitest run src/__tests__/model-router.test.ts`
Expected: 2 new tests FAIL (bonus is only +10, tests expect ≥20 difference)

**Step 3: Update CURRENT_INFO_RE and web search bonus**

In `src/web/src/lib/types.ts` line 91, replace:
```typescript
// OLD:
const CURRENT_INFO_RE = /\b(latest|current|today|recent|news|2026|now|this week|this month)\b/i
```

Wait — `CURRENT_INFO_RE` is actually in `model-router.ts` line 91, not types.ts. Update there:

```typescript
// NEW:
const CURRENT_INFO_RE = /\b(latest|current|today|recent|news|202[5-9]|203\d|now|right now|this week|this month|as of|trending|what's new|what's happening)\b/i
```

In `src/web/src/lib/model-router.ts` line 133, change bonus from +10 to +25:
```typescript
// OLD:
if (caps.webSearch && CURRENT_INFO_RE.test(query)) score += 10
// NEW:
if (caps.webSearch && CURRENT_INFO_RE.test(query)) score += 25
```

**Step 4: Run tests to verify they pass**

Run: `cd src/web && npx vitest run src/__tests__/model-router.test.ts`
Expected: ALL tests PASS (including existing web search bonus test)

**Step 5: Commit**

```bash
git add src/web/src/lib/model-router.ts src/web/src/__tests__/model-router.test.ts
git commit -m "fix: strengthen temporal query detection and web search routing bonus"
```

---

### Task 2: Add knowledge cutoff filtering to recommendModel

**Files:**
- Modify: `src/web/src/lib/model-router.ts:157-190` (recommendModel)
- Test: `src/web/src/__tests__/model-router.test.ts`

**Step 1: Write the failing test**

Add to `model-router.test.ts` in the `recommendModel` describe block:

```typescript
it("excludes stale models for temporal queries in auto mode", () => {
  // Start on GPT-4o-mini (cutoff 2024-10) with a temporal query
  const gpt4oMini = MODELS.find((m) => m.id === "openrouter/openai/gpt-4o-mini")!
  const result = recommendModel(
    "What are the latest AI developments in 2026?",
    gpt4oMini,
    [],
    0,
    "high",
  )
  // Should NOT stay on GPT-4o-mini (cutoff 2024-10, 17+ months old)
  // Should recommend a model with recent cutoff + web search
  expect(result.model.id).not.toBe(gpt4oMini.id)
  expect(result.model.capabilities?.knowledgeCutoff).toBeDefined()
})

it("prefers web-search-capable model for temporal queries", () => {
  const gpt4oMini = MODELS.find((m) => m.id === "openrouter/openai/gpt-4o-mini")!
  const result = recommendModel(
    "What's happening with AI regulation right now?",
    gpt4oMini,
    [],
    0,
    "high",
  )
  // Grok has webSearch: true — should be preferred
  const grok = MODELS.find((m) => m.id === "openrouter/x-ai/grok-4.1-fast")!
  expect(result.model.id).toBe(grok.id)
})
```

**Step 2: Run tests to verify they fail**

Run: `cd src/web && npx vitest run src/__tests__/model-router.test.ts`
Expected: FAIL — `recommendModel` picks cheapest model (Llama at $0.10) regardless of cutoff

**Step 3: Add cutoff filtering to recommendModel**

In `src/web/src/lib/model-router.ts`, in `recommendModel()` after line 166 (`const minScore = MIN_SCORE[complexity]`), add:

```typescript
// For temporal queries, filter out models with stale knowledge cutoffs
const isTemporalQuery = CURRENT_INFO_RE.test(query)
const CUTOFF_MAX_AGE_MONTHS = 3
```

Then in the model candidate loop (line 178), add a cutoff check:

```typescript
for (const candidate of MODELS) {
  // Skip if context would exceed model's effective window
  if (estimatedInputTokens > candidate.effectiveContextWindow) continue

  // For temporal queries, skip models with stale knowledge cutoff
  if (isTemporalQuery && candidate.capabilities?.knowledgeCutoff) {
    const cutoffDate = new Date(candidate.capabilities.knowledgeCutoff + "-01")
    const ageMs = Date.now() - cutoffDate.getTime()
    const ageMonths = ageMs / (30 * 24 * 60 * 60 * 1000)
    if (ageMonths > CUTOFF_MAX_AGE_MONTHS) continue
  }

  const score = scoreModelForQuery(candidate, query)
  if (score < minScore) continue

  const candidateCost = estimateTurnCost(candidate, contextChars, estimatedOutput)
  if (candidateCost < bestCost) {
    bestModel = candidate
    bestCost = candidateCost
  }
}
```

Note: `CURRENT_INFO_RE` is defined at module scope (line 91) so it's accessible here.

**Step 4: Run tests to verify they pass**

Run: `cd src/web && npx vitest run src/__tests__/model-router.test.ts`
Expected: ALL tests PASS

**Step 5: Commit**

```bash
git add src/web/src/lib/model-router.ts src/web/src/__tests__/model-router.test.ts
git commit -m "fix: exclude stale-cutoff models from temporal query routing"
```

---

### Task 3: Add chat stream retry with model fallback (backend)

**Files:**
- Modify: `src/mcp/routers/chat.py`
- No Python test file exists for chat.py — skip backend test (manually tested)

**Step 1: Add fallback pool and helper**

At the top of `chat.py`, after the imports, add:

```python
# Models to try when the primary model fails with a retryable error.
# Ordered by reliability + cost. Provider family extracted to avoid
# retrying the same provider that just failed.
CHAT_FALLBACK_POOL = [
    "openai/gpt-4o-mini",
    "google/gemini-2.5-flash",
    "x-ai/grok-4.1-fast",
    "anthropic/claude-sonnet-4.6",
]

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _model_family(model_id: str) -> str:
    """Extract provider family: 'openai/gpt-4o-mini' → 'openai'."""
    return model_id.split("/")[0] if "/" in model_id else model_id


def _pick_fallback(failed_model: str) -> str | None:
    """Pick the first fallback model from a different provider family."""
    failed_family = _model_family(failed_model)
    for candidate in CHAT_FALLBACK_POOL:
        if _model_family(candidate) != failed_family:
            return candidate
    return None
```

**Step 2: Refactor _proxy_stream for retry**

Replace the existing `_proxy_stream` function. The key change: extract the single-attempt streaming into `_attempt_stream`, and have `_proxy_stream` call it with retry:

```python
async def _attempt_stream(
    req: ChatRequest,
    bare_model: str,
    request_id: str,
    api_key: str,
) -> AsyncGenerator[bytes, None] | int:
    """Attempt to stream from a single model.

    Returns an async generator on success, or the HTTP status code on
    retryable failure. Non-retryable errors yield an SSE error event
    via the generator (caller should forward it).
    """
    payload: dict = {
        "model": bare_model,
        "messages": [{"role": m.role, "content": m.content} for m in req.messages],
        "temperature": req.temperature,
        "stream": True,
    }
    if req.max_tokens is not None:
        payload["max_tokens"] = req.max_tokens

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://cerid.ai",
        "X-Title": "Cerid AI",
    }
    if request_id:
        headers["X-Request-ID"] = request_id

    timeout = httpx.Timeout(120.0, connect=10.0)

    try:
        client = httpx.AsyncClient(timeout=timeout)
        response = await client.send(
            client.build_request(
                "POST",
                f"{OPENROUTER_BASE}/chat/completions",
                json=payload,
                headers=headers,
            ),
            stream=True,
        )
        if response.status_code != 200:
            error_body = (await response.aread()).decode(errors="replace")[:500]
            await response.aclose()
            await client.aclose()
            logger.error(
                "OpenRouter error %d for model=%s: %s",
                response.status_code, bare_model, error_body,
            )
            if response.status_code in RETRYABLE_STATUS_CODES:
                return response.status_code
            # Non-retryable — yield error and stop
            async def _err_gen():
                err = json.dumps({
                    "error": {
                        "message": f"Upstream error ({response.status_code})",
                        "type": "upstream_error",
                    }
                })
                yield f"data: {err}\n\ndata: [DONE]\n\n".encode()
            return _err_gen()

        # Success — return a generator that streams chunks and cleans up
        async def _stream_gen():
            try:
                actual_model_emitted = False
                async for chunk in response.aiter_bytes():
                    if not actual_model_emitted:
                        try:
                            text = chunk.decode(errors="replace")
                            for line in text.split("\n"):
                                stripped = line.strip()
                                if stripped.startswith("data: ") and stripped != "data: [DONE]":
                                    p = json.loads(stripped[6:])
                                    actual = p.get("model")
                                    if actual and actual != bare_model:
                                        update = json.dumps(
                                            {"cerid_meta_update": {"actual_model": actual}}
                                        )
                                        yield f"data: {update}\n\n".encode()
                                    actual_model_emitted = True
                                    break
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return _stream_gen()

    except (httpx.ConnectError, httpx.ReadTimeout) as exc:
        logger.error("OpenRouter %s for model=%s", type(exc).__name__, bare_model)
        return 503  # Treat connection errors as retryable


async def _proxy_stream(req: ChatRequest, request_id: str, api_key: str = "") -> AsyncGenerator[bytes, None]:
    """Stream chat completion with retry-on-failure using a different model."""
    effective_key = api_key or OPENROUTER_API_KEY
    bare_model = _strip_prefix(req.model)

    # Emit metadata event
    meta = json.dumps({
        "cerid_meta": {
            "requested_model": req.model,
            "resolved_model": bare_model,
        }
    })
    yield f"data: {meta}\n\n".encode()

    result = await _attempt_stream(req, bare_model, request_id, effective_key)

    # If retryable failure, try a fallback model
    if isinstance(result, int):
        failed_status = result
        fallback = _pick_fallback(bare_model)
        if fallback:
            logger.info(
                "Model %s failed (%d), falling back to %s",
                bare_model, failed_status, fallback,
            )
            fallback_meta = json.dumps({
                "cerid_meta_update": {"fallback_model": fallback, "original_error": failed_status}
            })
            yield f"data: {fallback_meta}\n\n".encode()

            result = await _attempt_stream(req, fallback, request_id, effective_key)

    # Final result handling
    if isinstance(result, int):
        # Both attempts failed
        err = json.dumps({
            "error": {
                "message": f"All models failed (last error: {result})",
                "type": "upstream_error",
            }
        })
        yield f"data: {err}\n\ndata: [DONE]\n\n".encode()
    else:
        # Success — yield all chunks from the generator
        async for chunk in result:
            yield chunk
```

**Step 2: Run typecheck**

Run: `cd src/mcp && python -m py_compile routers/chat.py`
Expected: No errors

**Step 3: Commit**

```bash
git add src/mcp/routers/chat.py
git commit -m "feat: add model fallback retry on chat stream errors"
```

---

### Task 4: Handle fallback model info in frontend

**Files:**
- Modify: `src/web/src/lib/api.ts` (~line 680-695, `streamChat` function)
- Modify: `src/web/src/lib/types.ts` (ChatModelInfo type)
- Modify: `src/web/src/hooks/use-chat.ts` (~line 43-46)
- Test: `src/web/src/__tests__/model-router.test.ts` or a new test

**Step 1: Write the failing test**

Add a new test in `src/web/src/__tests__/model-router.test.ts` at the end:

```typescript
describe("streamChat fallback handling", () => {
  it("ChatModelInfo type includes optional fallback_model", () => {
    // Type-level test — if ChatModelInfo doesn't have fallback_model,
    // this won't compile
    const info: import("@/lib/types").ChatModelInfo = {
      requested_model: "test",
      resolved_model: "test",
      fallback_model: "openai/gpt-4o-mini",
    }
    expect(info.fallback_model).toBe("openai/gpt-4o-mini")
  })
})
```

**Step 2: Update ChatModelInfo type**

In `src/web/src/lib/types.ts`, find the `ChatModelInfo` interface and add `fallback_model`:

```typescript
export interface ChatModelInfo {
  requested_model: string
  resolved_model: string
  actual_model?: string
  fallback_model?: string
  original_error?: number
}
```

**Step 3: Handle cerid_meta_update.fallback_model in streamChat**

In `src/web/src/lib/api.ts`, in the `streamChat` function's SSE parsing loop, find where `cerid_meta_update` is handled and add fallback_model:

```typescript
if (parsed.cerid_meta_update) {
  if (parsed.cerid_meta_update.actual_model) {
    onModelInfo?.({ ...modelInfo, actual_model: parsed.cerid_meta_update.actual_model })
  }
  if (parsed.cerid_meta_update.fallback_model) {
    onModelInfo?.({
      ...modelInfo,
      resolved_model: parsed.cerid_meta_update.fallback_model,
      fallback_model: parsed.cerid_meta_update.fallback_model,
      original_error: parsed.cerid_meta_update.original_error,
    })
    console.warn(
      `[chat] Model fallback: original failed (${parsed.cerid_meta_update.original_error}), using ${parsed.cerid_meta_update.fallback_model}`,
    )
  }
  continue
}
```

**Step 4: Log fallback in use-chat.ts**

In `src/web/src/hooks/use-chat.ts`, the `onModelInfo` callback at line 43-46 already calls `onModelResolved`. The `resolvedModel` variable will be updated by the fallback event, so the correct model shows in the UI. No additional code needed — just verify the callback chain works.

**Step 5: Run tests**

Run: `cd src/web && npx vitest run`
Expected: ALL tests PASS (including the new type test)

**Step 6: Commit**

```bash
git add src/web/src/lib/types.ts src/web/src/lib/api.ts src/web/src/__tests__/model-router.test.ts
git commit -m "feat: handle model fallback metadata in frontend chat stream"
```

---

### Task 5: Fix verification stream cancellation resilience

**Files:**
- Modify: `src/web/src/hooks/use-verification-stream.ts:315-319`
- Test: `src/web/src/__tests__/verification-stream.test.ts`

**Step 1: Write the failing test**

Add to `verification-stream.test.ts`:

```typescript
it("does NOT abort stream when verification is in progress (verifying phase)", async () => {
  // Create a slow stream that takes time to complete
  let resolveStream: () => void
  const streamComplete = new Promise<void>((resolve) => { resolveStream = resolve })
  const abortFn = vi.fn()

  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      // Emit extraction_complete + claim_extracted to move past "extracting" phase
      const events = [
        { type: "extraction_complete", method: "llm", count: 1 },
        { type: "claim_extracted", claim: "Claim A", index: 0, claim_type: "factual" },
      ]
      for (const event of events) {
        controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`))
      }
      // Wait before completing — simulates slow verification
      await streamComplete
      const remaining = [
        { type: "claim_verified", index: 0, claim: "Claim A", claim_type: "factual", status: "verified", confidence: 0.9, source: "", reason: "OK", verification_method: "cross_model" },
        { type: "summary", verified: 1, unverified: 0, uncertain: 0, total: 1, overall_confidence: 0.9, extraction_method: "llm" },
      ]
      for (const event of remaining) {
        controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`))
      }
      controller.close()
    },
  })

  mockStreamFn.mockReturnValue({
    response: Promise.resolve({ ok: true, status: 200, body } as unknown as Response),
    abort: abortFn,
  })

  const { result, unmount } = renderHook(() =>
    useVerificationStream("text", "conv-cancel-1", true, 1),
  )

  // Wait for verifying phase (extraction_complete received)
  await waitFor(() => {
    expect(result.current.phase).toBe("verifying")
  })

  // Unmount while verification is in progress
  unmount()

  // abort should NOT have been called — stream should complete in background
  expect(abortFn).not.toHaveBeenCalled()

  // Complete the stream
  resolveStream!()
})
```

**Step 2: Run test to verify it fails**

Run: `cd src/web && npx vitest run src/__tests__/verification-stream.test.ts`
Expected: FAIL — "does NOT abort stream when verification is in progress" fails because current cleanup always calls `abort()`

**Step 3: Update cleanup function**

In `src/web/src/hooks/use-verification-stream.ts`, replace lines 315-319:

```typescript
// OLD:
return () => {
  cancelled = true
  clearTimeout(timeoutId)
  abort()
}

// NEW:
return () => {
  clearTimeout(timeoutId)
  // Only abort streams that haven't started producing results.
  // Once extraction completes and verification begins, let the stream
  // finish in the background — React 18 state setters are safe after
  // unmount (they're no-ops). The 180s timeout prevents resource leaks.
  if (!receivedSummary && claims.length === 0) {
    cancelled = true
    abort()
  }
  // Note: we intentionally do NOT set cancelled=true when the stream
  // is in progress, so processStream() continues processing events.
}
```

Wait — `claims` is React state and not accessible in the cleanup closure directly. We need a ref to track whether extraction has started. Let me use a simpler approach — track whether any claim events have been received via a ref:

```typescript
// Add a ref before the main useEffect (near line 77):
const hasReceivedEventsRef = useRef(false)

// Inside processStream(), after the extraction_complete case (line 213):
case "extraction_complete":
  setExtractionMethod(event.method ?? null)
  setPhase("verifying")
  hasReceivedEventsRef.current = true
  break

// Replace cleanup (lines 315-319):
return () => {
  clearTimeout(timeoutId)
  if (!hasReceivedEventsRef.current) {
    // Stream hasn't produced results yet — safe to abort
    cancelled = true
    abort()
  }
  // If events were received, let the stream complete naturally.
  // The 180s timeout (line 166) acts as the resource leak safety net.
}
```

Also reset `hasReceivedEventsRef` when starting a new stream (inside the effect, before calling `processStream()`):
```typescript
hasReceivedEventsRef.current = false
```

**Step 4: Update existing "aborts stream on unmount" test**

The existing test at line 220-230 expects `abort()` to always be called on unmount. With our change, it should only abort if no events were received. The test uses `makeSSEStream(HAPPY_EVENTS)` which streams all events instantly, so by the time `unmount()` runs, events may or may not have been processed. Update the test to verify abort is called when no events have been received:

```typescript
it("aborts stream on unmount before events received", () => {
  // Create a stream that hasn't emitted any events yet
  const abortFn = vi.fn()
  const body = new ReadableStream<Uint8Array>({
    start() {
      // Never enqueue anything — simulates slow backend
    },
  })
  mockStreamFn.mockReturnValue({
    response: Promise.resolve({ ok: true, status: 200, body } as unknown as Response),
    abort: abortFn,
  })

  const { unmount } = renderHook(() =>
    useVerificationStream("text", "conv-abort-1", true, 1),
  )

  unmount()
  expect(abortFn).toHaveBeenCalled()
})
```

**Step 5: Run tests to verify they pass**

Run: `cd src/web && npx vitest run src/__tests__/verification-stream.test.ts`
Expected: ALL tests PASS

**Step 6: Commit**

```bash
git add src/web/src/hooks/use-verification-stream.ts src/web/src/__tests__/verification-stream.test.ts
git commit -m "fix: don't abort verification stream after extraction starts"
```

---

### Task 6: Run full test suite and typecheck

**Files:** None — verification only

**Step 1: Run frontend tests**

Run: `cd src/web && npx vitest run`
Expected: ALL tests PASS (should be ~440+ tests across 34+ test files)

**Step 2: Run TypeScript typecheck**

Run: `cd src/web && npx tsc --noEmit`
Expected: No errors

**Step 3: Run Python lint**

Run: `cd src/mcp && python -m ruff check routers/chat.py`
Expected: No errors

**Step 4: Commit (if any fixes needed)**

Only commit if test/lint fixes were needed.

---

### Task 7: Update docs and ISSUES.md

**Files:**
- Modify: `docs/ISSUES.md` — add bug entries and mark resolved
- Modify: `CLAUDE.md` — update test count if it changed

**Step 1: Add entries to ISSUES.md**

Add three new resolved entries under the appropriate section. Increment resolved count in header.

**Step 2: Update CLAUDE.md test count**

If total frontend tests changed, update the count in CLAUDE.md.

**Step 3: Commit**

```bash
git add docs/ISSUES.md CLAUDE.md
git commit -m "docs: update ISSUES.md and CLAUDE.md for model router fixes"
```

---

### Task 8: Push and verify CI

**Step 1: Push to remote**

```bash
git push origin main
```

**Step 2: Check CI status**

```bash
gh run list --limit 1
```

Wait for CI to pass. If any job fails, investigate and fix.

---

## Task Dependency Graph

```
Task 1 (temporal regex + boost) ──┐
Task 2 (cutoff filtering)     ────┤── independent frontend fixes
                                   │
Task 3 (backend retry)        ────┤── independent backend fix
Task 4 (frontend fallback)    ────┘── depends on Task 3 (needs backend SSE format)
                                   │
Task 5 (stream cancellation)  ────┘── independent frontend fix
                                   │
Task 6 (full test suite)      ────── depends on Tasks 1-5
Task 7 (docs)                 ────── depends on Task 6
Task 8 (push + CI)            ────── depends on Task 7
```

Tasks 1, 2, 3, and 5 can run in parallel. Task 4 depends on Task 3. Tasks 6-8 are sequential.
