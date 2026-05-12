# C3.2 — SPLADE-v3 Sparse Retrieval + Adaptive Recommendation Engine

**Repo:** `/Users/sunrunner/Develop/cerid-ai-internal`
**Target version:** v0.93.3 (continuing the 0.93.x line per the operator's versioning directive)
**Cycle:** Workstream E — Cycle 3 follow-on (C3.2 re-entry)

---

## Context

The 3-cycle RAG roadmap (`tasks/2026-05-11-rag-three-cycle-roadmap.md`) shipped 10 of 14 phases over v0.93.0–v0.93.2. C3.2 (sparse retrieval) was honestly deferred when the spike script reported that public BGE-M3 ONNX exports lacked the sparse head. Subsequent investigation found two things:

1. **The spike conclusion was partially wrong.** `aapot/bge-m3-onnx` and `yuniko-software/bge-m3-onnx` do ship 3-head exports. BGE-M3 sparse is technically usable.
2. **BGE-M3 sparse is still not the right choice.** The 2025-2026 literature explicitly says BGE-M3's sparse component underperforms SPLADE on BEIR/TREC-DL benchmarks. SPLADE-v3 is also smaller (~140 MB vs 2.16 GB), faster (~50-100 ms vs 227 ms per doc), has an official Sentence Transformers ONNX path, and has a trivial head (`log(1 + ReLU(max-pool(MLM logits)))`).

This plan ships SPLADE-v3 as the third retriever alongside the existing dense bi-encoder + BM25, fused via the existing N-way `rrf_fuse`. The feature defaults OFF.

It also addresses a **broader UX gap**: Cerid has three retrieval features (HyPE, parent-child, RRF) wired but flag-gated, with no in-app mechanism to tell the operator "your corpus has grown — turn this on." This plan introduces a **general adaptive-recommendation engine** as net-new infrastructure. SPLADE-v3 is its first user; HyPE + parent-child + RRF piggyback at no extra UI cost.

The recommendation engine is **one-way enable-only** (it never suggests turning a feature off). The banner is **dismissable per-session AND permanently per-tenant** (matches GitHub's notification model).

---

## Design summary

### Recommendation engine (net-new infrastructure)

- A new `ConfigRecommenderJob` (BaseJob subclass) runs every 6 hours via APScheduler.
- It queries Neo4j for non-eval-corpus Artifact count, reads current settings, and writes a Redis hash `cerid:recommendations` of features whose conditions are met but flags are off.
- `GET /health` gains a `recommended_features` array fed from that hash (filtered by per-tenant dismissals).
- The Settings pane gains a `RecommendationBanner` that polls `/health` every 60 s and renders dismissable cards.
- Each feature declares its threshold in a registry at `core/config/recommendations.py` — a single source of truth.

### SPLADE-v3 (first user of the engine)

- New encoder at `core/retrieval/sparse.py` (pure-Python, lazy-init, thread-safe). Sidecar fast-path with local-ONNX fallback. Two impl branches in `_encode`: `_encode_full_model` (if the ONNX export already bakes the head) vs `_encode_with_bolted_head` (if backbone-only, head bolted in numpy from the MLM weights). Picks at init by inspecting `session.get_outputs()`.
- New inverted index at `core/retrieval/sparse_index.py` (mirrors `core/retrieval/bm25.py` shape — per-domain JSONL corpus + in-memory inverted index).
- New flag `RETRIEVAL_SPARSE_ENABLED` + new fusion mode `tri_rrf` extending the existing `HYBRID_FUSION_MODE` enum.
- Query path: when `tri_rrf` is on, sparse retrieval runs in parallel with vector + BM25 in `asyncio.gather`; the three ranking lists feed `rrf_fuse` (already N-way capable).
- Ingest path: child-level chunks only, mirroring BM25 / HyPE pattern. Two-phase commit unchanged.
- Sidecar `/encode/sparse` endpoint definition added to the client; sidecar-side endpoint is a separate follow-on PR.

### Documentation cascade

Doc updates land in a single doc-only commit at the end of the cycle (Phase G) covering all v0.93.0–v0.93.3 work, not just C3.2.

---

## Files to create / modify

### New backend

| Path | Purpose |
|---|---|
| `src/mcp/core/retrieval/sparse.py` | SPLADE-v3 encoder. Public: `is_available()`, `encode_text()`, `encode_batch()`, `dot()`. Lazy `SpladeEncoder` singleton. |
| `src/mcp/core/retrieval/sparse_index.py` | Per-domain inverted index. Public: `get_index(domain)`, `index_chunks(...)`, `search_sparse(...)`, `rebuild_all()`, `is_available()`. Mirrors `core/retrieval/bm25.py:60-336`. |
| `src/mcp/core/config/recommendations.py` | Net-new module: `RecommendationSpec` dataclass + `RECOMMENDATIONS` registry. Each entry: `id`, `flag_name`, `condition_fn(stats) -> bool`, `reason_template`, `enable_payload`. Initial registry contains 4 entries: `sparse_retrieval`, `hype_indexing`, `parent_child_retrieval`, `rrf_fusion`. |
| `src/mcp/app/processor/jobs/config_recommender.py` | `ConfigRecommenderJob(BaseJob)` with `job_type="config_recommender"`. Pulls Neo4j artifact count, walks the registry, writes `cerid:recommendations` Redis hash. Idempotent. |
| `src/mcp/app/routers/recommendations.py` | Endpoints: `POST /settings/recommendations/{id}/dismiss` (server-side dismissal), `DELETE /settings/recommendations/{id}` (clear after enable). Per-tenant via existing tenant-scope helpers. |

### Modified backend

| Path | Change |
|---|---|
| `src/mcp/config/features.py` | Add `RETRIEVAL_SPARSE_ENABLED` near line 288 (next to `RETRIEVAL_HYPE_ENABLED`). Register `"enable_sparse_retrieval"` in `FEATURE_TOGGLES`. Add `RECOMMENDER_CORPUS_THRESHOLD = int(os.getenv("CERID_RECOMMEND_SPARSE_AT", "100"))` plus thresholds for HyPE + parent-child + RRF. |
| `src/mcp/config/settings.py` | At the `HYBRID_*` block (~L125–135): add `HYBRID_RRF_SPARSE_WEIGHT`. Update `HYBRID_FUSION_MODE` comment to document `tri_rrf`. Add `SPLADE_MODEL_PATH`, `SPLADE_ONNX_FILENAME`, `SPLADE_TOP_K_TERMS = 256`. Add `SCHEDULE_CONFIG_RECOMMENDER = "0 */6 * * *"`. |
| `src/mcp/core/agents/query_agent.py` | Around line 427–467: when `fusion_mode == "tri_rrf"` AND `RETRIEVAL_SPARSE_ENABLED`, add third `asyncio.to_thread` task for `search_sparse`. Pass `[vec, bm25, sparse]` + weights `[vec_w, bm25_w, sparse_w]` to `rrf_fuse`. Zero-cost when flag is off (no encoder load, no Chroma probe). |
| `src/mcp/app/services/ingestion.py` | At lines 467–469 and 859–863 (both BM25 call sites): after BM25, add a guarded `if config.RETRIEVAL_SPARSE_ENABLED: sparse_index_chunks(domain, bm25_ids, bm25_texts, tenant_id=...)` wrapped in `try/log_swallowed_error("app.services.ingestion.sparse_index", e)`. |
| `src/mcp/utils/inference_sidecar_client.py` | Add `async def sidecar_encode_sparse(texts, is_query=False)` mirroring `sidecar_embed`. Endpoint: `POST /encode/sparse`. Wrap in existing `"sidecar"` circuit breaker. |
| `src/mcp/app/routers/settings.py` | Extend `SettingsUpdateRequest` with `enable_sparse_retrieval: bool \| None`, `hybrid_fusion_mode: Literal["weighted_sum","rrf","tri_rrf"] \| None`, `hybrid_rrf_sparse_weight: float \| None`. Echo in GET response. PATCH handler uses `set_toggle("enable_sparse_retrieval", ...)` + direct assignment for the others. Persists via existing `write_settings_with_retry()`. |
| `src/mcp/app/routers/health.py` | Add helper `_load_recommendations(redis, tenant_id)` that reads `cerid:recommendations` hash, filters out entries in `cerid:recommendations:dismissed:{tenant}` set. Emit as `recommended_features: list[{id, reason, triggered_at, enable_payload}]`. Honor existing 10-s cache. |
| `src/mcp/app/scheduler.py` | New `async def _run_config_recommender()` after `_run_ingest_recovery` (~L263) — try-enqueue → fallback to direct service call. Register via `_scheduler.add_job(_run_config_recommender, CronTrigger.from_crontab(config.SCHEDULE_CONFIG_RECOMMENDER), id="config_recommender", ...)` inside `start_scheduler()`. |
| `src/mcp/app/main.py` | Register the new `recommendations` router. |
| `src/mcp/app/processor/jobs/__init__.py` | Add `ConfigRecommenderJob` import + `__all__` entry (canonical pattern from C1's d03622c HyPE fix). |

### New frontend

| Path | Purpose |
|---|---|
| `src/web/src/components/settings/recommendation-banner.tsx` | Polls `/health` (react-query, `staleTime: 60000`). Renders one Card per `recommended_features` entry. Three actions: **Enable now** (PATCH /settings with `enable_payload` + DELETE recommendation), **Maybe later** (sessionStorage snooze), **Dismiss permanently** (POST /settings/recommendations/{id}/dismiss). Amber border palette (matches existing "Custom" preset badge). |
| `src/web/src/lib/api/recommendations.ts` | Client helpers: `dismissRecommendation(id)`, `clearRecommendation(id)`. |
| `src/web/src/__tests__/recommendation-banner.test.tsx` | Renders only when health includes a rec; fires 3 actions correctly; respects sessionStorage snooze. |

### Modified frontend

| Path | Change |
|---|---|
| `src/web/src/lib/types.ts` | Extend `ServerSettings` (~L780) + `SettingsUpdate` (~L859) with the three new fields. Add `HealthResponse.recommended_features?: RecommendedFeature[]` interface. |
| `src/web/src/components/settings/pipeline-section.tsx` | Inside Customize disclosure (~L96), add new `PipelineToggle` for "Sparse Retrieval (SPLADE-v3)" with nested `SegmentedControl` for fusion mode (`Weighted` / `RRF (2-way)` / `RRF (3-way)`). Auto-pick `tri_rrf` when toggle flips on. Accepts a `recommended` prop. |
| `src/web/src/components/settings/settings-primitives.tsx` | Extend `PipelineToggle` signature with `recommended?: { reason: string }`. When set, render inline amber `Badge` ("Recommended") + `InfoTip` containing the reason. |
| `src/web/src/components/settings/settings-pane.tsx` | Mount `<RecommendationBanner patch={patch} />` above the section list. |

---

## Build order

### Phase A — sparse encoder + index (~1.5 days)

1. **Resolve ONNX export shape first.** Before writing the encoder, manually pull `naver/splade-v3` and run `python -c "import onnxruntime; s=onnxruntime.InferenceSession('...'); print([o.name for o in s.get_outputs()])"`. If outputs include `logits` (full-model export), use `_encode_full_model`. Otherwise use `_encode_with_bolted_head` (download `AutoModelForMaskedLM(...).cls.predictions.decoder.weight` once, cache as `.npz`).
2. Implement `core/retrieval/sparse.py` with both impl branches, picked at init via `session.get_outputs()`.
3. Implement `core/retrieval/sparse_index.py` (clone `bm25.py` structure; persist `data/sparse/{domain}.jsonl`).
4. Wire sidecar `/encode/sparse` client endpoint in `utils/inference_sidecar_client.py` (sidecar server impl is a separate follow-on PR).
5. **Tests (mocked)**: `tests/test_core_retrieval_sparse.py` + `tests/test_core_retrieval_sparse_index.py`. Pin a 3-doc fixture; assert sparse non-empty, dot-product invariants, persistence round-trip, tenant filtering, idempotent re-add, corrupted-line tolerance.

### Phase B — 3-way RRF wire-in (~0.5 days)

1. `config/settings.py` — add `HYBRID_RRF_SPARSE_WEIGHT`, document `tri_rrf`.
2. `core/agents/query_agent.py` — extend the BM25 branch with the sparse `asyncio.to_thread` call when `fusion_mode == "tri_rrf"`.
3. `core/retrieval/rrf.py` — confirm N-way already; add a 3-way test in `tests/test_rrf_fuse.py`.
4. **Test**: integration with staged ChromaDB + BM25 + sparse JSONL; assert 3-way fusion can surface IDs that no single list ranks first.

### Phase C — Ingest-time sparse indexing (~0.5 days)

1. Patch both `app/services/ingestion.py` call sites.
2. **Test**: `tests/test_ingestion_sparse_indexing.py` — flag-off no-op; flag-on indexes child chunks only; sparse exception doesn't break two-phase commit.

### Phase D — Recommender engine + scheduler + /health surface (~1.5 days)

1. `core/config/recommendations.py` — registry + dataclass.
2. `app/processor/jobs/config_recommender.py` — Cypher: `MATCH (a:Artifact) WHERE coalesce(a.sub_category,'') <> 'eval-corpus' RETURN count(DISTINCT a.artifact_id) AS n`. Writes Redis hash entries `{reason, triggered_at, enable_payload, corpus_size}` for each rec whose `condition_fn(stats)` is true AND whose flag is off.
3. `app/scheduler.py` — add `_run_config_recommender` + scheduler entry.
4. `app/routers/health.py` — add `recommended_features` to the response.
5. `app/routers/recommendations.py` — dismiss/clear endpoints.
6. `app/main.py` — register the new router. `processor/jobs/__init__.py` — register the job.
7. **Tests**: `tests/test_config_recommender_job.py` (Neo4j fixture: n=99 → no rec; n=100 → rec written; flag-on → no rec; eval-corpus excluded). `tests/test_health_recommended_features.py` (Redis fixture, dismissal honored, cache TTL respected). `tests/test_recommendations_router.py` (PATCH/DELETE round-trip).

### Phase E — Settings router PATCH/GET extension (~0.5 days)

1. Extend `SettingsUpdateRequest` + GET payload in `app/routers/settings.py`.
2. Validators raise `HTTPException(400)` for invalid `hybrid_fusion_mode`.
3. Regenerate `docs/ROUTER_REGISTRY.md` via `scripts/gen_router_registry.py`.
4. Regenerate `.env.example` via `scripts/gen_env_example.py`.
5. **Test**: `tests/test_settings_router_sparse.py` (PATCH echo, invalid mode rejected, persistence to SYNC_DIR mocked).

### Phase F — Frontend (~1.5 days)

1. `lib/types.ts` — type extensions.
2. `settings-primitives.tsx` — extend `PipelineToggle` with `recommended` prop.
3. `pipeline-section.tsx` — new toggle + nested SegmentedControl.
4. `recommendation-banner.tsx` — new component.
5. `lib/api/recommendations.ts` — client helpers.
6. `settings-pane.tsx` — mount banner.
7. **Tests**: `__tests__/recommendation-banner.test.tsx` + extend existing `pipeline-section` tests to cover the new toggle.

### Phase G — Doc cascade + ship (~1 day)

Single doc-only commit at end. See § Doc punch-list below. Bumps `pyproject.toml` to **v0.93.3**. Includes the canonical plan doc at `docs/plans/2026-05-12-c3-2-sparse-retrieval-plan.md` (which is THIS plan).

---

## Test strategy

| Layer | Test type | Files |
|---|---|---|
| Encoder | Mocked unit | `tests/test_core_retrieval_sparse.py` |
| Index | Mocked unit + fsync round-trip | `tests/test_core_retrieval_sparse_index.py` |
| RRF | Unit (3-way fusion) | `tests/test_rrf_fuse.py` (extend) |
| Query path | Mocked integration | `tests/test_query_agent_sparse.py` |
| Ingest | Mocked integration | `tests/test_ingestion_sparse_indexing.py` |
| Recommender job | Mocked Neo4j + Redis | `tests/test_config_recommender_job.py` |
| Health surface | Mocked Redis | `tests/test_health_recommended_features.py` |
| Recommendations router | TestClient round-trip | `tests/test_recommendations_router.py` |
| Settings router | TestClient round-trip | `tests/test_settings_router_sparse.py` |
| Banner UI | Vitest + Testing Library | `__tests__/recommendation-banner.test.tsx` |
| Pipeline UI | Vitest (extend) | existing `pipeline-section.test.tsx` |

**Live-stack tests deferred** to operator step — same pattern as HyPE eval gate. Eval gate parked until corpus growth.

---

## Verification gates (run before each phase commit)

- `.venv/bin/ruff check src/mcp/`
- `.venv/bin/mypy src/mcp/`
- `cd src/mcp && ../../.venv/bin/lint-imports` — confirms `core/retrieval/sparse*.py` doesn't import `app/`.
- `python3 scripts/lint-no-silent-catch.py src/mcp` — every new `except Exception` must call `log_swallowed_error("module.path", e)`.
- `PYTHONPATH=src/mcp .venv/bin/pytest src/mcp/tests/test_core_retrieval_sparse* src/mcp/tests/test_config_recommender_job.py src/mcp/tests/test_settings_router_sparse.py src/mcp/tests/test_recommendations_router.py`
- `python3 scripts/gen_router_registry.py --check`
- `python3 scripts/gen_env_example.py --check`
- Frontend: `cd src/web && npx tsc --noEmit && npx vitest run && npx eslint . --quiet`
- After Phase D: `curl /health | jq .recommended_features` against the sandbox stack.
- Eval gate: **parked** for 20-doc dev corpus (recall-saturated). Operator runs `docs/EVAL_BASELINES.md` procedure post-corpus-growth.

---

## Doc punch-list (Phase G — single commit)

| File | Action |
|---|---|
| `docs/plans/2026-05-12-c3-2-sparse-retrieval-plan.md` | **NEW** — this plan committed verbatim as the canonical execution doc. |
| `tasks/todo.md` | **NEW or UPDATE** — short pointer: `Active: docs/plans/2026-05-12-c3-2-sparse-retrieval-plan.md`. Use `git add -f` since `tasks/` is gitignored. |
| `CHANGELOG.md` | Add v0.93.3 entry covering SPLADE-v3 + recommender engine. Reference plan doc. |
| `docs/API_REFERENCE.md` | Add `POST /wiki/write_note`, `GET/PUT /briefs/settings`, `GET /watched-folders/{id}/vault-profile` (C3 carry-over). Add settings router additions (`enable_sparse_retrieval`, `hybrid_fusion_mode`, `hybrid_rrf_sparse_weight`). Add `GET /health.recommended_features` + `POST/DELETE /settings/recommendations/{id}`. |
| `docs/COMPLETED_PHASES.md` | Add rows for v0.93.0 (HyPE wiring), v0.93.1 (C2.1–C2.6), v0.93.2 (C3.1 spike, C3.3 two-way write, C3.4 synthesis writeback), v0.93.3 (C3.2 SPLADE + recommender). |
| `docs/ARCHITECTURE.md` | New short section "Sparse retrieval" referencing `core/retrieval/sparse.py` + the recommender job. Note `core ↛ app` rule preserved. |
| `docs/EVAL_BASELINES.md` | Append a "C3.2 ship row" alongside the prior deferral row, noting the SPLADE pivot from BGE-M3 with rationale. |
| `docs/MODEL_PRELOAD.md` | Add SPLADE-v3 to the optional preload list (~140 MB FP32, ~50 MB INT8). |
| `docs/SDK_GUIDE.md` (public) | Add `write_note()` method to SDK client. |
| `README.md` | Bump version badge to v0.93.3. Feature list: add "Hybrid retrieval (vector + BM25 + SPLADE-v3 sparse, RRF-fused)" + "Adaptive configuration recommender". |
| `tasks/2026-05-11-rag-three-cycle-roadmap.md` | Append single line under C3.2 pointing to the new plan doc. |
| `docs/ROUTER_REGISTRY.md` | Auto-regenerated via `scripts/gen_router_registry.py`. |
| `.env.example` | Auto-regenerated via `scripts/gen_env_example.py`. |

---

## UX opinions (cite-back, not litigatable)

- **Corpus-size thresholds** (initial registry values, env-overridable):
  - `sparse_retrieval` at 100 unique non-eval Artifacts
  - `hype_indexing` at 100
  - `parent_child_retrieval` at 100
  - `rrf_fusion` at 500 (the C1 eval ledger noted RRF needs larger corpus to dilute chunk-redundancy effect)
- **Banner placement at top of Settings pane**, not a global toast — recommendations are configuration nudges, not alerts.
- **Three-action dismissal**: "Enable now" + "Maybe later" (session-snooze) + "Dismiss permanently" (server-side per-tenant). Matches GitHub.
- **"Recommended" badge palette = amber**, same as the existing "Custom" preset badge — keeps visual vocabulary consistent.
- **Auto-pick `tri_rrf` when sparse toggle flips on**, with the segmented control still exposed for revert.
- **Recommender is enable-only** (locked decision). No "we recommend disabling" suggestions in v1.

---

## Risk register

| Risk | Mitigation |
|---|---|
| SPLADE-v3 ONNX export is backbone-only and we have to bolt the MLM head | Two impl branches in `sparse.py`, picked at init by inspecting `session.get_outputs()`. Spike the ONNX shape FIRST (Phase A step 1). |
| Sidecar `/encode/sparse` server-side endpoint doesn't exist yet | Client-only PR ships first; local-ONNX fallback works without sidecar. Sidecar follow-on PR in a future cycle. |
| Recommender job runs concurrent with ingest, causes Neo4j load spikes | LOW priority job; cron at 0 */6, off-peak; 30-second wall-clock timeout. |
| Operator disables sparse but corpus still grows; banner re-appears every session | Per-tenant "Dismiss permanently" stored server-side resolves this. |
| Sparse vector storage at 100K-chunk scale (~100-200 MB per domain JSONL) | Already in line with BM25 footprint. No new infrastructure needed. |
| Eval gate fails at 20-doc dev corpus (same saturation as HyPE) | Eval gate parked by design. Wiring is the v0.93.3 deliverable; gate flips post-corpus-growth. |

---

## Existing utilities to reuse (do NOT reinvent)

- `core/retrieval/bm25.py:60-336` — clone for `sparse_index.py` shape (per-domain JSONL + tenant filtering + fsync + corrupted-line tolerance).
- `core/retrieval/rrf.py` — already N-way; just call with 3 lists.
- `app/routers/settings.py:set_toggle()` — canonical boolean flag mutation; persists via `write_settings_with_retry()` to SYNC_DIR.
- `app/db/redis/processor_queue.py:enqueue_job(payload=...)` — canonical pattern from C1 `d03622c`; recommender job uses this.
- `core/processor/job.py:BaseJob` ABC — subclass for `ConfigRecommenderJob`.
- `app/scheduler.py:_run_*` pattern — clone for `_run_config_recommender`.
- `app/routers/health.py` invariants block — extend with `recommended_features`.
- `core/utils/swallowed.py:log_swallowed_error()` — required for every `except Exception` in hot paths.
- `components/settings/settings-primitives.tsx` — `PipelineToggle`, `SegmentedControl`, `Badge` (amber), `InfoTip`. Reuse, don't duplicate.
- `components/settings/pipeline-section.tsx:96-209` — canonical disclosure shape; new toggle slots in.
- `lib/log-swallowed.ts:logSwallowedError(err, reason)` — frontend swallow helper.

---

## Out of scope (future cycles)

- Sidecar server-side `/encode/sparse` endpoint (client-only this cycle).
- Multilingual sparse retrieval (SPLADE-v3 is English-only via BERT vocab). Future: investigate MILCO (`arxiv 2510.00671`) when ONNX tooling matures.
- "Recommend disabling" direction (locked: enable-only).
- Per-feature thresholds tunable via UI (env-var only this cycle).
- Eval gate run (operator step, post-corpus-growth).

---

## Verification end-to-end

After Phase G is committed:

1. `git log --oneline v0.93.2..HEAD` — confirm clean linear history.
2. `make preservation-check` — 35 preservation gates pass.
3. CI green on internal main (lint + typecheck + test + frontend + docker + preservation).
4. Sync to public via `python3 scripts/sync-repos.py to-public --track-deletions`; validate; commit.
5. Tag `v0.93.3` on both repos; watch public CI green.
6. **Smoke test against sandbox**:
   - `docker exec ai-companion-mcp-sandbox printenv RETRIEVAL_SPARSE_ENABLED` → empty (default off).
   - `curl http://127.0.0.1:8898/health | jq .recommended_features` → returns array (may be empty if corpus < 100).
   - Ingest 100 docs into sandbox → wait 6 hours OR manually run the recommender job → `curl /health.recommended_features` → contains `sparse_retrieval`.
   - Open Settings pane in the UI → see amber banner; click "Enable now" → settings PATCH succeeds → banner clears → sparse toggle flips on → ingest a new doc → `data/sparse/{domain}.jsonl` populated.
