# Phase 51: Hardening, RAG Evolution & Commercial-Grade Architecture

> **Created:** 2026-03-28
> **Refined:** 2026-03-28 (v2 — synthesized from 3 deep research audits)
> **Status:** Planning
> **Scope:** 6-week improvement plan, 8 tracks, 22 sprints
> **Pre-requisites:** All phases through 50 complete, Monte Carlo evaluation baseline established
> **Codebase snapshot:** 35,769 lines Python (excl. venv/tests), 1,425 lines api.ts, 352 bare `except Exception` across 88 files

## Executive Summary

Phase 51 transforms Cerid AI from a feature-complete prototype into a commercial-grade, community-ready codebase. Driven by three deep audits:

1. **Architecture Audit** — 29 routers, 10 agents, 7 middleware layers mapped. 18 files >500 lines. 352 bare `except Exception` blocks across 88 files. Feature flag enforcement scattered (12+ inline bypass). Ollama routing is all-or-nothing.
2. **Verification Pipeline Audit** — 3,368-line hallucination detection system with 6 modules. Monte Carlo baseline: 84% classification, 14% complex recall, 100% current-event recall. Confidence calibration has 4-factor system with known gaps.
3. **Production RAG Research** — 15 frameworks compared, 25+ papers analyzed. Parent-child retrieval, HyDE, retrieval caching, Graph RAG, RAGAS evaluation identified as highest-impact gaps.

### Anti-Vibe-Coding Principles

Every sprint in this plan is designed to eliminate patterns common in AI-assisted codebases:

| Vibe-Code Smell | Our Defense | Where Enforced |
|-----------------|------------|----------------|
| Bare `except Exception: pass` | Exception hierarchy + ruff BLE001 rule | Sprint 1A, CI gate |
| Magic numbers scattered in logic | `config/constants.py` + ruff rule | Sprint 6B |
| God files (>500 lines) | Decomposition targets + file size lint | Sprint 6A |
| Copy-paste error handling | Centralized `@handle_errors` decorator | Sprint 1A |
| Untested feature flags | Tier test matrix in CI | Sprint 4B |
| No degradation strategy | 4-tier fallback chain | Sprint 1B |
| Stale comments / dead code | `ruff` ERA rules + review sweep | Sprint 6C |
| Inconsistent logging levels | Structured logging standard | Sprint 6C |
| Tests that test nothing | Monte Carlo with statistical baselines | Sprint 7B |
| No evaluation pipeline | RAGAS + CI gate | Sprint 7A |

### Context-Limit & Compaction Awareness

Large AI-assisted codebases suffer from **context window fragmentation** — when the AI loses track of architectural decisions, naming conventions, and inter-module contracts after compaction events. This plan builds structural defenses:

| Problem | Defense | Implementation |
|---------|---------|----------------|
| AI forgets module contracts after compaction | Each module gets a `MODULE.md` with interface spec | Sprint 4D |
| AI re-invents patterns that already exist | `config/constants.py` and `errors.py` are canonical; CLAUDE.md references them | Sprint 1A, 6B |
| AI creates inconsistent error handling | `@handle_errors` decorator is the ONE pattern — no alternatives | Sprint 1A |
| AI loses track of file decomposition | Import re-exports preserve all paths; `__init__.py` is the map | Sprint 6A |
| AI duplicates utility functions | `utils/` has clear single-purpose modules with docstrings | Sprint 6A |
| AI doesn't know which tier a feature belongs to | `@require_feature()` decorator is the ONLY gate — no inline checks | Sprint 4B |
| AI generates tests without baselines | `tests/eval/baselines/` stores golden metrics; tests assert against them | Sprint 7A |
| AI can't find the right config pattern | `config/settings.py` is the SINGLE source; env vars documented in `.env.example` | Sprint 5A |
| God files overwhelm context windows | Files >500 lines decomposed into focused modules | Sprint 6A |
| AI hallucinates function signatures | Type hints on all public APIs; mypy strict mode | Sprint 6D |

**Key rule:** Every module boundary must be discoverable from `CLAUDE.md` or module-level docstrings. When an AI agent loses context, it should be able to reconstruct the architecture from file-level documentation alone.

---

## Architecture Principles

1. **Core stays lean** — Production install pulls only runtime dependencies
2. **Dev/eval tooling is opt-in** — Monte Carlo, benchmarks, RAGAS live in `requirements-eval.txt`
3. **Graceful degradation everywhere** — Every external call has a fallback path
4. **Tier separation is enforced** — `@require_feature()` is the ONLY gate mechanism
5. **Local-first where possible** — Ollama handles pipeline tasks; cloud models for reasoning
6. **Module boundaries are documented** — Each module's public API is docstring-specified
7. **No silent failures** — Every `except` block either logs + degrades or raises a typed error
8. **Constants are centralized** — No magic numbers in business logic
9. **Files stay focused** — Target <400 lines per module; >500 triggers decomposition

---

## Track 1: Error Handling & Resilience Hardening

### Sprint 1A: Exception Hierarchy & Error Discipline (3-4 hours)

**Problem:** 352 bare `except Exception` blocks across 88 files. 60+ with no logging. 15+ with silent `pass`. Production failures go unreported. AI agents regenerate inconsistent error handling patterns after compaction.

**Implementation:**

```python
# src/mcp/errors.py — THE canonical error module
class CeridError(Exception):
    """Base exception for all Cerid errors. All handlers catch this."""
    def __init__(self, message: str, *, error_code: str, details: dict | None = None):
        super().__init__(message)
        self.error_code = error_code  # machine-parseable: "INGESTION_PARSE_FAILED"
        self.details = details or {}

class IngestionError(CeridError):     # parse failures, dedup, chunking
class RetrievalError(CeridError):     # ChromaDB, Neo4j, embedding failures
class VerificationError(CeridError):  # claim extraction, verdict parsing
class RoutingError(CeridError):       # model selection, Bifrost, Ollama
class SyncError(CeridError):          # import, export, manifest
class ProviderError(CeridError):      # LLM provider issues
    class CreditExhaustedError(ProviderError):  # 402 — relocate from existing
    class RateLimitError(ProviderError):         # 429
class ConfigError(CeridError):        # missing env vars, invalid settings
```

