# Model Router Resilience — Design Document

**Date:** 2026-03-13
**Status:** Approved
**Scope:** 3 bugs in model routing / verification pipeline

---

## Problem Statement

Three related issues in the model routing and verification pipeline:

1. **Verification marks temporal claims as "uncertain"** instead of routing to Grok for real-time verification
2. **Chat model router doesn't re-route to real-time models** for queries requiring current data
3. **Chat model errors (e.g., Llama 5xx) surface directly to user** with no fallback to alternate model

---

## Fix 1: Chat Model Router — Web Search Boost Too Weak

### Root Cause

`model-router.ts` line 133: `scoreModelForQuery()` gives Grok only a **+10 bonus** when `CURRENT_INFO_RE` matches. But the router optimizes for **cost**, not capability — `recommendModel()` picks the cheapest model meeting a minimum score threshold (`MIN_SCORE`). Since Grok ($0.20/$0.50) is cheap but not always the cheapest, and the +10 bonus doesn't push it past the cost optimization, temporal queries often stay on the currently selected model.

Additionally, `CURRENT_INFO_RE` (`/\b(latest|current|today|recent|news|2026|now|this week|this month)\b/i`) is missing temporal patterns that appear in real conversations:
- "as of" / "as of 2025"
- "right now"
- "what's happening"
- Specific recent date references

### Fix

1. **Increase web search bonus from +10 to +25** in `scoreModelForQuery()` — this ensures Grok's capability score dominates over marginal cost differences when a query needs real-time data.

2. **Expand `CURRENT_INFO_RE`** to include: `as of`, `right now`, `happening`, `trending`, `what's new`, year ranges 2025-2029.

3. **Add `knowledgeCutoff` awareness to `recommendModel()`** — when `CURRENT_INFO_RE` matches, filter out models whose `knowledgeCutoff` is older than 3 months from today. This prevents routing to GPT-4o-mini (cutoff 2024-10) for a "latest news" query.

### Files Changed
- `src/web/src/lib/model-router.ts` — `scoreModelForQuery()`, `recommendModel()`
- `src/web/src/lib/types.ts` — update `CURRENT_INFO_RE` pattern

### Verification Model Routing (Already Correct)

The **verification** pipeline already routes temporal claims correctly:
- `_is_current_event_claim()` (patterns.py:353) detects temporal claims
- `_verify_claim_externally()` (verification.py:780) routes to `VERIFICATION_CURRENT_EVENT_MODEL` (Grok 4.1 Fast :online)
- Ignorance, evasion, recency, and citation claims ALL force `is_current_event = True`

The "uncertain" results the user observed likely come from Grok returning `"insufficient_info"` verdict, which maps to "uncertain" in `_parse_verification_verdict()` (line 604). This is correct behavior — it means Grok searched the web and couldn't find authoritative sources. No code change needed here.

---

## Fix 2: Chat Stream Error Retry with Model Fallback

### Root Cause

`chat.py` `_proxy_stream()` (line 124): when OpenRouter returns a non-200 status, the error is formatted as an SSE error event and the stream ends immediately. No retry with a different model is attempted.

`use-chat.ts` (line 55): the frontend catches the error and appends it to the message as `**Error:** Upstream error (502)`. No fallback logic.

The backend has no awareness of alternative models — it receives a single `model` field from the frontend and proxies to OpenRouter. If that model is down, the request fails.

### Fix

**Backend (`chat.py`)**: Add retry-with-fallback to `_proxy_stream()`:

1. On retryable errors (429, 500, 502, 503, 504, timeout), try the next model from a fallback list
2. Fallback list: derive from `MODELS` — exclude the failed model, pick the next cheapest model that meets `MIN_SCORE` for the detected intent
3. Max 1 retry (2 total attempts) to keep latency bounded
4. Emit a `cerid_meta_update` SSE event with `{"fallback_model": "..."}` so the frontend knows a switch occurred
5. Non-retryable errors (400, 401, 403) propagate immediately

**Frontend (`use-chat.ts`)**: Handle `cerid_meta_update.fallback_model` — update the resolved model display and log the fallback.

### Fallback Model Selection

```python
CHAT_FALLBACK_POOL = [
    "openai/gpt-4o-mini",           # Budget, reliable
    "google/gemini-2.5-flash",      # Budget, reliable
    "x-ai/grok-4.1-fast",           # Cheap + web search
    "anthropic/claude-sonnet-4.6",  # Premium fallback
]
```

On failure: remove the failed model's provider family, pick the first available from the pool.

### Files Changed
- `src/mcp/routers/chat.py` — `_proxy_stream()`, add `CHAT_FALLBACK_POOL`, retry logic
- `src/web/src/lib/api.ts` — handle `cerid_meta_update.fallback_model` in `streamChat()`
- `src/web/src/hooks/use-chat.ts` — log fallback model switch

---

## Fix 3: Verification Stream Cancellation Resilience

### Root Cause

`use-verification-stream.ts` line 315-319: the React effect cleanup calls `abort()` unconditionally on unmount. In React 18 StrictMode, this triggers on every dev-mode double-mount (already handled by the debounce guard at line 120-146). However, legitimate unmounts during verification (e.g., user sends a new message, causing re-render of the message component) will abort an in-flight stream.

The existing `enabledRef` pattern (line 88-93) correctly prevents settings hydration from killing streams, but doesn't address component unmount during active verification.

### Fix

**Only abort if no results have been received yet.** If extraction has started (phase moved past "extracting"), let the stream complete in the background:

```typescript
return () => {
  // Don't abort streams that have already started producing results —
  // let them complete in the background and write to state.
  // Only abort truly abandoned streams (no events received yet).
  if (phase === "idle" || phase === "extracting") {
    cancelled = true
    clearTimeout(timeoutId)
    abort()
  } else {
    // Stream is producing results — let it finish naturally.
    // The timeout (180s) acts as the safety net.
    clearTimeout(timeoutId)
  }
}
```

**Caveat:** Since React state setters work even after unmount (they're no-ops in React 18), this is safe. The state updates from a backgrounded stream simply won't render until the component remounts. The 180s timeout prevents resource leaks.

### Files Changed
- `src/web/src/hooks/use-verification-stream.ts` — cleanup function in main `useEffect`

---

## What This Does NOT Change

- No new models added to the pool
- No changes to claim extraction prompts or verification system prompts
- No changes to the verification UI rendering (ClaimOverlay, footnotes)
- No new dependencies
- Bifrost config.yaml unchanged (it's only used for backend-internal calls now)
- Circuit breaker thresholds unchanged

---

## Test Plan

### Fix 1 (Router web search boost)
- Unit test: `scoreModelForQuery()` with temporal queries returns Grok with highest score
- Unit test: `recommendModel()` with "latest news" query recommends Grok even when cheaper models exist
- Unit test: models with old `knowledgeCutoff` excluded for temporal queries
- Manual: send "What are the latest developments in AI?" — verify model switches to Grok in auto mode

### Fix 2 (Chat error retry)
- Unit test: `_proxy_stream()` retries with fallback model on 502
- Unit test: non-retryable errors (400, 401) propagate immediately
- Unit test: max 1 retry (doesn't loop)
- Unit test: `cerid_meta_update.fallback_model` emitted on successful fallback
- Frontend test: `streamChat()` parses fallback model info
- Manual: simulate Llama downtime → verify fallback to GPT-4o-mini

### Fix 3 (Stream cancellation)
- Unit test: cleanup aborts stream when phase is "idle"
- Unit test: cleanup does NOT abort when phase is "verifying"
- Integration: send message while verification is running → verify results still appear
