# Embedding Model Evaluation

> Evaluation of embedding models for Cerid AI's RAG pipeline.

## Current Model

**all-MiniLM-L6-v2** (ChromaDB server default)
- Dimensions: 384
- Parameters: 22M
- Max sequence length: 256 tokens
- MTEB average score: ~58 (STS benchmark)
- Speed: Very fast on CPU (~14,000 sentences/sec)
- License: Apache 2.0

ChromaDB computes embeddings server-side when documents or queries are passed
without explicit embedding vectors. No client-side embedding library is needed.

## Candidate Models

| Model | Dims | Params | MTEB Avg | Speed | Notes |
|-------|------|--------|----------|-------|-------|
| **all-MiniLM-L6-v2** (current) | 384 | 22M | ~58 | Very fast | Good baseline, fast |
| **bge-small-en-v1.5** | 384 | 33M | ~62 | Fast | Best drop-in (same dims, better quality) |
| **all-mpnet-base-v2** | 768 | 110M | ~63 | Medium | Higher quality, 2x storage |
| **nomic-embed-text-v1.5** | 768 | 137M | ~65 | Medium | Strong performance, Matryoshka dims |
| **bge-large-en-v1.5** | 1024 | 335M | ~64 | Slow | Diminishing returns vs size |

## Trade-offs

### Dimension Size
- 384-dim: ~1.5 KB per chunk embedding. Low storage, fast retrieval.
- 768-dim: ~3 KB per chunk. Moderate increase, better semantic capture.
- 1024-dim: ~4 KB per chunk. Marginal quality improvement for significant cost.

For a personal KB (hundreds to low thousands of chunks), storage is not a concern.
The main trade-off is CPU inference speed during ingestion.

### Migration Cost
Changing the embedding model requires **full re-ingestion**:
1. All ChromaDB collections must be deleted
2. All documents re-ingested to compute new embeddings
3. BM25 indexes are unaffected (text-based, model-independent)

With the current corpus (~400 chunks), re-ingestion takes under 5 minutes.

## Recommendation

**Stick with all-MiniLM-L6-v2 for now.** The quality delta for a small personal KB
is marginal. The hybrid search pipeline (BM25 + vector + graph + reranking) compensates
for embedding model limitations through complementary retrieval signals.

If quality becomes a concern:
1. **First upgrade:** bge-small-en-v1.5 — same 384 dimensions, better quality, drop-in compatible
2. **Full upgrade:** nomic-embed-text-v1.5 — significant quality boost, requires 768-dim collections

## Configuration

The embedding model is configurable via environment variables:

```bash
EMBEDDING_MODEL=all-MiniLM-L6-v2   # default
EMBEDDING_DIMENSIONS=384             # must match model output
```

To swap models in the future:
1. Set `EMBEDDING_MODEL` to the new model name
2. Update `EMBEDDING_DIMENSIONS` to match
3. Confirm the model is wired in `src/mcp/core/utils/embeddings.py`
   (the `OnnxEmbeddingFunction` is already generic — most HF models
   work without code changes)
4. Re-ingest all documents

## ONNX execution provider

Local inference goes through ONNX Runtime via
[`src/mcp/core/utils/embeddings.py`](../src/mcp/core/utils/embeddings.py),
which calls
[`core.utils.onnx_providers.resolve_providers()`](../src/mcp/core/utils/onnx_providers.py)
to pick the best available provider for the host. The default priority is:

1. `CUDAExecutionProvider`   — Linux + NVIDIA GPU
2. `ROCMExecutionProvider`   — Linux + AMD GPU
3. `CoreMLExecutionProvider` — macOS Apple Silicon
4. `DmlExecutionProvider`    — Windows DirectML
5. `CPUExecutionProvider`    — universal fallback (always appended)

Unavailable providers degrade gracefully — the resolver intersects the
preference list with `ort.get_available_providers()`, so a CUDA host
without the CUDA wheel installed silently lands on CPU instead of
crashing on session creation.

Operators can override the priority order via
`ONNX_EXECUTION_PROVIDERS` (comma-separated, in order). The same
resolver is reused by the cross-encoder reranker
([`core.retrieval.reranker`](../src/mcp/core/retrieval/reranker.py)) so
both inference paths track the same hardware policy.

The session loads at first request; the chosen provider list is logged
to `ai-companion.onnx_providers` at INFO so operators can confirm GPU
pickup post-deploy.