**Centralized error handler decorator:**
```python
# src/mcp/utils/error_handler.py
def handle_errors(*, fallback=None, log_level="error", breaker_name=None):
    """THE pattern for error handling. No other patterns allowed."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except CeridError:
                raise  # already typed, let FastAPI handler catch
            except Exception as exc:
                logger.log(log_level, f"{func.__name__} failed", exc_info=exc,
                           extra={"error_code": "UNHANDLED", "function": func.__name__})
                if breaker_name:
                    circuit_breaker(breaker_name).record_failure()
                if fallback is not None:
                    return fallback
                raise RoutingError(str(exc), error_code="UNHANDLED_ERROR")
        return wrapper
    return decorator
```

**Action items:**
- [ ] Create `src/mcp/errors.py` with hierarchy above
- [ ] Create `src/mcp/utils/error_handler.py` with `@handle_errors` decorator
- [ ] Add FastAPI exception handler in `main.py`: `CeridError` → structured JSON response with `error_code`
- [ ] Sweep priority targets (15 silent `pass` blocks):
  - `routers/automations.py:181-182` — silent scheduler failure
  - `routers/kb_admin.py:401-402` — silent artifact deletion failure
  - `routers/providers.py:106-107, 162-163` — silent provider init failure
  - `sync/user_state.py:33,43` — silent state load failure
  - `main.py:312-313` — silent startup failure
- [ ] Sweep remaining 337 `except Exception` blocks — replace with typed errors + `@handle_errors`
- [ ] Add ruff rule `BLE001` (blind exception) to CI — zero tolerance after this sprint
- [ ] Add Sentry integration: `CeridError.error_code` as Sentry fingerprint for grouping

**Verification gate:**
- [ ] `ruff check src/mcp/ --select BLE001` returns 0 violations
- [ ] All 15 priority `pass` blocks replaced with logging + typed errors
- [ ] Sentry receives test error with correct `error_code` fingerprint
- [ ] Existing test suite still passes (no regressions from error changes)

---

### Sprint 1B: Multi-Tier Graceful Degradation (2-3 hours)

**Problem:** No systematic fallback chain. Individual circuit breakers exist but no coordinated degradation strategy. When ChromaDB goes down, the entire query pipeline fails instead of degrading gracefully.

**Implementation:**

```python
# src/mcp/utils/degradation.py
class DegradationTier(Enum):
    FULL = "full"           # all features available
    LITE = "lite"           # reduced retrieval, skip reranking
    DIRECT = "direct"       # no retrieval, parametric LLM only
    CACHED = "cached"       # semantic cache hits only
    OFFLINE = "offline"     # static error responses only

class DegradationManager:
    """Tracks current system capability tier based on service health."""
    def current_tier(self) -> DegradationTier: ...
    def can_retrieve(self) -> bool: ...
    def can_verify(self) -> bool: ...
```

**Action items:**
- [ ] Create `src/mcp/utils/degradation.py` with tier system
- [ ] Implement 4-layer query fallback in `agents/query_agent.py`:
  1. **Full RAG** — all 8 retrieval strategies active
  2. **Lite RAG** — top-k only, skip reranking/decomposition/MMR
  3. **Direct LLM** — no retrieved context, parametric knowledge only
  4. **Cached response** — semantic cache hit from prior queries
- [ ] Track `fallback_tier` in Redis metrics (observability dashboard)
- [ ] Add degradation status to `/health/status`: `healthy` / `degraded` / `minimal` / `cached-only`
- [ ] Per-stage Ollama fallback: if Ollama circuit opens for a stage, fall back to Bifrost for THAT stage only

**Verification gate:**
- [ ] Kill ChromaDB → query returns result from Direct LLM tier (not 500)
- [ ] Kill Bifrost → query returns cached result or graceful error
- [ ] `/health/status` correctly reports `degraded` when ChromaDB is down
- [ ] Metrics show `fallback_tier` distribution in Redis

---

## Track 2: Verification Pipeline Improvements

### Sprint 2A: Close Monte Carlo Gaps (3-4 hours)

**Problem:** Monte Carlo evaluation revealed:
- Complex claim recall: 14% (patterns only catch causality/comparison, not arithmetic)
- Ignorance detection: 60% (narrow patterns miss capability-limit phrases)
- Verdict parsing: text-wrapped JSON confidence extraction fails
- Numeric alignment: requires 2+ checks for penalty (single mismatch ignored)

**Action items:**
- [ ] Expand `COMPLEX_CLAIM_PATTERNS` in `patterns.py`:
  - Add arithmetic patterns: `\b\d+\s*[×÷+\-*/]\s*\d+`, `factorial`, `probability`, `sum of`
  - Add logical reasoning: `\b(?:every|all|no|none|some)\b.*\b(?:therefore|must|cannot)\b`
  - Add statistical: `\b(?:average|median|mean|standard deviation|correlation)\b`
  - Target: raise recall from 14% → 50%+ on math/logical claims
- [ ] Expand `_is_ignorance_admission` patterns:
  - Add capability-limit: `"I'm unable to"`, `"I cannot access"`, `"I don't have the ability"`
  - Add real-time caveats: `"I don't have real-time"`, `"I cannot browse"`, `"my training data"`
  - Target: raise accuracy from 60% → 80%+
- [ ] Fix `_parse_verification_verdict` text-wrapped JSON:
  - Regex extract JSON block: `\{[^{}]*"verdict"[^{}]*\}` from surrounding text
  - Preserve confidence from extracted JSON (currently overridden)
- [ ] Relax `_check_numeric_alignment` penalty:
  - Apply when `total_checks >= 1 AND match_ratio == 0.0` (currently requires 2+)
  - Add year proximity: `|year_claim - year_source| > 2` → penalty
- [ ] Re-run Monte Carlo, update baseline metrics in `tests/eval/baselines/monte_carlo.json`

