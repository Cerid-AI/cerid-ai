# Phase 25 — Intelligent Model Routing & Response Quality

> **Status:** Planning
> **Estimated effort:** 45-65 hours across 3 sub-phases
> **Prerequisites:** Phase 24 (verification enhancements) complete
> **Dependencies:** Bifrost config access, OpenRouter API

---

## Context

Phase 24 expanded hallucination detection with evasion, citation, recency, and ignorance claim types. The verification infrastructure is mature. Now we need the model routing to be equally intelligent — selecting the right model for each query based on capabilities, not just cost.

**User vision:** Smart routing that understands model strengths, automatically routes to the best model, supports user corrections, and enhances response quality with KB integration.

---

## Critical Blocker: Bifrost Dual-Routing Conflict

**The code audit revealed that the client-side model selection has no effect.** Bifrost's `semantic-intent` strategy overrides the `model` field in every request, routing based on its own LLM-based intent classification. The model dropdown, cost estimates, switch dialog, and all routing recommendations are currently non-functional.

**This must be resolved in Phase 25A before any routing work has meaning.**

---

## Phase 25A — Routing Foundation & Model Catalog Update

**Goal:** Fix the Bifrost routing conflict, update the model catalog to March 2026, align model IDs across the stack, capture actual model used.

**Estimated effort:** 12-16 hours

### A1. Resolve Bifrost routing (client model pass-through)

Configure Bifrost to respect the client-supplied `model` field when present, falling back to intent classification only when no model is specified (or when auto-route delegates to Bifrost).

**File:** `stacks/bifrost/config.yaml`

Change strategy behavior so that when the request body includes a `model` field, Bifrost forwards to that model directly. When `model` is absent or empty, Bifrost uses intent classification as today. This preserves Bifrost's value (budget tracking, intent analytics, fallback) while giving the client authority over model selection.

If Bifrost's Go/TS internals don't support this natively, the alternative is **Option C: Direct OpenRouter** — route chat requests directly to OpenRouter from the frontend, using Bifrost only for analytics/budget. The `streamChat()` function in `api.ts` would POST to OpenRouter's `/v1/chat/completions` endpoint directly with the full model ID.

**Decision required:** Investigate Bifrost's routing strategy options to determine if pass-through mode exists, or if direct OpenRouter routing is needed.

### A2. Capture actual model from stream response

Currently `ChatMessage.model` is set to the client-selected model before streaming starts. OpenRouter returns the actual `model` in the SSE response chunks.

**File:** `src/web/src/hooks/use-chat.ts`

Parse the `model` field from the first SSE chunk and update the assistant message's `model` field. This ensures audit analytics, verification model tracking, and cost calculations use the real model.

### A3. Update model catalog to March 2026

The current MODELS array has several retired/superseded models. Update based on research:

**File:** `src/web/src/lib/types.ts`

**Remove (retired/superseded):**
- `openrouter/openai/gpt-4o` (retiring Apr 2026, replaced by GPT-5.x)
- `openrouter/openai/gpt-4o-mini` (being phased out)
- `openrouter/meta-llama/llama-3.3-70b-instruct` (superseded by Llama 4)
- `openrouter/deepseek/deepseek-chat-v3-0324` (superseded by V3.2)

**Add:**
| Model | ID | Input $/1M | Output $/1M | Context | Strengths |
|-------|----|-----------|------------|---------|-----------|
| Claude Sonnet 4.6 | `openrouter/anthropic/claude-sonnet-4.6` | $3.00 | $15.00 | 200K | Coding (SWE-bench 79.6%), agents |
| Claude Opus 4.6 | `openrouter/anthropic/claude-opus-4.6` | $5.00 | $25.00 | 200K | Frontier reasoning, long-horizon agents |
| GPT-5.1 | `openrouter/openai/gpt-5.1` | $1.25 | $10.00 | 400K | Agentic, 400K context, balanced |
| o3-mini | `openrouter/openai/o3-mini` | $1.10 | $4.40 | 200K | STEM reasoning, efficient |
| Gemini 2.5 Flash | `openrouter/google/gemini-2.5-flash` | $0.30 | $2.50 | 1M | Budget, 1M context, multimodal |
| Gemini 2.5 Pro | `openrouter/google/gemini-2.5-pro` | $1.25 | $10.00 | 1M | Production 1M context |
| Grok 4.1 Fast | `openrouter/x-ai/grok-4.1-fast` | $0.20 | $0.50 | 2M | Native web+X search, 2M context |
| DeepSeek V3.2 | `openrouter/deepseek/deepseek-v3.2` | $0.25 | $0.38 | 128K | 90% of GPT-5.1 at 1/50th cost |
| Llama 4 Maverick | `openrouter/meta-llama/llama-4-maverick` | $0.15 | $0.60 | 1.05M | Open-source, 400B MoE, multimodal |

