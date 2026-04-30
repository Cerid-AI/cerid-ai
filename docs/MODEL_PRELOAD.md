# Model Preload Trade-off

> **Workstream E Phase E.6 — UX wiring around `CERID_PRELOAD_MODELS`.**
> The cerid stack uses two ONNX models for retrieval enhancement:
> a cross-encoder reranker and a sentence embedder. This doc covers
> how they're delivered and how to control the trade-off.

## What's at stake

Two ONNX models that drive cerid's retrieval quality:

| Model | Size (uncompressed) | Used for |
|---|---|---|
| `cross-encoder/ms-marco-MiniLM-L-6-v2` (+ quantised int8 variant) | ~75 MB | Reranking the top-N retrieval candidates |
| `Snowflake/snowflake-arctic-embed-m-v1.5` | ~300 MB | Sentence embedding (768-dim) |

Combined Docker image overhead with both pre-baked: **~3 GB** (incl. HuggingFace cache metadata + tokenisers + the int8 reranker variant).

## The two ways to deliver them

### Pre-baked (today's default — `CERID_PRELOAD_MODELS=true`)

The Dockerfile's `models` stage downloads both via `huggingface_hub.hf_hub_download()` at build time, into `/home/cerid/.cache/huggingface/`. The runtime stage copies the cache forward.

**Pros:**
- First semantic query is fast — no download stall
- Image is hermetic; works offline / in CI / behind air-gapped firewalls
- Reproducible — model weights are fixed at image build

**Cons:**
- ~3 GB image overhead even for users who haven't run a query yet
- CI/CD pulls/pushes more bytes per cycle
- Forces Docker rebuild to pick up upstream model updates

### Lazy (`CERID_PRELOAD_MODELS=false`)

The Dockerfile's `models` stage prints `Skipping model preload (CERID_PRELOAD_MODELS=false)` and copies a sentinel forward. The runtime image is ~3 GB lighter.

On first call to `core/retrieval/reranker.py:_load_model` or `core/utils/embeddings.py:OnnxEmbeddingFunction._load`, the model downloads from HuggingFace synchronously (5–15s on typical broadband, longer on slow links). A thread-safe singleton lock (`reranker.py` lines 42–44) prevents concurrent downloads. Subsequent calls use the in-memory model.

**Pros:**
- ~3 GB lighter image; faster CI cycles, less storage burn
- Models update independently of the cerid release — pull the latest on first call after a fresh container start
- Works for embedded / low-bandwidth deployments where the operator wants tight control over what gets downloaded

**Cons:**
- First semantic query stalls 5–15s (silent — no UX indication today)
- Requires HuggingFace reachability at runtime (offline deployments break)
- Per-container cold-start cost on each replica

## How to switch

Set `CERID_PRELOAD_MODELS` in `.env` and rebuild the image:

```bash
# Lean image (Workstream E Phase E.6.5 default):
docker compose build --no-cache mcp-server
docker compose up -d
# Models download on first query OR explicitly via the setup wizard /
# Settings GUI / `curl -X POST http://localhost:8888/setup/models/preload`

# Pre-baked image (offline / air-gapped / CI):
echo "CERID_PRELOAD_MODELS=true" >> .env
docker compose build --no-cache mcp-server
docker compose up -d
```

The compose file's `build.args.CERID_PRELOAD_MODELS: ${CERID_PRELOAD_MODELS:-false}` propagates the env var to the Dockerfile. No flag wrangling.

## Pre-downloading after the image is built

When the lean image is running and you want to warm models without waiting for the first query:

```bash
curl -X POST http://localhost:8888/setup/models/preload
# {
#   "status": "ok",
#   "reranker_status": "loaded", "reranker_ms": 8200,
#   "embedder_status": "loaded", "embedder_ms": 12100,
#   "total_ms": 20300
# }
```

The Settings → System → **Inference Models** card in the React GUI surfaces the same endpoint with a one-click "Download models (~38 MB)" button + per-model timing. Cached state flips badges to "cached" and switches the button to "Re-warm cache" so operators can refresh after a model upgrade.

## Status check

```bash
curl http://localhost:8888/setup/models/status
# {
#   "reranker": {"loaded": true,  "size_mb": 75,  "cached_at": "2026-04-29T..."},
#   "embedder": {"loaded": false, "size_mb": null, "cached_at": null}
# }
```

The `/health` endpoint also reports inference provider availability + measured first-call latency, see `inference_config.py` `inference_health_payload()`.

## What happens if HuggingFace is unreachable

| Path | Behaviour |
|---|---|
| Reranker download fails | `core/agents/query_agent.py:545-611` rerank step records `reranker_status="onnx_failed_no_fallback"` and returns vector+BM25 results in their original blended order. Query succeeds; result quality degrades by the cross-encoder's expected lift. |
| Embedder download fails | The first ingest / first query that needs to embed a chunk raises an exception. Currently no graceful fallback — operator must restart with HuggingFace reachable, or pre-bake via `CERID_PRELOAD_MODELS=true`. |

Operators in air-gapped environments should keep `CERID_PRELOAD_MODELS=true` OR mirror the HuggingFace cache to an internal artifact store (set `HF_ENDPOINT` to point at the mirror).

## GPU sidecar (Phase E.6.4 — wired)

The GPU sidecar (`scripts/cerid-sidecar.py`) auto-detects CoreML / CUDA / ROCm and exposes inference at `http://localhost:8889/{embed,rerank}`. Phase E.6.4 wired the sidecar into both inference call sites:

- `core/utils/embeddings.OnnxEmbeddingFunction.__call__` checks `inference_config.get_inference_config()`. When `provider == "fastembed-sidecar"` AND `sidecar_available`, embeds via `utils.inference_sidecar_client.sidecar_embed()`. Sync→async bridged via the proven ThreadPoolExecutor + fresh-event-loop pattern.
- `core/agents/query_agent._rerank_cross_encoder` does the same check + uses `sidecar_rerank()`. Successful sidecar reranks tag results with `reranker_status="sidecar"` so observability can attribute the path.

Failures fall through to local ONNX silently (logged via `log_swallowed_error`). The sidecar is a separate process — `CERID_PRELOAD_MODELS=false` works alongside an active sidecar; the routing prefers the sidecar's GPU when reachable and only loads local ONNX if the sidecar restarts or the dim check fails.

Operators force the sidecar path via `INFERENCE_MODE=fastembed-sidecar` in `.env` or leave `INFERENCE_MODE=auto` and let the auto-detector pick when it's reachable.

## See also

- Driver doc: `tasks/2026-04-28-workstream-e-rag-modernization.md`
- Dockerfile: `src/mcp/Dockerfile` (stages: builder → models → runtime)
- Runtime loaders: `src/mcp/core/retrieval/reranker.py`, `src/mcp/core/utils/embeddings.py`
- Sidecar: `scripts/cerid-sidecar.py`
- Compose: `docker-compose.yml` `mcp-server.build.args`