**Verification gate:**
- [ ] Monte Carlo classification accuracy ≥ 90% (from 84%)
- [ ] Complex claim recall ≥ 50% (from 14%)
- [ ] Ignorance detection ≥ 80% (from 60%)
- [ ] All existing verification tests still pass
- [ ] Baseline metrics file updated with new numbers

---

### Sprint 2B: Metamorphic Verification (4-5 hours)

**Problem:** Verification relies on cross-model agreement only. MetaRAG paper (Sep 2025) shows perturbation-based testing catches hallucinations that cross-model misses.

**Action items:**
- [ ] Create `agents/hallucination/metamorphic.py`:
  - Decompose answer into atomic factoids (reuse `_extract_claims_heuristic`)
  - Generate controlled mutations: synonym substitution (should preserve), antonym substitution (should break)
  - Score: penalty when synonym NOT entailed by context, or antonym IS entailed
  - Output: `metamorphic_score` (0-1), per-claim perturbation results
- [ ] Integrate as optional verification stage (gated by `@require_feature("metamorphic_verification")`)
- [ ] Use Ollama for mutation generation (small model task — ideal for local LLM)
- [ ] Add Monte Carlo tests for metamorphic accuracy

**Verification gate:**
- [ ] 15+ unit tests for metamorphic module
- [ ] Feature gate returns 403 on Community tier
- [ ] Ollama fallback to Bifrost works when Ollama is down
- [ ] Monte Carlo metamorphic subset shows ≥70% detection rate

---

### Sprint 2C: Local Verification via Ollama (2-3 hours)

**Problem:** All verification uses cloud models (OpenRouter). FaithLens paper shows 8B models match GPT-4.1 on faithfulness detection.

**Action items:**
- [ ] Add `VERIFICATION_USE_LOCAL` config (default: false)
- [ ] Route heuristic extraction and simple factual verification through Ollama when enabled
- [ ] Keep complex/recency/evasion on cloud (quality threshold)
- [ ] Per-stage circuit breaker: `"ollama-extraction"`, `"ollama-verification"`
- [ ] Fallback: local failure → seamless escalation to cloud

**Verification gate:**
- [ ] Side-by-side comparison: 20 claims verified locally vs cloud, agreement ≥85%
- [ ] Circuit breaker test: kill Ollama → verification completes via cloud
- [ ] No regression in Monte Carlo metrics

---

## Track 3: RAG Pipeline Evolution

### Sprint 3A: Retrieval-Level Caching (2-3 hours)

**Problem:** Semantic cache exists for final responses but not intermediate retrieval. Production systems report 60-80% hit rates. ARC paper shows 80% latency reduction.

**Implementation:**
```python
# src/mcp/utils/retrieval_cache.py
class RetrievalCache:
    """Redis-backed cache for ChromaDB retrieval results.

    Key design: quantized query embedding (int8) → serialized chunk set.
    Invalidation: generation counter incremented on KB ingest/delete.
    """
    PREFIX = "cerid:retrieval:"
    GENERATION_KEY = "cerid:retrieval:generation"
    DEFAULT_TTL = 1800  # 30 minutes

    async def get(self, query_embedding: list[float]) -> list[ChunkResult] | None: ...
    async def set(self, query_embedding: list[float], results: list[ChunkResult]) -> None: ...
    async def invalidate_all(self) -> None:
        """Increment generation counter — all existing keys become stale."""
```

**Action items:**
- [ ] Create `src/mcp/utils/retrieval_cache.py` with generation-counter invalidation
- [ ] Integrate before ChromaDB query in `agents/query_agent.py`
- [ ] Bust cache on KB ingest/delete events (hook into ingestion service)
- [ ] Add cache hit rate to observability metrics
- [ ] Config: `RETRIEVAL_CACHE_TTL` env var (default 1800s)

**Verification gate:**
- [ ] Repeated identical query hits cache (verify via Redis key inspection)
- [ ] KB ingest invalidates cache (generation counter increments)
- [ ] Cache miss on novel query (expected behavior)
- [ ] Metrics dashboard shows cache hit rate

---

### Sprint 3B: Parent-Child Document Retrieval (3-4 hours)

**Problem:** Single-granularity chunking. Research consensus: parent-child gives "surgical precision of small-chunk search with rich context of large-document generation."

**Implementation:**
```python
# ChromaDB metadata schema for parent-child
child_metadata = {
    "parent_chunk_id": "doc_abc_chunk_0",   # links to parent
    "chunk_level": "child",                  # "parent" or "child"
    "parent_token_count": 800,               # parent size for budget
    "child_index": 2,                        # position within parent
}
# Ratio: 4:1 to 8:1 (child:parent token count)
```

**Action items:**
- [ ] Modify `utils/chunker.py` to produce two-tier chunks:
  - Child: 100-300 tokens (search precision)
  - Parent: 500-1500 tokens (generation context)
  - Store `parent_chunk_id` in child metadata
- [ ] ChromaDB: add `parent_chunk_id`, `chunk_level`, `child_index` metadata
- [ ] Retrieval: search child chunks → retrieve parent chunks for context assembly
- [ ] Add `pkb_rebuild_parentchild` admin endpoint for re-chunking existing KB
- [ ] Feature flag: `ENABLE_PARENT_CHILD_RETRIEVAL` (default: false)
- [ ] Migration path: existing single-level chunks treated as both parent and child

**Verification gate:**
- [ ] Ingest test document → verify parent+child chunks created with correct metadata
- [ ] Query retrieves child → context assembly uses parent (larger context window)
- [ ] Rebuild endpoint processes existing KB without data loss
- [ ] Feature flag off → old chunking behavior unchanged
- [ ] 10+ tests covering parent-child creation, retrieval, and rebuild

---

### Sprint 3C: HyDE Fallback Retrieval (2-3 hours)

**Problem:** Low-confidence retrieval has no recovery beyond query decomposition. HyDE bridges query-document vocabulary gap.