### A4. Add ModelCapabilities to each model

**File:** `src/web/src/lib/types.ts`

```typescript
export interface ModelCapabilities {
  reasoning: number       // 0-100 (from Chatbot Arena / Artificial Analysis)
  coding: number          // 0-100 (SWE-bench, LiveCodeBench)
  creative: number        // 0-100 (Arena creative writing)
  factual: number         // 0-100 (MMLU-Pro, GPQA Diamond)
  webSearch: boolean      // native or :online web search available
  vision: boolean         // multimodal image input
  knowledgeCutoff: string // ISO date of reliable knowledge
  arenaScore: number      // Chatbot Arena ELO normalized 0-100
}
```

Populate with data from Chatbot Arena, Artificial Analysis AAII, and SWE-bench. Scores are static — updated by developer when models change. A future enhancement could fetch from OpenRouter API.

### A5. Align model IDs across the stack

Standardize all model references:

| Location | Current Format | Action |
|----------|---------------|--------|
| `src/web/src/lib/types.ts` MODELS | `openrouter/...` | Update model slugs |
| `src/web/src/lib/model-router.ts` TIER_MODELS | `openrouter/...` | Remove (replaced by capability scoring in 25B) |
| `stacks/bifrost/config.yaml` intent_categories | bare slugs | Update to match current models |
| `src/mcp/config/settings.py` CATEGORIZE_MODELS | `openrouter/...` | Update to current models |
| `src/mcp/config/settings.py` VERIFICATION_MODEL_POOL | `openrouter/...` | Update pool |

Fix the `grok-4` vs `grok-4-fast` discrepancy. Update Bifrost fallback models.

### A6. Fix edge cases and cost display

**File:** `src/web/src/lib/model-router.ts`
- Guard for empty query: `if (!query.trim()) return "simple"`
- Fix cost display precision: `<$0.0001` for near-zero costs

**File:** `src/web/src/__tests__/model-router.test.ts`
- Add boundary tests: empty string, messageCount 20 vs 21, query length 30/500, kbInjections 2 vs 3
- Add non-ASCII query test (route as "medium", not crash)

### A7. Update Bifrost intent config

**File:** `stacks/bifrost/config.yaml`

Update intent targets to current models:
```yaml
intent_categories:
  - name: coding
    target_model: anthropic/claude-sonnet-4.6
  - name: research
    target_model: x-ai/grok-4.1-fast
  - name: simple
    target_model: google/gemini-2.5-flash
  - name: general
    target_model: deepseek/deepseek-v3.2
fallback_models:
  - google/gemini-2.5-flash
  - meta-llama/llama-4-maverick
```

### Files (Phase 25A)

| File | Action |
|------|--------|
| `stacks/bifrost/config.yaml` | Modify — pass-through mode + updated models |
| `src/web/src/lib/types.ts` | Modify — new MODELS array, ModelCapabilities interface |
| `src/web/src/lib/api.ts` | Modify — potentially direct OpenRouter routing |
| `src/web/src/hooks/use-chat.ts` | Modify — capture actual model from stream |
| `src/web/src/lib/model-router.ts` | Modify — edge case fixes, cost display |
| `src/mcp/config/settings.py` | Modify — updated model IDs |
| `src/web/src/__tests__/model-router.test.ts` | Modify — boundary tests, capability fixtures |
| `src/web/src/__tests__/types.test.ts` | Modify — validate capabilities on all MODELS |

---

## Phase 25B — Capability-Aware Auto-Routing

**Goal:** Replace hardcoded tier lists with capability scoring. Add auto-routing mode. Show routing transparency.

**Estimated effort:** 18-24 hours
**Depends on:** Phase 25A (model catalog + Bifrost fix)

### B1. Capability-based model scoring

**File:** `src/web/src/lib/model-router.ts`

Replace `TIER_MODELS` with `scoreModelForQuery()`:

