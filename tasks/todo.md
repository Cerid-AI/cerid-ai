# Cerid AI Core — Consolidated Sprint TODO

> **Updated:** 2026-04-07
> **Source:** Comprehensive beta test + wiring audit

---

## Blocker

- [ ] **B1:** Orphaned YAML in ci.yml breaks CI parsing — delete dangling `env:` block at lines 254-257 (5 min)

## High Priority

- [ ] **H1:** Wire `orchestrated_query()` into the HTTP route — add `rag_mode` to `AgentQueryRequest`, route to orchestrator when `rag_mode != "manual"`. Fixes Knowledge Console KB/Memory/External sections always empty in Smart mode. (`app/routers/agents.py`, `agents/retrieval_orchestrator.py` — 4 hrs)
- [ ] **H2:** Wire external data sources into retrieval — call `registry.query_all()` from orchestrator in parallel with KB + memory queries. Normalize results into `source_breakdown["external"]`. Add `/data-sources/query` endpoint. Option to ingest results into KB. (`agents/retrieval_orchestrator.py`, `utils/data_sources/base.py`, `routers/data_sources.py` — 2 days)

## Medium Priority

- [ ] **M1:** Add model update endpoints — `GET /models/updates`, `POST /models/updates/check`, `POST /models/updates/dismiss/{id}` (all 404 currently). (`app/routers/models.py`, `utils/model_registry.py` — 2 hrs)
- [ ] **M2:** Fix `recall_memories` score filter — API filters on `score` but dict uses `adjusted_score`. Change to `r.get("adjusted_score", r.get("score", 0))`. (`app/routers/agents.py:424` — 5 min)
- [ ] **M3:** Fix `domain_conversations` collection errors — change `get_collection` to `get_or_create_collection`. (`core/agents/memory.py:549` — 5 min)
- [ ] **M4:** Per-message verification scoping — clicking old assistant message should swap verification report. (`hooks/use-verification-orchestrator.ts:7-18` — 1 day)
- [ ] **M5:** Auto-trigger memory extraction after chat stream completes (currently manual endpoint only). (`app/routers/chat.py` or frontend post-stream hook — 1 day)
- [ ] **M6:** Add `rag_mode` to `GET /settings` response and `PATCH /settings` model — enables cross-device sync. (`app/routers/settings.py` — 2 hrs)

## Low Priority

- [ ] **L1:** Pre-warm `conversations` collection at startup. (`app/main.py:252` — 5 min)
- [ ] **L2:** Fix CLAUDE.md stale reference to `config/constants.py`. (`CLAUDE.md` — 5 min)
- [ ] **L3:** Update `docs/ISSUES.md` with open items from this audit. (`docs/ISSUES.md` — 15 min)
- [ ] **L4:** Bridge-layer router cleanup — extract remaining bridge routers to `app/routers/`. (Sprint item)

---

## Completed This Session

- [x] Heuristic-first claim extraction (8-25s → <1.2s first event)
- [x] Adaptive verification timeouts (12s cross-model / 25s web-search / 30s expert)
- [x] Smart verification routing (cross-model for pre-2024, web-search for post-2024)
- [x] KB term-overlap sanity filter (blocks spurious vector matches)
- [x] Surrounding context passed to verifier (prevents decontextualized false refutations)
- [x] Sibling claim list in response_context
- [x] "Confidence" → "Relevance" label in KB panels
- [x] Confidence bar only shows with visible results (not sub-threshold backend matches)
- [x] `/agent/memory/recall` endpoint added
- [x] `/setup/system-check` endpoint with Docker/Ollama/env detection
- [x] Auto-inject KB context defaults to true
- [x] Ollama auto-enabled when detected in wizard
- [x] Pipeline pre-warms at startup + re-warm on Apply Config
- [x] `all_healthy` field in `/setup/health` response
- [x] Wizard clears stale progress on system reset
- [x] API key Test button validates env-configured keys via `__env__` sentinel
- [x] Agent console SSE endpoint path corrected
- [x] nginx returns 404 for missing hashed assets (prevents stale JS cache)
- [x] Verification timeout text clarified (pipeline tasks, not full verification)
