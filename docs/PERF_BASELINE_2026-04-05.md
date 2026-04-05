# Performance Baseline — 2026-04-05

**Platform:** Docker ARM64 (Apple M1, 16GB unified memory)
**Version:** v0.80 + Phase 1 InferenceConfig
**Inference:** onnx-cpu (Docker default, no GPU passthrough)

## Measurements

| Metric | Value | Notes |
|--------|-------|-------|
| **Cold startup** | 42.8s | Includes Docker restart + ONNX model download/load |
| **Ingest latency** | ~8,000ms (avg 3 runs) | Embedding + ChromaDB + Neo4j write |
| **Query e2e (cold)** | 36,505ms | First query — LLM cold start via Bifrost |
| **Query e2e (warm)** | ~89ms | Subsequent queries (semantic cache hit) |
| **Health check** | ~49ms (warm) | 10s cache TTL; first call ~113ms |
| **MCP container RSS** | 1,009 MiB / 6 GiB limit | ONNX models in memory |
| **Frontend container** | 7.8 MiB / 256 MiB limit | Nginx serving built assets |
| **Redis** | 21.9 MiB / 2 GiB limit | LRU eviction at 1GB |
| **ChromaDB** | 161.6 MiB / 1 GiB limit | 2 collections |
| **Neo4j** | 721.7 MiB / 4 GiB limit | 8 artifacts + schema |

## Raw Data

```json
{
  "cold_startup_s": 42.8,
  "ingest_latency_ms": [9101.0, 8792.9, 6101.5],
  "query_simple_ms": [36504.9, 121.8, 56.0],
  "memory_usage": {
    "ai-companion-mcp": "1009MiB / 6GiB",
    "cerid-web": "7.793MiB / 256MiB",
    "ai-companion-redis": "21.91MiB / 2GiB",
    "ai-companion-chroma": "161.6MiB / 1GiB",
    "ai-companion-neo4j": "721.7MiB / 4GiB"
  },
  "health_latency_ms": [112.8, 45.7, 49.4]
}
```

## Inference Detection

```json
{
  "provider": "onnx-cpu",
  "tier": "degraded",
  "platform": "linux-arm",
  "gpu": false,
  "onnx_providers": ["CPUExecutionProvider"],
  "message": "CPU-only inference (Docker default)"
}
```

## Observations

1. **Cold startup is high (42.8s)** — dominated by ONNX model download from HuggingFace cache + model load. Phase 2 will add background warmup.
2. **First query extremely slow (36.5s)** — LLM cold start via Bifrost/OpenRouter. Subsequent queries hit semantic cache (~89ms).
3. **Ingest latency ~8s** — includes embedding (ONNX), ChromaDB write, Neo4j relationship creation. Expected for CPU-only.
4. **Memory reasonable** — MCP at ~1GB is within the 1.5GB target for ONNX models loaded.
5. **Inference detection working** — correctly identifies Docker ARM64 as CPU-only degraded tier.
