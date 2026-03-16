# Phase 40: Semantic Cache, Verification OOM, CI/Docker Hardening

> **Date:** 2026-03-16
> **Status:** Design approved
> **Scope:** M1 (semantic cache activation), J1 (verification OOM fix), K1/K2/K3/K7 (CI/Docker hardening)
> **Deferred:** K4 (plugin management UI) — separate brainstorm session

---

## Workstream 1: M1 — Semantic Cache Activation

### Problem

`ENABLE_SEMANTIC_CACHE=true` is set in `docker-compose.yml` but the cache never activates. `get_embedding_function()` returns `None` for the default server-side model (`all-MiniLM-L6-v2`), so `semantic_cache.py` short-circuits before any lookup. The HNSW index is always empty.

### Solution

Switch to client-side Snowflake Arctic Embed M v1.5 (768d, 8192 ctx, MTEB SOTA for its size). The integration code already exists — only configuration and Dockerfile changes are needed.

**Model choice rationale:** Arctic v1.5 over v2.0 because v2.0 ONNX int8 is 296MB vs v1.5's ~80MB, and MTEB-R scores are nearly identical (0.554 vs 0.551). v2.0 adds multilingual support which is unnecessary for an English-only personal KB.

### Files Changed

| File | Change | Lines |
|------|--------|-------|
| `src/mcp/Dockerfile` | Add Arctic ONNX model + tokenizer pre-download | +3 |
| `src/mcp/docker-compose.yml` | Set `EMBEDDING_MODEL` env var | +1 |
| `src/mcp/config/features.py` | Change `SEMANTIC_CACHE_DIM` default 384 → 768 | 1 |
| `src/mcp/config/settings.py` | Change `EMBEDDING_MODEL` default to Arctic v1.5 | 1 |

### Dockerfile Addition

Follows existing reranker pattern — pre-download during image build to avoid cold-start latency:

```dockerfile
RUN python -c "\
from huggingface_hub import hf_hub_download; \
hf_hub_download('Snowflake/snowflake-arctic-embed-m-v1.5', 'onnx/model.onnx'); \
hf_hub_download('Snowflake/snowflake-arctic-embed-m-v1.5', 'tokenizer.json')"
```

### docker-compose.yml Addition

```yaml
environment:
  - EMBEDDING_MODEL=Snowflake/snowflake-arctic-embed-m-v1.5
```

### config/features.py Change

```python
SEMANTIC_CACHE_DIM = int(os.getenv("SEMANTIC_CACHE_DIM", "768"))  # was 384
```

### config/settings.py Change

```python
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Snowflake/snowflake-arctic-embed-m-v1.5")  # was all-MiniLM-L6-v2
```

### What Already Works (No Changes)

- `utils/embeddings.py` — `OnnxEmbeddingFunction` loads ONNX, applies Arctic query prefix (`"Represent this sentence for searching relevant passages: "`), mean pooling + L2 normalization, Matryoshka truncation support
- `deps.py` — `_EmbeddingAwareClient` detects non-default model, auto-injects embedding function into all ChromaDB collection calls
- `utils/semantic_cache.py` — HNSW index uses `embed_fn` from deps, stores/retrieves with configured dimension, Redis persistence every 5 inserts
- All query paths flow through `get_chroma()` which returns the embedding-aware client

### Operational Steps (Post-Deploy)

1. `./scripts/backup-kb.sh` — snapshot before destructive re-ingest
2. Rebuild: `./scripts/start-cerid.sh --build`
3. Clear each domain: `curl -X DELETE localhost:8888/kb-admin/clear-domain/{domain}` for all 6 domains (coding, finance, projects, personal, general, inbox)
4. Re-ingest: run file watcher (`python scripts/watch_ingest.py`) against `~/cerid-archive/`
5. Verify: set `ENABLE_STEP_TIMER=true` temporarily, run two semantically similar queries, confirm `semantic_cache: hit` in MCP container logs

### Risk

Low. All integration code exists. Destructive re-ingest is mitigated by `backup-kb.sh` snapshot.

---

## Workstream 2: J1 — Verification OOM Fix

### Problem

Verification of LLM responses with 10+ factual claims causes MCP container OOM-kill under the memory limit. Each claim verification loads BM25 indices, runs ONNX cross-encoder reranking, and issues ChromaDB vector queries — all memory-intensive. The existing `VERIFY_CLAIM_MAX_CONCURRENT=3` semaphore caps parallelism but leaves only ~160MB headroom at peak.

### Root Cause Analysis

- **BM25 indices:** Already singleton-cached at module level (`_indexes` dict in `bm25.py`) — not the issue
- **ONNX reranker:** Already singleton with thread-safe double-check lock (`reranker.py`) — not the issue
- **Real issue:** Container memory limit (was 2GB, raised to 3GB in Phase 39B) is still tight when 3 concurrent claims each hit external verification fallbacks. Each claim can spawn 2-3 Bifrost HTTP calls, with response buffers + deserialization consuming 150-300MB per claim. Peak: 3 claims × 250MB = 750MB + base process + BM25 indices + ONNX session = ~2.5GB.

### Solution

Two-part fix: increase container limit + add memory-aware safety valve.

### Files Changed

| File | Change | Lines |
|------|--------|-------|
| `src/mcp/docker-compose.yml` | Raise memory limit 3g → 4g | 1 |
| `src/mcp/agents/hallucination/streaming.py` | Add memory-aware guard before semaphore acquire | +12 |
| `src/mcp/config/settings.py` | Add `VERIFY_MEMORY_FLOOR_MB` setting | +3 |
| `src/mcp/requirements.txt` | Add `psutil` (if not already present) | +1 |
| `src/mcp/requirements.lock` | Regenerate with psutil pinned | regen |

