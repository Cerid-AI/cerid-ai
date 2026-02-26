# Cerid AI — Issues & Backlog

> **Created:** 2026-02-25
> **Status:** Post Phase 9 + Neo4j auth hardening
> **Purpose:** Track known bugs, feature gaps, and architecture evaluations for upcoming phases.

---

## A. UI Layout Bugs

### A1. Chat Input & Metrics Dashboard Viewport Overflow

**Severity:** Medium
**Status:** Open

Chat input box and metrics dashboard don't stay visible unless the browser window is tall enough. On shorter viewports, the metrics dashboard scrolls out of view above messages, and the chat input may not be visible without scrolling.

**Root cause:** `ChatInput` and `ChatDashboard` in `chat-panel.tsx` are positioned via CSS flex column order, not `sticky`. The `ScrollArea` takes `flex-1` space, but the surrounding elements aren't pinned.

**Suggested fix:** Make `ChatInput` sticky at the bottom. Collapse or make `ChatDashboard` collapsible/sticky. Consider a compact metrics bar that's always visible.

**Files:**
- `src/web/src/components/chat/chat-panel.tsx` (lines 145–305 — layout structure)
- `src/web/src/components/chat/chat-dashboard.tsx`
- `src/web/src/components/chat/chat-input.tsx`

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
**Status:** Open

When KB context is injected into a chat message, users can't see what sources were used or where they appear in the response. Currently:
- A system message is silently prepended to the API call (hidden from UI)
- A small "`N source(s)`" badge appears in the input area before sending
- After sending, there's no per-message indicator of which sources were used
- The KB Context Pane shows "N sources ready to inject" in the sidebar

**Suggested fix:** After sending a message with injected context, show an expandable "Sources used" section below the assistant's response. Include filename, domain, relevance score, and a snippet. Consider a "view source" button that highlights the relevant artifact in the KB pane.

**Files:**
- `src/web/src/components/chat/chat-panel.tsx` (lines 116–127 — context injection)
- `src/web/src/components/chat/chat-input.tsx` (lines 62–66 — source badge)
- `src/web/src/hooks/use-kb-context.ts` (injection logic)
- `src/web/src/components/chat/message-list.tsx` (message rendering)

### B3. No Model Context Break Indicator

**Severity:** Medium
**Status:** Open

When a user switches models mid-conversation, all messages stay in one contiguous array with no visual marker. There's no "Model changed from X to Y" divider, no per-message model badge, and no warning about context continuity.

**What exists:** `use-conversations.ts` has `updateModel()` which saves the selected model. `use-chat.ts` passes the current model per-send. But conversation history doesn't segment by model.

**Suggested fix:** Add a system-style divider message when model changes ("Switched to Claude Sonnet"). Add a small model badge on each message bubble. Consider an optional "start fresh context" toggle when switching.

**Files:**
- `src/web/src/hooks/use-conversations.ts` (lines 103–111 — updateModel)
- `src/web/src/hooks/use-chat.ts` (line 16 — model per-send)
- `src/web/src/components/chat/chat-panel.tsx` (model selector)
- `src/web/src/components/chat/message-list.tsx` (message rendering)

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
**Status:** Open

Related to B3. When the user manually switches models (not auto-routed), the chat needs a clear visual break between model contexts. Consider:
- Per-message model badge (small label showing which model generated each response)
- Conversation fork/branch UI (split into separate threads per model)
- Context summary on switch (auto-generate a "here's what happened so far" summary)
- Option to start fresh (new context window) or continue (replay full history)

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

1. **A1** — Chat viewport fix (quick CSS fix, high user impact)
2. **B2** — Source attribution (core UX gap)
3. **B3 + D2** — Model context break (related, implement together)
4. **D1** — Smart routing evaluation (informs D2 implementation)
5. **B1** — Audit agent interactivity
6. **C1** — Taxonomy update
7. **C2** — Curation agent (requires C1)
8. **E2** — RAG evaluation (foundational, informs E1)
9. **E1** — Artifact preview (depends on E2 decisions)
