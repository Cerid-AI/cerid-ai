# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Quenchforge REST client for GPU-accelerated embeddings + reranking
on Intel Mac + AMD discrete GPU hardware (v0.93.8).

Quenchforge already serves the LLM chat path (``core.utils.internal_llm``
+ ``core.routing.smart_router``).  This module extends GPU acceleration
to the embeddings and reranking workloads, which on Intel Mac + AMD
fall back to CPU through ONNX runtime (no AMD-Mac ONNX execution
provider exists; ROCm is Linux-only).

Endpoints used:

* ``POST /v1/embeddings``  — OpenAI-compatible wire format.
* ``POST /v1/rerank``      — OpenAI-compatible wire format.

Both endpoints are opt-in per-feature via:

* ``EMBEDDINGS_PROVIDER=quenchforge`` (default ``sidecar`` keeps
  current behavior)
* ``RERANK_PROVIDER=quenchforge``      (default ``sidecar``)

When opted in, the operator MUST also set the corresponding Quenchforge
side env var so the daemon loads the right model:

* ``QUENCHFORGE_EMBED_MODEL``   — must produce the same dimensionality
  as ``EMBEDDING_DIMENSIONS`` (default 768) or ChromaDB will reject the
  vectors at insert time.  The client validates this on the first
  response and fails loud rather than silently corrupting the index.
* ``QUENCHFORGE_RERANK_MODEL``  — opt-in; not yet dimension-checked
  because reranker scores are scalars.

The circuit breaker is shared with the LLM-chat path (``quenchforge``
breaker name) so a single Quenchforge outage trips ALL Quenchforge
consumers at once, with the existing fall-through to ONNX-sidecar +
in-process providers.

SPLADE-v3 is intentionally NOT routed here — Quenchforge does not
expose a sparse-encode endpoint as of v0.3.1.  Cerid's own sidecar at
``scripts/cerid-sidecar.py`` continues to serve SPLADE.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from core.utils.circuit_breaker import get_breaker

logger = logging.getLogger("ai-companion.quenchforge")

_client: httpx.AsyncClient | None = None


def _get_quenchforge_url() -> str:
    """Resolve the Quenchforge base URL.

    Mirrors the routing in ``core/utils/internal_llm.py`` and
    ``app/routers/ollama_proxy.py`` — falls through to ``OLLAMA_URL``
    when ``QUENCHFORGE_URL`` is unset so a single-service install at
    the default port 11434 keeps working.
    """
    return (
        os.getenv("QUENCHFORGE_URL")
        or os.getenv("OLLAMA_URL")
        or "http://localhost:11434"
    )


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _client


def is_embeddings_provider_quenchforge() -> bool:
    """True when the operator picked Quenchforge for the embedder."""
    return os.getenv("EMBEDDINGS_PROVIDER", "sidecar").strip().lower() == "quenchforge"


def is_rerank_provider_quenchforge() -> bool:
    """True when the operator picked Quenchforge for the reranker."""
    return os.getenv("RERANK_PROVIDER", "sidecar").strip().lower() == "quenchforge"


async def quenchforge_embed(
    texts: list[str],
    is_query: bool = False,
) -> list[list[float]]:
    """Embed texts via Quenchforge's OpenAI-compatible /v1/embeddings.

    Returns list of embedding vectors of dimension ``EMBEDDING_DIMENSIONS``.
    Raises ``ValueError`` on dimension mismatch and on any HTTP failure
    so the caller can fall back to the ONNX-sidecar path.

    The ``is_query`` flag is accepted for API symmetry with
    :func:`utils.inference_sidecar_client.sidecar_embed` but Quenchforge
    doesn't expose a query/passage prefix toggle on the OpenAI wire —
    the model treats both identically.  If the configured Quenchforge
    embedding model requires asymmetric prompting, the operator must
    pre-prefix client-side.
    """
    _ = is_query  # accepted for symmetry
    breaker = get_breaker("quenchforge")
    url = _get_quenchforge_url()
    client = await _get_client()
    model = os.getenv("QUENCHFORGE_EMBED_MODEL", "")
    if not model:
        raise RuntimeError(
            "EMBEDDINGS_PROVIDER=quenchforge requires QUENCHFORGE_EMBED_MODEL "
            "to be set so the daemon knows which model to load.",
        )

    t0 = time.perf_counter()

    async def _call():
        resp = await client.post(
            f"{url}/v1/embeddings",
            json={"model": model, "input": texts},
        )
        resp.raise_for_status()
        return resp.json()

    data = await breaker.call(_call)
    latency_ms = (time.perf_counter() - t0) * 1000

    # OpenAI wire: {"data": [{"embedding": [...], "index": 0}, ...]}
    items = data.get("data") or []
    # Sort by index to defend against out-of-order responses.
    items.sort(key=lambda x: x.get("index", 0))
    embeddings = [item.get("embedding", []) for item in items]

    if embeddings:
        from config.settings import EMBEDDING_DIMENSIONS
        expected = EMBEDDING_DIMENSIONS if EMBEDDING_DIMENSIONS > 0 else 768
        actual = len(embeddings[0])
        if actual != expected:
            raise ValueError(
                f"Quenchforge embedding dimension mismatch: expected {expected}, "
                f"got {actual}.  Set QUENCHFORGE_EMBED_MODEL to a model whose "
                f"output dimension matches EMBEDDING_DIMENSIONS, or re-embed "
                f"the corpus at the new dimension.",
            )

    # Latency tracking — separate from the sidecar EMA so we can see
    # the GPU vs CPU split when both are configured.
    try:
        from utils.inference_config import get_inference_config
        cfg = get_inference_config()
        if cfg.embed_latency_ms > 0:
            cfg.embed_latency_ms = cfg.embed_latency_ms * 0.7 + latency_ms * 0.3
        else:
            cfg.embed_latency_ms = latency_ms
    except Exception as exc:  # noqa: BLE001 — latency tracking is best-effort
        logger.debug("Latency tracking failed: %s", exc)

    logger.debug(
        "Quenchforge embed: %d texts in %.1fms (model=%s)",
        len(texts), latency_ms, model,
    )
    return embeddings


