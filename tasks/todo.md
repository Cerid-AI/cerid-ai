# Cerid AI — Sprint TODO

> **Updated:** 2026-04-15
> **Version:** 0.83.0 (Search tuning + Memory efficacy + Verification rigor + Bug-hunt fixes)

---

## Completed — Session 2026-04-15 (bug-hunt + structural fix sprint)

- [x] Comprehensive new-user bug-hunt on public repo (fresh clone, setup, validate-env, API sweep, GUI walkthrough, E2E) — **15 bugs identified**
- [x] Root-cause grouping (15 surface bugs → 8 root causes) — `tasks/bughunt-fix-plan.md`
- [x] Parallel sub-agent swarm execution (6 agents: Alpha/Bravo/Charlie/Delta/Echo/Foxtrot)
- [x] **Fix E — Embedding singleton + startup dim-check + `/admin/collections/repair`** (`src/mcp/core/utils/embeddings.py`, `app/startup.py`, `app/routers/kb_admin.py` + 3 tests)
- [x] **Fix D + #1 — `/agents/activity/*` alias router + SSE exponential back-off + unified INSTALL.md** (`routers/agent_console.py`, `use-agent-activity-stream.ts`, `INSTALL.md` + tests)
- [x] **Fix A+F — Remove age from public README + CERID_SYNC_DIR_HOST rename + CONTRIBUTING drift** (`.env.example`, `docker-compose.yml`, `README.md`, `CONTRIBUTING.md`, `validate-env.sh` guard)
- [x] **Fix B — `scripts/lib/healthcheck.sh` shared library + auth-aware Redis/Neo4j + Bifrost skip + zombie cleanup** (all 3 scripts)
- [x] **Fix C — `MIN_VERIFIABLE_LENGTH` FE/BE alignment 200 → 25** + cross-reference comment + regression test
- [x] **Fix G — Tab title "Cerid Core" → "Cerid AI" + KB counter unification + Knowledge Digest errors drill-through modal** (`App.tsx`, `knowledge-pane.tsx`, `digest-card.tsx`, `digest.py`, `types.ts`)
- [x] Integration cross-check — wiring confirmed at main.py:572 (startup) + main.py:762 (activity_router); Charlie's `.env.age` guard + Delta's lib source coexist cleanly
- [x] Python syntax verification (11 touched files) — all clean
- [x] Shell syntax verification (3 scripts) — all clean
- [x] Frontend verification — tsc clean, eslint clean, vitest **719 pass** (+14 over 705 baseline), build 533KB main bundle
- [x] `validate-env.sh` live run — 13/14 pass (1 pre-existing data-dir failure unrelated)
- [x] Rebuilt MCP + cerid-web against internal tree
- [x] Live smoke — Paris canary verifies via cross_model; `/agents/activity/recent` HTTP 200; API sweep 6/7 OK
- [x] Commit by logical phase — internal `863b77e`, `07f7b6c`, `e8e31f0`, `3debefb` — all pushed to `origin/main`
- [x] Sync to public — validator clean; public `329db27` pushed
- [x] `tasks/lessons.md` — appended 10 new lessons from this session
- [x] `tasks/SESSION-REVIEW-2026-04-15.md` — full context dump for next session

## Review — Session 2026-04-15

### Result
- **15/15 bugs fixed**, all as structural root-cause resolutions (no symptom patches)
- **Frontend tests: +14** (705 → 719 passing; 1 skipped)
- **Zero regressions** in the 42-case verification battery baseline
- **Zero open PRs, zero stale branches** on both repos
- **Both CIs running** at session close (check status at next session start)

### Commits shipped
| SHA | Summary |
|---|---|
| `863b77e` | fix(pipeline): critical new-user blockers — embedding singleton, agent activity stream, install docs |
| `07f7b6c` | fix(onboarding): public-repo doc polish, CERID_SYNC_DIR rename, healthcheck rewrite |
| `e8e31f0` | fix(ux): verification-panel wiring + tab title + KB counter + errors drill-through |
| `3debefb` | chore(sync): exclude tool caches from public sync |
| `329db27` | (public) fix(pipeline+onboarding+ux): comprehensive bug-hunt fixes |

### Known open items (deferred)
- **Compound-sentence sub-claim extraction** — flagged in fix plan; would need extractor prompt tuning or post-pass splitter
- **`/conversations` 404** — pre-existing API shape drift, not in this fix scope
- **6 pre-existing pytest failures** (test_hallucination + test_taxonomy mock drift) — confirmed pre-existing on main; separate cleanup
- **`stacks/infrastructure/data/` missing** on this dev machine — `validate-env.sh` flags but unrelated to any code change

### Next-session entry point
See `tasks/SESSION-REVIEW-2026-04-15.md` for the complete pickup handoff.

---

---

## Completed — Session 2026-04-13

- [x] Source-aware external query construction (adapt_query/is_relevant per source, intent-based routing)
- [x] CRAG retrieval quality gate (supplements with external sources when KB results are poor)
- [x] Verified-fact-to-memory promotion pipeline (high-confidence claims auto-promote to empirical memories)
- [x] Tiered memory authority boost (0.05-0.25 based on verification status/confidence)
- [x] Refresh-on-read memory decay (decay_anchor reset on retrieval, Ebbinghaus rehearsal)
- [x] NLI consolidation guard (prevents semantic drift during memory merges)
- [x] Fix hardcoded NLI threshold 0.5 → config 0.7 in Self-RAG
- [x] Fact-relationship verification (temporal/entity/specificity alignment checks)
- [x] Graph-guided verification (Neo4j relationship structure as evidence)
- [x] Authoritative external verification (LLM synthesizes from external data, not parametric memory)
- [x] Full conversation context threading to expert verification mode
- [x] Dynamic confidence scoring per external source (Wikipedia title match, Wolfram non-answer, DuckDuckGo .gov boost)
- [x] Memory efficacy measurement module (eval-only)
- [x] Fix Docker path issue in test_retrieval_orchestrator.py
- [x] Fix deduplicate_results crash on external results without artifact_id
- [x] Fix non-streaming endpoint missing expert_mode/user_query pass-through
- [x] Fix verified_memory field mapping (status/similarity vs verdict/confidence)
- [x] Fix httpx.HTTPError missing from all 6 data source exception handlers
- [x] Fix Wolfram API HTTP → HTTPS (credential exposure)
- [x] Fix ClaimOverlay: kb_nli claims now show artifact link/snippet
- [x] Add expert mode indicator badge to ClaimOverlay
- [x] 65 new pipeline enhancement tests + Docker path fix (2374 backend + 705 frontend tests)

---

## Immediate — This Session (2026-04-12)

- [x] Fix setup wizard dark mode: `useTheme()` called in App.tsx so `dark` class applies during wizard (bg-circuit, glow-teal now render)
- [x] Fix "Try It Out" wizard performance: skip reranking, reduce top_k 10→3, cut post-upload delay 1s→300ms, shorten retries 3.5s→1.1s
- [x] Fix chat event loop stalls: `asyncio.Semaphore(2)` on agent queries, `asyncio.to_thread` on ChromaDB/embedding/reranking sync calls, skip auto-inject on first message, AbortController on timed-out KB fetches
- [x] Wire missing `replaceMessages` prop to `useChatSend` (enables history compression)
- [x] Add `useReranking` and `signal` options to `queryKB` API client
- [x] System check card: retry logic with 5s backoff when backend unreachable
- [x] Setup wizard: `canSkip` prop for returning users who already configured
- [x] Docker: extended healthcheck start_period, improved watchdog with `os._exit`, `init: true` for signal handling

## Previous Session (2026-04-10)

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