### Container Limit Change

```yaml
deploy:
  resources:
    limits:
      cpus: "4"
      memory: 4g  # was 3g
```

### Memory-Aware Guard

In `streaming.py`, add a pre-semaphore memory check. If available container memory drops below `VERIFY_MEMORY_FLOOR_MB` (default 512MB), pause verification until memory frees up. This prevents OOM even with cascading fallbacks.

```python
import psutil

async def _wait_for_memory(floor_mb: int, label: str) -> None:
    """Block until available memory exceeds floor_mb."""
    while psutil.virtual_memory().available < floor_mb * 1024 * 1024:
        logger.warning("Verification paused (%s): available memory below %dMB floor", label, floor_mb)
        await asyncio.sleep(1.0)
```

Called before `async with sem:` in `_verify_indexed()`.

### Settings Addition

```python
# Minimum available memory (MB) before allowing a new claim verification
VERIFY_MEMORY_FLOOR_MB = int(os.getenv("VERIFY_MEMORY_FLOOR_MB", "512"))
```

### Why Not More Complex Solutions

- Singleton ONNX/BM25 already exists — verified in exploration
- `gc.collect()` after each claim adds ~50ms latency for marginal benefit
- Streaming path already uses `asyncio.as_completed()` — task creation is fine
- Batch processing restructure would require significant refactor of the streaming SSE pipeline for minimal gain
- The real fix is adequate headroom (4GB on 160GB host is trivial) + a safety valve

---

## Workstream 3: CI/Docker Hardening (K1, K2, K3, K7)

### K1: Codecov Integration

**Problem:** CI generates `coverage.xml` but doesn't upload to Codecov for PR coverage gates.

**Fix:** Add `codecov/codecov-action@v5` step after test job in `.github/workflows/ci.yml`.

```yaml
- name: Upload coverage to Codecov
  if: always()
  uses: codecov/codecov-action@v5
  with:
    files: coverage.xml
    fail_ci_if_error: false
```

Note: Requires `CODECOV_TOKEN` secret in GitHub repo settings. `fail_ci_if_error: false` prevents CI breakage if Codecov is down.

### K2: Dependency License Scanning

**Problem:** No license compatibility scanning. GPL-incompatible deps could slip in.

**Fix:** Add two steps to CI security job:

```yaml
- name: License audit (Python)
  run: |
    pip install pip-licenses
    pip-licenses --fail-on="GPL-2.0-only;GPL-3.0-only" --format=table

- name: License audit (Node)
  working-directory: src/web
  run: npx license-checker --failOn "GPL-2.0;GPL-3.0" --production
```

### K3: ReDoS Regex Audit

**Problem:** No automated check for catastrophic backtracking in regex patterns.

**Fix:** Add `dlint` check to CI security job. `dlint DUO138` specifically catches ReDoS-vulnerable patterns in Python.

```yaml
- name: ReDoS regex audit
  run: |
    pip install dlint
    python -m flake8 --select=DUO138 src/mcp/
```

### K7: Multi-Stage MCP Dockerfile

**Problem:** Single-stage Dockerfile includes build dependencies (gcc, build-essential) in final image. ~200MB wasted.

**Fix:** Convert to 3-stage build:

```dockerfile
# Stage 1: Builder — install Python deps with build tools
FROM python:3.11.14-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc build-essential libffi-dev
COPY requirements.lock .
RUN pip install --require-hashes --no-cache-dir -r requirements.lock \
    && pip install huggingface_hub

# Stage 2: Models — pre-download ONNX models
FROM builder AS models
RUN python -c "\
from huggingface_hub import hf_hub_download; \
hf_hub_download('cross-encoder/ms-marco-MiniLM-L-6-v2', 'onnx/model.onnx'); \
hf_hub_download('cross-encoder/ms-marco-MiniLM-L-6-v2', 'onnx/model_quint8_avx2.onnx'); \
hf_hub_download('cross-encoder/ms-marco-MiniLM-L-6-v2', 'tokenizer.json'); \
hf_hub_download('Snowflake/snowflake-arctic-embed-m-v1.5', 'onnx/model.onnx'); \
hf_hub_download('Snowflake/snowflake-arctic-embed-m-v1.5', 'tokenizer.json')"

# Stage 3: Runtime — slim image with only what's needed
FROM python:3.11.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 cerid
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=models /root/.cache/huggingface /home/cerid/.cache/huggingface
RUN chown -R cerid:cerid /home/cerid/.cache
COPY --chown=cerid:cerid . .
USER cerid
EXPOSE 8888
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"]
```

Build tools never reach runtime stage. Estimated ~200MB image size reduction.

---

## Issue Tracker Updates

After implementation, update `docs/ISSUES.md`:
- **M1:** Mark resolved with Phase 40 reference
- **J1:** Mark resolved (container limit + memory guard)
- **K1, K2, K3, K7:** Mark resolved
- **K4:** Remains open (deferred to separate session)
- Update header: "6 open → 1 open (K4), 94 resolved"

## Test Plan

- **M1:** Rebuild image, verify embedding function activates (`get_embedding_function() is not None`), re-ingest sample artifacts, run two similar queries, confirm cache hit in logs
- **J1:** Run verification on a response with 10+ claims, monitor container memory via `docker stats`, confirm no OOM
- **K1:** Push branch, verify Codecov upload appears in CI logs
- **K2:** Intentionally add a GPL-2.0 dep in a test branch, verify CI fails
- **K3:** Verify `dlint DUO138` runs cleanly on existing codebase
- **K7:** Compare `docker images` size before/after multi-stage conversion
