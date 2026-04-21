"""Utils — shared infrastructure for retrieval, LLM, caching, and resilience.

Key modules:
  llm_client.py      — Direct OpenRouter HTTP calls with retry
  smart_router.py    — Capability-based model scoring and routing
  circuit_breaker.py — Circuit breaker registry for external calls
  embeddings.py      — ONNX embedding function (Arctic Embed M v1.5, 768-dim)
  chunker.py         — Semantic text chunking with overlap
  semantic_cache.py  — Quantized int8 query cache with HNSW index
  error_handler.py   — @handle_errors() decorator (the ONE error pattern)
  retrieval_cache.py — Redis cache with cerid:{domain}:{key} prefix convention
  degradation.py     — Graceful degradation when services are unavailable
"""
