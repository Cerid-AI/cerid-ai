# Phase 51: Hardening, RAG Evolution & Architecture Refinement

> **Created:** 2026-03-28
> **Status:** Planning
> **Scope:** Multi-sprint improvement plan across 8 vectors
> **Pre-requisites:** All phases through 50 complete, Monte Carlo evaluation baseline established

## Executive Summary

Comprehensive hardening plan driven by three data sources:
1. **Monte Carlo evaluation** (33 tests, 54 scenarios) revealing classification accuracy at 84%, complex claim recall at 14%, and confidence calibration gaps
2. **Code quality audit** (7.2/10 score) identifying 135 bare `except Exception` blocks, 18 god files >500 lines, and configuration sprawl
3. **RAG competitive research** against 15 frameworks and 25+ papers, identifying Graph RAG, parent-child retrieval, and metamorphic verification as highest-impact gaps

## Architecture Principles

1. **Core stays lean** — Production install pulls only what's needed to run
2. **Dev/eval tooling is opt-in** — Monte Carlo, benchmarks, beta tests live in separate packages
3. **Graceful degradation everywhere** — Every external call has a fallback path
4. **Tier separation is enforced** — Community/Pro/Enterprise boundaries are clear and tested
5. **Local-first where possible** — Ollama handles pipeline tasks; cloud models for reasoning

---

## Track 1: Error Handling & Resilience Hardening

### Sprint 1A: Exception Hierarchy (3-4 hours)

**Problem:** 135 bare `except Exception` blocks across backend. 60+ with no logging. 15+ with silent `pass`. Production failures go unreported.

**Action:**
- [ ] Create `src/mcp/errors.py` — exception hierarchy:
  ```
  CeridError (base)
  ├── IngestionError (parse failures, dedup, chunking)
  ├── RetrievalError (ChromaDB, Neo4j, embedding)
  ├── VerificationError (claim extraction, verdict parsing)
  ├── RoutingError (model selection, Bifrost, Ollama)
  ├── SyncError (import, export, manifest)
  ├── ProviderError (LLM provider issues)
  │   ├── CreditExhaustedError (402 — already exists, relocate)
  │   └── RateLimitError (429)
  └── ConfigError (missing env vars, invalid settings)
  ```
- [ ] Sweep all 135 `except Exception` blocks — replace with specific types, add structured logging
- [ ] Priority targets (silent `pass` blocks):
  - `routers/automations.py:181-182` — silent scheduler failure
  - `routers/kb_admin.py:401-402` — silent artifact deletion failure
  - `routers/providers.py:106-107, 162-163` — silent provider init failure
  - `sync/user_state.py:33,43` — silent state load failure
  - `main.py:312-313` — silent startup failure
- [ ] Add ruff rule to CI: ban bare `except Exception:` without logging (custom ruff plugin or `B001`/`BLE001`)
- [ ] Standardize logging levels: `DEBUG` for non-blocking fallbacks, `WARNING` for degraded paths, `ERROR` for circuit-breaker-worthy failures

### Sprint 1B: Multi-Tier Graceful Degradation (2-3 hours)

**Problem:** No systematic fallback chain. Individual circuit breakers exist but no coordinated degradation strategy.

**Action:**
- [ ] Implement 4-layer query fallback in `agents/query_agent.py`:
  1. **Full RAG** — all features (reranking, graph enrichment, multi-query, Self-RAG)
  2. **Lite RAG** — fewer documents, skip reranking, skip decomposition
  3. **Direct LLM** — no retrieved context, parametric knowledge only
  4. **Cached response** — semantic cache hit from prior queries
- [ ] Track `fallback_tier` in Redis metrics (observability dashboard)
- [ ] Add `/health` degradation status: `healthy` / `degraded` / `minimal` / `cached-only`
- [ ] Per-stage Ollama fallback: if Ollama circuit opens, fall back to Bifrost for that specific stage (not all-or-nothing)

---

## Track 2: Verification Pipeline Improvements

### Sprint 2A: Close Monte Carlo Gaps (3-4 hours)

