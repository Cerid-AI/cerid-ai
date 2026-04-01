# Phase 40: Semantic Cache, Verification OOM, CI/Docker Hardening — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate semantic cache (M1), fix verification OOM (J1), harden CI/Docker (K1/K2/K3/K7).

**Architecture:** Three independent workstreams touching shared infrastructure files. Tasks ordered to avoid conflicts: Dockerfile + docker-compose changes batched in Task 1-2, CI changes in Task 5, verification fix in Task 3-4.

**Tech Stack:** Python 3.11, FastAPI, ONNX Runtime, ChromaDB, Docker multi-stage builds, GitHub Actions

**Spec:** [`docs/superpowers/specs/2026-03-16-phase40-open-items-design.md`](../specs/2026-03-16-phase40-open-items-design.md)

---

## File Map

| File | Task | Action |
|------|------|--------|
| `src/mcp/Dockerfile` | 1 | Rewrite → 3-stage (builder, models, runtime) |
| `src/mcp/docker-compose.yml` | 2 | Modify → add env vars, raise memory 3G→4G |
| `src/mcp/config/settings.py` | 2, 4 | Modify → change EMBEDDING_MODEL default, add VERIFY_MEMORY_FLOOR_MB |
| `src/mcp/config/features.py` | — | No change needed (SEMANTIC_CACHE_DIM not in this file — spec was incorrect) |
| `src/mcp/utils/semantic_cache.py` | 2 | Modify → change _HNSW_DIM default 384→768 |
| `src/mcp/agents/hallucination/streaming.py` | 3 | Modify → add cgroup-aware memory guard |
| `src/mcp/tests/test_memory_guard.py` | 3 | Create → unit tests for memory guard |
| `src/mcp/tests/test_semantic_cache_dim.py` | 2 | Create → verify dim config propagation |
| `.github/workflows/ci.yml` | 5 | Modify → add Codecov, license scan, ReDoS audit |
| `docs/ISSUES.md` | 6 | Modify → mark M1, J1, K1-K3, K7 resolved |
| `tasks/todo.md` | 6 | Modify → add Phase 40 completion section |

---

## Chunk 1: Infrastructure (Tasks 1-2)

### Task 1: Multi-Stage MCP Dockerfile (K7)

**Files:**
- Modify: `src/mcp/Dockerfile` (full rewrite, currently 31 lines)

- [ ] **Step 1: Read the current Dockerfile to confirm starting state**

```bash
cat src/mcp/Dockerfile
```

Expected: 31-line single-stage Dockerfile with `python:3.11.14-slim` base, gcc install+purge, cerid user, reranker model pre-download.

- [ ] **Step 2: Rewrite Dockerfile as 3-stage build**

Replace the entire `src/mcp/Dockerfile` with:

```dockerfile
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Stage 1: Builder — install Python deps with build tools
FROM python:3.11.14-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip setuptools

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Stage 2: Models — pre-download ONNX models into HuggingFace cache
FROM builder AS models

RUN python -c "\
from huggingface_hub import hf_hub_download; \
hf_hub_download('cross-encoder/ms-marco-MiniLM-L-6-v2', 'onnx/model.onnx'); \
hf_hub_download('cross-encoder/ms-marco-MiniLM-L-6-v2', 'onnx/model_quint8_avx2.onnx'); \
hf_hub_download('cross-encoder/ms-marco-MiniLM-L-6-v2', 'tokenizer.json'); \
hf_hub_download('Snowflake/snowflake-arctic-embed-m-v1.5', 'onnx/model.onnx'); \
hf_hub_download('Snowflake/snowflake-arctic-embed-m-v1.5', 'tokenizer.json')"

# Stage 3: Runtime — slim image without build tools
FROM python:3.11.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 cerid

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=models /root/.cache/huggingface /home/cerid/.cache/huggingface
RUN chown -R cerid:cerid /home/cerid/.cache

WORKDIR /app
COPY --chown=cerid:cerid . .

USER cerid
EXPOSE 8888
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"]
```

