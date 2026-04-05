# Cerid AI — Unified Implementation Plan

**Date:** 2026-04-05
**Version:** 1.0
**Scope:** All remaining work merged into a single 4.5-week execution plan
**Sources:** CONSOLIDATED_ISSUES_2026-04-05, TIERED_INFERENCE_ARCHITECTURE, FINAL_PUNCH_LIST_V2, performance scan

---

## Executive Summary

This plan merges five work streams into a phased execution plan:

| Stream | Items | Source |
|--------|-------|--------|
| **A** | B-MEDIUM issues (B18, B23, B26, B30) | Consolidated Issues |
| **B** | B-LOW issues (B31-B41) | Consolidated Issues |
| **C** | Tiered Inference (Phases 1-6) | TIERED_INFERENCE_ARCHITECTURE.md |
| **D** | Punch List Sections 3-5 (wiring, multi-OS, Ollama) | FINAL_PUNCH_LIST_V2 |
| **E** | Performance detection & tuning | Codebase scan |

Each phase ends with: CI green, live verification, performance measurement snapshot.

---

## Phase 1: Critical Infrastructure — Tiered Inference + Remaining Critical Fixes (Week 1)

> Goal: GPU-aware inference detection operational, remaining B-CRITICAL items closed.

### 1.1 InferenceConfig + Detection (Tiered Inference Phase 1)

| Task | Files | Detail |
|------|-------|--------|
| Create `InferenceProvider` dataclass + `InferenceConfig` singleton | `src/mcp/utils/inference_config.py` (NEW) | Platform enum, `detect_embedding_provider()`, GPU probing per platform (macOS Metal, Linux CUDA/ROCm, Windows DirectML) |
| Create sidecar HTTP client | `src/mcp/utils/inference_sidecar_client.py` (NEW) | `/embed` + `/rerank` endpoints with circuit breaker `"sidecar"` |
| Add env vars | `src/mcp/config/settings.py` (after line 496) | `CERID_SIDECAR_PORT=8889`, `CERID_SIDECAR_URL`, `INFERENCE_RECHECK_INTERVAL=300` |
| Wire detection into startup | `src/mcp/main.py` (lifespan) | Call `detect_embedding_provider()` after infra health checks, before warmup |
| Switch ONNX providers to dynamic | `src/mcp/utils/embeddings.py:94`, `src/mcp/utils/reranker.py:60` | Replace `["CPUExecutionProvider"]` with `get_inference_config().onnx_providers` |
| Add `inference` field to health | `src/mcp/routers/health.py` | Provider, tier, gpu, embed_latency_ms, rerank_latency_ms, message |
| Add `.env.example` entries | `.env.example` | `CERID_SIDECAR_PORT=8889`, `INFERENCE_RECHECK_INTERVAL=300` |

**Tests:** `tests/test_inference_config.py` — platform detection, fallback chains, provider probing mocks.

### 1.2 Remaining B-CRITICAL Fixes (if not yet closed)

These were identified in consolidated issues. Verify status before implementing — some may have been fixed in prior sessions.

| Item | File | Change |
|------|------|--------|
| B1: Heuristic claim patterns | `agents/hallucination/patterns.py` | Add `"X is a/an Y"` and `"X was created/founded/developed by Y"` regex patterns |
| B2: SSE error events | `agents/hallucination/streaming.py` | try/except around `verify_response_streaming()`, yield error event |
| B3: Self-test TTL | `agents/hallucination/startup_self_test.py:82` | `ex=86400` → `ex=3600` |
| B4: Retest after configure | `routers/setup.py` configure success path | `await run_verification_self_test(get_redis())` |
| B5: Recheck endpoint | `routers/setup.py` | `POST /setup/retest-verification` |
| B6: Health "LLM required" | `health-dashboard.tsx` | Show "Requires API key" when verification unavailable + no providers |
| B7: Skip metadata in wizard | `services/ingestion.py` + `routers/upload.py` | `skip_metadata` query param, first 200 chars fallback |

### 1.3 Performance Baseline (Stream E — Measurement)

