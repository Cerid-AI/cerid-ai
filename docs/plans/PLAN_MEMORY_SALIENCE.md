# Memory Salience — Development Plan

> **Last updated:** 2026-03-29
> **Scope:** Complete the Phase 51 memory salience system — fix bugs, close integration gaps, update frontend, harden tests
> **Driven by:** Deep audit of `agents/memory.py`, `config/settings.py`, `db/neo4j/`, `tools.py`, `memories-pane.tsx`, and all test files
> **Estimate:** 4 sprints (S1–S4), each independently shippable

---

## Current State

The Phase 51 salience scoring engine is **fully implemented** in `agents/memory.py`:
- 6-type classification (empirical, decision, preference, project_context, temporal, conversational)
- Per-type decay: FSRS power-law for long-lived types, exponential for transient, step-function for temporal
- Recency-weighted access reinforcement via Neo4j `access_log`
- Source authority scaling
- Legacy type migration (fact→empirical, action_item→project_context)

However, the system has **bugs, dead code, integration gaps, and thin test coverage** that prevent it from being production-grade.

---

## Sprint 1: Bug Fixes & Dead Code Cleanup (Day 1)

> **Goal:** Fix all known bugs and remove dead code. Zero-risk, no behavioral changes for consumers.

### 1.1 Fix `stability_days` encoding divergence
- **Bug:** ChromaDB stores `"inf"` (string), Neo4j migration stores `-1.0` (float sentinel)
- **Impact:** If recall ever reads stability from Neo4j, empirical memories would incorrectly decay at 30-day exponential (the `<= 0` guard substitutes `MEMORY_HALF_LIFE_DAYS`)
- **Fix:** `migrations.py:migrate_memory_salience` → store `999999.0` instead of `-1.0` for infinite stability. Add a comment explaining the sentinel. Alternatively, store `"inf"` as a string property in Neo4j to match ChromaDB.
- **Files:** `src/mcp/db/neo4j/migrations.py`, `src/mcp/scripts/migrate_memory_salience.py`

### 1.2 Fix `pkb_memory_archive` output schema
- **Bug:** `tools.py` outputSchema for `pkb_memory_archive` lists `memories_stored` and `results` (copy-pasted from `pkb_memory_extract`). Actual return is `{archived_count, retention_days, cutoff_date, timestamp}`.
- **Fix:** Correct the outputSchema to match the actual return shape.
- **File:** `src/mcp/tools.py`

### 1.3 Fix `pkb_memory_recall` output — expose enriched fields
- **Bug:** Tool response drops `age_days`, `source_authority`, `summary`, `base_similarity`. The `created_at` field is always `""`. The `source` field misleadingly maps to `memory_type`.
- **Fix:** Add `age_days`, `source_authority`, `summary` to tool response. Rename `source` → `memory_type`. Populate `created_at` from `valid_from` metadata (already present in recall results).
- **File:** `src/mcp/tools.py`

### 1.4 Fix docstring: "71%" → correct retention figure
- **Bug:** `calculate_memory_score` docstring says "At t=S: retains 71%" but the FSRS formula `(1 + t/(9S))^(-0.5)` at `t=S` gives `≈ 94.9%`.
- **Fix:** Correct the docstring.
- **File:** `src/mcp/agents/memory.py`

### 1.5 Remove dead code: `MEMORY_TYPE_REINFORCEMENT_BOOST`
- **Issue:** Defined in `settings.py` (6 entries), never imported or referenced anywhere.
- **Fix:** Delete the dict. If per-type boost is needed later, re-add it when wiring.
- **File:** `src/mcp/config/settings.py`

### 1.6 Harden migration script idempotency guard
- **Bug:** `migrate_memory_salience.py` uses `if meta.get("source_authority"):` which is falsy for `"0"`. Use `if "source_authority" in meta` instead.
- **File:** `src/mcp/scripts/migrate_memory_salience.py`

---

## Sprint 2: Frontend Type Alignment (Day 2)

> **Goal:** Align the React GUI with the Phase 51 6-type schema. Users see correct type labels and filters.

### 2.1 Update `memories-pane.tsx` type constants
- **Current:** 4 types: `fact`, `decision`, `preference`, `action_item` with hardcoded colors/icons
- **Target:** 6 types: `empirical`, `decision`, `preference`, `project_context`, `temporal`, `conversational`
- **Include:** Type labels, filter tabs, color assignments, icon mappings
- **File:** `src/web/src/components/memories/memories-pane.tsx`

### 2.2 Add legacy type display fallback
- Existing memories with old types (`fact`, `action_item`) should display with their migrated label (`empirical`, `project_context`). Apply the same migration map the backend uses.
- **File:** `src/web/src/components/memories/memories-pane.tsx`

### 2.3 Update `types.ts` memory type union
- Add the 6-type union to the frontend type system.
- **File:** `src/web/src/lib/types.ts`

### 2.4 Run frontend tests and fix any breakage
- `npx vitest run` — update any memory-related test expectations.

---

## Sprint 3: Test Coverage (Day 3–4)

> **Goal:** Cover the critical untested paths. Priority order: recall (read path), conflict detection, conflict resolution.

### 3.1 Track `test_memory_salience.py` in git
- The file exists but is untracked. It contains 35 tests that won't run in CI until committed.
- **Action:** `git add src/mcp/tests/test_memory_salience.py`
- Also track `src/mcp/scripts/migrate_memory_salience.py`