**Problem:** Monte Carlo evaluation revealed:
- Complex claim recall: 14% (patterns only catch causality/comparison, not arithmetic)
- Ignorance detection: 60% (narrow patterns miss capability-limit phrases)
- Verdict parsing: text-wrapped JSON confidence extraction fails
- Numeric alignment: requires 2+ checks for penalty (single mismatch ignored)

**Action:**
- [ ] Expand `COMPLEX_CLAIM_PATTERNS` in `patterns.py`:
  - Add arithmetic/computation patterns: `\b\d+\s*[×÷+\-*/]\s*\d+`, `factorial`, `probability`, `sum of`
  - Add logical reasoning: `\b(?:every|all|no|none|some)\b.*\b(?:therefore|must|cannot)\b`
  - Target: raise recall from 14% → 50%+ on math/logical claims
- [ ] Expand `_is_ignorance_admission` patterns:
  - Add capability-limit phrases: `"I'm unable to"`, `"I cannot access"`, `"I don't have the ability"`
  - Add real-time data caveats: `"I don't have real-time"`, `"I cannot browse"`
  - Target: raise accuracy from 60% → 80%+
- [ ] Fix `_parse_verification_verdict` text-wrapped JSON handling:
  - Extract JSON from surrounding text before parsing (regex: `\{[^{}]+\}`)
  - Preserve confidence from extracted JSON block
- [ ] Relax `_check_numeric_alignment` penalty threshold:
  - Apply penalty when `total_checks >= 1 AND match_ratio == 0.0` (currently requires 2+)
  - Add year proximity check: `|year_claim - year_source| > 2` → penalty
- [ ] Re-run Monte Carlo after fixes, update baseline metrics in test docstrings

### Sprint 2B: Metamorphic Verification (4-5 hours)

**Problem:** Current verification relies on cross-model agreement. No perturbation-based signal. MetaRAG paper (Sep 2025) shows metamorphic testing catches hallucinations that cross-model misses.

**Action:**
- [ ] Add `agents/hallucination/metamorphic.py`:
  - Decompose answer into atomic factoids (reuse `_extract_claims_heuristic`)
  - Generate controlled mutations: synonym substitution (should preserve entailment), antonym substitution (should break entailment)
  - Score: penalty when synonym variant is NOT entailed by context, or antonym IS entailed
  - Output: metamorphic_score (0-1), per-claim perturbation results
- [ ] Integrate as optional verification stage (gated by `CERID_TIER >= "pro"`)
- [ ] Use Ollama for mutation generation (small model task — perfect for local LLM)
- [ ] Add Monte Carlo tests for metamorphic verification accuracy

### Sprint 2C: Local Verification via Ollama (2-3 hours)

**Problem:** All verification calls use cloud models (OpenRouter). FaithLens paper shows 8B models match GPT-4.1 on faithfulness detection. Ollama integration is all-or-nothing with no per-stage routing.

**Action:**
- [ ] Add `VERIFICATION_USE_LOCAL` config (default: false)
- [ ] When enabled + Ollama available: route heuristic extraction and simple factual verification through Ollama
- [ ] Keep complex/recency/evasion verification on cloud models (quality threshold)
- [ ] Per-stage circuit breaker: `"ollama-extraction"`, `"ollama-verification"` (separate from global `"ollama"`)
- [ ] Fallback: if local model fails, seamlessly escalate to cloud

---

## Track 3: RAG Pipeline Evolution

### Sprint 3A: Retrieval-Level Caching (2-3 hours)

**Problem:** Semantic query cache exists for final responses but not for intermediate retrieval results. Production systems report 60-80% hit rates on retrieval cache. ARC paper shows 80% latency reduction.

**Action:**
- [ ] Add `utils/retrieval_cache.py`:
  - Key: quantized query embedding (int8, same as semantic cache)
  - Value: serialized chunk set (IDs + scores + metadata)
  - TTL: 30 minutes (configurable via `RETRIEVAL_CACHE_TTL`)
  - Storage: Redis (same instance, prefix `cerid:retrieval:`)
- [ ] Integrate in `agents/query_agent.py` before ChromaDB query
- [ ] Cache invalidation: bust on KB ingest/delete events
- [ ] Metrics: cache hit rate tracked in observability dashboard