```typescript
function detectQueryDomain(query: string): keyof ModelCapabilities {
  if (/\b(code|function|debug|implement|refactor|api|schema|class|algorithm|bug)\b/i.test(query))
    return "coding"
  if (/\b(latest|recent|current|today|this week|breaking|news|announced)\b/i.test(query))
    return "factual"  // + webSearch bonus
  if (/\b(write|story|poem|creative|imagine|generate|draft)\b/i.test(query))
    return "creative"
  return "reasoning"
}

function scoreModelForQuery(
  model: ModelOption,
  query: string,
  complexity: Complexity,
  costSensitivity: CostSensitivity,
): number {
  const domain = detectQueryDomain(query)
  const capScore = model.capabilities[domain]

  // Web search bonus/penalty for recency-sensitive queries
  const needsWeb = /\b(latest|current|today|this week|right now|breaking|202[5-9])\b/i.test(query)
  const webBonus = needsWeb ? (model.capabilities.webSearch ? 15 : -20) : 0

  // Cost factor: cheaper models get bonus proportional to sensitivity
  const costPerTurn = estimateTurnCost(model, query.length, complexity === "simple" ? 200 : complexity === "medium" ? 500 : 1000)
  const costMultiplier = costSensitivity === "high" ? 30 : costSensitivity === "low" ? 5 : 15
  const costBonus = Math.max(0, costMultiplier * (1 - costPerTurn / 0.01))

  // Knowledge cutoff penalty: older models penalized for factual queries
  const cutoffAge = (Date.now() - new Date(model.capabilities.knowledgeCutoff).getTime()) / (1000 * 60 * 60 * 24 * 30)
  const cutoffPenalty = domain === "factual" ? Math.min(cutoffAge * 2, 20) : 0

  return capScore + webBonus + costBonus + (model.capabilities.arenaScore * 0.1) - cutoffPenalty
}
```

`recommendModel()` calls `scoreModelForQuery()` on all MODELS, picks highest scorer.

### B2. Three-way routing mode

**File:** `src/web/src/lib/types.ts`

```typescript
export type RoutingMode = "manual" | "recommend" | "auto"
```

**File:** `src/web/src/hooks/use-settings.ts`

Replace `autoModelSwitch` boolean with `RoutingMode` state. Persists to localStorage + server.

**File:** `src/mcp/config/features.py`

```python
ROUTING_MODE = os.getenv("ROUTING_MODE", "recommend")  # manual/recommend/auto
```

**File:** `src/mcp/routers/settings.py`

Expose `routing_mode` in GET/PATCH `/settings`.

### B3. Auto-routing in chat panel

**File:** `src/web/src/components/chat/chat-panel.tsx`

When `routingMode === "auto"`, before `send()`:
1. Score all models for the current query
2. If best model differs from selected, auto-switch
3. Store `routingReason` on the assistant message
4. Show transparent explanation bar (auto-dismissing, 5s)

```tsx
{routingReason && routingMode === "auto" && (
  <div className="flex items-center gap-2 border-b bg-blue-500/5 px-4 py-1 text-xs text-blue-400">
    <Zap className="h-3 w-3" />
    <span>Auto-routed to {modelLabel}: {routingReason}</span>
  </div>
)}
```

### B4. Routing explanation in recommendation banner

When `routingMode === "recommend"`, enhance the existing banner to show WHY:

```
"coding query" → Claude Sonnet 4.6 (coding: 92/100) — saves ~$0.003/turn
"needs live data" → Grok 4.1 Fast (web search + recent cutoff) — saves ~$0.01/turn
```

The `ModelRecommendation` type gets a `reasoning: string` field explaining the capability match.

### B5. Settings pane update

**File:** `src/web/src/components/settings/settings-pane.tsx`

Replace model router toggle with three-way selector:

```tsx
<Select value={routingMode} onValueChange={updateRoutingMode}>
  <SelectItem value="manual">Manual — select model yourself</SelectItem>
  <SelectItem value="recommend">Recommend — suggest model switches</SelectItem>
  <SelectItem value="auto">Auto — switch models automatically</SelectItem>
</Select>
```

Info tooltip: "Auto mode selects the best model per message based on query type, model capabilities, and cost sensitivity. You can see why a model was selected in the routing explanation bar."

### B6. Model select dropdown enhancement

**File:** `src/web/src/components/chat/model-select.tsx`

Show capability badges in the dropdown:
- "web" badge (blue) for webSearch-capable models
- "vision" badge (purple) for vision-capable models
- Cost tier indicator (color-coded: green=$, amber=$$, red=$$$)
- Knowledge cutoff age (e.g., "Aug 2025")

