# Changelog

All notable changes to cerid-ai are documented here.

## v0.91.1 — Infrastructure migrations + GraphRAG retrieval (2026-05-09)

Chromadb 0.5 → 1.x (client + server) + Neo4j 5.26 → 2026.04.0 calver
(server + APOC + GDS plugin) + the full Workstream E Phase 4 GraphRAG
arc (entity layer + community layer + auto query routing) all landed
end-to-end on top of v0.91.0. The shared compose stack on the
maintainer machine has been migrated lossless (chromadb: 13,264
chunks; Neo4j: 11,672 artifacts + 12,840 nodes + 46,622 rels), and
both internal and public CIs are green.

### What's already on `main`
- **Phase 1 (`5040e96`)** — `core/retrieval/semantic_cache.py` rewritten
  atop a chromadb collection. Drops the chroma-hnswlib transitive,
  retires the 16-byte `_HNSW_MAGIC` dim self-heal protocol, adds lazy
  orphan eviction. Public API preserved; layering preserved (no
  chromadb import in `core/`).
- **Phase 2a (`b169c91`)** — `OnnxEmbeddingFunction` gains the chromadb
  1.x EF contract: `name()` / `get_config()` / `build_from_config()`.
  No-op on 0.5; live the moment the pin lifts.
- **Phase 2b (`2d5e5df`)** — `chromadb>=1,<2` in `requirements.txt` +
  `requirements.lock` regenerated; `_startup_compat.py` deleted (1.x
  retired the chromadb→posthog telemetry path so the shim is dead);
  heartbeat probe paths in `app/main.py` + `app/routers/setup.py`
  flipped `/api/v1/heartbeat` → `/api/v2/heartbeat`;
  `scripts/reembed_collection.py` updated for `IncludeEnum` retirement.
- **Phase 2c (`9ce7c54`)** — `app/sync/import_.py` migrated to the v2
  REST path scheme `/api/v2/tenants/{tenant}/databases/{db}/collections/...`
  via a single `_v2_collections_base()` helper.
- **Cleanup (`d582cfb`)** — dead `SEMANTIC_CACHE_HNSW_EF` env var removed.

### Phase 3 — committed locally, **not pushed**

`docker-compose.yml` chromadb block: image
`chromadb/chroma:0.5.23` → `chromadb/chroma:1.5.9`; volume mount target
`:/chroma/chroma` → `:/data` (1.x persistence path moved); healthcheck
`/api/v1/heartbeat` → `/api/v2/heartbeat`.

**WARNING — destructive on first boot.** chromadb 1.x runs
`migration_mode: "apply"` against the host data dir; the 0.5-era sqlite
+ segment files are transformed in place. **One-way.** Rollback
requires a tarball snapshot restore.

**Operator runbook before activating** (full version in
`docs/DEPENDENCY_UPGRADES.md` § ChromaDB Phase 3):

1. `docker compose stop chromadb`
2. `./scripts/backup-kb.sh` (snapshot all three volumes)
3. `git pull` (after maintainer pushes the Phase 3 commit)
4. `docker compose pull chromadb && docker compose up -d chromadb`
5. Verify `/api/v2/heartbeat` 200 + collections survived (preservation
   harness)

### Neo4j Phase 2 — server bump 5.26 → 2026.04.0 + GDS plugin

`docker-compose.yml` neo4j block: image `neo4j:5.26.21-community` →
`neo4j:2026.04.0-community`; plugins
`["apoc"]` → `["apoc","graph-data-science"]`; heap `1G → 4G` (GDS in-memory
projections); container memory `4G → 8G`; healthcheck `start_period`
`30s → 60s` (longer cold-boot with GDS catalog registration).

**Pre-edit verification (empirical, against `neo4j:2026.04.0-community`):**
- Driver `neo4j>=6,<7` (currently 6.1.0) speaks the calver Bolt
  protocol cleanly: auth probe `RETURN 1` works; `CREATE CONSTRAINT
  ... IF NOT EXISTS FOR ... REQUIRE` works; `MERGE` + `MATCH` +
  relationship writes work.
- `CALL gds.version()` returns `2026.04.0` (synced version line).
- `apoc.version()` returns `2026.04.0`.
- Plugin name correction: `gds` short form is rejected by calver
  (accepted: `apoc`, `apoc-extended`, `bloom`, `fleet-management`,
  `genai`, `graph-data-science`). Earlier migration plan had the
  short form; corrected to full name.
- Direct in-place upgrade 5.26 → 2026.x is supported (Neo4j docs;
  no intermediate version, no store-format change between 4.4 and
  2026.x); existing databases default to Cypher 5 on first 2026.x
  boot.

**Operator runbook before activation** (also in
`docs/DEPENDENCY_UPGRADES.md` § Neo4j Phase 2):

1. `docker compose stop cerid-web mcp-server neo4j`
2. `./scripts/backup-kb.sh` (snapshot all three volumes)
3. `git pull` (after maintainer pushes the Phase 2 commit)
4. `docker compose pull neo4j`
5. `docker compose up -d neo4j` — wait for healthy (cold boot ~45–60s
   with GDS plugin install on first boot)
6. `docker compose up -d mcp-server cerid-web`
7. Verify `curl -s http://127.0.0.1:8888/health` shows
   `services.neo4j == "connected"` + `healthy_invariants == true`
8. Verify GDS available:
   `cypher-shell` → `CALL gds.version()` returns `2026.04.0`

Rollback: stop neo4j, restore the snapshot tarball over
`./stacks/infrastructure/data/neo4j`, revert the docker-compose neo4j
block to the 5.26.21 image + `["apoc"]` plugins + 1G heap + 4G memory,
restart.

### Workstream E — GraphRAG retrieval (Phases 4a + 4b end-to-end, 2026-05-09)

Builds the entity + community layers on top of the upgraded
chromadb 1.x + Neo4j calver stack. All sub-phases landed on `main`;
the corpus backfill (operator-invocable) and apscheduler wiring of
`refresh_communities.py` are the only remaining touch-points before
this arc is fully populated on the live dataset.

**Phase 4a — entity layer + local mode:**