### Sprint 3B: Parent-Child Document Retrieval (3-4 hours)

**Problem:** Current chunking uses single granularity. Research shows parent-child retrieval gives "surgical precision of small-chunk search with rich context of large-document generation."

**Action:**
- [ ] Modify chunker to produce two-tier chunks:
  - **Child chunks** (100-300 tokens): fine-grained, used for search precision
  - **Parent chunks** (500-1500 tokens): coarse, used for generation context
  - Store parent_id in child chunk metadata
- [ ] ChromaDB: add `parent_chunk_id` metadata field
- [ ] Retrieval: search child chunks, retrieve parent chunks for context assembly
- [ ] Migration: add `pkb_rebuild_parentchild` admin endpoint for re-chunking existing KB
- [ ] Feature flag: `ENABLE_PARENT_CHILD_RETRIEVAL` (default: false initially)

### Sprint 3C: HyDE Fallback (2-3 hours)

**Problem:** When initial retrieval confidence is low, the system has no recovery mechanism beyond query decomposition. HyDE generates a hypothetical answer to bridge query-document vocabulary gap.

**Action:**
- [ ] Add HyDE as a retrieval fallback in `agents/query_agent.py`:
  - Trigger: when top retrieval score < threshold (e.g., 0.4) AND adaptive retrieval gate says "retrieve"
  - Generate hypothetical document via LLM (Ollama preferred — small model task)
  - Re-embed hypothetical document, search KB with that embedding
  - Merge results with original retrieval via RRF
- [ ] Ollama-first: use local model for hypothesis generation (fast, free)
- [ ] Track `hyde_activated` in metrics

### Sprint 3D: Graph RAG Layer — LightRAG (5-6 hours, future)

**Problem:** Neo4j stores entity relationships but no automated entity extraction or community detection. LightRAG achieves 70-90% of Microsoft GraphRAG quality at 1/100th cost.

**Action:**
- [ ] Evaluate LightRAG integration feasibility:
  - Entity extraction from ingested documents (Ollama for cost)
  - Store in existing Neo4j (extend schema with `:Entity` nodes and `:RELATES_TO` edges)
  - Community detection via Neo4j GDS (Leiden algorithm)
  - Community summaries for global query context
- [ ] Prototype with 100-document subset before full integration
- [ ] Feature flag: `ENABLE_GRAPH_RAG` (Pro tier)

---

## Track 4: Repository Architecture & Packaging

### Sprint 4A: Separate Dev/Eval from Core Install (3-4 hours)

**Problem:** Monte Carlo evaluation harness, beta tests, eval benchmarks, and dev fixtures ship with the main install. Users pulling the repo get test infrastructure they don't need.

**Action:**
- [ ] Create `requirements-eval.txt` (separate from requirements-dev.txt):
  ```
  ragas>=0.2
  sentence-transformers  # for offline eval
  scipy  # for statistical analysis
  ```
- [ ] Move evaluation-only test files to `tests/eval/` (not `tests/`):
  - `test_verification_monte_carlo.py` → `tests/eval/`
  - `tests/beta/eval/` already exists — consolidate
- [ ] Move `tests/beta/` entirely into `tests/integration/` or `tests/e2e/`
- [ ] CI: pytest markers for test tiers:
  - `@pytest.mark.unit` — runs always (fast, no external deps)
  - `@pytest.mark.integration` — needs Docker services
  - `@pytest.mark.eval` — Monte Carlo, benchmarks (slow, optional)
- [ ] `Makefile` targets: `make test` (unit only), `make test-all`, `make test-eval`
- [ ] Update `.claudeignore` to skip eval data files
- [ ] Docker image: exclude `tests/` entirely from production image (already done via `.dockerignore` — verify)

### Sprint 4B: Tier Enforcement Hardening (2-3 hours)

**Problem:** Tier gating exists but enforcement is scattered. 12+ inline `if config.FEATURE_TIER == "pro"` checks. `@require_feature()` decorator exists but underutilized.

