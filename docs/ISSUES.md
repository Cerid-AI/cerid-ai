# Cerid AI — Issues & Backlog

> **Created:** 2026-02-25
> **Last updated:** 2026-02-26
> **Status:** Phase 10A + 10B + codebase audit + dependency management complete. 4 of 9 issues resolved, 5 open.
> **Purpose:** Track known bugs, feature gaps, and architecture evaluations for upcoming phases.

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
**Status:** Open

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
**Status:** Open

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
**Status:** Open — needs design

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
**Status:** Open — needs evaluation

Current `model-router.ts` scores message complexity and recommends models based on cost sensitivity, but doesn't account for:
- Context window cost when switching models mid-conversation (full history must be re-sent)
- Token budget awareness (how much of the target model's context window will be consumed)
- Context preparation/injection for the new model (summarize prior context to reduce token count)
- Cost comparison: "switching to Model B will cost X tokens for context replay vs. Y tokens to continue with Model A"

**What exists:** `model-router.ts` has `scoreComplexity()`, `recommendModel()`, and cost tiers. `use-chat.ts` sends full message history per-call. No token counting or context summarization.

**Suggested approach:** Add a token estimator (tiktoken or char-based approximation). Before recommending a switch, calculate the context replay cost. Optionally offer "summarize and switch" which compresses history before sending to the new model. Show the cost delta to the user.

**Files:**
- `src/web/src/lib/model-router.ts` (lines 98–156 — recommendation logic)
- `src/web/src/hooks/use-chat.ts` (send with full history)
- `src/web/src/components/chat/chat-panel.tsx` (model recommendation banner)

### D2. Chat Model Switch UX

**Severity:** Medium
**Status:** Partially Resolved (Phase 10B, 2026-02-26)

**Resolved items:**
- ✅ Per-message model badge with provider colors (always visible)
- ✅ "Switched from X to Y" divider between model switches

**Remaining items (deferred to Phase 10C):**
- [ ] Context summary on switch (summarize prior history before replaying to new model)
- [ ] "Start fresh" option (new context window vs. continue with full replay)
- [ ] Conversation fork/branch UI (exploratory)

**Files:** Same as B3 + D1.

---

## E. Architecture Evaluations

### E1. Artifact Preview/Generation & Interactive Editing

**Severity:** Low (exploratory)
**Status:** Open — needs research

Evaluate options for in-GUI artifact handling:
- **Preview:** PDF rendering (pdf.js), code syntax highlighting (already have markdown), spreadsheet/table preview, email rendering
- **Generation:** Save chat responses as artifacts ("Save to KB" button), generate artifacts from templates
- **Interactive editing:** Edit artifact metadata (title, domain, tags) in-place, edit content with re-chunking, annotation/highlighting on artifacts
- **Version history:** Track changes to artifacts over time

**Dependencies:** Requires decisions on E2 (how artifacts are stored/vectorized) before implementation.

### E2. RAG Integration & Vectorization Strategy

**Severity:** High (foundational)
**Status:** Open — needs research

Evaluate the current RAG architecture and options for improvement:
- **Current state:** ChromaDB for vectors (default embedding model), BM25 for keyword search, hybrid 60/40 weighting, LLM reranking via Bifrost
- **Context window stuffing vs. retrieval-augmented:** Current approach injects top-K results as system message. Evaluate whether direct model RAG (if supported) would be more effective.
- **Embedding model choice:** Currently using ChromaDB's default. Evaluate dedicated embedding models (OpenAI ada-002, Cohere embed, local models like all-MiniLM).
- **Re-embedding strategy:** When to re-embed (content change, model upgrade, quality improvement)?
- **Memory/chat history vectorization:** Currently memories are stored as KB artifacts. Evaluate vectorizing conversation history for cross-conversation recall.
- **Hybrid retrieval tuning:** Current BM25 weight (0.4) and vector weight (0.6) are hardcoded. Evaluate query-dependent weighting, learned weights, or A/B testing.

**Files:**
- `src/mcp/agents/query_agent.py` (multi-domain query, reranking)
- `src/mcp/utils/bm25.py` (BM25 index)
- `src/mcp/config.py` (HYBRID_VECTOR_WEIGHT, HYBRID_KEYWORD_WEIGHT, GRAPH_* settings)
- `src/mcp/deps.py` (ChromaDB client configuration)
- `src/mcp/agents/memory.py` (memory extraction and storage)

---

## Priority Order (Suggested)

1. ~~**A1** — Chat viewport fix~~ ✅ Resolved (Phase 10A)
2. ~~**B2** — Source attribution~~ ✅ Resolved (Phase 10A)
3. ~~**B3 + D2** — Model context break~~ ✅ Resolved (Phase 10B) — "start fresh" deferred to 10C
4. **D1** — Smart routing + token cost evaluation — Phase 10C
5. **B1** — Audit agent interactivity — Phase 10D
6. **C1** — Taxonomy update — Phase 10D
7. **C2** — Curation agent (requires C1) — Phase 10E (design)
8. **E2** — RAG evaluation (foundational, informs E1) — Phase 10F (research)
9. **E1** — Artifact preview (depends on E2 decisions)