async def quenchforge_rerank(
    query: str,
    documents: list[str],
) -> list[float]:
    """Rerank via Quenchforge's OpenAI-compatible /v1/rerank.

    Returns list of relevance scores aligned with ``documents``.  Raises
    on failure so the caller can fall back to the ONNX-sidecar reranker.

    Quenchforge's /v1/rerank follows the Cohere/Voyage convention —
    response carries ``{"results": [{"index": i, "relevance_score": s}, ...]}``
    in arbitrary order.  We sort by index and emit the scores aligned
    with the original document order so the caller's existing
    sigmoid-clipped score contract stays intact.
    """
    breaker = get_breaker("quenchforge")
    url = _get_quenchforge_url()
    client = await _get_client()
    model = os.getenv("QUENCHFORGE_RERANK_MODEL", "")
    if not model:
        raise RuntimeError(
            "RERANK_PROVIDER=quenchforge requires QUENCHFORGE_RERANK_MODEL "
            "to be set so the daemon knows which model to load.",
        )

    t0 = time.perf_counter()

    async def _call():
        resp = await client.post(
            f"{url}/v1/rerank",
            json={"model": model, "query": query, "documents": documents},
        )
        resp.raise_for_status()
        return resp.json()

    data = await breaker.call(_call)
    latency_ms = (time.perf_counter() - t0) * 1000

    results = data.get("results") or []
    # Build an index → score map; missing indices get 0.0 so the output
    # always matches the input length.
    by_idx = {int(r.get("index", -1)): float(r.get("relevance_score", 0.0)) for r in results}
    scores = [by_idx.get(i, 0.0) for i in range(len(documents))]

    try:
        from utils.inference_config import get_inference_config
        cfg = get_inference_config()
        if cfg.rerank_latency_ms > 0:
            cfg.rerank_latency_ms = cfg.rerank_latency_ms * 0.7 + latency_ms * 0.3
        else:
            cfg.rerank_latency_ms = latency_ms
    except Exception as exc:  # noqa: BLE001 — latency tracking is best-effort
        logger.debug("Latency tracking failed: %s", exc)

    logger.debug(
        "Quenchforge rerank: %d docs in %.1fms (model=%s)",
        len(documents), latency_ms, model,
    )
    return scores


async def quenchforge_health() -> dict | None:
    """Probe the Quenchforge daemon's canonical /health endpoint.

    Returns ``{"status": "ok"}`` on success or ``None`` if the daemon
    is unreachable.  /health is intentionally lighter than /api/tags
    (no models-dir walk) and is the endpoint the Quenchforge gateway
    exposes for liveness probes — verified directly against the
    upstream gateway.go.

    Used by the setup wizard's recommend-active-backend flow and by
    the health endpoint's circuit-breaker introspection.
    """
    try:
        client = await _get_client()
        url = _get_quenchforge_url()
        resp = await client.get(f"{url}/health", timeout=2)
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:  # noqa: BLE001 — health probe is best-effort
        logger.debug("Quenchforge health probe failed: %s", exc)
    return None


async def close() -> None:
    """Shutdown the shared httpx client.  Called from the app lifespan."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None