| Metric | How to Measure | Target |
|--------|---------------|--------|
| Cold startup time | `time docker compose up cerid-mcp` → first health 200 | Baseline (expect ~8-12s) |
| Embedding latency (batch 10) | `GET /health` → `inference.embed_latency_ms` | Baseline (expect ~15-25ms Docker CPU) |
| Rerank latency (15 docs) | `GET /health` → `inference.rerank_latency_ms` | Baseline (expect ~50ms Docker CPU) |
| Query e2e (simple) | Timed `/agent/query` call | Baseline (expect ~800-1500ms) |
| Query e2e (complex/decomposed) | Timed `/agent/query` with decomposition | Baseline (expect ~2-4s) |
| Frontend initial load | Lighthouse Performance score | Baseline |
| Memory (MCP container) | `docker stats cerid-mcp` RSS | Baseline |

**Deliverable:** `docs/PERF_BASELINE_2026-04-05.md` with all measurements.

### Phase 1 Exit Criteria

- [ ] `detect_embedding_provider()` returns correct provider on macOS ARM
- [ ] `/health` includes `inference` field
- [ ] All B-CRITICAL items verified working (hallucination self-test passes, SSE errors handled)
- [ ] CI green (all 6 jobs)
- [ ] Performance baseline document written

---

## Phase 2: UX Polish + Performance Baseline (Week 2)

> Goal: B-MEDIUM items closed, performance measurement instrumented, sidecar service built.

### 2.1 FastEmbed Sidecar + Startup Integration (Tiered Inference Phase 2)

| Task | Files | Detail |
|------|-------|--------|
| Create sidecar server | `scripts/cerid-sidecar.py` (NEW) | ~150-line FastAPI: `/embed`, `/rerank`, `/health`. Wraps FastEmbed + cross-encoder ONNX |
| Create install script | `scripts/install-sidecar.sh` (NEW) | Platform-detecting: macOS ARM → `onnxruntime-silicon`, Linux NVIDIA → `onnxruntime-gpu`, etc. |
| Wire into startup | `scripts/start-cerid.sh` | Add phase `[0/4] Inference Sidecar` — probe, start if installed, warn if missing |
| Add sidecar HTTP path to embeddings | `utils/embeddings.py` | If provider == `fastembed-sidecar`, call sidecar `/embed` instead of local ONNX |
| Add sidecar HTTP path to reranker | `utils/reranker.py` | If provider == `fastembed-sidecar`, call sidecar `/rerank` instead of local ONNX |

**Tests:** `tests/test_inference_sidecar.py` — HTTP client contract, health check, mock sidecar.

### 2.2 B-MEDIUM UX Items

| Item | File(s) | Change |
|------|---------|--------|
| **B18: Sub-menu formatting audit** | Chat toolbar popovers (grep `Popover`, `DropdownMenu` in `src/web/src/components/chat/`) | Consistent padding (p-2), font sizes (text-sm), dividers between sections, tooltips on all interactive items |
| **B23: Recent imports scroll UX** | KB import list component (`src/web/src/components/kb/`) | Default collapsed, max 4 visible, "Show N more" expandable link, scrollable container with max-height |
| **B26: Health tab layout** | `src/web/src/components/setup/health-dashboard.tsx` | Redesign: group into Infrastructure / AI Pipeline / Optional categories. Card layout with status indicator, last-checked timestamp, expandable detail. Auto-refresh 30s |
| **B30: External search debugging** | `src/mcp/utils/data_sources/` + `DataSourceRegistry` | Debug `query_all()`: check enabled sources, circuit breaker state. Add structured logging. Frontend: "No data sources enabled" message with settings link |

### 2.3 Performance Quick Wins (Stream E)

| Optimization | File | Change | Expected Impact |
|-------------|------|--------|-----------------|
| **HNSW tuning** | `stacks/infrastructure/docker-compose.yml` (ChromaDB env) | Add `CHROMA_HNSW_M=12`, `CHROMA_HNSW_EF_CONSTRUCTION=400` | 2-5% better recall |
| **Gate reranker warmup** | `src/mcp/main.py:326` | Check `ENABLE_RERANKING` flag before warmup | ~1s faster startup when disabled |
| **Background embedding warmup** | `src/mcp/main.py:317-320` | Move warmup to `asyncio.create_task()` — server accepts requests while warming | ~1.5s faster cold startup |
| **Ollama keep-alive pool** | `src/mcp/utils/internal_llm.py:49` | Increase `max_keepalive_connections=5` → `8` | <5% latency on high concurrency |

### Phase 2 Exit Criteria

- [ ] Sidecar server runs on macOS ARM with CoreML acceleration
- [ ] `start-cerid.sh` detects and reports sidecar status
- [ ] All 4 B-MEDIUM items verified with screenshots
- [ ] HNSW parameters applied, embedding latency re-measured
- [ ] CI green
- [ ] Performance snapshot #2 (compare to Phase 1 baseline)

