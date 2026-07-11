# Inference Providers

Cerid routes four inference workloads independently — LLM completion,
dense embeddings, cross-encoder reranking, and NLI entailment — so you can
mix a cloud LLM with local embeddings, or run everything locally with no
cloud key at all. This page is the operator matrix `INSTALL.md` points to.

## The three provider families

| Provider | What it is | Workloads | Needs |
|---|---|---|---|
| **openrouter** (default for LLM) | Hosted multi-model gateway | LLM completion, verification judges | `OPENROUTER_API_KEY` |
| **ollama / quenchforge** | Local inference on the Ollama API (`:11434`). [Quenchforge](https://github.com/Cerid-AI/quenchforge) is our supervisor for AMD-Mac Metal GPUs; stock Ollama works the same way | LLM completion, embeddings, reranking | A running local server; no cloud key |
| **in-process ONNX** (automatic fallback) | Models run inside the MCP container (CPU by default) | Embeddings, reranking, NLI | Nothing — ships with the image |

The optional **cerid sidecar** (`scripts/cerid-sidecar.py`) adds native-GPU
embedding/rerank/sparse encoding on hosts where the container cannot see
the GPU (Apple Metal, CUDA); the runtime auto-detects it.

## Choosing per workload

```bash
# .env — every knob independent
INTERNAL_LLM_PROVIDER=openrouter   # or: ollama | quenchforge
EMBEDDINGS_PROVIDER=in-process     # or: ollama | quenchforge
RERANK_PROVIDER=in-process         # or: ollama | quenchforge
```

- `INTERNAL_LLM_PROVIDER=ollama` (or `quenchforge`) switches **all** LLM
  stages to the local server — this is the fully-local mode. When the
  startup preflight detects a live local LLM on `:11434`,
  `OPENROUTER_API_KEY` is not required.
- Embeddings and reranking fall back automatically:
  sidecar → local server → in-process ONNX (GPU) → in-process ONNX (CPU).
- NLI entailment always runs in-process (ONNX, CPU).

Verify what actually resolved at runtime:

```bash
curl -s http://localhost:8888/health | jq .inference_routing
```

## Per-stage overrides

Every internal LLM call carries a `stage` name (visible in logs and
`/health`). Two env patterns override routing for a single stage without
touching the global default:

```bash
# Route one stage to a different provider
PROVIDER_STAGE_LONGMEMEVAL_SCORE=openrouter

# Pin one stage to a specific model
PROVIDER_STAGE_FAITHFULNESS_DECOMPOSE_MODEL=openrouter/google/gemini-2.5-flash
```

Stage names normalize `/` and `-` to `_` and uppercase (stage
`longmemeval/score` → `PROVIDER_STAGE_LONGMEMEVAL_SCORE`). Resolution
order: env override → pipeline profile → global default. Unpinned stages
resolve models through the role/tier policy (`config/stage_profiles.py`)
— no model ids are hardcoded at call sites.

## Recipes

**Cloud key, local everything else (default posture):**
```bash
OPENROUTER_API_KEY=sk-or-...
# embeddings/rerank default to in-process — nothing else needed
```

**Fully local, no cloud key:**
```bash
INTERNAL_LLM_PROVIDER=ollama          # quenchforge if on AMD-Mac Metal
# have ollama/quenchforge serving on :11434 before start-cerid.sh
```

**AMD-Mac (Intel + Radeon) operators:** use quenchforge — stock
llama.cpp Metal output is incorrect on non-UMA AMD GPUs. Model picks per
VRAM tier: `docs/AMD_GPU_MODEL_RECOMMENDATIONS.md`.

## Related

- `INSTALL.md` — first-run flow and port table
- `docs/MODEL_PRELOAD.md` — which ONNX models ship in the image
- `docs/AMD_GPU_MODEL_RECOMMENDATIONS.md` — vetted GGUF picks
- `/health.inference_routing` — live per-workload provider introspection