| Sub-phase | Commit | What |
|---|---|---|
| 4a.0 | `e085d98` | `neo4j-graphrag>=1.16,<2` dep |
| 4a.1 | `e085d98` | spike: ship custom `ChromaNeo4jRetriever` shim |
| 4a.2 | `fbe6eed` | `(:Entity)` schema (`canonical_id` UNIQUE + `name`/`entity_type` indexes) |
| 4a.3 | `89ec055` | `core/agents/entity_extraction.py` + `app/db/neo4j/entity.py` (LLM NER, type vocab, canonical_id, idempotent UPSERT) |
| 4a.4 | `4dccaed` | `scripts/backfill_entities.py` — resumable, checkpointed; 5-art pilot validated |
| 4a.5 | `5dc7445` | `core/retrieval/graphrag_retriever.py` — `ChromaNeo4jRetriever` shim subclassing `ExternalRetriever` |
| 4a.6 | `56d2e21` | `RETRIEVAL_MODE` switch + step-6 `graph_expand_results_via_entities` + `embed_query` polymorphism fix (chromadb 1.x list-vs-string protocol) |
| 4a.7 | `b676ac3` | `tests/eval/graphrag_local_benchmark.py` — entity-grounded pseudo-gold harness |

Layering preserved throughout: `core/` keeps zero `chromadb` import
(the chroma collection is injected via `_CacheBackend`-style protocol
into `ChromaNeo4jRetriever`).

**Phase 4b — community layer + global mode:**

| Sub-phase | Commit | What |
|---|---|---|
| 4b.0 | `97a5b12` | `graphdatascience>=1.21,<2` Python client |
| 4b.1 | `5a3e658` | `app/db/neo4j/community_detection.py` — Leiden over Entity CO_MENTIONED graph (UNDIRECTED projection, all hierarchical levels); writes `(:Community)` + `IN_COMMUNITY` edges |
| 4b.2 | `0531040` | `app/db/neo4j/community_summaries.py` — top-K-by-degree entities + 1 representative chunk per entity → `call_internal_llm` (Ollama Haiku) → cached on `Community.summary` |
| 4b.3 | `419ac3e` | `core/agents/query_router.py` — heuristic v1 (`>15 words ∧ no quoted spans ∧ no proper nouns → global`) |
| 4b.4 | `4eff566` | `RETRIEVAL_MODE=auto` + `graph_expand_results_via_communities` in step-6; `scripts/refresh_communities.py` composes Leiden + summaries for nightly invocation |
| 4b.5 | `6e0905a` | `tests/eval/graphrag_global_benchmark.py` + full unit-suite green (3065 tests) |

**Live verification** (against running stack: chromadb 1.5.9 + neo4j
2026.04.0 + GDS 2026.04.0; 26-artifact pilot dataset):

- Entity extraction: 26 artifacts → 143 entities, 7 distinct types
- Local-mode expansion: seed → 5 related artifacts via 4 shared entities
- Leiden: 24 communities at level 0, **modularity 0.79**, semantically coherent (financial-crisis / oncology / food-science / embryology clusters)
- Global-mode expansion: seed → community summary covering its theme

**CI hotfix** (`2d33001`): typecheck (dict-set annotation in
`community_detection`), strict silent-catch in projection cleanup
(`log_swallowed_error` instead of `pass`), DUO138 ReDoS false-positive
on `_PROPER_NOUN_RE` (documented + `# noqa: DUO138`).

**Corpus purge + post-tag operator activation (2026-05-09):**

- Discovered ~75% of the 11,755 artifact mass was bulk medical literature
  (5,183 PubMed-like + 3,591 MED-NNNN); largely irrelevant for the
  documented "general use + personal-assistant knowledge context" RAG
  purpose. The memory-artifact subspace was further dominated by 2,846
  `memory_empirical_*` records — diagnostic test outputs from
  `cerid-trading-agent` that landed in cerid-ai's KB by accident.
- Authorised purge: dropped the 2,846 trading-agent diagnostic artifacts
  from chromadb `domain_conversations` + Neo4j (cascade DETACH DELETE
  pulled 16,524 MENTIONS edges; orphan-Entity cleanup dropped 1,337
  nodes + 6 CO_MENTIONED edges). Final corpus: **8,910 artifacts**.
  Snapshot at `backups/2026-05-09_18-09-57` if rollback needed.
- Tiered backfill produced 382 artifacts with entity mentions across
  T1 (personal/work, 42 art), T2 (real memories, 7 art), T3 (top-500
  by quality from the MED bulk, 333 art). **2,536 entities, 3,367
  MENTIONS edges, 24,322 CO_MENTIONED edges.**
- Leiden refresh: **410 / 245 / 230 / 228 communities** across levels
  0..3 (1,093 total), 28.2s. **405 LLM-summarised** (rest below size
  threshold).

**GraphRAG eval lift on the populated graph:**

| Metric | baseline | local_graphrag | lift |
|---|---|---|---|
| NDCG@10 | 0.011 | 0.107 | **+875%** |
| Recall@10 | 0.005 | 0.034 | **+653%** |
| Precision@10 | 0.010 | 0.092 | **+822%** |
| Queries scoring nonzero | 1/10 | 3/10 | 3x coverage |

Eval is pseudo-gold (entity-mention sets stand in for human labels),
so absolute scores are bounded by per-query gold sets that exceed
top-k. The meaningful signal — the **~10x lift of local_graphrag over
baseline across all metrics** — confirms entity-neighborhood
expansion does real work on the populated graph.

Global-mode benchmark: 4/5 thematic queries surfaced ≥1 community
summary in `global_graphrag` mode vs zero in `local_graphrag` —
the routing-shape distinction holds as designed.

## v0.91.0 — Workstream A close-out + C + D Phase 1 + E Phase 2 (2026-05-03 → 2026-05-08)

Cross-project SLO hardening (with `benchmark-slo` now PR-blocking) +
Pro-tier checkout end-to-end + Python 3.12 runtime bump + retrieval-quality
work + the systemic close-out of the trading-agent interface ledger.

### Workstream A close-out gate flipped (2026-05-08)