**Implementation:**
```python
# Trigger conditions (ALL must be true):
# 1. Top retrieval score < 0.4 (low confidence)
# 2. Adaptive retrieval gate says "retrieve" (not "skip")
# 3. HyDE not already attempted for this query (prevent loops)

# Algorithm:
# 1. Generate hypothetical answer via LLM (Ollama preferred)
# 2. Embed the hypothetical answer
# 3. Search KB with hypothetical embedding
# 4. Merge with original results via Reciprocal Rank Fusion (RRF)
```

**Action items:**
- [ ] Add HyDE as retrieval fallback in `agents/query_agent.py`
- [ ] Trigger: top score < 0.4 AND retrieval gate = "retrieve"
- [ ] Ollama-first for hypothesis generation (cheap, fast)
- [ ] Fallback: if Ollama unavailable, use Bifrost for hypothesis
- [ ] Track `hyde_activated` in observability metrics
- [ ] Anti-loop: flag query to prevent recursive HyDE

**Verification gate:**
- [ ] Low-confidence query triggers HyDE (verify via logs)
- [ ] HyDE result improves retrieval score (side-by-side comparison)
- [ ] Ollama fallback to Bifrost works
- [ ] No infinite loops (flag prevents re-trigger)
- [ ] 8+ tests covering trigger, generation, merge, and loop prevention

---

### Sprint 3D: Graph RAG Layer — LightRAG (5-6 hours, future)

**Problem:** Neo4j stores entity relationships but no automated entity extraction or community detection. LightRAG achieves 70-90% of Microsoft GraphRAG quality at 1/100th cost.

**Action items:**
- [ ] Evaluate LightRAG integration feasibility (prototype with 100-doc subset)
- [ ] Entity extraction from ingested documents (Ollama for cost)
- [ ] Store in Neo4j: `:Entity` nodes + `:RELATES_TO` edges (extend existing schema)
- [ ] Community detection via Neo4j GDS (Leiden algorithm)
- [ ] Community summaries for global query context
- [ ] Feature flag: `ENABLE_GRAPH_RAG` (Pro tier, `@require_feature`)

**Verification gate:**
- [ ] 100-document prototype produces entity graph
- [ ] Query with Graph RAG returns richer context than without
- [ ] Feature gate enforced (Community tier → 403)
- [ ] Neo4j schema migration is backward-compatible

---

## Track 4: Repository Architecture & Packaging

### Sprint 4A: Separate Dev/Eval from Core Install (3-4 hours)

**Problem:** Monte Carlo harness, beta tests, eval benchmarks ship with main install. Users get test infrastructure they don't need. Eval dependencies (ragas, scipy) inflate install size.

**Action items:**
- [ ] Create `requirements-eval.txt`:
  ```
  ragas>=0.2
  sentence-transformers
  scipy
  ```
- [ ] Move evaluation-only test files to `tests/eval/`:
  - `test_verification_monte_carlo.py` → `tests/eval/`
  - Consolidate `tests/beta/eval/` into `tests/eval/`
- [ ] Move `tests/beta/` into `tests/integration/` or `tests/e2e/`
- [ ] Add pytest markers for test tiers:
  - `@pytest.mark.unit` — fast, no external deps
  - `@pytest.mark.integration` — needs Docker services
  - `@pytest.mark.eval` — Monte Carlo, benchmarks (slow)
- [ ] `Makefile` targets: `make test` (unit), `make test-all`, `make test-eval`
- [ ] Docker image: verify `tests/` excluded from production image via `.dockerignore`
- [ ] Update `.claudeignore` to skip eval data files

**Verification gate:**
- [ ] `make test` runs only unit tests (< 60 seconds)
- [ ] `make test-eval` runs Monte Carlo + RAGAS
- [ ] Production Docker image does NOT contain `tests/` or `requirements-eval.txt`
- [ ] CI runs all three tiers in separate jobs

---

### Sprint 4B: Tier Enforcement Hardening (2-3 hours)

**Problem:** 12+ inline `if config.FEATURE_TIER == "pro"` checks bypass `@require_feature()`. After compaction, AI agents don't know which pattern to use and generate inline checks.

**Action items:**
- [ ] Audit ALL tier-gated code paths — replace inline checks with `@require_feature()`
- [ ] Add tier test matrix in CI: run with `CERID_TIER=community`, verify Pro features return 403
- [ ] Consolidate `config/features.py` + `utils/features.py` into single `config/features.py`
- [ ] Document tier boundaries in `docs/TIER_MATRIX.md`
- [ ] Add ruff custom check or grep CI step: `grep -r "FEATURE_TIER" src/mcp/ | grep -v "@require_feature"` must return 0

**Verification gate:**
- [ ] Zero inline tier checks outside `config/features.py`
- [ ] CI tier matrix: all Pro features return 403 when `CERID_TIER=community`
- [ ] `TIER_MATRIX.md` documents every feature with its tier
- [ ] grep CI gate passes

---

### Sprint 4C: Health Check & Operational Readiness (1-2 hours)

**Problem:** `/health` doesn't report degradation. No readiness vs liveness distinction.

**Action items:**
- [ ] Split `/health` into:
  - `/health/live` — process running? (always 200 unless crashed)
  - `/health/ready` — dependencies reachable? (Neo4j, ChromaDB, Redis, Ollama)
  - `/health/status` — detailed degradation with per-service status + circuit breaker state
- [ ] Docker healthchecks use `/health/live` (fast, no dep check)
- [ ] Orchestrator readiness gates use `/health/ready`
- [ ] Add version, uptime, and tier info to `/health/status`

**Verification gate:**
- [ ] `/health/live` returns 200 even when ChromaDB is down
- [ ] `/health/ready` returns 503 when any critical dependency is down
- [ ] `/health/status` shows circuit breaker states and degradation tier
- [ ] Docker healthcheck updated in Dockerfile

---

### Sprint 4D: Module Documentation for Context Resilience (2-3 hours)

**Problem:** After compaction events, AI agents lose track of module responsibilities, public APIs, and inter-module contracts. They then generate code that violates module boundaries or duplicates existing utilities.

