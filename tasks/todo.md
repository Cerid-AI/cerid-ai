# Cerid AI — Sprint TODO

> **Updated:** 2026-04-05
> **Version:** 0.82.0 (all P0 complete)

---

## Immediate — This Session

- [x] Bump pyproject.toml 0.80.0 → 0.82.0
- [x] Fix CLAUDE.md repo path references (was pointing to wrong local dirs)
- [x] Create this todo file for sprint tracking
- [ ] Merge `feature/leapfrog-roadmap` into main (includes all phase-c work)
  - 27 unmerged commits (15 phase-c + 12 leapfrog features)
  - 60+ merge conflicts — needs structured resolution
  - **Contents:** core/app extraction, contract ABCs, concrete stores, eval harness, enterprise ABAC, mobile PWA, CRDT sync, benchmarks, 547 updated test targets, security fixes
  - **Strategy:** Resolve in batches — docs/config first, then agents/core, then utils, then tests

---

## Next Sprint — P1 Features (from ROADMAP.md)

- [ ] Private Mode (ephemeral sessions, 4 security levels)
- [ ] Conversation Management UX (archive, bulk delete, history search)
- [ ] Agent Communication Console (real-time activity panel)
- [ ] Model Management & Auto-Update Detection
- [ ] Pro Tier Purchase Path (Stripe, license validation)

---

## Outstanding Code Issues (from CONSOLIDATED_ISSUES)

### B-CRITICAL
- [ ] B1: Heuristic claim extractor misses simple claims (`hallucination/patterns.py`)
- [ ] B2: SSE verify-stream has no error event on exception (`hallucination/streaming.py`)
- [ ] B3: Self-test TTL 24h → 1h (`hallucination/startup_self_test.py`)
- [ ] B4: Re-run verification self-test after keys configured (`routers/setup.py`)
- [ ] B5: Add manual "Re-check" endpoint for verification (`routers/setup.py`)
- [ ] B6: Health dashboard "offline" → "Requires API key" (`health-dashboard.tsx`)
- [ ] B7: Skip LLM metadata in wizard context (`services/ingestion.py`)

### B-HIGH
- [ ] B8: `onEnrich` not wired — enrichment buttons missing
- [ ] B9: `onSelectForVerification` may be unwired
- [ ] B10: Artifact card expand shows no additional content
- [ ] B13: Quote stripping missing in `get_configured_providers()`
- [ ] B15: virtiofs Errno 35 handling — wire `virtiofs_retry.py`

---

## Doc Fixes

- [ ] Fix version mismatch: CLAUDE.md references `COMPLETED_PHASES.md` which doesn't exist (history is in CHANGELOG.md)
- [ ] API_REFERENCE.md MCP tools count: claims 21 but lists 16 (formatting issue)
- [ ] Document `CERID_USE_BIFROST` env var in ENV_CONVENTIONS.md

---

## Repo Hygiene

- [ ] Reduce silent `except: pass` threshold from 21 → 0
- [ ] Address 117 failing Python tests (38 in hallucination, 12 in ingestion — see tests/BUG_REPORT.md)
- [ ] Merge or close dependabot branches (3 active)
- [ ] Clean stale branches after leapfrog merge: `phase-c-core-extraction`, `sync/unified-plan`

## Future Sprint Ideas
- [ ] **Knowledge Packs**: Downloadable curated fact packs (Wikidata structured facts subset, ~50K core facts). Useful for air-gapped/offline deployments, demo scenarios, and speed (local KB <10ms vs 2-5s API). Design: SPARQL query pulls core facts → markdown → user downloads in Settings. Low priority since cross-model verification handles general knowledge already.

- [ ] **Hardware-Aware Preset Recommendations**: Setup wizard detects RAM/CPU/GPU but doesn't use results to recommend presets. Add recommendation logic in wizard step 7: "Based on your 160GB RAM and 16-core CPU, we recommend Balanced for optimal quality." Also add "Recommended for your system" badge to the matching preset card in Settings. Infrastructure exists (host_info.py, system-check endpoint) — needs UI wiring only.
- [ ] **GPU-Aware Model Selection**: When GPU acceleration detected, surface it in the setup wizard: "GPU detected — local models will run faster with Metal/CUDA acceleration." Currently GPU is detected but not used for routing decisions.