- **`benchmark-slo` promoted to PR-blocking.** After 4 consecutive green
  main runs (matching the `sdk-openapi-drift` 2026-04-21 precedent),
  `continue-on-error: true` removed and `benchmark-slo` added to the
  `docker` job's `needs[]` list. Real-OpenRouter latency drift now
  blocks merges, complementing the deterministic budget-plumbing tests
  in `test_memory.py` / `test_memory_consolidation.py` that gate the
  per-stage `asyncio.wait_for` wrappers.

### Workstream D Phase 1 — Python 3.12 runtime (2026-05-08)

- **Python 3.11 → 3.12.** Dockerfile (builder + runtime stages +
  site-packages path), `pyproject.toml` (`requires-python`, ruff
  `target-version`, mypy `python_version`), and 14 `setup-python`
  blocks across `ci.yml` all converged on 3.12. The lock-sync job
  was already running in `python:3.12-slim`; CI / Dockerfile / lock
  now all match. Lock regenerated; no Python-side code changes
  required (no `match` adoption needed for the bump itself).
- **Neo4j Python driver pin tightened to `>=6,<7`.** Audit-verified
  clean: all sessions use `with`, `Result.summary()` migrated to
  `consume()`, no deprecated `read_transaction`/`write_transaction`,
  no `Transaction.sync`. Driver 7.x doesn't exist on PyPI yet
  (latest is 6.2.0); the next phase of the Neo4j upgrade is
  server-side (5.26 → 7.x + GDS plugin), tracked separately due to
  the one-way data-volume migration.

### Deferred (multi-day, separate sessions)

- **chromadb 0.5 → 1.x.** `semantic_cache.py` rewrite-class (~520 LOC),
  `chroma-hnswlib` drops, REST v1→v2 path changes, one-way data-volume
  migration. 4-phase plan documented in `tasks/todo.md`.
- **Neo4j server 5 → 7 + GDS plugin.** Memory budget bump required for
  GDS in-memory projections (heap 1G → 4G, container 4G → 8G).
- **E.4a/4b GraphRAG.** Gated on Neo4j 7 + GDS. Spike resolved:
  `ChromaNeo4jRetriever` subclass (~150 LOC) keeps vectors in Chroma
  while entity graph lives in Neo4j 7 — preserves the
  vectors-in-Chroma / graph-in-Neo4j architectural split documented in
  `docs/COMPETITIVE_ANALYSIS.md`.
- **ESLint 9 → 10.** Original blocker (`eslint-plugin-react-hooks`)
  cleared; new blocker is `eslint-plugin-jsx-a11y@6.10.2` (peer caps
  at ESLint 9, no release in ~18 months). ~1-hour task once jsx-a11y
  ships.

### Cerid-AI interface contracts (closes the trading-agent ledger)

Five systemic invariants land here, each addressing a class of problem
the trading-agent had to work around client-side. Every change is
schema-enforced (Pydantic + OpenAPI), test-gated, and back-compatible.

- **D — Object-envelope contract on `/agent/memory/recall`.** Bare
  `[]` returns broke naive `body.get(...)` parsers and inflated
  consumer error rates. Now returns `{memories, total}` via a typed
  `MemoryRecallResponse` model with `response_model=` enforcement on
  the route.
- **E — `min_length=1` on required-but-empty-trapped identifiers.**
  Empty `conversation_id` no longer reaches the handler's runtime 422
  branch — Pydantic rejects up-front, the constraint surfaces in the
  OpenAPI spec, and the `sdk-openapi-drift` gate keeps it stable.
- **C — `slo_budget_ms` on `/sdk/v1/llm/complete`.** Smart-router
  filters tiers by their empirical p95 latency profile; if no tier
  fits the budget, the handler returns `503` with a `Retry-After`
  header carrying the floor p95. Never silently downgrades — quality
  drops would be invisible to the caller. Response now carries
  `tier_p95_ms` so callers can tune adaptive client-side timeouts.
- **B — `mode=fast | thorough` on `/agent/hallucination`.** Fast mode
  runs claim extraction only and returns claims marked
  `status='uncertain'` with `nli_skipped=true` — same envelope, no
  cross-model NLI cost. Thorough mode (default) preserves existing
  behaviour. Trading-agent's client-side `asyncio.wait_for(2.0)` wrap
  becomes a server-side contract.
- **A — async-by-default `memory_extract` with sync escape hatch.**
  When `MEMORY_QUEUE_MODE=async`, `POST /sdk/v1/memory/extract`
  returns `202` + `job_id` + `Location` header; callers poll
  `GET /sdk/v1/memory/extract/jobs/{job_id}` for the result.
  `?wait=true` forces sync for callers that need the result inline.
  Lifts the extract→consolidate→store pipeline off the request slot
  entirely — closes the residual 1.0% timeout cluster the per-stage
  budgets (Phase 1.2) couldn't reach.

The async/202 pattern shipped here is the canonical answer for any
future endpoint whose response is "acceptance, not result." A new
`cerid-memory` queue runs alongside `cerid-ingest`; the same worker
process drains both, and each queue has an independent
`*_QUEUE_MODE` opt-in flag.

### Pro tier checkout (Workstream C)

- **Stripe Checkout end-to-end shipped.** The Pro Settings pane's
  upgrade button now opens a Stripe-hosted Checkout URL in a new
  tab; manual license-key entry remains as the offline-activation
  fallback.
- **Webhook coverage expanded** to handle the full subscription
  lifecycle. The previously-unhandled `customer.subscription.updated`
  event now deactivates the license on `past_due` / `unpaid` /
  `canceled` / `incomplete_expired` statuses; `active` / `trialing` /
  `paused` no-op (paused is a customer-initiated vacation hold —
  entitlement is preserved during the pause window).
- **Comprehensive unit-test coverage** added for the billing router
  (every endpoint and every webhook branch, deterministic, < 1 s,
  no live Stripe required).

### Cross-project SLOs (Workstream A)

- **Phase 1.2 — `/sdk/v1/memory/extract` per-stage budgets.** Three
  unbounded LLM call sites (`extract_memories`, `_llm_classify`,
  `resolve_memory_conflict`) now wrapped in `asyncio.wait_for` with
  empirically sized budgets (12s on the load-bearing extract, 8s on
  the consolidation/conflict siblings) and `log_swallowed_error` on
  the timeout branch. Replaces the httpx 20s default that was
  absorbing the 5.7% long tail observed in trading-agent's 20.4h soak.
  Removes `xfail(strict=True)` from the SLO test
  `test_memory_extract_under_10s` so the budget is now a hard CI gate.