**Action:**
- [ ] Audit all tier-gated code paths — replace inline checks with `@require_feature()` decorator
- [ ] Add tier test matrix in CI: run tests with `CERID_TIER=community` and verify Pro features return 403
- [ ] Consolidate `config/features.py` + `utils/features.py` into single `config/features.py`
- [ ] Document tier boundaries in `docs/TIER_MATRIX.md`:
  | Feature | Community | Pro | Enterprise |
  |---------|-----------|-----|------------|
  | Core RAG | ✓ | ✓ | ✓ |
  | Verification | ✓ | ✓ | ✓ |
  | Metamorphic verification | — | ✓ | ✓ |
  | Graph RAG | — | ✓ | ✓ |
  | Multi-modal (OCR/audio/vision) | — | ✓ | ✓ |
  | Multi-user auth | — | — | ✓ |
  | SLA & support | — | — | ✓ |

### Sprint 4C: Health Check & Operational Readiness (1-2 hours)

**Problem:** `/health` endpoint exists but doesn't report degradation status. No readiness vs liveness distinction.

**Action:**
- [ ] Split `/health` into:
  - `/health/live` — is the process running? (always 200 unless crashed)
  - `/health/ready` — are all dependencies reachable? (Neo4j, ChromaDB, Redis, Ollama if enabled)
  - `/health/status` — detailed degradation report with per-service status
- [ ] Add circuit breaker state to health output
- [ ] Docker healthchecks use `/health/live` (fast, no dependency check)
- [ ] Orchestrator readiness gates use `/health/ready`

---

## Track 5: Ollama Extensibility

### Sprint 5A: Per-Stage Ollama Routing (3-4 hours)

**Problem:** Ollama integration is all-or-nothing. `INTERNAL_LLM_PROVIDER` switches everything or nothing. No per-stage routing. No fallback when Ollama is down for a specific task.

**Action:**
- [ ] Replace global `INTERNAL_LLM_PROVIDER` with per-stage config:
  ```python
  PIPELINE_PROVIDERS = {
      "claim_extraction": "ollama",      # cheap, fast
      "query_decomposition": "ollama",   # small model task
      "topic_extraction": "ollama",      # keyword extraction
      "memory_resolution": "ollama",     # pattern matching
      "verification_simple": "ollama",   # factual claims
      "verification_complex": "bifrost", # recency/evasion need web search
      "reranking": "ollama",             # cross-encoder local
      "chat_generation": "bifrost",      # user-facing quality matters
  }
  ```
- [ ] Each stage has independent circuit breaker (`ollama-{stage}`)
- [ ] Per-stage fallback: Ollama failure → Bifrost for that stage only
- [ ] Settings API: expose per-stage routing in `/settings` for GUI configuration
- [ ] Backward compat: `INTERNAL_LLM_PROVIDER=ollama` sets all stages to Ollama

### Sprint 5B: Ollama Model Management (2-3 hours)

**Action:**
- [ ] Auto-detect available Ollama models at startup
- [ ] Recommend models per pipeline stage:
  - Extraction/classification: `qwen2.5:1.5b` (default, 1GB)
  - Verification: `llama3.3:8b` (better reasoning, 4.7GB)
  - Embedding: `nomic-embed-text` (zero API cost)
- [ ] Model pull progress in GUI (already exists via `/ollama/pull` — wire to settings)
- [ ] Memory/GPU detection: warn if selected model exceeds available resources

---

## Track 6: Code Quality Remediation

### Sprint 6A: God File Decomposition (4-5 hours)

**Problem:** 18 files >500 lines in backend, 4 in frontend. Largest: `test_hallucination.py` (2773), `verification.py` (1590), `query_agent.py` (1134).

**Priority splits:**
- [ ] `verification.py` (1590 lines) → extract:
  - `verdict_parsing.py` — `_parse_verification_verdict`, `_interpret_recency_verdict`, `_invert_*_verdict`
  - `confidence.py` — `_compute_adjusted_confidence`, `_check_numeric_alignment`, `_build_verification_details`
  - `memory_verify.py` — `_query_memories`, memory-aware verification
  - Keep `verification.py` as orchestrator importing from sub-modules
- [ ] `query_agent.py` (1134 lines) → extract:
  - `decomposer.py` — query decomposition and sub-retrieval
  - `assembler.py` — context assembly, facet coverage, budget management
  - Keep `query_agent.py` as main entry point