Key changes from original:
- 3-stage build eliminates gcc/build-essential from runtime (~200MB savings)
- Models stage downloads both reranker AND Arctic embedding ONNX (M1 requirement)
- `WORKDIR /app` in runtime stage (was missing — review finding)
- `curl` kept in runtime for healthcheck

- [ ] **Step 3: Verify Dockerfile builds locally**

```bash
docker build -t cerid-mcp-test src/mcp/
```

Expected: Successful build. Note the image size for comparison.

- [ ] **Step 4: Compare image sizes**

```bash
docker images cerid-mcp-test --format "table {{.Repository}}\t{{.Size}}"
```

Expected: Runtime image should be ~200MB smaller than the previous single-stage image.

- [ ] **Step 5: Commit**

```bash
git add src/mcp/Dockerfile
git commit -m "build: convert MCP Dockerfile to multi-stage (K7)

Three stages: builder (deps + build tools), models (ONNX pre-download),
runtime (slim image). Eliminates gcc/build-essential from final image.
Adds Snowflake Arctic Embed M v1.5 ONNX model pre-download for M1.
"
```

---

### Task 2: Semantic Cache Config + Container Limit (M1 + J1 config)

**Files:**
- Modify: `src/mcp/docker-compose.yml:25-37`
- Modify: `src/mcp/config/settings.py:122`
- Modify: `src/mcp/utils/semantic_cache.py:42`
- Create: `src/mcp/tests/test_semantic_cache_dim.py`

- [ ] **Step 1: Write failing test — verify SEMANTIC_CACHE_DIM defaults to 768**

Create `src/mcp/tests/test_semantic_cache_dim.py`:

```python
"""Verify semantic cache dimension matches Arctic embedding model (768d)."""
import os


def test_semantic_cache_dim_default_is_768():
    """SEMANTIC_CACHE_DIM should default to 768 for Snowflake Arctic Embed M v1.5."""
    # Remove env var if set, to test the code default
    env_backup = os.environ.pop("SEMANTIC_CACHE_DIM", None)
    try:
        # Re-import to pick up the default
        import importlib
        import utils.semantic_cache as sc_mod
        importlib.reload(sc_mod)
        assert sc_mod._HNSW_DIM == 768, f"Expected 768, got {sc_mod._HNSW_DIM}"
    finally:
        if env_backup is not None:
            os.environ["SEMANTIC_CACHE_DIM"] = env_backup


def test_semantic_cache_dim_overridable_via_env(monkeypatch):
    """SEMANTIC_CACHE_DIM should be overridable via environment variable."""
    monkeypatch.setenv("SEMANTIC_CACHE_DIM", "256")
    import importlib
    import utils.semantic_cache as sc_mod
    importlib.reload(sc_mod)
    assert sc_mod._HNSW_DIM == 256


def test_embedding_model_default_is_arctic():
    """EMBEDDING_MODEL should default to Snowflake Arctic v1.5."""
    env_backup = os.environ.pop("EMBEDDING_MODEL", None)
    try:
        import importlib
        import config.settings as settings_mod
        importlib.reload(settings_mod)
        assert settings_mod.EMBEDDING_MODEL == "Snowflake/snowflake-arctic-embed-m-v1.5"
    finally:
        if env_backup is not None:
            os.environ["EMBEDDING_MODEL"] = env_backup
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/mcp && python -m pytest tests/test_semantic_cache_dim.py -v
```

Expected: FAIL — `_HNSW_DIM` is currently 384, `EMBEDDING_MODEL` is `all-MiniLM-L6-v2`.

- [ ] **Step 3: Update semantic_cache.py — change _HNSW_DIM default**

In `src/mcp/utils/semantic_cache.py`, line 42, change:

```python
# Before:
_HNSW_DIM = int(os.getenv("SEMANTIC_CACHE_DIM", "384"))  # MiniLM-L6 default

# After:
_HNSW_DIM = int(os.getenv("SEMANTIC_CACHE_DIM", "768"))  # Arctic Embed M v1.5 default
```

- [ ] **Step 4: Update settings.py — change EMBEDDING_MODEL default**

In `src/mcp/config/settings.py`, line 122, change:

```python
# Before:
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

# After:
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Snowflake/snowflake-arctic-embed-m-v1.5")
```

