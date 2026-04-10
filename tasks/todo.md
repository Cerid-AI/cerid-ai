# Cerid AI — Sprint TODO

> **Updated:** 2026-04-10
> **Version:** 0.82.0 (Phase C architecture + NLI)

---

## Immediate — This Session

- [x] Bump pyproject.toml 0.80.0 → 0.82.0
- [x] Fix CLAUDE.md repo path references
- [x] Create this todo file for sprint tracking
- [ ] Merge `feature/leapfrog-roadmap` into main (27 unmerged commits, 60+ conflicts)
  - Strategy: resolve in batches — docs/config first, then agents/core, then utils, then tests

---

## Next Sprint — P1 Features (from ROADMAP.md)

- [ ] Private Mode (ephemeral sessions, 4 security levels)
- [ ] Conversation Management UX (archive, bulk delete, history search)
- [ ] Agent Communication Console (real-time activity panel)
- [ ] Model Management & Auto-Update Detection
- [ ] Pro Tier Purchase Path (Stripe, license validation)

---

## Outstanding Code Issues (from CONSOLIDATED_ISSUES)

### B-CRITICAL (status verified 2026-04-10)
- [x] B1: Heuristic claim patterns — `STRONG_FACTUAL_PATTERNS` has comparatives + attribution. Missing "X is a/an Y" definitional pattern (partial fix; comparative + "created by" covered)
- [x] B2: SSE verify-stream error events — 2 error event yields found in `core/agents/hallucination/streaming.py`
- [x] B3: Self-test TTL — confirmed `ex=3600` (1h) in `startup_self_test.py`
- [x] B4: Re-run verification after configure — `retest-verification` endpoint exists in `routers/setup.py`
- [x] B5: Manual "Re-check" endpoint — `POST /setup/retest-verification` exists
- [x] B6: Health dashboard "Requires API key" — confirmed in `health-dashboard.tsx`
- [x] B7: Skip LLM metadata in wizard — `skip_metadata` param exists in `routers/upload.py` + `services/ingestion.py`

### B-HIGH (status verified 2026-04-10)
- [x] B8: `onEnrich` — wired in `chat-panel.tsx` (1 reference)
- [ ] B9: `onSelectForVerification` — NOT wired in `chat-panel.tsx` (0 references). Needs investigation.
- [x] B10: Artifact card expand — expand/preview functionality exists (10+ references in `artifact-card.tsx`)
- [x] B13: Quote stripping — `app/routers/setup.py` has strip logic
- [ ] B15: virtiofs Errno 35 — `virtiofs_retry.py` exists but NOT wired into `sync/status.py` or `services/ingestion.py`

---

## Doc Fixes

- [x] COMPLETED_PHASES.md reference — file exists, reference valid
- [x] API_REFERENCE.md formatting — fixed missing line breaks in MCP tools list
- [x] Document `CERID_USE_BIFROST` env var in ENV_CONVENTIONS.md

---

## Repo Hygiene

- [ ] Reduce silent `except: pass` threshold from 21 → 0
- [ ] Address 117 failing Python tests (38 in hallucination, 12 in ingestion)
- [x] Update Trivy CI action to Node 24 compatible SHA
- [ ] Clean stale branches after leapfrog merge

## Future Sprint Ideas

- [ ] **Knowledge Packs**: Downloadable curated fact packs (Wikidata subset, ~50K core facts)
- [ ] **Hardware-Aware Preset Recommendations**: Setup wizard uses RAM/CPU/GPU detection to recommend presets
- [ ] **GPU-Aware Model Selection**: Surface GPU acceleration in setup wizard for model routing