---

## Phase 3: Platform + Ollama (Week 3)

> Goal: Multi-OS compatibility verified, Ollama wizard UX improved, GUI wired to inference tier.

### 3.1 GUI Integration + Degradation Messaging (Tiered Inference Phase 3)

| Task | Files | Detail |
|------|-------|--------|
| Inference Tier row in Settings | `src/web/src/components/settings/` (ProviderStatus area) | Green/yellow/red indicator per tier: Optimal (GPU), Good (CPU sidecar), Degraded (Docker CPU) |
| Ollama step degraded-mode panel | `src/web/src/components/setup/ollama-step.tsx` | Warning panel when CPU-only: shows install instructions for Ollama + sidecar |
| CPU-only chat banner | `src/web/src/components/chat/` (DegradationBanner area) | Handle `inference_provider_changed` SSE; show once per session (localStorage 24h) |
| InferenceStatus type | `src/web/src/lib/types.ts` | `{ provider, tier, gpu, embed_latency_ms, rerank_latency_ms, message }` |
| Parse inference from /health | `src/web/src/hooks/use-settings.ts` | Extract `inference` field from health response |

### 3.2 Periodic Re-Check + Auto-Switch (Tiered Inference Phase 4)

| Task | Files | Detail |
|------|-------|--------|
| Recheck loop | `utils/inference_config.py` | `_inference_recheck_loop()` coroutine, every 300s. Upgrade/downgrade logic with tier ordering |
| Start loop in lifespan | `main.py` | `asyncio.create_task(_inference_recheck_loop())` |
| Provider switch metric | `routers/observability.py` | Add `inference_provider_switch` to `MetricsCollector` |
| SSE event + toast | `src/web/src/hooks/use-chat.ts` | Listen for `inference_provider_changed`, show upgrade/downgrade toast |

### 3.3 Multi-OS Compatibility (Punch List Section 4)

| Platform | Key Checks | Action Items |
|----------|-----------|--------------|
| **macOS ARM** (primary) | virtiofs Errno 35, Ollama via `host.docker.internal`, GPU passthrough N/A | Wire `virtiofs_retry.py` util into `sync/status.py` (S4.1) + `services/ingestion.py` (S4.2). Verify `HOST_MEMORY_GB` in docker-compose |
| **macOS Intel** | Same as ARM minus Metal | Verify Ollama CPU-only recommendations work |
| **Linux x86_64** | Native Docker, `nvidia-smi` detection, `localhost` Ollama URL | Test `docker compose` v2 plugin. Verify NVIDIA Container Toolkit detection. Add SELinux `:z` mount docs |
| **Linux ARM64** | No AVX2, ONNX ARM64 build | Verify ChromaDB embedding works on ARM. Add `RERANK_ONNX_FILENAME` override if needed |
| **Windows WSL2** | Cross-filesystem mounts, PowerShell script needed | Document `.wslconfig` memory recommendation. Test archive path with `/mnt/c/` paths. Add `start-cerid.ps1` stub or document WSL2 bash requirement |

**Platform-aware OLLAMA_URL:** Auto-detect in `docker-compose.yml`: macOS/Windows → `host.docker.internal:11434`, Linux → `localhost:11434`. The existing auto-detection in `routers/providers.py:153-174` tries both — verify this is reliable.

### 3.4 Ollama Wizard UX (Punch List Section 5 + B37-B40)

| Item | File(s) | Change |
|------|---------|--------|
| **B37: VRAM detection** | `src/mcp/routers/providers.py` (recommendations endpoint) | Linux: parse `nvidia-smi --query-gpu=memory.total --format=csv,noheader`. macOS ARM: unified memory = total RAM (note this). Windows/WSL2: same nvidia-smi |
| **B38: CPU-only penalty** | Same endpoint | If no GPU AND not Apple Silicon → reduce model rec by one tier. Add note: "CPU inference is slower" |
| **B39: Inference speed estimates** | Same endpoint + `ollama-step.tsx` | Add `expected_tokens_per_sec` field per model. Calculate from hardware: Metal=fast, CUDA=fast, CPU=slow. Display in wizard |
| **B40: Download progress** | `src/web/src/components/setup/ollama-step.tsx` | Parse Ollama pull API streaming response. Show progress bar with bytes/total |
| **Wizard resource impact** | `ollama-step.tsx` | Show: "This model uses X GB (Y% of your memory)" |
| **Semi-automated install** | `ollama-step.tsx` | Detect OS → show platform-specific install command (brew/curl/link) |