- [ ] **Step 5: Update docker-compose.yml — add env vars + raise memory**

In `src/mcp/docker-compose.yml`:

After line 26 (`RERANK_ONNX_FILENAME=onnx/model_quint8_avx2.onnx`), add:

```yaml
      - EMBEDDING_MODEL=Snowflake/snowflake-arctic-embed-m-v1.5
      - SEMANTIC_CACHE_DIM=768
```

On line 37, change:

```yaml
# Before:
          memory: 3G

# After:
          memory: 4G
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd src/mcp && python -m pytest tests/test_semantic_cache_dim.py -v
```

Expected: All 3 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/mcp/utils/semantic_cache.py src/mcp/config/settings.py src/mcp/docker-compose.yml src/mcp/tests/test_semantic_cache_dim.py
git commit -m "feat(M1): activate semantic cache with Arctic Embed M v1.5

- Change EMBEDDING_MODEL default to Snowflake/snowflake-arctic-embed-m-v1.5
- Change _HNSW_DIM default from 384 to 768 (Arctic output dimension)
- Add EMBEDDING_MODEL + SEMANTIC_CACHE_DIM env vars to docker-compose.yml
- Raise MCP container memory limit from 3G to 4G (J1 partial fix)
- Add 3 tests verifying dimension config propagation
"
```

---

## Chunk 2: Verification OOM Fix (Tasks 3-4)

### Task 3: Cgroup-Aware Memory Guard (J1)

**Files:**
- Modify: `src/mcp/agents/hallucination/streaming.py:247-272`
- Modify: `src/mcp/config/settings.py` (add setting after line 197)
- Create: `src/mcp/tests/test_memory_guard.py`

- [ ] **Step 1: Write failing tests for memory guard**

Create `src/mcp/tests/test_memory_guard.py`:

```python
"""Tests for cgroup-aware memory guard in verification streaming."""
import asyncio
from unittest.mock import patch, MagicMock
import pytest


def test_container_memory_available_returns_none_outside_container():
    """When cgroup files don't exist, return None (no-op guard)."""
    from agents.hallucination.streaming import _container_memory_available_mb
    # On host (no cgroup files), should return None
    result = _container_memory_available_mb()
    # Could be None (no cgroup) or a float (if running in container)
    assert result is None or isinstance(result, float)


def test_container_memory_available_parses_cgroup_files(tmp_path):
    """When cgroup files exist, correctly compute available MB."""
    from agents.hallucination.streaming import _container_memory_available_mb
    max_file = tmp_path / "memory.max"
    current_file = tmp_path / "memory.current"

    # 4GB limit, 2.5GB used = 1.5GB available = 1536 MB
    max_file.write_text("4294967296\n")  # 4 * 1024^3
    current_file.write_text("2684354560\n")  # 2.5 * 1024^3

    with patch("agents.hallucination.streaming._CGROUP_MEMORY_MAX", max_file), \
         patch("agents.hallucination.streaming._CGROUP_MEMORY_CURRENT", current_file):
        result = _container_memory_available_mb()

    assert result is not None
    assert abs(result - 1536.0) < 1.0  # ~1536 MB


def test_container_memory_available_returns_none_for_unlimited(tmp_path):
    """When cgroup memory.max is 'max' (no limit), return None."""
    from agents.hallucination.streaming import _container_memory_available_mb
    max_file = tmp_path / "memory.max"
    current_file = tmp_path / "memory.current"

    max_file.write_text("max\n")
    current_file.write_text("1073741824\n")

    with patch("agents.hallucination.streaming._CGROUP_MEMORY_MAX", max_file), \
         patch("agents.hallucination.streaming._CGROUP_MEMORY_CURRENT", current_file):
        result = _container_memory_available_mb()

    assert result is None


@pytest.mark.asyncio
async def test_wait_for_memory_noop_outside_container():
    """_wait_for_memory should return immediately when not in a container."""
    from agents.hallucination.streaming import _wait_for_memory
    with patch("agents.hallucination.streaming._container_memory_available_mb", return_value=None):
        # Should not block
        await asyncio.wait_for(_wait_for_memory(512, "test"), timeout=1.0)


