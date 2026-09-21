# Inference Providers

Cerid routes four inference workloads independently — LLM completion,
dense embeddings, cross-encoder reranking, and NLI entailment — so you can
mix a cloud LLM with local embeddings, or run everything locally with no
cloud key at all. This page is the operator matrix `INSTALL.md` points to.

## The provider families

| Provider | What it is | Workloads | Needs |
|---|---|---|---|
| **openrouter** (default for LLM) | Hosted multi-model gateway | LLM completion, verification judges | `OPENROUTER_API_KEY` |
| **ollama** | Local inference on the Ollama API (`:11434`) | LLM completion only — Cerid has no Ollama embed/rerank dispatch, and Ollama serves no cross-encoder rerank endpoint | A running local server; no cloud key |
| **quenchforge** | [Quenchforge](https://github.com/Cerid-AI/quenchforge), our Ollama-API-compatible supervisor for AMD-Mac Metal GPUs, on the same `:11434` | LLM completion, embeddings, reranking | A running local server with the embed/rerank slots configured; no cloud key |
| **sidecar** (`scripts/cerid-sidecar.py`) | Native-GPU helper on the host, for hosts where the container cannot see the GPU | Embeddings, reranking, sparse encoding | The sidecar process running; auto-detected |
| **in-process ONNX** (terminal fallback) | Models run inside the MCP container (CPU by default) | Embeddings, reranking, NLI | Nothing — ships with the image |

`INTERNAL_LLM_PROVIDER` accepts `openrouter | ollama | quenchforge`.
`EMBEDDINGS_PROVIDER` and `RERANK_PROVIDER` accept
`sidecar | quenchforge | in-process` — `PATCH /settings` rejects anything
else with a 400, and a value set directly in `.env` that is not one of the
three resolves to `in-process` rather than failing, so check
`/health.inference_routing` after changing one.

## Choosing per workload

```bash
# .env — every knob independent
INTERNAL_LLM_PROVIDER=openrouter   # or: ollama | quenchforge
EMBEDDINGS_PROVIDER=sidecar        # or: quenchforge | in-process
RERANK_PROVIDER=sidecar            # or: quenchforge | in-process
```

`sidecar` is the default for both embeddings and reranking when the env
var is unset.

- `INTERNAL_LLM_PROVIDER=ollama` (or `quenchforge`) switches **all** LLM
  stages to the local server — this is the fully-local mode. When the
  startup preflight detects a live local LLM on `:11434`,
  `OPENROUTER_API_KEY` is not required.
- Embeddings and reranking fall back automatically:
  configured provider → sidecar → in-process ONNX. The in-process ONNX
  model is the end of that chain, so its weights must be on disk **whatever
  provider you configure** — `/setup/models/status` reports them as the
  lane's `fallback` when a remote provider is set, and a degraded lane is
  listed in `/health.degraded_lanes`.
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
# embeddings/rerank default to sidecar, falling back to in-process ONNX
# when no sidecar is running — nothing else needed
```

**Fully local, no cloud key:**
```bash
INTERNAL_LLM_PROVIDER=ollama          # quenchforge if on AMD-Mac Metal
# have ollama/quenchforge serving on :11434 before start-cerid.sh
```

**Local GPU embeddings + reranking (quenchforge only):**
```bash
INTERNAL_LLM_PROVIDER=quenchforge
EMBEDDINGS_PROVIDER=quenchforge
RERANK_PROVIDER=quenchforge
# the daemon needs its own QUENCHFORGE_EMBED_MODEL / QUENCHFORGE_RERANK_MODEL
# and the GGUF files present — see docs/TIERED_INFERENCE_ARCHITECTURE.md
```

**AMD-Mac (Intel + Radeon) operators:** use quenchforge — stock
llama.cpp Metal output is incorrect on non-UMA AMD GPUs. Model picks per
VRAM tier: `docs/AMD_GPU_MODEL_RECOMMENDATIONS.md`.

## Profiles and the background slot

`CERID_ENVIRONMENT_PROFILE` (`cloud-first` / `hybrid` / `local-only`) sets the
provider defaults above *for you*, per-stage, rather than one global knob — an
operator value already set in the environment always wins over the profile's
default. `INTERNAL_LLM_MODEL_BACKGROUND` names a second, smaller local model
dedicated to background stages (entity extraction, wiki summary, topic
extraction) so a slow class-A host isn't paying 7B-model latency for
enrichment work it doesn't need full quality for. Full routing table, the
degrade rule, and the background-slot resolution order:
[`docs/ENVIRONMENT_PROFILES.md`](ENVIRONMENT_PROFILES.md).

## Related

- `INSTALL.md` — first-run flow and port table
- `docs/MODEL_PRELOAD.md` — which ONNX models ship in the image
- `docs/AMD_GPU_MODEL_RECOMMENDATIONS.md` — vetted GGUF picks
- `/health.inference_routing` — live per-workload provider introspection