- [ ] `api.ts` (1425 lines) → split by domain:
  - `api/kb.ts`, `api/chat.ts`, `api/settings.ts`, `api/verification.ts`
  - Re-export from `api/index.ts` for backward compat
- [ ] `test_hallucination.py` (2773 lines) → split by claim type:
  - `test_verification_factual.py`
  - `test_verification_recency.py`
  - `test_verification_evasion.py`
  - `test_verification_ignorance.py`
  - `test_verification_integration.py` (streaming, routing)

### Sprint 6B: Magic Numbers → Named Constants (1-2 hours)

**Action:**
- [ ] Create `config/constants.py`:
  ```python
  MAX_ARTIFACT_LIST = 10_000
  MAX_ARTIFACTS_PER_DOMAIN = 200
  MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024  # 50MB
  HEALTH_CACHE_TTL_SECONDS = 10.0
  A2A_TASK_TTL_SECONDS = 3600
  OLLAMA_READ_TIMEOUT = 120.0
  OLLAMA_CONNECT_TIMEOUT = 10.0
  MONTHLY_BUDGET_USD = 20.0
  OBSERVABILITY_RETENTION_SECONDS = 10_000
  ```
- [ ] Replace all hardcoded values with constant references
- [ ] Add ruff rule for magic number detection (or manual review)

### Sprint 6C: Logging Discipline (1-2 hours)

**Action:**
- [ ] Standardize log levels across all routers:
  - `DEBUG` — non-blocking fallback activated, cache miss, optional feature skipped
  - `INFO` — successful ingestion, query completed, verification done
  - `WARNING` — degraded path taken, circuit breaker tripped, retry needed
  - `ERROR` — unrecoverable failure, data corruption risk, security event
- [ ] Add structured logging fields: `{"event": "...", "claim_type": "...", "model": "...", "latency_ms": ...}`
- [ ] Ensure every `except` block logs at minimum `WARNING`

---

## Track 7: Evaluation & Continuous Quality

### Sprint 7A: RAGAS Integration (3-4 hours)

**Problem:** No continuous evaluation pipeline. Existing `eval/` harness is disconnected from CI. RAGAS is the 2026 standard for RAG evaluation.

**Action:**
- [ ] Add RAGAS metrics to `tests/eval/`:
  - Faithfulness (answer supported by context?)
  - Context precision (are retrieved docs relevant?)
  - Context recall (are all relevant docs retrieved?)
  - Answer relevancy (does answer address the query?)
- [ ] Create golden dataset: 50 hand-labeled query→document→answer triples from real KB
- [ ] CI gate: weekly scheduled eval run, alert if metrics drop >5% from baseline
- [ ] Target baselines: Faithfulness >0.8, Context Precision >0.75, NDCG@5 >0.70

### Sprint 7B: Monte Carlo Expansion (2-3 hours)

**Action:**
- [ ] Expand corpus from 54 → 150+ scenarios:
  - Add 20 citation verification scenarios
  - Add 30 multi-hop reasoning claims
  - Add 20 numeric precision claims (currency, percentages, dates)
  - Add 15 domain-specific claims (finance, code, science)
  - Add 10 adversarial claims (near-miss facts, plausible-but-wrong)
- [ ] Add bootstrap confidence intervals (95% CI) for each metric
- [ ] Add parameter sensitivity analysis: how much does accuracy change with threshold tuning?
- [ ] Track Monte Carlo metrics over time (store results in `tests/eval/baselines/`)

---

## Track 8: Research-Informed Feature Roadmap

### Features to Add (prioritized by research)