**Action items:**
- [ ] Add module-level docstrings to every `__init__.py` and major module:
  ```python
  """Hallucination detection pipeline.

  Modules:
    extraction.py — Claim extraction (LLM + heuristic fallback)
    patterns.py   — Pattern matching for claim classification
    verification.py — Claim verification orchestrator (4 fallback levels)
    streaming.py  — SSE streaming for real-time verification
    metamorphic.py — Perturbation-based verification (Pro tier)
    persistence.py — Verification result storage

  Public API:
    verify_response_streaming(response, kb_context, ...) → AsyncGenerator[SSEEvent]
    extract_claims(text, ...) → list[Claim]
    classify_claim(claim_text) → ClaimType

  Dependencies: ChromaDB, Neo4j, Redis, Bifrost/Ollama
  Error types: VerificationError
  Feature flags: metamorphic_verification (Pro)
  """
  ```
- [ ] Create `docs/ARCHITECTURE_MAP.md` — one-page module dependency graph
- [ ] Ensure CLAUDE.md references all key modules with their responsibilities
- [ ] Add `# Context: This module handles X. See Y for related functionality.` header comments to files >200 lines

**Verification gate:**
- [ ] Every `agents/`, `routers/`, `utils/`, `services/` directory has documented `__init__.py`
- [ ] `ARCHITECTURE_MAP.md` covers all 10 agents, 29 routers, 7 middleware layers
- [ ] Fresh AI agent can determine correct module for a new feature from docs alone (manual test)

---

## Track 5: Ollama Extensibility

### Sprint 5A: Per-Stage Ollama Routing (3-4 hours)

**Problem:** Ollama is all-or-nothing via `INTERNAL_LLM_PROVIDER`. No per-stage routing. When Ollama fails, ALL stages fail instead of just the affected one.

**Implementation:**
```python
# src/mcp/config/settings.py — addition
PIPELINE_PROVIDERS: dict[str, str] = {
    "claim_extraction": os.getenv("PROVIDER_CLAIM_EXTRACTION", "ollama"),
    "query_decomposition": os.getenv("PROVIDER_QUERY_DECOMPOSITION", "ollama"),
    "topic_extraction": os.getenv("PROVIDER_TOPIC_EXTRACTION", "ollama"),
    "memory_resolution": os.getenv("PROVIDER_MEMORY_RESOLUTION", "ollama"),
    "verification_simple": os.getenv("PROVIDER_VERIFICATION_SIMPLE", "ollama"),
    "verification_complex": os.getenv("PROVIDER_VERIFICATION_COMPLEX", "bifrost"),
    "reranking": os.getenv("PROVIDER_RERANKING", "ollama"),
    "chat_generation": os.getenv("PROVIDER_CHAT_GENERATION", "bifrost"),
}
# Backward compat: INTERNAL_LLM_PROVIDER=ollama sets ALL to Ollama
```

**Action items:**
- [ ] Add `PIPELINE_PROVIDERS` config dict with per-stage env var overrides
- [ ] Each stage gets independent circuit breaker: `ollama-{stage_name}`
- [ ] Per-stage fallback: Ollama failure → Bifrost for THAT stage only
- [ ] Settings API: expose per-stage routing in `/settings` for GUI configuration
- [ ] Backward compat: `INTERNAL_LLM_PROVIDER=ollama` sets all stages to Ollama
- [ ] GUI: per-stage provider selector in Settings → Models tab

**Verification gate:**
- [ ] Set `PROVIDER_CLAIM_EXTRACTION=ollama`, `PROVIDER_CHAT_GENERATION=bifrost` → each routes correctly
- [ ] Kill Ollama → claim extraction falls back to Bifrost, chat generation unaffected
- [ ] `INTERNAL_LLM_PROVIDER=ollama` backward compat still works
- [ ] Settings API returns per-stage provider configuration
- [ ] 10+ tests covering routing, fallback, and backward compat

---

### Sprint 5B: Ollama Model Management (2-3 hours)

**Action items:**
- [ ] Auto-detect available Ollama models at startup (populate in-memory registry)
- [ ] Recommend models per stage:
  - Extraction/classification: `qwen2.5:1.5b` (1GB)
  - Verification: `llama3.3:8b` (4.7GB)
  - Embedding: `nomic-embed-text` (zero API cost)
- [ ] Model pull progress in GUI (wire existing `/ollama/pull` to settings)
- [ ] Memory/GPU detection: warn if model exceeds available resources
- [ ] Startup validation: warn (not error) if recommended model not pulled

**Verification gate:**
- [ ] Startup logs list available Ollama models
- [ ] Missing recommended model → warning in logs + `/health/status`
- [ ] GUI shows model pull progress
- [ ] Resource warning triggers for models exceeding available memory

---

## Track 6: Code Quality Remediation

### Sprint 6A: God File Decomposition (4-5 hours)

**Problem:** 18 files >500 lines in production code. Largest: `verification.py` (1590), `query_agent.py` (1134), `tools.py` (921), `agents.py` router (774). Large files overwhelm AI context windows and make targeted edits error-prone.

**Decomposition targets:**

**`verification.py` (1590 lines) → 4 modules:**
- [ ] `verdict_parsing.py` — `_parse_verification_verdict`, `_interpret_recency_verdict`, `_invert_*_verdict`
- [ ] `confidence.py` — `_compute_adjusted_confidence`, `_check_numeric_alignment`, `_build_verification_details`
- [ ] `memory_verify.py` — `_query_memories`, memory-aware verification
- [ ] `verification.py` — orchestrator importing from sub-modules (target: <400 lines)

**`query_agent.py` (1134 lines) → 3 modules:**
- [ ] `decomposer.py` — query decomposition and sub-retrieval
- [ ] `assembler.py` — context assembly, facet coverage, budget management
- [ ] `query_agent.py` — main entry point (target: <400 lines)

**`api.ts` (1425 lines) → 5 modules:**
- [ ] `api/kb.ts`, `api/chat.ts`, `api/settings.ts`, `api/verification.ts`, `api/index.ts`
- [ ] Re-export from `api/index.ts` for backward compat (zero-breakage migration)

**`test_hallucination.py` (2773 lines) → 5 files:**
- [ ] `test_verification_factual.py`
- [ ] `test_verification_recency.py`
- [ ] `test_verification_evasion.py`
- [ ] `test_verification_ignorance.py`
- [ ] `test_verification_integration.py` (streaming, routing)