- **Phase 1.3 — `/observability/restarts` endpoint + 10s healthcheck
  timeout.** New endpoint exposes process start time, uptime, and a
  Redis-backed monotonic restart counter so trading-agent and other
  dependents can detect MCP boots in one call. Healthcheck timeout
  bumped 5s → 10s so a slow `/sdk/v1/*` response under peak load
  can't false-positive into a restart.
- **Phase 1 close-out gate — split-test design.** Replace the original
  "promote `benchmark-slo` to PR-blocking after 7 consecutive green
  main runs" plan with a two-test approach:
  * **Deterministic budget-plumbing tests** in `test_memory.py` /
    `test_memory_consolidation.py` / `test_pipeline_enhancements.py`
    monkey-patch the per-stage budget down to 50 ms and assert the
    `asyncio.wait_for` actually fires + the fallback path runs +
    `log_swallowed_error` is called. Run inside the default `test`
    job — already PR-blocking, so budget regressions are caught on
    every PR.
  * **Live `benchmark-slo` job** soft-promoted onto the PR pipeline
    with `continue-on-error: true`. Surfaces real-OpenRouter latency
    drift via job-summary + JSON artifact without gating merges.
    Promote to blocking by removing `continue-on-error` and adding
    to the `docker` job's `needs[]` once 4 consecutive green main
    runs accumulate (matching the `sdk-openapi-drift` precedent).

### Retrieval quality

- **Phase 2b — layout-aware parsing flipped to default ON.** Clean win
  on every dimension against the live eval-corpus: `+0.05 MRR`,
  `+0.024 NDCG@10`, `+0.088 precision@5`, latency *improved* 5–14%
  across percentiles. Revertable via `ENABLE_LAYOUT_AWARE_PARSING=false`.
  The new floor lives in `tests/eval/baselines/retrieval.json`.
- **Phase 2a — keyword-coverage scoring family.** Adds the
  exploratory-suite scoring mode that the dual-gate decision (Phase 1.2
  audit) reserved for the 5 keyword-only public datasets.
- **Phase 2b.1 — BEIR seed plumbing.** SciFact (300 queries) and
  NFCorpus (323 queries) gold judgments captured against cerid's
  `relevant_paths` schema. BEIR cache defaults to `/tmp/` (the
  eval-corpus mount is read-only).
- **Phase 2c — nightly exploratory eval workflow.**
  `.github/workflows/eval-exploratory.yml` runs daily at 07:30 UTC,
  boots the stack, seeds the synthetic corpus, runs `benchmark_suite`
  across the synthetic + 5 keyword-only public datasets, and posts a
  job summary + uploads the JSON report. `workflow_dispatch` exposes
  a `seed_beir` toggle for the ~2.5h BEIR pass. Drift is signal, not
  a gate — PR merges still gate on `benchmark-slo` and `ragas-eval`.
- **Phase 3a (RRF default) and Phase 3b (contextual chunks default)
  tested and reverted.** RRF regressed every metric on chunk-level
  corpora; contextual lifted precision@5 only while blowing the
  latency budget by +59% p95. Both remain opt-in via env. Findings
  ledgered in [`docs/EVAL_BASELINES.md`](docs/EVAL_BASELINES.md).

### Surface

- **`/sdk/v1/llm/complete`** — smart-routed LLM completion endpoint
  that fans out to OpenRouter / Ollama via the existing `llm_client`
  routing. OpenAPI baseline regenerated.

### Reliability

- **Asyncio test migration.** 9 test files
  (`test_foundation`, `test_pipeline_enhancements`, `test_query_agent`,
  `test_ingestion`, `test_async_ingestion`, plus four orchestrator /
  budget / tools / workflows files) moved off the deprecated
  `asyncio.get_event_loop().run_until_complete(...)` pattern onto
  `asyncio.run(...)`. Clears the 30-test `RuntimeError: There is no
  current event loop` cluster on Python 3.12.
- **`requirements.lock` regenerated** against `python:3.12-slim` —
  `lock-sync` CI gate back to green.
- **Langfuse compose fix.** `HOSTNAME=0.0.0.0` so Next.js standalone
  binds all interfaces, plus `127.0.0.1` (not `localhost`) in the
  healthcheck so busybox `wget` doesn't get refused over IPv6. Both
  root causes documented inline in `stacks/langfuse/docker-compose.yml`.
- **Trivy ignore additions.** `CVE-2026-4878` (libcap TOCTOU race —
  not reachable in single-user container) and `CVE-2026-33845`
  (GnuTLS DTLS path — we use TLS only via httpx). Both with
  re-evaluation dates.

### Sync hygiene

- **`scripts/sync-repos.py` public-repo path resolver.** Walks
  `CERID_PUBLIC_REPO` env → sibling `cerid-ai` (canonical) → sibling
  `cerid-ai-public` (legacy alias). No more "public repo not found"
  on the canonical layout.
- **`.sync-manifest.yaml`** — removed a stale `internal_only` entry
  whose target file no longer exists on disk; closes the
  ``WARN  Missing internal-only file`` noise on every
  ``sync-repos validate``.
- **Untracked `packages/sdk/typescript/dist/`** build output. Added
  to `.gitignore`. Regenerated by `npm run build` when needed.

## v0.90.0 — 2026-04-22

Cerid 0.90 hardens the reliability story while keeping the install
path one command.

### What's new

- **First-run setup wizard.** Guided eight-step path (Welcome → Keys →
  Storage → Ollama → Apply → Health → Try → Mode) replaces the
  copy-edit-restart loop. System check probes Docker, configured keys,
  and Ollama before you commit; hardware auto-detection sizes the
  default mode for your machine.
- **Verification pipeline hardens to one auto-persisting call.**
  `/agent/hallucination` now persists by default — no second
  `/verification/save` round-trip. Streaming verification emits a
  `persisted:{success}` SSE event so the frontend can trust the result.
- **Preservation harness — every release gates on real-stack tests.**
  35 integration tests boot the full docker-compose stack and assert
  end-to-end capability before a merge to main. The `preservation`
  CI job is blocking from this release forward.