### Phase 3 Exit Criteria

- [ ] Inference tier displayed in Settings UI with correct indicator color
- [ ] Auto-switch detected when starting/stopping Ollama during runtime
- [ ] virtiofs retry logic wired and tested on macOS
- [ ] Ollama wizard shows VRAM, speed estimates, download progress
- [ ] Linux x86_64 tested with Docker compose v2 (or documented as tested)
- [ ] CI green
- [ ] Performance snapshot #3

---

## Phase 4: Enhancement + Optimization (Week 4)

> Goal: B-LOW items implemented, Ollama embedding path wired, measured performance tuning applied.

### 4.1 Ollama Embedding + LLM Stage Routing (Tiered Inference Phase 5)

| Task | Files | Detail |
|------|-------|--------|
| Ollama embed path | `utils/embeddings.py` | If provider == `ollama`, call `/api/embed`. **Critical:** validate dimensions match `EMBEDDING_DIMENSIONS` (768) before activating |
| Route `ai_categorize()` via internal LLM | `utils/metadata.py:182` | Use `call_internal_llm()` when `INTERNAL_LLM_PROVIDER=ollama` |
| Route `contextualize_chunks()` via internal LLM | `utils/contextual.py:34` | Same pattern |
| Verify existing LLM stage routing | `utils/internal_llm.py`, `config/settings.py:510` | Confirm `PIPELINE_PROVIDERS` dict routes correctly per-stage when Ollama enabled |

### 4.2 B-LOW Items

| Item | File(s) | Change |
|------|---------|--------|
| **B31: Chat query grouping** | Artifact model + KB UI | Add `conversation_id` field to Artifact model. KB UI shows conversation artifacts as expandable groups |
| **B32: Regenerate all synopses** | `routers/health.py` or new endpoint + health tab | `POST /artifacts/regenerate-all-synopses` → iterate artifacts missing synopses, queue LLM calls. Progress indicator in UI |
| **B33: Feedback loop design** | Design doc first | Document: what gets saved, when, how it affects future responses. Current: saves assistant responses to KB. Propose: opt-in per conversation with clear indicator. **Output: design doc, not implementation** |
| **B34: Model management UX** | New component in settings | Virtual scrolling for 350+ models, search/filter by name/provider/capability, sort by name/cost/context length, per-model pricing display |
| **B35: Chinese models via OpenRouter** | Model selector filtering | Don't filter OpenRouter passthrough list. Add disclaimer on Chinese-origin models: "This model is from a non-US provider. Enterprise compliance policies may restrict use." USG compliance = bundled/default only |
| **B36: File picker for storage** | Setup wizard storage step | Replace text input with OS file picker dialog. Web: `<input type="file" webkitdirectory>` polyfill. Electron: `dialog.showOpenDialog` |
| **B41: KB title editing** | `artifact-card.tsx` | Inline-editable title. On save, `PATCH /artifacts/{id}` with new title |

### 4.3 Measured Performance Tuning (Stream E)

Compare Phase 3 snapshot to Phase 1 baseline. Apply targeted optimizations based on actual measurements:

| Condition | If True → Action | Target |
|-----------|------------------|--------|
| Cold startup > 10s | Move embedding warmup to background task; lazy-load reranker | < 8s |
| Embedding latency > 20ms (with sidecar) | Check sidecar GPU detection, ONNX provider selection | < 5ms (GPU), < 15ms (CPU) |
| Query e2e > 2s (simple query) | Profile: is it embedding, retrieval, LLM, or assembly? Add timing logs per stage | < 1.5s |
| Retrieval cache hit rate < 30% | Consider reducing TTL from 30min to 15min for better freshness/hit balance | > 40% hit rate |
| Frontend Lighthouse < 80 | Check lazy loading, prefetch critical panes, optimize images | > 85 |
| MCP container RSS > 2GB | Check for leaked ONNX sessions, unbounded caches | < 1.5GB |

### Phase 4 Exit Criteria

- [ ] Ollama embedding path tested with dimension validation
- [ ] All B-LOW items implemented (B33 = design doc only)
- [ ] Performance tuning applied based on measurements (at least 2 optimizations)
- [ ] CI green
- [ ] Performance snapshot #4

