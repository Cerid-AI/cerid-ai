# Tiered Inference Architecture

> Automatic detection and selection of the fastest available inference provider for embeddings, reranking, and LLM pipeline stages across all supported platforms.

**Version:** 1.0 | **Date:** 2026-04-05 | **Status:** Specification

---

## Table of Contents

1. [Provider Decision Tree](#1-provider-decision-tree)
2. [FastEmbed Sidecar (Option 3)](#2-fastembed-sidecar-option-3)
3. [Degraded Mode (Option 4)](#3-degraded-mode-option-4)
4. [Function Offloading Matrix](#4-function-offloading-matrix)
5. [Self-Optimization Logic](#5-self-optimization-logic)
6. [Implementation Phases](#6-implementation-phases)

---

## 1. Provider Decision Tree

At startup the system probes the host environment and selects the best available inference provider for each workload class. The decision tree runs once during `main.py` initialization and again on periodic re-check.

### 1.1 Embedding Provider Selection

```
                        ┌──────────────────┐
                        │  Detect Platform  │
                        └────────┬─────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                   ▼
         ┌─────────┐      ┌──────────┐        ┌──────────┐
         │  macOS   │      │  Linux   │        │ Windows  │
         └────┬────┘      └────┬─────┘        └────┬─────┘
              │                │                    │
       ┌──────┴──────┐   ┌────┴─────┐         ┌───┴────┐
       ▼             ▼   ▼          ▼         ▼        ▼
   Apple Silicon   Intel  NVIDIA  AMD/CPU   WSL2+GPU  WSL2 CPU
       │             │      │       │          │        │
       ▼             ▼      ▼       ▼          ▼        ▼
   (see below)   (see below) ...   ...        ...      ...
```

### 1.2 Per-Platform Fallback Chains

Each platform follows a strict priority order. The system tries Option 1 first; if unavailable, falls through to the next.

#### macOS — Apple Silicon (M1/M2/M3/M4)

| Priority | Provider | Backend | Expected Perf | Detection |
|----------|----------|---------|---------------|-----------|
| **Option 1** | Ollama | Metal GPU via `/api/embed` | ~3ms/batch-10 (768-dim) | `curl -s http://{OLLAMA_URL}/api/tags` returns 200 |
| **Option 2** | FastEmbed sidecar | `onnxruntime-silicon` (CoreML/Metal) | ~5ms/batch-10 | Sidecar health check at `http://localhost:8889/health` |
| **Option 3** | ONNX in-process (host) | `CoreMLExecutionProvider` | ~5ms/batch-10 | `ort.get_available_providers()` includes `CoreMLExecutionProvider` |
| **Option 4** | ONNX Docker CPU | `CPUExecutionProvider` | ~15-25ms/batch-10 | Always available (current default) |

#### macOS — Intel + AMD Radeon

| Priority | Provider | Backend | Expected Perf | Detection |
|----------|----------|---------|---------------|-----------|
| **Option 1** | Ollama | CPU (no GPU accel on Intel Mac) | ~20ms/batch-10 | Health check |
| **Option 2** | FastEmbed sidecar | `onnxruntime` (CPU, AVX2) | ~12ms/batch-10 | Sidecar health |
| **Option 3** | ONNX in-process | `CPUExecutionProvider` | ~15ms/batch-10 | Always available |
| **Option 4** | ONNX Docker CPU | `CPUExecutionProvider` | ~20-30ms/batch-10 | Always available |

#### Linux — NVIDIA GPU

| Priority | Provider | Backend | Expected Perf | Detection |
|----------|----------|---------|---------------|-----------|
| **Option 1** | Ollama | CUDA | ~2ms/batch-10 | Health check + `nvidia-smi` exits 0 |
| **Option 2** | FastEmbed sidecar | `onnxruntime-gpu` (CUDA) | ~3ms/batch-10 | Sidecar health |
| **Option 3** | ONNX in-process | `CUDAExecutionProvider` | ~3ms/batch-10 | `ort.get_available_providers()` includes `CUDAExecutionProvider` |
| **Option 4** | ONNX Docker CPU | `CPUExecutionProvider` | ~15-25ms/batch-10 | Always available |

#### Linux — AMD GPU (ROCm)

| Priority | Provider | Backend | Expected Perf | Detection |
|----------|----------|---------|---------------|-----------|
| **Option 1** | Ollama | ROCm | ~4ms/batch-10 | Health check + `rocm-smi` exits 0 |
| **Option 2** | FastEmbed sidecar | `onnxruntime` + MIGraphX EP | ~6ms/batch-10 | Sidecar health |
| **Option 3** | ONNX in-process | `MIGraphXExecutionProvider` | ~6ms/batch-10 | Provider available check |
| **Option 4** | ONNX Docker CPU | `CPUExecutionProvider` | ~15-25ms/batch-10 | Always available |

#### Linux — CPU-only

| Priority | Provider | Backend | Expected Perf | Detection |
|----------|----------|---------|---------------|-----------|
| **Option 1** | Ollama | CPU (AVX2/AVX-512) | ~18ms/batch-10 | Health check |
| **Option 2** | FastEmbed sidecar | `onnxruntime` (CPU) | ~12ms/batch-10 | Sidecar health |
| **Option 3** | ONNX in-process | `CPUExecutionProvider` | ~15ms/batch-10 | Always available |
| **Option 4** | ONNX Docker CPU | `CPUExecutionProvider` | ~20-30ms/batch-10 | Always available |

#### Windows WSL2 — NVIDIA GPU

| Priority | Provider | Backend | Expected Perf | Detection |
|----------|----------|---------|---------------|-----------|
| **Option 1** | Ollama | CUDA via WSL2 GPU passthrough | ~3ms/batch-10 | Health check + `nvidia-smi` in WSL |
| **Option 2** | FastEmbed sidecar | `onnxruntime-gpu` or `onnxruntime-directml` | ~4ms/batch-10 | Sidecar health |
| **Option 3** | ONNX in-process | `DmlExecutionProvider` | ~5ms/batch-10 | Provider available check |
| **Option 4** | ONNX Docker CPU | `CPUExecutionProvider` | ~20-30ms/batch-10 | Always available |

#### Windows WSL2 — CPU-only

| Priority | Provider | Backend | Expected Perf | Detection |
|----------|----------|---------|---------------|-----------|
| **Option 1** | FastEmbed sidecar | `onnxruntime` (CPU) | ~12ms/batch-10 | Sidecar health |
| **Option 2** | ONNX in-process | `CPUExecutionProvider` | ~15ms/batch-10 | Always available |
| **Option 3** | ONNX Docker CPU | `CPUExecutionProvider` | ~20-30ms/batch-10 | Always available |

> **Note:** Ollama is not listed for Windows CPU-only because without GPU passthrough the benefit over in-process ONNX is marginal and adds HTTP overhead.

### 1.3 Detection Pseudocode

```python
def detect_embedding_provider() -> InferenceProvider:
    """Run at startup. Returns best available provider."""

    platform = detect_platform()  # -> Platform enum

    # Option 1: Ollama (except Windows CPU-only)
    if platform != Platform.WINDOWS_CPU:
        if probe_ollama_health():
            gpu = detect_gpu(platform)
            if gpu or platform in (Platform.MACOS_ARM, Platform.LINUX_CPU):
                return InferenceProvider(
                    name="ollama",
                    backend=gpu.backend if gpu else "cpu",
                    url=OLLAMA_URL,
                    tier="gpu" if gpu else "cpu",
                )

    # Option 2: FastEmbed sidecar
    if probe_sidecar_health():
        return InferenceProvider(
            name="fastembed-sidecar",
            backend=_sidecar_backend(platform),
            url="http://localhost:8889",
            tier="gpu" if _sidecar_has_gpu() else "cpu",
        )

    # Option 3: In-process ONNX with best available EP
    best_ep = _best_onnx_provider(platform)
    if best_ep != "CPUExecutionProvider":
        return InferenceProvider(
            name="onnx-local",
            backend=best_ep,
            url=None,  # in-process
            tier="gpu",
        )

    # Option 4: Docker CPU fallback
    return InferenceProvider(
        name="onnx-docker-cpu",
        backend="CPUExecutionProvider",
        url=None,
        tier="cpu-degraded",
    )


def _best_onnx_provider(platform: Platform) -> str:
    """Return highest-performance ONNX EP available on this platform."""
    import onnxruntime as ort
    available = ort.get_available_providers()
    priority = {
        Platform.MACOS_ARM:     ["CoreMLExecutionProvider"],
        Platform.MACOS_INTEL:   [],  # CPU only
        Platform.LINUX_NVIDIA:  ["CUDAExecutionProvider", "TensorrtExecutionProvider"],
        Platform.LINUX_AMD:     ["MIGraphXExecutionProvider", "ROCMExecutionProvider"],
        Platform.LINUX_CPU:     [],
        Platform.WINDOWS_NVIDIA:["DmlExecutionProvider", "CUDAExecutionProvider"],
        Platform.WINDOWS_CPU:   ["DmlExecutionProvider"],
    }
    for ep in priority.get(platform, []):
        if ep in available:
            return ep
    return "CPUExecutionProvider"
```

---

## 2. FastEmbed Sidecar (Option 3)

A lightweight host-side microservice that runs ONNX embedding and reranking inference with GPU acceleration, independent of Docker. Started automatically by `start-cerid.sh` when detected.

### 2.1 What It Is

- A single-file FastAPI server (~150 lines) wrapping FastEmbed + cross-encoder ONNX
- Runs on the host (not in Docker) to access GPU hardware directly
- Exposes two endpoints: `POST /embed` and `POST /rerank`
- Listens on port `8889` (configurable via `CERID_SIDECAR_PORT`)
- Model files cached in `~/.cache/cerid-models/`

### 2.2 Per-Platform Installation

#### macOS ARM (Apple Silicon)

```bash
# Install onnxruntime with CoreML/Metal support
pip install fastembed onnxruntime-silicon

# The sidecar auto-detects CoreMLExecutionProvider
# Metal acceleration via ANE (Apple Neural Engine)
```

**ONNX providers used:** `["CoreMLExecutionProvider", "CPUExecutionProvider"]`

**Expected performance:**
| Operation | Docker CPU | Sidecar (CoreML) | Speedup |
|-----------|-----------|-------------------|---------|
| Embed batch-10 (768-dim) | ~15ms | ~3-5ms | 3-5x |
| Rerank 15 candidates | ~50ms | ~10-15ms | 3-5x |

#### macOS Intel

```bash
# Standard ONNX runtime (no GPU acceleration on Intel Mac)
pip install fastembed onnxruntime
```

**ONNX providers used:** `["CPUExecutionProvider"]`

**Expected performance:** ~1.5-2x over Docker CPU (native vs emulated, better thread scheduling).

#### Linux NVIDIA

```bash
# CUDA-accelerated ONNX runtime
pip install fastembed onnxruntime-gpu

# Requires: CUDA 11.8+ or 12.x, cuDNN 8.x+
# Verify: python -c "import onnxruntime; print(onnxruntime.get_available_providers())"
```

**ONNX providers used:** `["CUDAExecutionProvider", "CPUExecutionProvider"]`

**Expected performance:**
| Operation | Docker CPU | Sidecar (CUDA) | Speedup |
|-----------|-----------|----------------|---------|
| Embed batch-10 (768-dim) | ~15ms | ~2-3ms | 5-8x |
| Rerank 15 candidates | ~50ms | ~5-8ms | 6-10x |

#### Linux AMD (ROCm)

```bash
# ROCm-accelerated ONNX runtime
pip install fastembed onnxruntime
# Install MIGraphX EP separately:
# See https://onnxruntime.ai/docs/execution-providers/MIGraphX-ExecutionProvider.html

# Requires: ROCm 5.x+, MIGraphX
```

**ONNX providers used:** `["MIGraphXExecutionProvider", "ROCMExecutionProvider", "CPUExecutionProvider"]`

**Expected performance:** ~4-8x over Docker CPU (varies by GPU model).

#### Windows (DirectML)

```bash
# DirectML works across NVIDIA, AMD, and Intel GPUs on Windows
pip install fastembed onnxruntime-directml
```

**ONNX providers used:** `["DmlExecutionProvider", "CPUExecutionProvider"]`

**Expected performance:** ~3-6x over Docker CPU.

### 2.3 Sidecar API Contract

```
POST /embed
  Request:  { "texts": ["str", ...], "model": "optional-override" }
  Response: { "embeddings": [[float, ...], ...], "dimensions": 768, "latency_ms": 3.2 }

POST /rerank
  Request:  { "query": "str", "documents": ["str", ...], "top_k": 15 }
  Response: { "scores": [float, ...], "latency_ms": 8.1 }

GET /health
  Response: { "status": "ok", "provider": "CoreMLExecutionProvider", "gpu": true,
              "models": {"embed": "snowflake-arctic-embed-m-v1.5", "rerank": "ms-marco-MiniLM-L-6-v2"} }
```

### 2.4 Startup Integration

`scripts/start-cerid.sh` gains a new phase `[0/4] Inference Sidecar`:

```bash
# Phase [0/4]: Check for sidecar
if curl -sf http://localhost:${CERID_SIDECAR_PORT:-8889}/health > /dev/null 2>&1; then
    echo "[0/4] Inference sidecar already running ($(curl -s http://localhost:8889/health | jq -r .provider))"
elif command -v cerid-sidecar &> /dev/null; then
    echo "[0/4] Starting inference sidecar..."
    cerid-sidecar --port ${CERID_SIDECAR_PORT:-8889} --daemon
    sleep 2
    curl -sf http://localhost:8889/health > /dev/null || echo "[0/4] WARNING: Sidecar failed to start"
else
    echo "[0/4] No inference sidecar found (pip install cerid-inference for GPU acceleration)"
fi
```

---

## 3. Degraded Mode (Option 4)

When neither Ollama, FastEmbed sidecar, nor host-side GPU ONNX is available, the system falls back to ONNX on Docker CPU. This is functional but significantly slower.

### 3.1 User Messaging

#### Setup Wizard (Step 4 — Ollama Configuration)

When degraded mode is detected during wizard, display:

```
┌─────────────────────────────────────────────────────────────┐
│  ⚠ CPU-Only Inference Mode                                  │
│                                                             │
│  Embedding generation is running on Docker CPU, which is    │
│  approximately 5-20x slower than GPU-accelerated mode.      │
│                                                             │
│  For significantly faster performance:                      │
│                                                             │
│  Option A: Install Ollama (recommended)                     │
│    brew install ollama && ollama serve                       │
│    ollama pull snowflake-arctic-embed:xs                     │
│                                                             │
│  Option B: Install the Cerid inference sidecar              │
│    pip install cerid-inference                               │
│    cerid-sidecar --daemon                                   │
│                                                             │
│  After installing either, restart Cerid to auto-detect.     │
│                                                             │
│  [ Continue with CPU mode ]  [ Re-check now ]               │
└─────────────────────────────────────────────────────────────┘
```

#### Settings UI — Provider Status Section

The Settings > Essentials > Provider Status section gains a new **Inference Tier** row:

| State | Display |
|-------|---------|
| Ollama GPU | `Inference: Ollama (Metal GPU) — Optimal` with green indicator |
| Ollama CPU | `Inference: Ollama (CPU) — Good` with yellow indicator |
| FastEmbed sidecar (GPU) | `Inference: Sidecar (CoreML) — Optimal` with green indicator |
| FastEmbed sidecar (CPU) | `Inference: Sidecar (CPU) — Good` with yellow indicator |
| ONNX in-process GPU | `Inference: ONNX (CoreML) — Optimal` with green indicator |
| ONNX Docker CPU | `Inference: Docker CPU — Degraded (5-20x slower)` with red indicator |

#### Chat Degradation Banner

When `tier == "cpu-degraded"`, the existing degradation banner system (from `DegradationManager`) shows:

> "Running in CPU-only inference mode. Embedding and reranking operations are slower than usual. [Learn more](docs/TIERED_INFERENCE_ARCHITECTURE.md)"

This banner appears once per session (dismissed via localStorage, 24h expiry matching wizard pattern).

#### Health Endpoint

`GET /health` response gains an `inference` field:

```json
{
  "status": "healthy",
  "inference": {
    "provider": "onnx-docker-cpu",
    "tier": "cpu-degraded",
    "gpu": false,
    "embed_model": "snowflake-arctic-embed-m-v1.5",
    "rerank_model": "ms-marco-MiniLM-L-6-v2",
    "embed_latency_ms": 22.4,
    "rerank_latency_ms": 48.7,
    "message": "Running in CPU-only mode. Install Ollama or the Cerid inference sidecar for 5-20x faster embeddings."
  }
}
```

---

## 4. Function Offloading Matrix

Ten compute-heavy functions benefit from tiered acceleration. The table maps each function to its optimal provider per platform.

### 4.1 Complete Matrix

| # | Function | File:Line | Workload | Current Provider |
|---|----------|-----------|----------|-----------------|
| 1 | `OnnxEmbeddingFunction.__call__()` | `utils/embeddings.py:112` | Bi-encoder embedding (768-dim) | ONNX CPUExecutionProvider |
| 2 | `_score_pairs()` / `rerank()` | `utils/reranker.py:78` / `:129` | Cross-encoder scoring | ONNX CPUExecutionProvider |
| 3 | `_extract_claims_llm()` | `agents/hallucination/extraction.py:362` | Claim extraction (LLM) | Bifrost → OpenRouter |
| 4 | `decompose_query()` | `utils/query_decomposer.py:97` | Query decomposition (LLM) | Bifrost → OpenRouter |
| 5 | `extract_memories()` | `agents/memory.py:44` | Memory extraction (LLM) | Bifrost → OpenRouter |
| 6 | `resolve_memory_conflict()` | `agents/memory.py:386` | Conflict resolution (LLM) | Bifrost → OpenRouter |
| 7 | `ai_categorize()` | `utils/metadata.py:182` | Document classification (LLM) | Bifrost → OpenRouter |
| 8 | `contextualize_chunks()` | `utils/contextual.py:34` | Chunk context generation (LLM) | Bifrost → OpenRouter |
| 9 | `_rerank_llm()` | `agents/assembler.py:115` | LLM reranking (fallback) | Bifrost → OpenRouter |
| 10 | `generate_hypothetical_document()` | `utils/hyde.py:43` | HyDE generation (LLM) | Ollama / OpenRouter |

### 4.2 Provider Selection Per Platform

**Functions 1-2 (ONNX inference — embeddings + reranking):**

| Platform | Option 1 | Option 2 | Option 3 | Fallback |
|----------|----------|----------|----------|----------|
| macOS ARM | Ollama `/api/embed` | Sidecar (CoreML) | ONNX CoreML in-process | Docker CPU |
| macOS Intel | Ollama (CPU) | Sidecar (CPU) | ONNX CPU in-process | Docker CPU |
| Linux NVIDIA | Ollama (CUDA) | Sidecar (CUDA) | ONNX CUDA in-process | Docker CPU |
| Linux AMD | Ollama (ROCm) | Sidecar (MIGraphX) | ONNX ROCm in-process | Docker CPU |
| Linux CPU | Ollama (CPU) | Sidecar (CPU) | ONNX CPU in-process | Docker CPU |
| Windows NVIDIA | Ollama (CUDA/WSL) | Sidecar (DirectML) | ONNX DML in-process | Docker CPU |
| Windows CPU | Sidecar (CPU) | ONNX CPU in-process | Docker CPU | — |

> **Note:** Ollama supports embeddings via `/api/embed` but does NOT support cross-encoder reranking. For function #2, Ollama is skipped and the sidecar/ONNX path is used regardless.

**Functions 3-10 (LLM pipeline stages):**

| Platform | Option 1 | Fallback |
|----------|----------|----------|
| Any + Ollama GPU | Ollama (GPU-accelerated) | Bifrost → OpenRouter |
| Any + Ollama CPU | Ollama (CPU) | Bifrost → OpenRouter |
| Any without Ollama | Bifrost → OpenRouter | — |

These are controlled by `INTERNAL_LLM_PROVIDER` and the per-stage `PIPELINE_PROVIDERS` dict in `config/settings.py:510`.

### 4.3 Expected Performance Summary

| Function | Docker CPU | Ollama GPU (M1) | Sidecar GPU | Speedup |
|----------|-----------|-----------------|-------------|---------|
| Embedding (batch 10) | ~15-25ms | ~3ms | ~3-5ms | **5-8x** |
| Reranking (15 docs) | ~50ms | N/A (no CE support) | ~10-15ms | **3-5x** |
| Claim extraction | 200-500ms | 50-150ms | N/A | **2-4x** |
| Query decomposition | 200-400ms | 50-100ms | N/A | **2-4x** |
| Memory extraction | 150-300ms | 40-80ms | N/A | **2-4x** |
| AI categorization | 200-500ms | 50-150ms | N/A | **2-4x** |
| Contextual chunking | 200-500ms/batch | 50-150ms/batch | N/A | **2-4x** |
| LLM reranking | 200-400ms | 50-100ms | N/A | **2-4x** |
| HyDE generation | 200-400ms | 50-100ms | N/A | **2-4x** |

**Per-query aggregate (embed + rerank + decompose + claim extraction):**
- Docker CPU baseline: ~480-1275ms
- Fully accelerated: ~113-335ms
- **End-to-end speedup: 3-4x**

---

## 5. Self-Optimization Logic

The system automatically detects, selects, and communicates its inference tier without manual configuration.

### 5.1 Startup Detection Sequence

Runs during `main.py` `lifespan()` startup, after infrastructure health checks but before the first request is served.

```
[1] Platform detection
    → os.uname(), platform.machine(), sys.platform
    → Classify into Platform enum (MACOS_ARM, LINUX_NVIDIA, etc.)

[2] GPU probe (platform-specific)
    → macOS: ioreg -l | grep -i "apple.*gpu" (ARM) or system_profiler SPDisplaysDataType (Intel)
    → Linux NVIDIA: nvidia-smi --query-gpu=name --format=csv,noheader
    → Linux AMD: rocm-smi --showproductname
    → Windows: wmic path win32_VideoController get name (via WSL bridge)

[3] Ollama probe
    → HTTP GET {OLLAMA_URL}/api/tags (2s timeout)
    → If 200: check for embedding model availability
    → If timeout/error: mark Ollama unavailable

[4] Sidecar probe
    → HTTP GET http://localhost:{CERID_SIDECAR_PORT}/health (1s timeout)
    → If 200: read provider and GPU status from response
    → If timeout/error: mark sidecar unavailable

[5] In-process ONNX probe
    → import onnxruntime; ort.get_available_providers()
    → Select best EP per platform priority list

[6] Provider selection
    → Walk the platform's fallback chain (Section 1.2)
    → Select first available provider
    → Store in global InferenceConfig singleton

[7] Warmup
    → Run 1 dummy embedding + 1 dummy rerank to pre-load models
    → Measure actual latency for health endpoint reporting

[8] Log result
    → INFO: "Inference provider: ollama (Metal GPU) — embed: 3.1ms, rerank: via sidecar 11.2ms"
    → or WARN: "Inference provider: onnx-docker-cpu (degraded) — embed: 22.4ms, rerank: 48.7ms"
```

### 5.2 Periodic Re-Check

A background task re-runs the detection sequence to catch provider changes (Ollama started/stopped, sidecar installed, GPU driver update).

**Interval:** Every 5 minutes (300 seconds).

**Behavior:**

```python
async def _inference_recheck_loop():
    """Background task: re-probe inference providers every 5 min."""
    while True:
        await asyncio.sleep(300)
        new_provider = detect_embedding_provider()
        current = get_inference_config()

        if new_provider.name != current.provider.name:
            if _is_upgrade(new_provider, current.provider):
                logger.info(
                    "Inference upgrade detected: %s → %s",
                    current.provider.name, new_provider.name,
                )
                update_inference_config(new_provider)
                await _warmup_provider(new_provider)
                # Notify GUI via SSE event
                await broadcast_event("inference_provider_changed", {
                    "previous": current.provider.name,
                    "current": new_provider.name,
                    "tier": new_provider.tier,
                })
            elif _is_downgrade(new_provider, current.provider):
                logger.warning(
                    "Inference downgrade detected: %s → %s (provider unavailable)",
                    current.provider.name, new_provider.name,
                )
                update_inference_config(new_provider)
                await broadcast_event("inference_provider_changed", {
                    "previous": current.provider.name,
                    "current": new_provider.name,
                    "tier": new_provider.tier,
                    "degraded": True,
                })
```

**Upgrade vs downgrade logic:**

```
Provider tiers (best → worst):
  ollama-gpu > sidecar-gpu > onnx-gpu > ollama-cpu > sidecar-cpu > onnx-cpu > docker-cpu

Auto-switch rules:
  - Upgrade: always switch immediately (better provider appeared)
  - Downgrade: switch only if current provider health check fails
    (don't downgrade just because a lower-priority provider was probed)
  - Ollama restart: if Ollama was the active provider and goes down,
    fall through to next available (sidecar → ONNX → Docker CPU)
  - Sidecar restart: same fallthrough logic
```

### 5.3 Settings UI — Current Inference Mode

The Settings > Essentials page shows the current inference configuration:

```
┌─────────────────────────────────────────────────────────────┐
│  Inference Engine                                           │
│                                                             │
│  Provider:     Ollama (Metal GPU)         ● Optimal         │
│  Embed model:  snowflake-arctic-embed-m-v1.5 (768-dim)     │
│  Rerank model: ms-marco-MiniLM-L-6-v2 (cross-encoder)     │
│  Embed latency:  3.1 ms/batch                              │
│  Rerank latency: 11.2 ms/15-candidates                     │
│  Last checked:   2 minutes ago                              │
│                                                             │
│  [ Re-check now ]                                           │
└─────────────────────────────────────────────────────────────┘
```

When degraded:

```
┌─────────────────────────────────────────────────────────────┐
│  Inference Engine                                           │
│                                                             │
│  Provider:     Docker CPU                 ● Degraded        │
│  Embed model:  snowflake-arctic-embed-m-v1.5 (768-dim)     │
│  Rerank model: ms-marco-MiniLM-L-6-v2 (cross-encoder)     │
│  Embed latency:  22.4 ms/batch                             │
│  Rerank latency: 48.7 ms/15-candidates                     │
│  Last checked:   1 minute ago                               │
│                                                             │
│  ⚠ Running 5-20x slower than GPU mode.                     │
│  Install Ollama or the Cerid sidecar for faster inference.  │
│                                                             │
│  [ Install guide ]  [ Re-check now ]                        │
└─────────────────────────────────────────────────────────────┘
```

### 5.4 Auto-Switch Communication

When the system detects a provider change, it communicates via:

1. **Server log:** INFO/WARN level message with old → new provider
2. **SSE event:** `inference_provider_changed` event on the existing `/events` stream
3. **GUI toast:** React GUI listens for the SSE event and shows a transient toast:
   - Upgrade: "Inference upgraded to Ollama (Metal GPU). Queries will be faster."
   - Downgrade: "Ollama unavailable. Falling back to Docker CPU inference."
4. **Health endpoint:** `/health` response reflects the new provider immediately

---

## 6. Implementation Phases

### Phase 1: Infrastructure — InferenceConfig + Detection (Week 1)

**New files:**

| File | Purpose |
|------|---------|
| `utils/inference_config.py` | `InferenceConfig` singleton, `InferenceProvider` dataclass, `detect_embedding_provider()`, platform detection, provider probing |
| `utils/inference_sidecar_client.py` | HTTP client for sidecar `/embed` and `/rerank` endpoints with circuit breaker |

**Modified files:**

| File | Change |
|------|--------|
| `config/settings.py` | Add `CERID_SIDECAR_PORT`, `CERID_SIDECAR_URL`, `INFERENCE_RECHECK_INTERVAL` env vars |
| `main.py` | Call `detect_embedding_provider()` during lifespan startup; start `_inference_recheck_loop()` background task |
| `routers/health.py` | Add `inference` field to health response |

**Exact code changes in `utils/embeddings.py`:**

```python
# Line 94 — BEFORE:
providers=["CPUExecutionProvider"],

# Line 94 — AFTER:
providers=get_inference_config().onnx_providers,
```

**Exact code changes in `utils/reranker.py`:**

```python
# Line 60 — BEFORE:
providers=["CPUExecutionProvider"],

# Line 60 — AFTER:
providers=get_inference_config().onnx_providers,
```

**Exact code changes in `config/settings.py`:**

```python
# After line 496 (OLLAMA_DEFAULT_MODEL) — ADD:
CERID_SIDECAR_PORT = int(os.getenv("CERID_SIDECAR_PORT", "8889"))
CERID_SIDECAR_URL = os.getenv("CERID_SIDECAR_URL", f"http://localhost:{CERID_SIDECAR_PORT}")
INFERENCE_RECHECK_INTERVAL = int(os.getenv("INFERENCE_RECHECK_INTERVAL", "300"))
```

---

### Phase 2: Sidecar Service + Startup Integration (Week 2)

**New files:**

| File | Purpose |
|------|---------|
| `scripts/cerid-sidecar.py` | Standalone FastAPI sidecar server (~150 lines), installable via `pip install cerid-inference` |
| `scripts/install-sidecar.sh` | Platform-detecting installer (detects OS + GPU → installs correct onnxruntime variant) |

**Modified files:**

| File | Change |
|------|--------|
| `scripts/start-cerid.sh` | Add phase `[0/4] Inference Sidecar` before infrastructure startup |
| `utils/embeddings.py` | Add sidecar HTTP path: if `inference_config.provider == "fastembed-sidecar"`, call sidecar `/embed` instead of local ONNX |
| `utils/reranker.py` | Add sidecar HTTP path: if `inference_config.provider == "fastembed-sidecar"`, call sidecar `/rerank` instead of local ONNX |
| `.env.example` | Add `CERID_SIDECAR_PORT=8889` |

**Sidecar install script logic (`scripts/install-sidecar.sh`):**

```bash
#!/usr/bin/env bash
set -euo pipefail

OS=$(uname -s)
ARCH=$(uname -m)

echo "Detecting platform..."

if [[ "$OS" == "Darwin" && "$ARCH" == "arm64" ]]; then
    echo "macOS Apple Silicon detected — installing onnxruntime-silicon"
    pip install fastembed onnxruntime-silicon
elif [[ "$OS" == "Darwin" ]]; then
    echo "macOS Intel detected — installing standard onnxruntime"
    pip install fastembed onnxruntime
elif [[ "$OS" == "Linux" ]] && command -v nvidia-smi &> /dev/null; then
    echo "Linux + NVIDIA GPU detected — installing onnxruntime-gpu"
    pip install fastembed onnxruntime-gpu
elif [[ "$OS" == "Linux" ]] && command -v rocm-smi &> /dev/null; then
    echo "Linux + AMD GPU (ROCm) detected — installing onnxruntime + MIGraphX"
    pip install fastembed onnxruntime
    echo "NOTE: MIGraphX EP requires manual ROCm setup. See docs."
elif [[ "$OS" == "Linux" ]]; then
    echo "Linux CPU-only detected — installing standard onnxruntime"
    pip install fastembed onnxruntime
else
    echo "Windows/WSL detected — installing onnxruntime-directml"
    pip install fastembed onnxruntime-directml
fi

echo "Done. Start the sidecar with: cerid-sidecar --daemon"
```

---

### Phase 3: GUI Integration + Degradation Messaging (Week 3)

**Modified files:**

| File | Change |
|------|--------|
| `src/web/src/components/settings/ProviderStatus.tsx` | Add Inference Tier row with green/yellow/red indicator |
| `src/web/src/components/setup/OllamaStep.tsx` | Add degraded-mode warning panel with install instructions |
| `src/web/src/components/chat/DegradationBanner.tsx` | Handle `inference_provider_changed` SSE event; show CPU-only banner |
| `src/web/src/hooks/use-settings.ts` | Parse new `inference` field from `/health` response |
| `src/web/src/lib/types.ts` | Add `InferenceStatus` type with provider/tier/latency fields |
| `routers/health.py` | Include `inference` object in health response (provider, tier, gpu, latency, message) |

---

### Phase 4: Periodic Re-Check + Auto-Switch (Week 4)

**Modified files:**

| File | Change |
|------|--------|
| `utils/inference_config.py` | Add `_inference_recheck_loop()` coroutine, upgrade/downgrade logic |
| `main.py` | Start recheck loop as `asyncio.create_task()` during lifespan |
| `routers/observability.py` | Add `inference_provider_switch` metric to `MetricsCollector` |
| `src/web/src/hooks/use-chat.ts` | Listen for `inference_provider_changed` SSE event, show toast notification |

---

### Phase 5: Ollama Embedding + LLM Stage Routing (Week 5)

**Modified files:**

| File | Change |
|------|--------|
| `utils/embeddings.py` | Add Ollama embed path: if `inference_config.provider == "ollama"`, call `/api/embed` instead of local ONNX. Dimension must match (768 for Arctic). |
| `utils/internal_llm.py:61` | `call_internal_llm()` already supports Ollama routing — no changes needed for LLM stages |
| `config/settings.py:510` | `PIPELINE_PROVIDERS` already supports per-stage Ollama override — no changes needed |
| `utils/metadata.py:182` | Modify `ai_categorize()` to use `call_internal_llm()` instead of direct Bifrost HTTP when `INTERNAL_LLM_PROVIDER=ollama` |
| `utils/contextual.py:34` | Modify `contextualize_chunks()` to use `call_internal_llm()` instead of direct Bifrost HTTP when Ollama available |

**Critical constraint for Ollama embeddings:** The Ollama embedding model must produce vectors of the same dimension as the configured `EMBEDDING_DIMENSIONS` (768 for Arctic). Mismatched dimensions corrupt the ChromaDB collection. The detection logic must validate dimensions match before activating Ollama embed.

---

### Phase 6: Validation + Documentation (Week 6)

**New tests:**

| Test file | Coverage |
|-----------|----------|
| `tests/test_inference_config.py` | Platform detection, provider fallback chains, upgrade/downgrade logic |
| `tests/test_inference_sidecar.py` | Sidecar HTTP client, embed/rerank contract, health check |
| `tests/test_inference_recheck.py` | Periodic re-check, auto-switch on Ollama start/stop |

**Benchmarks:**

| Benchmark | Method |
|-----------|--------|
| Embedding latency per provider | 100 batch-10 calls, measure p50/p95/p99 |
| Reranking latency per provider | 100 rerank-15 calls, measure p50/p95/p99 |
| End-to-end query latency | 50 queries through full pipeline, compare CPU vs GPU |

**Documentation updates:**

| File | Change |
|------|--------|
| `CLAUDE.md` | Add Tiered Inference section referencing this doc |
| `docs/OPERATIONS.md` | Add sidecar startup/troubleshooting |
| `.env.example` | Add `CERID_SIDECAR_PORT`, `INFERENCE_RECHECK_INTERVAL` |
| `docs/ROADMAP.md` | Mark tiered inference as completed |

---

## Appendix A: Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `INTERNAL_LLM_PROVIDER` | `bifrost` | Global LLM provider for pipeline stages (`bifrost` / `ollama`) |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_DEFAULT_MODEL` | `llama3.2:3b` | Default Ollama model for LLM stages |
| `OLLAMA_ENABLED` | `false` | Enable Ollama integration |
| `CERID_SIDECAR_PORT` | `8889` | FastEmbed sidecar listen port |
| `CERID_SIDECAR_URL` | `http://localhost:8889` | FastEmbed sidecar base URL |
| `INFERENCE_RECHECK_INTERVAL` | `300` | Seconds between provider re-checks |
| `EMBEDDING_MODEL` | `Snowflake/snowflake-arctic-embed-m-v1.5` | Embedding model name (ONNX) |
| `RERANK_CROSS_ENCODER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder model name (ONNX) |
| `PROVIDER_CLAIM_EXTRACTION` | (global) | Per-stage provider override |
| `PROVIDER_QUERY_DECOMPOSITION` | (global) | Per-stage provider override |
| `PROVIDER_TOPIC_EXTRACTION` | (global) | Per-stage provider override |
| `PROVIDER_MEMORY_RESOLUTION` | (global) | Per-stage provider override |
| `PROVIDER_VERIFICATION_SIMPLE` | (global) | Per-stage provider override |
| `PROVIDER_RERANKING` | (global) | Per-stage provider override |

## Appendix B: File Reference Map

All source files referenced in this document:

| File | Key Lines | Role |
|------|-----------|------|
| `config/settings.py` | 158, 200, 492, 496, 510, 522 | Provider config, model IDs, stage routing |
| `utils/embeddings.py` | 44, 94, 112 | ONNX embedding function, CPUExecutionProvider |
| `utils/reranker.py` | 60, 78, 129 | ONNX cross-encoder, CPUExecutionProvider |
| `utils/internal_llm.py` | 61, 97 | Internal LLM router, Ollama caller |
| `utils/llm_client.py` | 91, 281, 320 | External LLM dispatch, Ollama direct |
| `utils/metadata.py` | 53, 108, 182 | Metadata extraction, AI categorization |
| `utils/contextual.py` | 34 | Contextual chunk generation |
| `utils/hyde.py` | 43 | HyDE generation |
| `utils/query_decomposer.py` | 97 | Query decomposition |
| `agents/hallucination/extraction.py` | 362, 468 | Claim extraction |
| `agents/hallucination/verification.py` | 336, 864 | Claim verification |
| `agents/hallucination/streaming.py` | 137 | Verification orchestrator |
| `agents/memory.py` | 44, 386 | Memory extraction, conflict resolution |
| `agents/assembler.py` | 64, 100, 115, 395 | Reranking, context assembly |
| `agents/decomposer.py` | 171 | Multi-domain query execution |
| `main.py` | (lifespan) | Startup detection, background recheck |
| `routers/health.py` | (health endpoint) | Inference status reporting |
| `scripts/start-cerid.sh` | (phase 0) | Sidecar startup integration |