- **Silent-error visibility.** Every broad-catch error path now flows
  through `log_swallowed_error()` and surfaces at
  `/health.invariants.swallowed_errors_last_hour`. No more hidden
  degradation.
- **SDK v1 with drift-guarded OpenAPI surface.** Twelve endpoints under
  `/sdk/v1/` plus a committed `docs/openapi-sdk-v1.json` baseline; CI
  fails on accidental shape changes. See [`docs/SDK_GUIDE.md`](docs/SDK_GUIDE.md).
- **Plugin system — five plugin types.** `parser`, `agent`, `tool`,
  `connector`, `sync`. See [`docs/PLUGIN_DEVELOPMENT.md`](docs/PLUGIN_DEVELOPMENT.md).
- **Repo structure cleanup.** Re-export bridges retired
  (`src/mcp/services/` and `src/mcp/agents/` removed; `src/mcp/utils/`
  is implementation-only). `import-linter` enforces the layer
  contract — `core/` never imports `app/`.

### Reliability fixes from real-world testing

- Chat key reload: the setup wizard's key change now applies without
  a container restart (was being captured at module-import time).
- Env-file persistence: wizard writes are anchored to a dedicated
  `CERID_ENV_FILE` mountpoint (was writing to an orphan path inside
  the container).
- Default secrets in `.env.example`: `OPENROUTER_API_KEY` is now
  declared and `REDIS_PASSWORD` ships with a non-empty default so
  first `docker compose up` works.
- Fixed an event-loop poisoning bug in the external-verification
  client when called from sync ingestion code.

For architecture, contributor conventions, and the preservation
harness, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
[`docs/CONVENTIONS.md`](docs/CONVENTIONS.md), and
[`docs/PRESERVATION.md`](docs/PRESERVATION.md).

## v0.84.0 — Reliability Remediation (2026-04-17 → 2026-04-18)

Audit-driven reliability, data-wiring, UX, and LLM-integration fixes across 32 commits. Every P0/P1 concern from the 2026-04-17 live beta audit addressed. Full plan at [`tasks/2026-04-17-reliability-remediation-plan.md`](tasks/2026-04-17-reliability-remediation-plan.md).

### Correctness (Wave 0)
- **`QueryEnvelope` single-writer** — unified `/agent/query` response shape. External results are now always mirrored into `sources` and `source_breakdown.external`; the degraded-budget path no longer drops them.
- **VerificationReport edges + backfill** — writer now always creates `[:EXTRACTED_FROM]` and `[:VERIFIED]`; stores `source_urls` / `verification_methods` on the node for external-verified claims. One-shot `m0001` migration backfills pre-existing reports.
- **Frontend triple-fire killed** — one chat turn used to spawn 3 identical `/agent/query` POSTs. `useChatSend` now skips the redundant refetch when TanStack cache is warm.
- **`DegradedBanner`** — ungrounded answers surface an amber banner with the backend's `degraded_reason`.
- **External source attribution** — `source_breakdown.external` merges into the assistant's "Sources used" pane.

### Reliability (Wave 1)
- **Retrieval budget + CB tuning** — external data-source circuit breakers relaxed from `failure_threshold=1, recovery=120s` to `3 / 30s`. Router-level CRAG gate suppresses external fan-out when top KB relevance ≥ `RETRIEVAL_QUALITY_THRESHOLD`.
- **Cancellation-safe SSE** — `/chat/stream` polls `request.is_disconnected()` and catches `CancelledError` / `GeneratorExit` to close upstream OpenRouter sockets on client abort. O(chunks²) usage parse short-circuited.
- **Partitioned concurrency pools** — `KB` / `CHAT` / `HEALTH` replace the process-wide `_QUERY_SEMAPHORE(2)`. `/health` polling no longer serializes behind chat turns. Queue depth visible at `/observability/queue-depth`.
- **Frontend abort cleanup** — `useChat` aborts on unmount. `queryKBOrchestrated` accepts an `AbortSignal` and threads TanStack's `signal` through.
- **Graceful 429** — rate-limit middleware returns `429` with `Retry-After` header and JSON body `retry_after` instead of dropping connections under burst load.

### Trust (Wave 2)
- **Claim cache schema v2** — keyed on `(claim, model, tier, response_context)`. No more stale verdicts across model swaps or pronoun-resolved claim collisions.
- **Cited-URL verification** — claims with `source_urls` fetch the page and NLI-entail before considering web search. Fabricated citations no longer get confirmed from unrelated search hits.
- **Stream-abort claim finalizer** — pending claim cards flip to `uncertain` on verify-stream close; no more forever-spinning popovers.
- **Startup invariants in `/health`** — collection dim checks, `verification_report_orphans` count, NLI load status. `/health` returns 503 on critical invariant violation.
- **`X-Cache` header + `cached: true` body** — cache hits observable from the client.
- **Version SSOT** — `/`, `/health`, and FastAPI `app.version` all read from `pyproject.toml` via `core/utils/version.py`. `/api/v1/*` dual mount retired.
- **Smart-router scored classification** — replaces first-keyword-match with weighted signals. `cost_sensitivity` now plumbs from `/agent/query` through `route()` and `call_llm`. All registered model IDs carry the `openrouter/` prefix.

### Hygiene (Wave 3)
- Favicon / apple-touch-icon / viewport zoom-lock + conversation-search a11y labels + rapid-Enter race guard.
- Rate-limit now covers `/setup/*`, `/admin/*` GETs, `/observability/*` GETs.
- **Semantic-cache dim self-heal** — stored HNSW blobs carry a magic+dim header; mismatch on load deletes the blob and cold-starts.
- **Dropbox `EDEADLK` retry** — 3-attempt exponential backoff; structured final-failure warning surfaced to the GUI.
- **Shared `httpx.AsyncClient` for OpenRouter credits** — module-level client + lazy getter eliminates per-poll socket churn.
- **Cross-machine settings/conversations reconciliation** — `updatedAt`-based version vector; drift now resolves on the next load.
- **Graceful reranker fallback** — ONNX cross-encoder failure returns results in original order with `reranker_status: "onnx_failed_no_fallback"` instead of crashing.