---

## Phase 5: Wiring Checks + Final Audit (3 Days)

> Goal: All 8 subsystem wiring checks pass, tiered inference validated + documented, final audit.

### 5.1 Tiered Inference Validation + Documentation (Phase 6)

| Task | Files | Detail |
|------|-------|--------|
| Inference config tests | `tests/test_inference_config.py` | Platform detection, provider fallback chains, upgrade/downgrade logic |
| Sidecar client tests | `tests/test_inference_sidecar.py` | HTTP contract, embed/rerank, health check |
| Recheck loop tests | `tests/test_inference_recheck.py` | Periodic re-check, auto-switch on Ollama start/stop |
| Benchmarks | Manual or scripted | 100 embed batch-10 calls (p50/p95/p99), 100 rerank-15 calls, 50 e2e queries |
| Update CLAUDE.md | `CLAUDE.md` | Add Tiered Inference section |
| Update OPERATIONS.md | `docs/OPERATIONS.md` | Sidecar startup/troubleshooting |
| Update ROADMAP.md | `docs/ROADMAP.md` | Mark tiered inference complete |

### 5.2 System-Wide Wiring Checks (Punch List Section 3)

Run all 8 subsystem QA scripts from FINAL_PUNCH_LIST_V2 Section 3:

| # | Subsystem | Key Checks |
|---|-----------|-----------|
| **3.1** | Setup Wizard Flow | All 7 steps: welcome, API keys (4 providers preconfigured), storage, Ollama, review, health, try-it-out, mode selection |
| **3.2** | Chat Pipeline | Message → LLM → stream → verification → claims → enrichment → dashboard metrics |
| **3.3** | KB Pipeline | Upload → parse (PDF/DOCX/XLSX/CSV/RTF/MD/TXT/code) → chunk → embed → store → query → retrieve |
| **3.4** | External API Pipeline | Query → classify → route → fetch (DuckDuckGo/Wikipedia) → display → optional save |
| **3.5** | Settings Persistence | Toggle/slider/dropdown changes survive page reload |
| **3.6** | Health Monitoring | Service up/down detection, degradation banner, health score |
| **3.7** | Memory System | Extract → store → recall → search → edit → delete |
| **3.8** | Analytics Pipeline | Event → record → aggregate → dashboard display |

**Format:** Each check produces a pass/fail checklist. Failures get logged as new issues with file:line references.

### 5.3 Final Performance Report

| Metric | Phase 1 Baseline | Phase 5 Final | Delta |
|--------|------------------|--------------|-------|
| Cold startup | _ s | _ s | _ % |
| Embed latency (batch 10) | _ ms | _ ms | _ % |
| Rerank latency (15 docs) | _ ms | _ ms | _ % |
| Query e2e (simple) | _ ms | _ ms | _ % |
| Query e2e (complex) | _ ms | _ ms | _ % |
| Frontend Lighthouse | _ | _ | _ pts |
| MCP container RSS | _ MB | _ MB | _ % |

**Deliverable:** `docs/PERF_REPORT_FINAL.md` with before/after comparisons.

### 5.4 Final Audit Checklist

- [ ] All 41 B-items from consolidated issues addressed (implemented or documented as deferred)
- [ ] Tiered inference Phases 1-6 complete
- [ ] Punch List Sections 3-5 complete
- [ ] All performance optimizations applied and measured
- [ ] CI green on both internal and public repos
- [ ] No regressions: full test suite passes (1740+ Python tests, Vitest frontend)
- [ ] USG compliance check: `grep -rn "deepseek\|qwen\|alibaba" src/ --include="*.py" --include="*.ts"` returns 0
- [ ] Public repo sync: cherry-pick core improvements, verify no Pro content leaks

### Phase 5 Exit Criteria

- [ ] All 8 wiring checks pass with documented evidence
- [ ] Tiered inference benchmarks documented
- [ ] Final performance report written
- [ ] Both repos (internal + public) synced and CI green
- [ ] Version bump committed

---

## Performance Opportunities Summary (Stream E)

Findings from codebase scan, integrated into phases above:

### Currently Well-Optimized (No Action Needed)