### B7. Tests

**File:** `src/web/src/__tests__/model-router.test.ts`
- `detectQueryDomain`: coding/research/creative/default detection
- `scoreModelForQuery`: web search queries prefer webSearch models, coding queries prefer high-coding models, cost sensitivity affects scoring
- Auto-routing: given coding query, selects highest coding scorer
- Knowledge cutoff penalty: older model penalized for factual queries
- Routing mode persistence and cycling

### Files (Phase 25B)

| File | Action |
|------|--------|
| `src/web/src/lib/model-router.ts` | Modify — capability scoring, remove TIER_MODELS |
| `src/web/src/lib/types.ts` | Modify — RoutingMode, routingReason on ChatMessage |
| `src/web/src/hooks/use-settings.ts` | Modify — RoutingMode state |
| `src/web/src/hooks/use-model-router.ts` | Modify — use new scoring |
| `src/web/src/components/chat/chat-panel.tsx` | Modify — auto-routing, explanation bar |
| `src/web/src/components/chat/model-select.tsx` | Modify — capability badges |
| `src/web/src/components/settings/settings-pane.tsx` | Modify — three-way selector |
| `src/mcp/config/features.py` | Modify — ROUTING_MODE |
| `src/mcp/routers/settings.py` | Modify — expose routing_mode |
| `src/web/src/__tests__/model-router.test.ts` | Modify — capability and auto-routing tests |
| `src/web/src/__tests__/use-settings.test.ts` | Modify — routing mode tests |

---

## Phase 25C — User Corrections, Response Quality & Enhanced KB

**Goal:** Enable user corrections that re-generate with corrected context. Enhance KB auto-injection with context awareness. Add optional inline verification.

**Estimated effort:** 15-20 hours
**Depends on:** Phase 25A (model catalog). Independent of 25B (can be parallelized).

### C1. User correction injection

The user can correct a response (e.g., "The GDP was $25T not $20T"). The system truncates the wrong response, injects the correction as a system message, and re-generates.

**File:** `src/web/src/components/chat/message-bubble.tsx`

Add correction trigger button (pencil icon) on assistant messages, appears on hover:

```tsx
<Button variant="ghost" size="icon" onClick={() => setShowCorrection(true)}>
  <PenLine className="h-3 w-3" />
</Button>
```

**File:** `src/web/src/components/chat/correction-input.tsx` (NEW)

Small inline component: text input + submit button below the message being corrected. Shows the original claim being corrected.

**File:** `src/web/src/components/chat/chat-panel.tsx`

Correction handler:
1. Find the assistant message being corrected
2. Truncate conversation after the preceding user message
3. Inject `[User correction]: <correction text>` as a system message
4. Re-send the original user query with corrected context
5. Re-trigger verification on the new response

### C2. Context-window-aware KB auto-injection

Currently, auto-inject blindly adds up to 3 results above the relevance threshold. Enhance with a token budget.

**File:** `src/web/src/components/chat/chat-panel.tsx`

```typescript
// Reserve 15% of context window for KB, max 30% of remaining capacity
const contextBudget = Math.min(
  currentModelObj.contextWindow * 0.15,
  (currentModelObj.contextWindow * 0.8 - estimatedConversationTokens) * 0.3
)

let injectedTokens = 0
const budgetedCandidates: KBQueryResult[] = []
for (const c of candidates) {
  const chunkTokens = Math.ceil(c.content.length / 4)
  if (injectedTokens + chunkTokens > contextBudget) break
  budgetedCandidates.push(c)
  injectedTokens += chunkTokens
}
```

### C3. Semantic dedup of injected context

**File:** `src/web/src/lib/kb-utils.ts` (NEW)

Jaccard similarity on word sets to deduplicate near-identical KB chunks before injection:

```typescript
export function deduplicateResults(results: KBQueryResult[], threshold = 0.7): KBQueryResult[] {
  const kept: KBQueryResult[] = []
  for (const r of results) {
    const rWords = new Set(r.content.toLowerCase().split(/\s+/))
    const isDuplicate = kept.some((k) => {
      const kWords = new Set(k.content.toLowerCase().split(/\s+/))
      const intersection = [...rWords].filter(w => kWords.has(w)).length
      const union = new Set([...rWords, ...kWords]).size
      return union > 0 && intersection / union > threshold
    })
    if (!isDuplicate) kept.push(r)
  }
  return kept
}
```

