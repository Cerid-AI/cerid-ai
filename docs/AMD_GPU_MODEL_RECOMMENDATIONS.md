# AMD GPU Model Recommendations (Quenchforge Integration)

> **Audience:** operators running cerid-ai on Intel Mac + AMD discrete
> GPU (Vega II, W6800X, W6900X, RDNA1/2) and using Quenchforge as the
> local inference backend.
> **First written:** v0.93.8 (2026-05-12).

Quenchforge runs llama.cpp + whisper.cpp under a Go gateway with a
single ggml-on-AMD-Mac correctness patch.  This doc is the matrix of
GGUF models cerid recommends for each workload on AMD GPU hardware,
sized by VRAM tier.

The recommendations below produce the **best quality-per-watt on AMD
Mac** as of mid-2026.  They are not the same picks you'd make on
Apple Silicon, where stock Ollama is the supported path and a wider
model catalog is available.

---

## Hardware tiers

| Tier | VRAM | Examples | Quenchforge support |
|---|---|---|---|
| **High** | 32 GB | Vega II Pro, Vega II Duo, W6800X, W6900X | Primary target |
| **Mid** | 16 GB | RX 6800, RX 6800 XT, RX 6900 XT | Supported |
| **Low** | 8 GB | RX 5700, RX 5700 XT, RX 6700 | Supported, tight |
| **Apple Silicon** | unified | M1–M4 | Non-degraded (but Ollama is the supported path) |

---

## LLM chat model

The default `qwen2.5:7b-instruct-q4_k_m` (Quenchforge's
`QUENCHFORGE_DEFAULT_MODEL`) is a sound choice across all tiers.  For
high-VRAM hardware you can step up; for low-VRAM you may want a
quantization step down.

| Tier | Recommended model | Quant | VRAM @ load | Tokens/sec on Vega II |
|---|---|---|---|---|
| High (32 GB) | `qwen2.5:14b-instruct-q4_k_m` | Q4_K_M | ~9 GB | ~35 tok/s |
| High (32 GB, quality) | `llama-3.1-8b-instruct-q5_k_m` | Q5_K_M | ~6 GB | ~50 tok/s |
| Mid (16 GB) | `qwen2.5:7b-instruct-q4_k_m` (default) | Q4_K_M | ~5 GB | ~55 tok/s |
| Low (8 GB) | `qwen2.5:7b-instruct-q3_k_m` | Q3_K_M | ~4 GB | ~60 tok/s |

**Setup:**

```bash
export INTERNAL_LLM_PROVIDER=quenchforge
export QUENCHFORGE_DEFAULT_MODEL=qwen2.5:14b-instruct-q4_k_m   # high tier
# or leave unset to use Quenchforge's built-in default
```

**Why these picks?**

* **Qwen 2.5 7B / 14B** — strong instruction-following + permissive
  license + excellent quantization tolerance (Q4_K_M loses very
  little quality vs Q8).
* **Llama 3.1 8B** — better English nuance, slightly slower; the
  trade is Llama-3.1's license caveats.
* **Quant choice:** Q4_K_M is the sweet spot.  Q5_K_M adds ~25% VRAM
  for marginal quality on most prompts; Q3_K_M drops measurable
  quality on multi-step reasoning.

---

## Embeddings model

**Hard constraint:** the embedding model MUST produce **768-dim**
vectors to match cerid's ChromaDB index dimensions.  Swapping to a
different dimension forces a full re-embed of every artifact.  The
client validates the response dimension on first call and refuses
to insert mismatched vectors.

| Model | Dim | Quant | Use when |
|---|---|---|---|
| `nomic-embed-text-v1.5-Q8_0` | 768 | Q8_0 | Default — best quality-per-byte at 768-dim |
| `snowflake-arctic-embed-m-v1.5-Q4_K_M` | 768 | Q4_K_M | Matches cerid's stock ONNX model — drop-in swap, no quality delta |
| `mxbai-embed-large-v1-Q4_K_M` | 1024 ❌ | — | **DON'T USE** — dimension mismatch |

**Setup:**

```bash
export EMBEDDINGS_PROVIDER=quenchforge
export QUENCHFORGE_EMBED_MODEL=nomic-embed-text-v1.5
# Drop the GGUF into Quenchforge's models dir or run:
#   quenchforge migrate-from-ollama
```

**Why these picks?**

* **Nomic v1.5** is the default because it's the most-validated 768-dim
  GGUF embedding model and has been benchmarked on ggml/llama.cpp for
  AMD Mac specifically.
* **Snowflake arctic-embed-m-v1.5** — cerid's stock CPU model.
  Identical dimension, identical output distribution.  The Q4_K_M GGUF
  trades a tiny amount of quality for ~3x speedup on AMD GPU.

**Not recommended:** any model not in this table.  Dimension mismatches
silently corrupt search results until detected by user complaint.

---

## Reranking model

**Hard constraint:** the reranker MUST produce a relevance score per
document (Cohere/Voyage wire on `/v1/rerank`).  The cerid path expects
scalar scores; ranking-only models won't work.