@pytest.mark.asyncio
async def test_wait_for_memory_blocks_when_low():
    """_wait_for_memory should block when available memory is below floor."""
    import agents.hallucination.streaming as streaming_mod
    from agents.hallucination.streaming import _wait_for_memory
    call_count = 0

    def mock_available():
        nonlocal call_count
        call_count += 1
        # First 2 calls: low memory. Third call: enough memory.
        return 256.0 if call_count <= 2 else 1024.0

    async def fast_sleep(duration):
        pass  # Skip actual sleep in tests

    with patch("agents.hallucination.streaming._container_memory_available_mb", side_effect=mock_available), \
         patch.object(streaming_mod.asyncio, "sleep", fast_sleep):
        await asyncio.wait_for(_wait_for_memory(512, "test"), timeout=5.0)

    assert call_count == 3  # 2 low + 1 sufficient
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd src/mcp && python -m pytest tests/test_memory_guard.py -v
```

Expected: FAIL — `_container_memory_available_mb` and `_wait_for_memory` don't exist yet.

- [ ] **Step 3: Add VERIFY_MEMORY_FLOOR_MB setting**

In `src/mcp/config/settings.py`, after line 197 (`VERIFY_CLAIM_MAX_CONCURRENT = ...`), add:

```python
# Minimum available container memory (MB) before allowing a new claim verification.
# Uses cgroup v2 files — no-op when running outside a memory-limited container.
VERIFY_MEMORY_FLOOR_MB = int(os.getenv("VERIFY_MEMORY_FLOOR_MB", "512"))
```

- [ ] **Step 4: Implement cgroup memory guard in streaming.py**

In `src/mcp/agents/hallucination/streaming.py`, add these imports and functions near the top of the file (after existing imports):

```python
import pathlib

_CGROUP_MEMORY_MAX = pathlib.Path("/sys/fs/cgroup/memory.max")
_CGROUP_MEMORY_CURRENT = pathlib.Path("/sys/fs/cgroup/memory.current")


def _container_memory_available_mb() -> float | None:
    """Return available memory in MB within the container cgroup, or None if not in a cgroup."""
    try:
        max_bytes = _CGROUP_MEMORY_MAX.read_text().strip()
        if max_bytes == "max":
            return None  # no limit set
        current_bytes = int(_CGROUP_MEMORY_CURRENT.read_text().strip())
        return (int(max_bytes) - current_bytes) / (1024 * 1024)
    except (FileNotFoundError, ValueError):
        return None  # not running in a cgroup-limited container


async def _wait_for_memory(floor_mb: int, label: str) -> None:
    """Block until available container memory exceeds floor_mb. No-op outside containers."""
    while True:
        available = _container_memory_available_mb()
        if available is None or available >= floor_mb:
            return
        logger.warning(
            "Verification paused (%s): container memory %.0fMB < %dMB floor",
            label, available, floor_mb,
        )
        await asyncio.sleep(1.0)
```

Then modify `_verify_indexed()` (around line 247-251) to call the guard before the semaphore:

```python
    async def _verify_indexed(idx: int, claim_text: str) -> tuple[int, dict[str, Any]]:
        """Verify a single claim with a per-claim timeout and concurrency limit."""
        await _wait_for_memory(config.VERIFY_MEMORY_FLOOR_MB, f"claim-{idx}")
        sem = _get_claim_verify_semaphore()
        try:
            async with sem:
                # ... rest unchanged
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd src/mcp && python -m pytest tests/test_memory_guard.py -v
```

Expected: All 5 tests PASS.

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
cd src/mcp && python -m pytest tests/ -v --tb=short -x
```

Expected: All 1340+ tests PASS (no regressions).

- [ ] **Step 7: Commit**

```bash
git add src/mcp/agents/hallucination/streaming.py src/mcp/config/settings.py src/mcp/tests/test_memory_guard.py
git commit -m "fix(J1): add cgroup-aware memory guard for verification OOM

Add _container_memory_available_mb() that reads cgroup v2 files to check
container memory headroom before allowing a new claim verification. Falls
back to no-op when running outside Docker. Add VERIFY_MEMORY_FLOOR_MB
setting (default 512MB). Combined with the 3G→4G container limit bump in
the previous commit, this prevents OOM with 10+ concurrent claim verifications.

5 new tests covering cgroup parsing, unlimited detection, and async guard behavior.
"
```