### Bifrost retirement (audit C-4)
- Bifrost **fully retired** — no container, no helper module, no URL. `utils/bifrost.py`, `core/utils/bifrost.py`, `stacks/bifrost/` deleted.
- Three pipeline callers migrated to `core.utils.llm_client.call_llm` (`utils/metadata.py` topic extraction, `core/utils/contextual.py` chunk summaries, `core/agents/maintenance.py` health probe).
- `USE_BIFROST` / `CERID_USE_BIFROST` env vars removed. `BIFROST_TIMEOUT` kept as a legacy name for a generic LLM timeout.
- `bifrost-*` circuit-breaker names preserved as historical identifiers for call-site categories (rerank / claims / verify / synopsis / memory / compress / decompose).
- `call_llm` / `call_llm_raw` now raise `RuntimeError` when `OPENROUTER_API_KEY` is unset — no silent re-route.
- nginx `/api/bifrost/` proxy removed.

### Testing + CI
- **Smoke harness** (`src/mcp/tests/load/smoke.py` + `make smoke`) — 8 scenarios covering `/health` concurrency, response-shape invariants, cache hit-rate, 429 graceful behaviour, SSE cancel, HOL blocking, CB flap.
- **4 previously-skipped tests restored** (+5 active / -5 skipped): SSE generator `CancelledError` path, reranker graceful-fallback contract, 3 agent-query budget-fixture tests.
- **CRAG inner-gate regression test** scans `query_agent.py` source to prevent re-introduction of the duplicate gate.
- **+16 new backend tests** across VerificationReport persistence, dim-validation, envelope shape-invariants, SSE cancellation, concurrency pools, claim cache key, cited-URL verification, startup invariants, metrics middleware, rate limiting, settings-sync retry, reranker fallback.
- **CI hygiene** — lint / typecheck / tsc / lockfile blockers cleared. Pinned `ruff==0.15.4` + `pip-tools==7.5.3` to match CI exactly.

### Deferred
- **Task 18 — chat-messages virtualization.** First attempt broke 46 testing-library measurement-dependent tests under jsdom. Needs a `@tanstack/react-virtual` approach that doesn't interfere with `measureElement` in jsdom.

### Post-deploy actions
- Run `python -m scripts.run_migrations` (m0001) to backfill existing `VerificationReport` provenance.
- `make version-file` is now part of `scripts/start-cerid.sh --build` so `get_version()` returns the real version in the MCP image.
- Operators see a one-time `embedding_dim_mismatch` ERROR log per mismatched Chroma collection, pointing at `POST /admin/collections/repair`.
- Claim cache v2 cold-starts existing `verf:claim:*` entries — expect 10-20× latency on first verification pass until the cache rewarms.

## v0.83.0 — Verification Hardening + Memory Efficacy + Bug-Hunt Sprint (2026-04-10 → 2026-04-15)

### Verification Pipeline Hardening (2026-04-13)
- **Round-2 claim sweep** — timed-out claims re-verified in a second pass with full conversation context
- **Expert verification mode** — Grok 4 as dedicated verification model for high-stakes claims (`VERIFICATION_EXPERT_MODEL`)
- **Authoritative external verification** — LLM synthesizes from external data sources rather than parametric memory
- **Graph-guided verification** — Neo4j relationship structure used as evidence for fact-relationship checks
- **Fact-relationship verification** — temporal/entity/specificity alignment validation
- **Dynamic confidence scoring** — per-source tuning (Wikipedia title match boost, Wolfram non-answer detection, DuckDuckGo .gov boost)

### Memory Efficacy (2026-04-13)
- **Source-aware external query construction** — per-source `adapt_query()`/`is_relevant()` with intent-based routing across 7 data sources
- **CRAG retrieval quality gate** — supplements with external sources when top KB relevance < `RETRIEVAL_QUALITY_THRESHOLD` (0.4)
- **Verified-fact-to-memory promotion** — high-confidence verified claims auto-promote to empirical `:Memory` nodes with `VERIFIED_BY` provenance
- **Tiered memory authority boost** — 4-tier system (0.05-0.25) based on verification status and confidence
- **Refresh-on-read memory decay** — Ebbinghaus rehearsal pattern resets `decay_anchor` on retrieval
- **NLI consolidation guard** — prevents semantic drift during memory merges via entailment threshold

### Bug-Hunt Sprint (2026-04-15) — 15 bugs → 8 root causes
- **Embedding singleton** — fixed split instantiation causing dimension mismatch on fresh installs + startup dim-check + `/admin/collections/repair` endpoint
- **Agent activity stream** — `/agents/activity/*` alias router + SSE exponential backoff (500ms base, 30s max) + abort-on-unmount
- **Healthcheck rewrite** — shared `scripts/lib/healthcheck.sh` library with auth-aware Redis/Neo4j checks + Bifrost skip + zombie container cleanup
- **Onboarding polish** — `CERID_SYNC_DIR_HOST` rename (backward-compat fallback), removed `age` from public README prereqs, fixed CONTRIBUTING.md Node/router path drift
- **Verification wiring** — `MIN_VERIFIABLE_LENGTH` FE/BE alignment 200→25, `onSelectForVerification` prop threaded through to `VerificationBadge`
- **UX fixes** — tab title "Cerid Core"→"Cerid AI", KB counter unification (`Showing X of Y`), Knowledge Digest errors drill-through modal with `DigestErrorItem` type

### Dependency Upgrades
- langgraph 0.6 → 1.1 (major)
- neo4j driver 5.28 → 6.1 (major)
- TypeScript 5.9 → 6.0 (major)
- Vite 7 → 8, @vitejs/plugin-react 5 → 6 (major)
- jsdom 28 → 29, lucide-react v0.577 → v1.8
- React 19.2.5, @tanstack/react-query 5.99