| Area | Current State | Assessment |
|------|--------------|------------|
| HTTP connection pooling | `httpx.AsyncClient` with 20 max / 10 keep-alive (OpenRouter), 10/5 (Ollama) | Excellent |
| Semantic cache early-return | Checked before any retrieval work in `query_agent.py:142-155` | Ideal placement |
| Batch DB writes | Single `collection.add()` per ingest, `UNWIND` in Neo4j | No N+1 detected |
| React code splitting | 6 lazy panes, manual vendor chunks, 800KB limit | Well-configured |
| ONNX model lazy loading | Double-checked locking, singleton globals | Thread-safe |
| Neo4j indexes | 5 indexes + 5 unique constraints on hot-path columns | Comprehensive |
| Redis memory policy | 1GB max, LRU eviction | Appropriate |

### Action Items (Integrated Into Phases)

| Priority | Optimization | Phase | Impact |
|----------|-------------|-------|--------|
| HIGH | ChromaDB HNSW tuning (M=12, EF_CONSTRUCTION=400) | Phase 2 | 2-5% better recall |
| MEDIUM | Gate reranker warmup behind feature flag | Phase 2 | ~1s faster startup |
| MEDIUM | Background embedding warmup (async) | Phase 2 | ~1.5s faster cold start |
| MEDIUM | Ollama keep-alive pool 5→8 | Phase 2 | <5% latency improvement |
| LOW | Retrieval cache TTL review (30min → 15min) | Phase 4 | Fresher results |
| LOW | Neo4j composite index on (domain, filename) | Phase 4 | Marginal query improvement |

---

## Cross-Phase Dependencies

```
Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5
  │            │            │            │            │
  │            │            │            │            └─ Wiring checks need
  │            │            │            │               ALL prior phases
  │            │            │            │
  │            │            │            └─ Ollama embed path needs
  │            │            │               InferenceConfig (Phase 1)
  │            │            │
  │            │            └─ GUI integration needs
  │            │               InferenceConfig + sidecar (Phases 1-2)
  │            │
  │            └─ Sidecar needs InferenceConfig (Phase 1)
  │               B-MEDIUM items are independent
  │
  └─ InferenceConfig is the foundation
     B-CRITICAL items are independent
```

**Parallelizable within phases:**
- Phase 1: InferenceConfig (backend) || B-CRITICAL fixes (backend) || Performance baseline (measurement)
- Phase 2: Sidecar (backend) || B-MEDIUM items (frontend) || Performance quick wins (config)
- Phase 3: GUI integration (frontend) || Multi-OS (devops) || Ollama wizard (full-stack)
- Phase 4: Ollama embed (backend) || B-LOW items (mixed) || Performance tuning (measurement)
- Phase 5: Wiring checks (QA) || Documentation (docs) || Final audit (mixed)

---

## Risk Register

| Risk | Mitigation |
|------|-----------|
| ChromaDB dimension mismatch with Ollama embeddings | Validate dimensions match EMBEDDING_DIMENSIONS (768) before activating Ollama embed path. Hard fail if mismatch |
| Sidecar installation complexity per platform | Provide `install-sidecar.sh` with auto-detection. Sidecar is optional — Docker CPU always works |
| virtiofs Errno 35 not reproducible in CI | Test manually on macOS Docker Desktop. Retry util has backoff — safe even if error doesn't occur |
| Windows WSL2 path translation breaks | Document WSL2 requirement clearly. Defer full Windows native support to future release |
| HNSW parameter change requires re-index | ChromaDB applies HNSW params per-collection at creation time. Existing collections keep old params. New collections get new params. Full re-index optional |
| Performance targets not met | Measure first, optimize second. Each phase has a performance snapshot for comparison |

---

## Commit & Push Protocol

After each phase completion:

```bash
# 1. Verify CI
cd ~/Develop/cerid-ai
git push origin main  # triggers 6-job CI

# 2. Sync to public (core improvements only)
git checkout -b sync/phase-N public/main
git cherry-pick <core-commits>  # exclude Pro/BSL content
grep -r "BSL-1.1" --include="*.py" --include="*.json" src/ plugins/ | grep -v "comment\|noqa"  # verify no leaks
git push public sync/phase-N
# Open PR on public repo

# 3. Tag
git tag -a v0.81-phase-N -m "Phase N: <description>"
```

---

*Plan generated 2026-04-05. Supersedes IMPLEMENTATION_PLAN_V2_2026-04-04.*
*Total scope: 4 B-MEDIUM + 11 B-LOW + 6 tiered inference phases + 8 wiring checks + 3 platform targets + 6 performance optimizations = ~45 work items across 4.5 weeks.*