---

### Task 4: Verify J1 OOM fix end-to-end

- [ ] **Step 1: Rebuild and start the stack**

```bash
./scripts/start-cerid.sh --build
```

Expected: All services start. MCP container now has 4G memory limit.

- [ ] **Step 2: Confirm container memory limit**

```bash
docker inspect ai-companion-mcp --format '{{.HostConfig.Memory}}'
```

Expected: `4294967296` (4GB in bytes).

- [ ] **Step 3: Test verification with 10+ claims (if stack is running)**

This step is manual / requires a running stack with an active KB. Skip if not currently running.

```bash
curl -s http://localhost:8888/health | jq .
```

If healthy, trigger a verification on a response known to have many claims and monitor:

```bash
docker stats ai-companion-mcp --no-stream
```

Expected: Memory usage stays well below 4GB during verification.

---

## Chunk 3: CI Hardening + Wrap-up (Tasks 5-6)

### Task 5: CI Pipeline Additions (K1, K2, K3)

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add Codecov upload to test job (K1)**

In `.github/workflows/ci.yml`, after line 47 (the pytest step), add:

```yaml
      - name: Upload coverage to Codecov
        if: success()
        uses: codecov/codecov-action@v5
        with:
          files: src/mcp/coverage.xml
          fail_ci_if_error: false
```

Note: The `coverage.xml` path needs `src/mcp/` prefix because the working directory for the pytest step is `src/mcp` but the action runs from repo root.

- [ ] **Step 2: Add license scanning (K2)**

**Python license audit** — add to `test` job after the Codecov step (Python deps already installed):

```yaml
      - name: License audit (Python)
        run: |
          pip install pip-licenses
          pip-licenses --fail-on="GPL-2.0-only;GPL-3.0-only" --format=table
```

**Node license audit** — add to `frontend` job after line 160 (`run: npm audit --audit-level=high`). Node deps are already installed via `npm ci` in this job:

```yaml
      - name: License audit (Node)
        run: npx license-checker --failOn "GPL-2.0;GPL-3.0" --production
```

- [ ] **Step 3: Add ReDoS regex audit to security job (K3)**

In `.github/workflows/ci.yml`, after line 106 (end of pip-audit step), add:

```yaml
      - name: ReDoS regex audit (dlint)
        run: |
          pip install dlint flake8
          python -m flake8 --select=DUO138 src/mcp/
```

- [ ] **Step 4: Run a local syntax check on the workflow**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "YAML valid"
```

Expected: "YAML valid"

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add Codecov upload, license scanning, ReDoS audit (K1/K2/K3)

K1: Upload coverage.xml to Codecov after test job (if: success())
K2: pip-licenses in test job, license-checker in frontend job
K3: dlint DUO138 ReDoS regex audit in security job
"
```

---

### Task 6: Update Issue Tracker + Task Tracker

**Files:**
- Modify: `docs/ISSUES.md`
- Modify: `tasks/todo.md`

- [ ] **Step 1: Update ISSUES.md — mark resolved items**

In `docs/ISSUES.md`:

