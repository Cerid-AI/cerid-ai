# Cerid AI — Sprint TODO

> **Updated:** 2026-04-15
> **Version:** 0.83.0

---

## Current Status

**All bugs resolved.** Zero open PRs, zero stale branches, both CIs green.
Codebase: 2,413 Python tests (131 files), 719 frontend tests (64 files), 26 MCP tools (21 core + 5 trading).

---

## Next Sprint — P1 Features (from ROADMAP.md)

- [ ] Private Mode (ephemeral sessions, 4 security levels)
- [ ] Conversation Management UX (archive, bulk delete, history search)
- [ ] Agent Communication Console (real-time activity panel)
- [ ] Model Management & Auto-Update Detection
- [ ] Pro Tier Purchase Path (Stripe, license validation)

---

## Integration Gaps (from 2026-04-15 audit)

- [ ] **CRITICAL**: `.env.example` missing ~170 env vars vs `settings.py` — regenerate comprehensive template with Required/Optional/Feature Flag sections + CI pre-commit hook
- [ ] Verification orchestrator message scoping — TODO in `use-verification-orchestrator.ts:11` for per-message targeting in multi-turn conversations
- [ ] Internal module imports fail silently (`sdk.py:190`, `agents.py:850`) — add logging/warnings + `/health` field for `internal_modules_available`
- [ ] Router registration clarity — 91 router files, subset registered in main.py. Document in `docs/ROUTER_REGISTRY.md`

## Deferred / Low Priority

- [ ] B15: `virtiofs_retry.py` exists but not wired into sync/ingestion (Docker-on-macOS edge case) — integrate into I/O hot paths or deprecate
- [ ] Compound-sentence sub-claim extraction — needs extractor prompt tuning or post-pass splitter
- [ ] 3 intentional pytest skip/xfail markers (flaky mock ordering, HNSW serialization, AsyncIOScheduler tracking)

## Follow-Up Upgrades (tracked in `docs/DEPENDENCY_UPGRADES.md`)

- [ ] chromadb / neo4j major version upgrades (blocked on client/server coupling)
- [ ] Python 3.12 runtime upgrade
- [ ] ESLint 10 (blocked on react-hooks plugin ecosystem)

---

## Future Sprint Ideas

- [ ] **Knowledge Packs**: Downloadable curated fact packs (Wikidata subset, ~50K core facts)
- [ ] **Hardware-Aware Preset Recommendations**: Setup wizard uses RAM/CPU/GPU detection to recommend presets
- [ ] **GPU-Aware Model Selection**: Surface GPU acceleration in setup wizard for model routing

---

## Completed Sessions (Archive)

<details>
<summary>Session 2026-04-15 — Bug-hunt + structural fix sprint</summary>

### Result
- **15/15 bugs fixed**, all as structural root-cause resolutions (no symptom patches)
- **Frontend tests: +14** (705 → 719 passing; 1 skipped)
- **Zero regressions** in the 42-case verification battery baseline

### Commits shipped
| SHA | Summary |
|---|---|
| `863b77e` | fix(pipeline): critical new-user blockers — embedding singleton, agent activity stream, install docs |
| `07f7b6c` | fix(onboarding): public-repo doc polish, CERID_SYNC_DIR rename, healthcheck rewrite |
| `e8e31f0` | fix(ux): verification-panel wiring + tab title + KB counter + errors drill-through |
| `3debefb` | chore(sync): exclude tool caches from public sync |
| `329db27` | (public) fix(pipeline+onboarding+ux): comprehensive bug-hunt fixes |

Full context: `tasks/SESSION-REVIEW-2026-04-15.md`

</details>

<details>
<summary>Session 2026-04-13 — Memory efficacy + verification rigor</summary>

- Source-aware external query construction, CRAG retrieval quality gate
- Verified-fact-to-memory promotion pipeline, tiered authority boost
- NLI consolidation guard, fact-relationship verification, graph-guided verification
- 65 new pipeline enhancement tests
- 19 bug fixes (httpx errors, Wolfram HTTPS, ClaimOverlay, field mapping)

</details>

<details>
<summary>Session 2026-04-12 — Setup wizard + performance</summary>

- Dark mode fix, wizard performance optimization, event loop stall fixes
- replaceMessages prop wiring, queryKB options, system check retry logic
- Docker healthcheck improvements

</details>

<details>
<summary>Session 2026-04-10 — Version bump + sprint setup</summary>

- Bumped pyproject.toml 0.80.0 → 0.82.0
- Fixed CLAUDE.md repo path references
- Created sprint tracking

</details>

<details>
<summary>Resolved Issues (B-CRITICAL + B-HIGH, all verified 2026-04-15)</summary>

- [x] B1: Heuristic claim patterns — comprehensive `STRONG_FACTUAL_PATTERNS` with comparatives + attribution
- [x] B2: SSE verify-stream error events — error yields in `streaming.py`
- [x] B3: Self-test TTL — confirmed `ex=3600` (1h) in `startup_self_test.py`
- [x] B4: Re-run verification after configure — `retest-services` endpoint in `routers/setup.py`
- [x] B5: Manual "Re-check" endpoint — exists and wired to health dashboard
- [x] B6: Health dashboard "Requires API key" — context-aware labels
- [x] B7: Skip LLM metadata in wizard — `skip_metadata` param in upload/ingestion
- [x] B8: `onEnrich` wired in `chat-panel.tsx`
- [x] B9: `onSelectForVerification` wired in `chat-messages.tsx:158` → `message-bubble.tsx:732`
- [x] B10: Artifact card expand/preview functional (10+ references)
- [x] B13: Quote stripping in `get_configured_providers()`
- [x] Doc fixes: COMPLETED_PHASES.md, API_REFERENCE.md formatting, CERID_USE_BIFROST documented
- [x] Silent `except: pass` reduced to 0 in production code
- [x] `/conversations` endpoint — full CRUD in `routers/user_state.py`
- [x] Dependabot branches — all merged/closed (0 open)
- [x] Leapfrog roadmap — merged April 5 (commit `33aff0d`), synced to public

</details>