**Pattern:** Use re-export bridges (`from .submodule import *`) so all existing imports continue to work. This is critical for context-limit resilience — AI agents that lost track of a refactor can still use old import paths.

**Verification gate:**
- [ ] All existing imports still work (re-export bridges)
- [ ] No file in `src/mcp/` exceeds 800 lines (stretch: 500)
- [ ] All tests pass after decomposition
- [ ] `ruff check` clean
- [ ] `mypy` clean

---

### Sprint 6B: Magic Numbers → Named Constants (1-2 hours)

**Problem:** Magic numbers scattered across business logic. After compaction, AI doesn't know what `10_000` or `120.0` mean and may change them or use different values.

**Action items:**
- [ ] Create `config/constants.py`:
  ```python
  """Centralized constants. THE source of truth for all magic numbers.

  AI agents: import from here. Never hardcode numeric literals in business logic.
  """
  # Artifact limits
  MAX_ARTIFACT_LIST = 10_000
  MAX_ARTIFACTS_PER_DOMAIN = 200
  MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024  # 50MB

  # Timeouts
  HEALTH_CACHE_TTL_SECONDS = 10.0
  A2A_TASK_TTL_SECONDS = 3600
  OLLAMA_READ_TIMEOUT = 120.0
  OLLAMA_CONNECT_TIMEOUT = 10.0
  BIFROST_TIMEOUT = 30.0

  # Budget & limits
  MONTHLY_BUDGET_USD = 20.0
  OBSERVABILITY_RETENTION_SECONDS = 10_000
  RATE_LIMIT_WINDOW_SECONDS = 60

  # Retrieval
  DEFAULT_TOP_K = 10
  RETRIEVAL_CACHE_TTL = 1800
  SEMANTIC_CACHE_SIMILARITY_THRESHOLD = 0.92
  HYDE_TRIGGER_THRESHOLD = 0.4

  # Verification
  VERIFICATION_TIMEOUT = 30.0
  MAX_CLAIMS_PER_RESPONSE = 20
  CONFIDENCE_FLOOR = 0.3
  ```
- [ ] Replace all hardcoded values with constant references
- [ ] Reference `config/constants.py` in CLAUDE.md as canonical source

**Verification gate:**
- [ ] `grep -rn "= 10_000\|= 120\.0\|= 30\.0" src/mcp/ --include="*.py"` returns only `constants.py`
- [ ] All tests pass
- [ ] CLAUDE.md updated with constants reference

---

### Sprint 6C: Logging & Dead Code Discipline (1-2 hours)

**Action items:**
- [ ] Standardize log levels:
  - `DEBUG` — fallback activated, cache miss, optional feature skipped
  - `INFO` — ingestion complete, query done, verification done
  - `WARNING` — degraded path, circuit breaker tripped, retry needed
  - `ERROR` — unrecoverable failure, data corruption risk
- [ ] Add structured logging fields: `{"event": "...", "claim_type": "...", "latency_ms": ...}`
- [ ] Enable ruff `ERA` rules (commented-out code detection)
- [ ] Sweep for dead imports (`F401`) and unused variables (`F841`)
- [ ] Remove stale TODO comments older than 3 months (or convert to GitHub issues)

**Verification gate:**
- [ ] `ruff check src/mcp/ --select ERA,F401,F841` returns 0 violations
- [ ] No `except` block without at minimum `logger.warning()`
- [ ] Structured logging fields present in high-traffic paths (query, ingestion, verification)

---

### Sprint 6D: Type Safety Hardening (2-3 hours)

**Problem:** After compaction, AI agents hallucinate function signatures. Strong type hints prevent this.

**Action items:**
- [ ] Enable mypy strict mode for `agents/`, `services/`, `utils/` (incremental, not all-at-once)
- [ ] Add return type annotations to all public functions (currently ~70% coverage)
- [ ] Add `TypedDict` for complex dict returns (verification results, query results)
- [ ] Add `Protocol` classes for pluggable components (LLM provider, cache backend)
- [ ] CI: mypy with `--disallow-untyped-defs` for new files

**Verification gate:**
- [ ] `mypy src/mcp/agents/ --disallow-untyped-defs` clean
- [ ] `mypy src/mcp/utils/ --disallow-untyped-defs` clean (excluding legacy files)
- [ ] All public APIs have return type annotations
- [ ] CI mypy check passes

---

## Track 7: Evaluation & Continuous Quality

### Sprint 7A: RAGAS Integration (3-4 hours)

**Problem:** No continuous evaluation pipeline. RAGAS is the 2026 standard for RAG evaluation. Without baselines, regressions go undetected.

**Implementation:**
```python
# tests/eval/ragas_eval.py
from ragas import evaluate
from ragas.metrics import faithfulness, context_precision, context_recall, answer_relevancy

# Golden dataset: 50 hand-labeled query→document→answer triples
# Stored in tests/eval/golden_dataset.json
# Format: [{"query": "...", "ground_truth": "...", "contexts": ["..."]}]

# CI gate: weekly scheduled run, alert if metrics drop >5%
# Baselines stored in tests/eval/baselines/ragas.json
```

**Action items:**
- [ ] Add `ragas` to `requirements-eval.txt`
- [ ] Create golden dataset: 50 query→document→answer triples from real KB
- [ ] Implement RAGAS eval harness in `tests/eval/ragas_eval.py`
- [ ] Store baselines in `tests/eval/baselines/ragas.json`
- [ ] CI: weekly scheduled eval, alert on >5% regression
- [ ] Target baselines: Faithfulness ≥0.80, Context Precision ≥0.75, NDCG@5 ≥0.70

**Verification gate:**
- [ ] `make test-eval` runs RAGAS and produces metrics report
- [ ] Baseline file exists with initial measurements
- [ ] CI weekly job configured (GitHub Actions schedule)
- [ ] Alert mechanism works (GitHub issue created on regression)

---

### Sprint 7B: Monte Carlo Expansion (2-3 hours)