### C4. Domain-aware injection headers

Improve injected context formatting with domain-specific labels:

```typescript
const contextParts = injected.map(r => {
  const label = r.domain === "coding" ? "Technical reference"
    : r.domain === "finance" ? "Financial data"
    : r.domain === "research" ? "Research notes"
    : "Knowledge base"
  return `--- ${label}: ${r.filename} (${r.domain}${r.sub_category ? `/${r.sub_category}` : ""}) ---\n${r.content}`
})
```

### C5. Optional inline verification details

**File:** `src/web/src/components/chat/message-bubble.tsx`

Add expandable verification details directly on the message (toggle to show/hide claim list inline, reusing claim card rendering from HallucinationPanel).

**File:** `src/web/src/hooks/use-settings.ts`

Add `showInlineVerification` boolean (localStorage-only, no server sync needed).

### C6. Tests

**File:** `src/web/src/__tests__/correction-flow.test.ts` (NEW)
- Correction truncates after correct message
- Correction injects system message with `[User correction]:` prefix
- Re-generation called with corrected context

**File:** `src/web/src/__tests__/kb-utils.test.ts` (NEW)
- Dedup with identical content
- Dedup with similar but not identical content (above/below threshold)
- Dedup with completely different content (no removal)
- Empty input returns empty
- Context budget calculation respects model context window

### Files (Phase 25C)

| File | Action |
|------|--------|
| `src/web/src/components/chat/correction-input.tsx` | Create — inline correction input |
| `src/web/src/lib/kb-utils.ts` | Create — dedup, context budget |
| `src/web/src/components/chat/chat-panel.tsx` | Modify — correction handler, context-aware injection |
| `src/web/src/components/chat/message-bubble.tsx` | Modify — correction button, inline verification |
| `src/web/src/hooks/use-settings.ts` | Modify — showInlineVerification |
| `src/web/src/__tests__/correction-flow.test.ts` | Create — correction tests |
| `src/web/src/__tests__/kb-utils.test.ts` | Create — dedup and budget tests |

---

## Verification Plan

### After Phase 25A
1. Run Python tests (expect 931+ passing)
2. Run frontend tests (expect 281+ passing, plus new boundary tests)
3. Manual test: open React GUI, select a model from dropdown, send a chat → confirm the selected model is actually used (not overridden by Bifrost)
4. Manual test: check assistant message's `model` field matches what was used
5. Verify all model IDs in Bifrost config, types.ts, settings.py are aligned

### After Phase 25B
1. Frontend tests (new capability scoring tests)
2. Manual test: set routing mode to "auto" → send a coding question → confirm Claude Sonnet 4.6 auto-selected
3. Manual test: send "what's the latest news about X" → confirm Grok 4.1 Fast auto-selected (webSearch)
4. Manual test: send "hello" → confirm budget model selected
5. Verify routing explanation bar appears and auto-dismisses

### After Phase 25C
1. Frontend tests (correction flow + dedup tests)
2. Manual test: receive a response → click correction button → enter correction → verify re-generation
3. Manual test: inject KB context → verify dedup removes near-duplicates
4. Manual test: verify context budget prevents over-injection on small-context models

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Bifrost doesn't support pass-through mode | High | Fall back to Option C (direct OpenRouter) |
| Model capability scores drift without updates | Medium | Document update procedure; consider OpenRouter API fetch |
| Auto-routing picks wrong model | Medium | "Recommend" mode as default; "auto" is opt-in |
| Correction re-generation costs double | Low | User-initiated; cost shown before re-send |
| KB dedup is too aggressive | Low | Configurable threshold (default 0.7) |

---

## Summary Table

| Phase | Deliverables | New Files | Modified Files | New Tests | Effort |
|-------|-------------|-----------|---------------|-----------|--------|
| **25A** | Bifrost fix, model catalog update, capabilities, ID alignment | 0 | 8 | ~15 | 12-16 hrs |
| **25B** | Capability scoring, auto-routing, transparency, settings | 0 | 11 | ~20 | 18-24 hrs |
| **25C** | User corrections, context-aware KB, dedup, inline verification | 4 | 4 | ~15 | 15-20 hrs |
| **Total** | | **4** | **~18 unique** | **~50** | **45-60 hrs** |

Dependency chain: **25A → 25B** (sequential). **25C** can run in parallel with 25B after 25A completes.