| Model | Quant | Use when |
|---|---|---|
| `bge-reranker-v2-m3` | Q4_K_M | Default — multilingual, strong on technical queries |
| `bge-reranker-v2-gemma` | Q4_K_M | Higher quality, slower (Gemma backbone) |
| `jina-reranker-v2-base-multilingual` | Q4_K_M | Best multilingual; needs GGUF conversion |

**Setup:**

```bash
export RERANK_PROVIDER=quenchforge
export QUENCHFORGE_RERANK_MODEL=bge-reranker-v2-m3
```

**Why these picks?**

* **BGE Reranker v2 m3** is the canonical free reranker that has a
  GGUF distribution + works on llama.cpp `--reranking` mode.  Quality
  is competitive with the cerid stock MS MARCO MiniLM but with
  meaningfully better recall on technical queries.

**Not recommended:** Cohere's commercial rerankers (API only — not
local) and stock MS MARCO MiniLM (no GGUF distribution at time of
writing; available as ONNX only via cerid's existing sidecar path).

---

## Whisper (future)

Cerid doesn't ship audio features yet, but Quenchforge exposes
`/v1/audio/transcriptions`.  When cerid adds audio ingest:

| Model | Quant | VRAM | Hint |
|---|---|---|---|
| `whisper-large-v3-turbo-q5_0` | Q5_0 | ~3 GB | Default — strong English |
| `whisper-medium-q5_0` | Q5_0 | ~1.5 GB | Low-tier hardware |

`QUENCHFORGE_WHISPER_MODEL=whisper-large-v3-turbo-q5_0`

> ⚠ Quenchforge defaults `QUENCHFORGE_WHISPER_GPU=false` because
> whisper.cpp Metal still has unpatched bugs on AMD Mac.  Even at CPU
> default it achieves "12.8× real-time on Xeon W-3245" per the
> Quenchforge README — acceptable for batch transcription, slow for
> live captioning.

---

## SPLADE-v3 sparse (NOT Quenchforge-routable)

Quenchforge has no sparse-encode endpoint as of v0.3.1.  Cerid's own
sidecar (`scripts/cerid-sidecar.py`) serves SPLADE at `/encode/sparse`,
giving GPU acceleration on Mac ARM64 (CoreML) and Linux (CUDA/ROCm) —
but NOT on Intel Mac + AMD where ONNX runtime has no execution
provider.

On Intel Mac + AMD, SPLADE-v3 runs CPU-only.  This is acceptable
because:
* SPLADE is opt-in (`RETRIEVAL_SPARSE_ENABLED=true`)
* The recommender surfaces it at 100+ docs
* The model is small (~140 MB FP32 / ~50 MB INT8)
* Encode time per query is ~30 ms on a Xeon W-3245 → acceptable for
  interactive search

If SPLADE encode time becomes a bottleneck on AMD Mac, file the
feature request upstream at `Cerid-AI/quenchforge` to add a sparse
endpoint.  Sparse is on Quenchforge's known-but-unscoped backlog.

---

## NLI verification (NOT Quenchforge-routable)

Cerid's NLI model (`cross-encoder/nli-deberta-v3-large` or similar)
runs on the local ONNX runtime hard-coded to CPU.  No sidecar
support today.  No Quenchforge endpoint.

On Intel Mac + AMD this is CPU-only.  Verification fires once per
claim per verified response — on average 3-6 NLI calls per chat turn.

Workarounds:
* Disable per-claim verification (`ENABLE_HALLUCINATION_CHECK=false`)
* Tune `HALLUCINATION_THRESHOLD` upward to reduce verifications
* Accept the CPU penalty (a 6-claim verification adds ~600 ms on a
  Xeon W-3245)

A future cycle could add NLI to the cerid sidecar — that would
unlock GPU on Mac ARM64 + Linux, still CPU on AMD Mac until
Quenchforge adds a classification endpoint.

---

## Disk layout

Quenchforge looks for GGUF files in:

```
~/Library/Application Support/Quenchforge/models/    # macOS default
$QUENCHFORGE_MODELS_DIR                              # if explicit
```

After downloading the recommended set above, the dir should look
like:

```
qwen2.5-7b-instruct-q4_k_m.gguf
nomic-embed-text-v1.5.Q8_0.gguf
bge-reranker-v2-m3.Q4_K_M.gguf
```

Total disk for the default trio: ~5 GB.

---

## Verify the routing

```bash
# After setting the env vars and starting quenchforge + cerid:
curl http://127.0.0.1:8898/health | jq .inference_routing
```

Expected (v0.93.8+) shape:

```json
{
  "llm":     {"provider": "quenchforge", "url": "http://localhost:11434"},
  "embed":   {"provider": "quenchforge", "url": "http://localhost:11434", "model": "nomic-embed-text-v1.5"},
  "rerank":  {"provider": "quenchforge", "url": "http://localhost:11434", "model": "bge-reranker-v2-m3"},
  "sparse":  {"provider": "in-process", "note": "Quenchforge has no sparse endpoint"},
  "nli":     {"provider": "in-process", "note": "CPU only on AMD Mac"}
}
```

If `provider` for `embed` or `rerank` shows `"sidecar"` or
`"in-process"` when you intended `quenchforge`, double-check the env
vars are exported in the cerid MCP container's environment (the
`.env` file, NOT just your shell).