**Action items:**
- [ ] Expand corpus from 54 → 150+ scenarios:
  - +20 citation verification scenarios
  - +30 multi-hop reasoning claims
  - +20 numeric precision claims (currency, percentages, dates)
  - +15 domain-specific claims (finance, code, science)
  - +10 adversarial claims (near-miss facts, plausible-but-wrong)
- [ ] Add bootstrap confidence intervals (95% CI) for each metric
- [ ] Add parameter sensitivity analysis: how much does accuracy change with threshold tuning?
- [ ] Store results in `tests/eval/baselines/monte_carlo.json` with timestamps

**Verification gate:**
- [ ] 150+ scenarios in Monte Carlo corpus
- [ ] Each metric has 95% CI reported
- [ ] Baseline file updated with new measurements
- [ ] Sensitivity analysis identifies optimal threshold ranges

---

## Track 8: Context-Limit Defensive Architecture

### Sprint 8A: Compaction-Resilient Code Patterns (1-2 hours)

**Problem:** In AI-assisted development, context compaction causes the AI to:
1. Forget module boundaries and create duplicate utilities
2. Use wrong import paths after refactoring
3. Regenerate error handling patterns inconsistently
4. Lose track of feature flag mechanisms
5. Create conflicting configuration patterns

**Action items — Structural defenses:**

- [ ] **Single-pattern enforcement:** For every cross-cutting concern, there is exactly ONE pattern:
  | Concern | The ONE Pattern | Location |
  |---------|----------------|----------|
  | Error handling | `@handle_errors` decorator | `utils/error_handler.py` |
  | Feature gating | `@require_feature()` decorator | `config/features.py` |
  | Configuration | `config/settings.py` env vars | `config/settings.py` |
  | Constants | `config/constants.py` | `config/constants.py` |
  | Logging | `structlog` with standard fields | Per-module logger |
  | Circuit breakers | `circuit_breaker(name)` context manager | `utils/circuit_breaker.py` |
  | Cache keys | `cerid:{domain}:{key}` Redis prefix | `utils/retrieval_cache.py` |

- [ ] **Import path stability:** All refactored modules use re-export bridges. Old import paths ALWAYS work.

- [ ] **CLAUDE.md as recovery document:** Update CLAUDE.md with:
  - Module responsibility map (one line per module)
  - "The ONE pattern" table above
  - Link to `config/constants.py` for all numeric values
  - Link to `errors.py` for error hierarchy
  - Link to `docs/TIER_MATRIX.md` for feature tiers

- [ ] **File header convention:** Every file >100 lines gets a 2-line header:
  ```python
  """Query agent — orchestrates RAG retrieval pipeline.
  Dependencies: ChromaDB, Neo4j, Redis. Errors: RetrievalError. See also: decomposer.py, assembler.py."""
  ```

- [ ] **CI check for pattern violations:**
  ```bash
  # No inline tier checks
  grep -r "FEATURE_TIER" src/mcp/ --include="*.py" | grep -v "config/features.py" | grep -v "@require_feature" → must be empty

  # No bare excepts
  ruff check src/mcp/ --select BLE001 → 0 violations

  # No magic numbers in business logic (spot check)
  # No duplicate utility functions (manual review per sprint)
  ```

**Verification gate:**
- [ ] CLAUDE.md updated with "ONE pattern" table
- [ ] Every file >100 lines has module docstring
- [ ] CI pattern-violation checks configured and passing
- [ ] Manual test: start fresh Claude Code session, ask it to add a new feature — verify it uses correct patterns from CLAUDE.md alone

---

### Sprint 8B: Test Architecture for Context Resilience (1-2 hours)

**Problem:** Tests that rely on implicit knowledge (e.g., "this mock should return X because of how module Y works internally") break when AI agents regenerate them after compaction. Tests should be self-documenting.

**Action items:**
- [ ] Every test file gets a header docstring explaining what it tests and why:
  ```python
  """Tests for claim classification accuracy.

  Baseline: 84% accuracy (2026-03-28, 54 scenarios).
  Target: 90% accuracy (Phase 51 Sprint 2A).
  Golden metrics: tests/eval/baselines/monte_carlo.json

  These tests verify that patterns.py correctly classifies claims into:
  factual, recency, evasion, ignorance, citation.
  """
  ```
- [ ] Test data factories use descriptive builders (not raw dicts):
  ```python
  # Good: self-documenting
  make_factual_claim("Earth orbits the Sun")
  make_evasion_claim("I'd rather not discuss that")

  # Bad: requires context to understand
  {"text": "Earth orbits the Sun", "type": "factual", "expected": True}
  ```
- [ ] Baseline metrics stored as JSON files, not hardcoded in test assertions
- [ ] Every test class has a `# Context:` comment linking to the module it tests

**Verification gate:**
- [ ] All test files >50 lines have header docstrings
- [ ] Monte Carlo baselines in JSON file (not hardcoded)
- [ ] Test data uses factory functions with descriptive names
- [ ] Fresh AI agent can understand test purpose from docstrings alone

---

## Execution Schedule