### Testing & CI
- **+14 frontend tests** (705 → 719) — verification orchestrator, agent activity stream, KB counter, digest drill-through
- **+4 backend tests** — embedding singleton, startup dim-check, collections repair, agent console router
- Sync manifest hygiene — `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `__pycache__` excluded from public sync
- Dependabot: ignore ESLint majors until react-hooks plugin supports v10, revert chromadb/langgraph upper-bound widening

### Documentation Re-Baseline (2026-04-15)
- Comprehensive audit: all open issues validated against code (zero actual bugs remaining)
- Version aligned across pyproject.toml, package.json, CLAUDE.md, tasks/todo.md
- Test counts updated (2,413 Python / 719 frontend), tool counts corrected (26 = 21 core + 5 trading)
- CI coverage floor corrected in docs (20%, not 70%)
- Stale todo items archived (leapfrog merge completed April 5, all B-CRITICAL/B-HIGH resolved)

## v0.82.0 — Unified Implementation Plan + Phase C Architecture (2026-04-05 → 2026-04-10)

### Phase C: Core Extraction + NLI Architecture (2026-04-08 → 2026-04-10)
- **Core/App split** — portable orchestrator core (`core/`) separated from application layer (`app/`). Bridge modules in `agents/`, `utils/`, `services/` re-export for backward compat.
- **`*_internal.py` pattern** — 7 Python files + 1 TypeScript file hold internal-only code; an internal bootstrap module registers the corresponding private routers at startup.
- **NLI entailment service** — `core/utils/nli.py` (ONNX, <10ms) powers verification, Self-RAG, RAGAS, and RAG pipeline claim validation.
- **Sync manifest** — `.sync-manifest.yaml` declares internal-only files, mixed files (hook markers), and forbidden strings for automated repo sync via `scripts/sync-repos.py`.
- **Contract ABCs** — `core/contracts/` defines VectorStore, GraphStore, CacheStore, LLMClient interfaces.
- **Concrete stores** — `app/stores/` implements ChromaVectorStore, Neo4jGraphStore, RedisCacheStore.
- **Source authority** — chat transcripts discounted 0.35x, memories retain full relevance.

### Post-Phase: Dependency Cleanup + Remaining Items
- **Dependency cleanup** — removed 8 unused deps (stripe/public, faster-whisper, requests, structlog/public, pytesseract, Pillow, bcrypt, PyJWT). Docker image 4.09→3.18 GB. Dependabot 33→2 vulns.
- **packages/desktop/** removed from public repo (kept in internal)
- **B31: Conversation grouping** — feedback from same conversation_id appends to existing KB artifact
- **B33: Feedback buttons** — ThumbsUp/ThumbsDown on assistant messages (POST /artifacts/{id}/feedback)
- **B35: Model compliance note** — footer in model selector about non-US model availability
- **B36: File picker** — browse button on archive path using File System Access API
- **Memory system fix** — get_collection → get_or_create_collection (fixes 500 on fresh installs)
- **Configurable model preload** — `CERID_PRELOAD_MODELS=false` Dockerfile ARG for smaller images
- **Startup prerequisites** — python3, curl, port availability, Docker memory checks
- **CI fixes** — test mock targets (requests→httpx), import sorting (I001), BLE001 suppressions

### Phase 1: Tiered Inference Detection
- **InferenceConfig singleton** — auto-detects platform (macOS ARM/Intel, Linux, Windows), GPU (Metal/CUDA/ROCm/DirectML), Ollama, and FastEmbed sidecar at startup
- **Dynamic ONNX providers** — embeddings.py and reranker.py use detected GPU providers instead of hardcoded CPU
- **Health endpoint** — `/health` now includes `inference` field with provider, tier, GPU, latency
- **Performance baseline** — documented in `docs/archive/2026-Q2/PERF_BASELINE_2026-04-05.md`

### Phase 2: FastEmbed Sidecar + UX Polish
- **Sidecar server** — `scripts/cerid-sidecar.py` wraps ONNX embed/rerank with native GPU acceleration
- **Sidecar installer** — `scripts/install-sidecar.sh` auto-detects platform and GPU for correct onnxruntime variant
- **Sidecar HTTP client** — `utils/inference_sidecar_client.py` with circuit breaker and latency tracking
- **B18: Sub-menu formatting** — consistent padding (p-2), font-weight, separator spacing across all toolbar popovers
- **B23: Recent imports scroll** — collapsible list, 4 default visible, "Show N more" expandable
- **B26: Health dashboard** — grouped by Infrastructure / AI Pipeline / Optional with section headers and auto-refresh
- **B30: External search debugging** — structured logging in `DataSourceRegistry.query_all()`
- **HNSW tuning** — ChromaDB M=12, EF_CONSTRUCTION=400 for better recall on new collections
- **Reranker warmup gating** — skipped when RERANK_MODE=none (~1s faster startup)
- **Ollama pool** — keep-alive connections increased 5→8

### Phase 3: GUI Integration + Recheck Loop
- **Inference tier in Settings** — green/blue/yellow badge showing optimal/good/degraded with provider name
- **Periodic re-check** — background loop every 300s detects Ollama start/stop, emits SSE event
- **Ollama wizard UX** — CPU-only warning, platform-specific install commands (brew/curl), copy buttons

### Phase 4: Ollama LLM Routing + B-LOW Items
- **ai_categorize() routing** — routes through `call_internal_llm()` when INTERNAL_LLM_PROVIDER=ollama
- **contextualize_chunks() routing** — same internal LLM routing for free local inference
- **B32: Synopsis regeneration** — `POST /artifacts/regenerate-all-synopses` with background processing
- **B33: Feedback loop design** — `docs/FEEDBACK_LOOP_DESIGN.md` (opt-in per conversation, quality gates)
- **B41: KB title editing** — already implemented (inline-editable with double-click + PATCH)

### Phase 5: Wiring Checks + Final Audit
- All 8 subsystem wiring checks passed (setup, chat, KB, external API, settings, health, memory, analytics)
- Regulated-deployment compliance verified (no Chinese-origin AI references)
- Documentation updated (CLAUDE.md, CHANGELOG.md)

### New Files
- `src/mcp/utils/inference_config.py` — tiered inference detection
- `src/mcp/utils/inference_sidecar_client.py` — sidecar HTTP client
- `scripts/cerid-sidecar.py` — FastEmbed sidecar server
- `scripts/install-sidecar.sh` — platform-aware installer
- `docs/archive/2026-Q2/PERF_BASELINE_2026-04-05.md` — performance baseline
- `docs/FEEDBACK_LOOP_DESIGN.md` — feedback loop design doc

## v0.81 — Beta Test Implementation (2026-04-04)

### Phase 1 (P0 — Critical Path)
- **PDF Drag-Drop & Ingestion** — Fix macOS file handler interception, add ChromaDB write-flush check, add `skip_quality` for faster wizard ingestion
- **Provider Detection** — Strip env var quotes, add unified `detect_provider_status()`, structured validation errors
- **Dev Tier Switch** — Hidden in production builds
- **Quality Scoring v2** — 6-dimension domain-adaptive scoring (richness, metadata, freshness, authority, utility, coherence), star/evergreen support
- **Preview Fix** — Handle external artifacts and malformed `chunk_ids` gracefully
- **Wizard Cleanup** — Remove Domains card, rename step to "Storage & Archive"

### Phase 2 (P1 — Usability & Polish)
- **Wizard Overhaul** — Optional Features step (Ollama + data sources), Bifrost hidden from health, health tooltips and fix actions
- **Custom LLM** — Custom OpenAI-compatible provider input, credits link, usage explainer
- **Chat UX** — Plain-language tooltips on all toolbar controls, privacy color escalation (green→red), verification cost explainer
- **KB Improvements** — MessageSquarePlus icon, chunk tooltip, star/evergreen buttons
- **Settings Polish** — Chunk size tooltip, cursor-default on Row, section state version bump

### Phase 3 (P2 — Backlog)
- **External Enrichment** — Enrich button on chat messages (Globe icon)
- **Console Consistency** — Read-only RAG mode display, pulse animation on unread badge
- **Custom API Wizard** — CustomApiSource backend (3 auth modes), CustomApiDialog frontend

### New Files
- `src/web/src/components/setup/optional-features-step.tsx`
- `src/web/src/components/setup/custom-provider-input.tsx`
- `src/web/src/components/kb/custom-api-dialog.tsx`
- `src/mcp/utils/data_sources/custom.py`

## [0.81] - 2026-04-03

### Features
- **Eval router wired up** — `POST /api/eval/run` and `GET /api/eval/benchmarks` now registered in main.py (self-gated by `CERID_EVAL_ENABLED`) (`f5bfc28`)
- **Typed Redis wrapper** — `utils/typed_redis.py` provides properly narrowed return types for sync `redis.Redis`, eliminating 57 mypy errors in one place (`4400bdf`)
- **Response model annotations** — 77 endpoints across 15 routers now have `response_model=` for proper OpenAPI schema generation. 13 new Pydantic model files under `models/` (`05b84ec`, `e3a3988`)
- **Code AST parser activated** — `parsers/code_ast.py` `@register_parser` decorators now fire via `__init__.py` import (`1cdc94d`)
- **Setup wizard** — 8-step onboarding with provider routing intelligence, degradation awareness, and health dashboard (`07a64a6`, `c09d2f6`, `b3fc202`)

### Bug Fixes
- **custom_agents pagination** — `total` field now returns actual DB count via `count_agents()` Cypher, not page size (`7aa7059`)
- **custom_agents query delegation** — passes `model_override`, `top_k`, returns `agent_config` with system_prompt/temperature/rag_mode/tools (`7aa7059`)
- **Duplicate endpoint removal** — removed `POST /chat/compress` from `chat.py` (duplicate with incompatible response key) and `GET /plugins` from `health.py` (shadowed by `plugins.py`) (`ef8489c`)
- **Frontend API bugs** — `fetchOpenRouterCredits` fixed to call `/providers/credits` (was 404), `toggleAutomation` fixed to use `/enable`/`disable` endpoints (was 404) (`ef8489c`)
- **error_handler.py** — bare `except: pass` replaced with debug logging for circuit breaker failures (`1cdc94d`)
- **test_ingestion.py** — narrowed bare `except Exception: pass` to specific expected exceptions (`1cdc94d`)
- **Trading mock paths** — 5 stale mock paths in `test_router_sdk.py` updated from `routers.sdk` to `routers.agents` (`77669a0`)
- **TOC test** — updated for `queueMicrotask`-based heading scan (`b36f490`)
- **Docker deployment** — resolved crashes when running without Bifrost (`02e979d`)

### Code Quality
- **ESLint warnings** — resolved all 28 warnings across 24 frontend files: 12 set-state-in-effect, 7 only-export-components, 5 exhaustive-deps, plus purity/ref/directive fixes (`2229d7e`)
- **Mypy errors** — 59 → 2 (only unrelated `multimodal.py` stubs remain) via `TypedRedis` wrapper (`4400bdf`)
- **Ruff lint** — 0 errors across 199+ Python files (maintained)
- **Dead code removed** — `utils/a2a_client.py`, `utils/agent_activity.py`, `utils/content_filter.py`, `tokenize_lower()` from `text.py` (`ad1ff81`)

### Documentation
- **CLAUDE.md** — CI jobs 8→6, coverage 70%→60%, test counts updated, agent list completed (`06b950a`)
- **API_Reference.md** — removed 10 phantom endpoints (trading proxy, boardroom SDK), added 18 real endpoints (custom agents, plugin registry, system monitor, webhooks), marked billing as internal (`56515ef`)

### Infrastructure
- **CI fixes** — multiple rounds of lint, typecheck, and test stabilization after setup wizard merge (`fa9b9df`, `9d354dd`, `9ff9ea0`, `e496922`, `98dc16e`, `bb0a981`)

### New Files
- `src/mcp/utils/typed_redis.py` — typed Redis facade (35 methods)
- `src/mcp/models/agents_response.py` — 14 response models for agent endpoints
- `src/mcp/models/artifacts.py` — 7 response models for artifact endpoints
- `src/mcp/models/data_sources.py` — 11 response models for data source endpoints
- `src/mcp/models/digest.py` — 4 response models
- `src/mcp/models/ingestion.py` — 6 response models
- `src/mcp/models/memories.py` — 4 response models
- `src/mcp/models/query.py` — 2 response models
- `src/mcp/models/settings.py` — 3 response models
- `src/mcp/models/taxonomy.py` — 5 response models
- `src/mcp/models/upload.py` — 4 response models
- `src/mcp/models/user_state.py` — 3 response models
- `src/mcp/models/watched_folders.py` — 3 response models
- `src/mcp/models/webhooks.py` — 5 response models