| Priority | Feature | Source | Effort | Tier |
|----------|---------|--------|--------|------|
| P0 | Retrieval-level caching | ARC paper, production consensus | 2-3h | Community |
| P0 | Multi-tier graceful degradation | Production patterns | 2-3h | Community |
| P0 | Exception hierarchy & error discipline | Code audit | 3-4h | Community |
| P1 | Parent-child document retrieval | RAGFlow, LlamaIndex, production consensus | 3-4h | Community |
| P1 | HyDE fallback retrieval | Academic consensus, low effort | 2-3h | Community |
| P1 | Per-stage Ollama routing | Architecture gap | 3-4h | Community |
| P1 | RAGAS continuous evaluation | Industry standard | 3-4h | Dev tooling |
| P2 | Metamorphic verification | MetaRAG paper (Sep 2025) | 4-5h | Pro |
| P2 | Graph RAG (LightRAG) | 31K stars, EMNLP 2025 | 5-6h | Pro |
| P2 | Local verification (FaithLens) | Dec 2025 paper | 2-3h | Pro |
| P3 | ColPali multi-modal retrieval | ICLR 2025 | 4-5h | Pro |
| P3 | Agentic RAG (A-RAG) | Feb 2026 paper | 6-8h | Pro |
| P3 | Multi-query expansion | Production consensus | 2-3h | Community |

### Features NOT to Add (avoid over-engineering)

- **Microsoft GraphRAG** — LightRAG achieves 70-90% quality at 1/100th cost. Only upgrade if benchmarks demand it.
- **Speculative RAG** — latency optimization via parallel drafts. Current latency is acceptable; adds significant complexity.
- **MARCH multi-agent verification** — too new (Mar 2026), unstable API. Wait 6 months.
- **Custom embedding models** — Arctic Embed M v1.5 is performing well. Don't switch unless NDCG drops.

---

## Execution Order

```
Week 1: Track 1 (Error handling) + Track 4A (Dev/eval separation)
         ├── Sprint 1A: Exception hierarchy
         ├── Sprint 4A: Separate dev/eval from core
         └── Sprint 6B: Magic numbers → constants

Week 2: Track 2A (Monte Carlo gaps) + Track 5A (Per-stage Ollama)
         ├── Sprint 2A: Close verification gaps
         ├── Sprint 5A: Per-stage Ollama routing
         └── Sprint 4C: Health check improvements

Week 3: Track 3 (RAG evolution) + Track 6A (God files)
         ├── Sprint 3A: Retrieval-level caching
         ├── Sprint 3C: HyDE fallback
         └── Sprint 6A: God file decomposition (start)

Week 4: Track 7 (Evaluation) + Track 3B (Parent-child)
         ├── Sprint 7A: RAGAS integration
         ├── Sprint 3B: Parent-child retrieval
         └── Sprint 6A: God file decomposition (finish)

Week 5+: Track 2B-C (Advanced verification) + Track 3D (Graph RAG)
          ├── Sprint 2B: Metamorphic verification (Pro tier)
          ├── Sprint 2C: Local verification via Ollama
          └── Sprint 3D: LightRAG evaluation + prototype
```

## Success Criteria

| Metric | Current | Target | Method |
|--------|---------|--------|--------|
| Monte Carlo classification accuracy | 84% | 90%+ | Sprint 2A |
| Complex claim recall | 14% | 50%+ | Sprint 2A |
| Bare `except Exception` blocks | 135 | 0 | Sprint 1A |
| God files (>500 lines) | 22 | <10 | Sprint 6A |
| Query fallback coverage | 1 tier | 4 tiers | Sprint 1B |
| Ollama per-stage routing | All-or-nothing | Per-stage | Sprint 5A |
| Retrieval cache hit rate | N/A | 60%+ | Sprint 3A |
| RAGAS Faithfulness | Unknown | >0.80 | Sprint 7A |
| Test tier separation | Mixed | Unit/Integration/Eval | Sprint 4A |
| CI eval gate | None | Weekly automated | Sprint 7A |

## Key Files

- `src/mcp/errors.py` — new exception hierarchy (Track 1)
- `src/mcp/agents/hallucination/patterns.py` — verification pattern expansion (Track 2)
- `src/mcp/agents/hallucination/metamorphic.py` — new metamorphic testing (Track 2)
- `src/mcp/utils/retrieval_cache.py` — new retrieval cache layer (Track 3)
- `src/mcp/config/constants.py` — centralized magic numbers (Track 6)
- `tests/eval/` — consolidated evaluation harness (Track 4)
- `docs/TIER_MATRIX.md` — tier boundary documentation (Track 4)
- `requirements-eval.txt` — evaluation-only dependencies (Track 4)