### 3.2 Add `recall_memories()` unit tests (HIGH PRIORITY)
- This is the **entire read path** of the memory system and is completely untested.
- Test cases:
  - Basic recall: mock ChromaDB query + Neo4j access_log → verify scoring and sort order
  - Salience filtering: inject memories of different types/ages → verify decay filtering works
  - Access reinforcement: verify Neo4j `access_count` and `access_log` are updated after recall
  - ChromaDB sync: verify `collection.update()` called with new access counts
  - Empty results: no matches → empty list returned
  - Neo4j down: recall still works (graceful degradation on access_log fetch failure)
- **File:** `src/mcp/tests/test_memory_recall.py` (new)

### 3.3 Add `detect_memory_conflict()` unit tests
- Mock ChromaDB similarity search at threshold boundary
- Test: above threshold → conflict returned, below → no conflict
- Test: empty collection → no conflict
- **File:** `src/mcp/tests/test_memory.py` (extend)

### 3.4 Add `resolve_memory_conflict()` unit tests
- Mock LLM responses for supersede/coexist/merge decisions
- Test merge path: verify `effective_content` is mutated correctly
- Test circuit-breaker-open fallback: defaults to coexist
- **File:** `src/mcp/tests/test_memory.py` (extend)

### 3.5 Fix `test_successful_storage` fixture issue
- The test patches `services.ingestion.ingest_content` but `memory.py` uses a lazy import that may resolve differently. Verify patch target is correct.
- **File:** `src/mcp/tests/test_memory.py`

---

## Sprint 4: Integration Enhancement (Day 5–6)

> **Goal:** Wire automatic memory recall into conversation context. This is the highest-value architectural improvement — without it, memories are write-only during chat.

### 4.1 Design: Automatic memory context injection
- **Current state:** Memories are extracted post-conversation (fire-and-forget) but NEVER recalled during subsequent conversations. The `conversations` domain is explicitly excluded from RAG queries when chat context is present (`query_agent.py:240`).
- **Options:**
  - (A) Add a `recall_memories()` call in the chat pipeline (before LLM call) and inject top-K results as system context. Simplest, but adds latency.
  - (B) Add memory recall as a step in the query agent's decomposition pipeline — only when the query seems to reference prior knowledge.
  - (C) Keep memories as an explicit MCP tool but make the query agent domain exclusion configurable.
- **Recommendation:** Option (A) with a lightweight similarity check — only inject if top recall score > 0.6. Gate behind `ENABLE_MEMORY_RECALL_INJECTION` feature flag (default: false initially).

### 4.2 Implement automatic recall injection
- Add `recall_memories()` call in the chat response pipeline
- Inject top-3 results as a `[Memory Context]` block in the system prompt
- Gate with feature flag, respect the existing `ENABLE_MEMORY_EXTRACTION` toggle
- **Files:** `src/mcp/routers/agents.py` (or wherever chat orchestration happens), `src/mcp/config/features.py`

### 4.3 Wire `SOURCE_AUTHORITY_WEIGHTS` at ingest time
- **Current:** All memories get `DEFAULT_SOURCE_AUTHORITY = 0.7` regardless of source.
- **Enhancement:** Auto-assign source authority based on extraction context:
  - User-stated content (explicit "remember this") → 1.0
  - Document-extracted → 0.9
  - LLM-extracted from conversation → 0.7 (current default)
  - Web search results → 0.4
- **Files:** `src/mcp/agents/memory.py` (`extract_and_store_memories`), `src/mcp/config/settings.py`

### 4.4 Evaluate `:Memory` node schema in `db/neo4j/memory.py`
- **Current:** Production memory pipeline uses `:Artifact` nodes exclusively. The `:Memory` node schema in `db/neo4j/memory.py` (with constraints, indexes, CRUD, supersede chains) is completely unused.
- **Decision needed:** Either:
  - (A) Migrate memories to dedicated `:Memory` nodes (cleaner schema, dedicated indexes)
  - (B) Delete `db/neo4j/memory.py` as dead code
- **Recommendation:** (B) for now — the Artifact-based approach works and migration adds risk. Document the decision. Revisit when memory volume justifies dedicated nodes.

---

## Out of Scope (Tracked Elsewhere)

These items are already in the Phase 51 todo section and are not part of this plan:

- Full 345 bare `except` sweep (BLE001)
- Parent-child document retrieval
- Graph RAG prototype
- Independent circuit breakers per stage
- RAGAS full integration
- Phase-C worktree: port memory.py to core/ (blocked until Phase C merges)

---

## Verification Gates

Each sprint must pass before proceeding:

| Sprint | Gate |
|--------|------|
| S1 | All existing tests pass. No new functionality, only fixes. |
| S2 | Frontend builds clean (`npx vitest run` + `npm run build`). Memory pane shows 6 types. |
| S3 | `pytest tests/test_memory*.py -v` — all new tests pass. Coverage of `agents/memory.py` > 80%. |
| S4 | Integration test: store memory → recall in subsequent conversation → verify injection. Feature flag off = no behavioral change. |

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| S1 fixes break existing memory consumers | Low | All fixes are schema-level or dead code; no behavioral changes |
| S2 frontend type rename breaks user data display | Medium | Legacy fallback mapping handles old types gracefully |
| S3 recall tests reveal bugs in scoring path | Medium | Better to find now than in production |
| S4 automatic injection adds chat latency | Medium | Gate behind feature flag, set minimum score threshold, measure before enabling |
| Phase-C worktree divergence grows | High | S1–S3 are main-branch only; S4 should be deferred if Phase-C merge is imminent |