```
Week 1: Foundation (Error handling + Repo structure)
├── Sprint 1A: Exception hierarchy + @handle_errors decorator
├── Sprint 4A: Dev/eval separation (requirements-eval.txt, test markers)
├── Sprint 6B: Magic numbers → config/constants.py
└── Sprint 8A: Compaction-resilient patterns + CLAUDE.md update
    GATE: ruff BLE001 clean, constants centralized, CLAUDE.md updated

Week 2: Verification + Ollama (Pipeline improvements)
├── Sprint 2A: Close Monte Carlo gaps (patterns, parsing, alignment)
├── Sprint 5A: Per-stage Ollama routing
├── Sprint 4C: Health check improvements (live/ready/status)
└── Sprint 6C: Logging discipline + dead code sweep
    GATE: Monte Carlo ≥90%, per-stage routing works, health endpoints split

Week 3: RAG Evolution (Retrieval improvements)
├── Sprint 3A: Retrieval-level caching (Redis, generation counter)
├── Sprint 3C: HyDE fallback retrieval
├── Sprint 6A: God file decomposition (start with verification.py)
└── Sprint 4D: Module documentation for context resilience
    GATE: Cache hit rate measurable, HyDE triggers correctly, verification.py <400 lines

Week 4: Evaluation + Parent-Child (Quality infrastructure)
├── Sprint 7A: RAGAS integration + golden dataset
├── Sprint 3B: Parent-child document retrieval
├── Sprint 6A: God file decomposition (finish query_agent.py, api.ts)
└── Sprint 8B: Test architecture for context resilience
    GATE: RAGAS baselines established, parent-child chunks created, all gods split

Week 5: Degradation + Tier Hardening
├── Sprint 1B: Multi-tier graceful degradation
├── Sprint 4B: Tier enforcement hardening (@require_feature everywhere)
├── Sprint 6D: Type safety hardening (mypy strict for agents/)
└── Sprint 5B: Ollama model management
    GATE: Degradation tested (kill services → graceful), zero inline tier checks

Week 6+: Advanced Features (Pro tier)
├── Sprint 2B: Metamorphic verification (Pro)
├── Sprint 2C: Local verification via Ollama
├── Sprint 3D: LightRAG evaluation + prototype
└── Sprint 7B: Monte Carlo expansion (150+ scenarios)
    GATE: Metamorphic detection ≥70%, Graph RAG prototype working
```

**Weekly cadence:**
1. Implement sprints
2. Run full test suite (`make test-all`)
3. Run Monte Carlo evaluation (`make test-eval`)
4. Update baseline metrics in `tests/eval/baselines/`
5. Update CLAUDE.md with any new patterns/modules
6. Commit with clear sprint reference in message

---

## Success Criteria

| Metric | Current | Target | Sprint |
|--------|---------|--------|--------|
| Monte Carlo classification | 84% | ≥90% | 2A |
| Complex claim recall | 14% | ≥50% | 2A |
| Ignorance detection | 60% | ≥80% | 2A |
| Bare `except Exception` blocks | 352 | 0 | 1A |
| God files (>500 lines, prod) | 18 | <8 | 6A |
| Query fallback tiers | 1 | 4 | 1B |
| Ollama routing granularity | All-or-nothing | Per-stage (8 stages) | 5A |
| Retrieval cache hit rate | N/A | ≥60% | 3A |
| RAGAS Faithfulness | Unknown | ≥0.80 | 7A |
| Test tier separation | Mixed | Unit/Integration/Eval | 4A |
| CI eval gate | None | Weekly automated | 7A |
| Inline tier checks | 12+ | 0 | 4B |
| Type annotation coverage | ~70% | ≥90% (public APIs) | 6D |
| Module documentation | Sparse | Every module | 4D |
| Magic numbers in logic | ~50 | 0 (all in constants.py) | 6B |

## Research-Informed Feature Roadmap

| Priority | Feature | Source | Effort | Tier |
|----------|---------|--------|--------|------|
| P0 | Exception hierarchy + error discipline | Code audit (352 bare excepts) | 3-4h | Community |
| P0 | Multi-tier graceful degradation | Production patterns | 2-3h | Community |
| P0 | Retrieval-level caching | ARC paper, production consensus | 2-3h | Community |
| P0 | Context-limit defensive architecture | Compaction analysis | 2-3h | Community |
| P1 | Parent-child document retrieval | RAGFlow, LlamaIndex consensus | 3-4h | Community |
| P1 | HyDE fallback retrieval | Academic consensus, low effort | 2-3h | Community |
| P1 | Per-stage Ollama routing | Architecture gap | 3-4h | Community |
| P1 | RAGAS continuous evaluation | Industry standard | 3-4h | Dev tooling |
| P1 | God file decomposition | Code quality audit | 4-5h | Community |
| P2 | Metamorphic verification | MetaRAG paper (Sep 2025) | 4-5h | Pro |
| P2 | Graph RAG (LightRAG) | 31K stars, EMNLP 2025 | 5-6h | Pro |
| P2 | Local verification (FaithLens) | Dec 2025 paper | 2-3h | Pro |
| P3 | ColPali multi-modal retrieval | ICLR 2025 | 4-5h | Pro |
| P3 | Agentic RAG (A-RAG) | Feb 2026 paper | 6-8h | Pro |

### Features NOT to Add

- **Microsoft GraphRAG** — LightRAG achieves 70-90% quality at 1/100th cost
- **Speculative RAG** — latency optimization we don't need yet
- **MARCH multi-agent verification** — too new (Mar 2026), unstable API
- **Custom embedding models** — Arctic Embed M v1.5 is performing well

---

## Key Files (New + Modified)

| File | Track | Purpose |
|------|-------|---------|
| `src/mcp/errors.py` | 1A | Exception hierarchy |
| `src/mcp/utils/error_handler.py` | 1A | `@handle_errors` decorator |
| `src/mcp/utils/degradation.py` | 1B | Multi-tier fallback manager |
| `src/mcp/utils/retrieval_cache.py` | 3A | Retrieval-level cache |
| `src/mcp/agents/hallucination/metamorphic.py` | 2B | Metamorphic testing |
| `src/mcp/agents/hallucination/verdict_parsing.py` | 6A | Extracted from verification.py |
| `src/mcp/agents/hallucination/confidence.py` | 6A | Extracted from verification.py |
| `src/mcp/agents/hallucination/memory_verify.py` | 6A | Extracted from verification.py |
| `src/mcp/agents/decomposer.py` | 6A | Extracted from query_agent.py |
| `src/mcp/agents/assembler.py` | 6A | Extracted from query_agent.py |
| `src/mcp/config/constants.py` | 6B | Centralized magic numbers |
| `src/web/src/lib/api/index.ts` | 6A | Split API client |
| `requirements-eval.txt` | 4A | Eval-only dependencies |
| `tests/eval/baselines/` | 7A | Golden metrics storage |
| `tests/eval/golden_dataset.json` | 7A | RAGAS golden dataset |
| `docs/TIER_MATRIX.md` | 4B | Tier boundary docs |
| `docs/ARCHITECTURE_MAP.md` | 4D | Module dependency graph |