1. Update header (line 5): change open count to `1 open` and resolved count to `94 resolved`. **Note:** Verify the exact current wording before editing — CLAUDE.md says "6 open" but the actual ISSUES.md header may differ.
2. M1 (line 987): Change `🔓 Open (2026-03-16)` → `✅ Resolved (Phase 40, 2026-03-16)`. Add resolution note: "Switched to Snowflake Arctic Embed M v1.5 (client-side ONNX, 768d). Updated SEMANTIC_CACHE_DIM default to 768. Multi-stage Dockerfile bakes model at build time. Requires destructive KB re-ingest."
3. J1 (line 827): Change `🔓 Open (Phase 35, 2026-03-10)` → `✅ Resolved (Phase 40, 2026-03-16)`. Add resolution note: "Raised container memory 3G→4G. Added cgroup-aware memory guard (_wait_for_memory) that pauses verification when available container memory drops below 512MB."
4. K1 (line 858): Change `🔲 Open` → `✅ Resolved (Phase 40, 2026-03-16)`. Note: "Added codecov/codecov-action@v5 to test job."
5. K2 (line 864): Change `🔲 Open` → `✅ Resolved (Phase 40, 2026-03-16)`. Note: "Added pip-licenses + license-checker to CI."
6. K3 (line 870): Change `🔲 Open` → `✅ Resolved (Phase 40, 2026-03-16)`. Note: "Added dlint DUO138 ReDoS audit to CI security job."
7. K7 (line 896): Change `🔲 Open` → `✅ Resolved (Phase 40, 2026-03-16)`. Note: "Converted to 3-stage Dockerfile (builder, models, runtime)."
8. Update Priority Order section (line 1009-1013): Remove all resolved items, leave only K4.

- [ ] **Step 2: Update tasks/todo.md — add Phase 40 section**

At the top of `tasks/todo.md` (after the header), add:

```markdown
## Phase 40: Semantic Cache, Verification OOM, CI/Docker Hardening (2026-03-16) ✅

### M1: Semantic Cache Activation
- [x] Multi-stage Dockerfile with Arctic Embed M v1.5 ONNX pre-download
- [x] Switch EMBEDDING_MODEL default to Snowflake/snowflake-arctic-embed-m-v1.5
- [x] Update _HNSW_DIM default from 384 to 768
- [x] Add EMBEDDING_MODEL + SEMANTIC_CACHE_DIM env vars to docker-compose.yml
- [x] Add 3 dimension config tests

### J1: Verification OOM Fix
- [x] Raise MCP container memory limit 3G → 4G
- [x] Add cgroup-aware memory guard (_wait_for_memory) with VERIFY_MEMORY_FLOOR_MB=512
- [x] Add 5 memory guard tests

### CI/Docker Hardening
- [x] K7: Convert MCP Dockerfile to 3-stage build (~200MB image reduction)
- [x] K1: Add Codecov upload to CI test job
- [x] K2: Add pip-licenses (Python) + license-checker (Node) to CI
- [x] K3: Add dlint DUO138 ReDoS regex audit to CI security job

### Operational (Post-Deploy)
- [ ] Run `./scripts/backup-kb.sh` before re-ingest
- [ ] Clear all 6 domains via `/kb-admin/clear-domain/{domain}`
- [ ] Re-ingest from `~/cerid-archive/` via file watcher
- [ ] Verify semantic cache activates: `ENABLE_STEP_TIMER=true`, check for `semantic_cache: hit`

---
```

- [ ] **Step 3: Commit**

```bash
git add docs/ISSUES.md tasks/todo.md
git commit -m "docs: mark M1, J1, K1-K3, K7 resolved in Phase 40

Update ISSUES.md: open count → 1 open (K4 remains), 94 resolved.
Update tasks/todo.md with Phase 40 completion section and
post-deploy operational checklist for KB re-ingest.
"
```

---

## Post-Implementation: KB Re-Ingest (Manual)

These steps are performed manually after deployment, not by the agentic worker:

1. `./scripts/backup-kb.sh`
2. `./scripts/start-cerid.sh --build`
3. For each domain (coding, finance, projects, personal, general, inbox):
   ```bash
   curl -X DELETE http://localhost:8888/kb-admin/clear-domain/coding
   curl -X DELETE http://localhost:8888/kb-admin/clear-domain/finance
   curl -X DELETE http://localhost:8888/kb-admin/clear-domain/projects
   curl -X DELETE http://localhost:8888/kb-admin/clear-domain/personal
   curl -X DELETE http://localhost:8888/kb-admin/clear-domain/general
   curl -X DELETE http://localhost:8888/kb-admin/clear-domain/inbox
   ```
4. Re-ingest: `cd src/mcp && python scripts/watch_ingest.py`
5. Verify cache: Set `ENABLE_STEP_TIMER=true` in docker-compose.yml, rebuild, run two similar queries, check logs for `semantic_cache: hit`.
