# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""HTTP client for the FastEmbed sidecar server.

Provides embed() and rerank() functions that call the sidecar's REST API
with circuit breaker protection and latency tracking.

Usage:
    from utils.inference_sidecar_client import sidecar_embed, sidecar_rerank
    embeddings = await sidecar_embed(["hello world"])
    scores = await sidecar_rerank("query", ["doc1", "doc2"])
"""
from __future__ import annotations

import logging
import time

import httpx

from utils.circuit_breaker import get_breaker

logger = logging.getLogger("ai-companion.sidecar")

_client: httpx.AsyncClient | None = None


def _get_sidecar_url() -> str:
    from config.settings import CERID_SIDECAR_URL
    return CERID_SIDECAR_URL


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _client


async def sidecar_embed(
    texts: list[str],
    is_query: bool = False,
) -> list[list[float]]:
    """Embed texts via the sidecar server.

    Returns list of embedding vectors. Raises on failure (caller should
    fall back to local ONNX).
    """
    breaker = get_breaker("sidecar")
    url = _get_sidecar_url()
    client = await _get_client()

    t0 = time.perf_counter()

    async def _call():
        resp = await client.post(
            f"{url}/embed",
            json={"texts": texts, "is_query": is_query},
        )
        resp.raise_for_status()
        return resp.json()

    data = await breaker.call(_call)
    latency_ms = (time.perf_counter() - t0) * 1000

    # Update inference config with measured latency
    try:
        from utils.inference_config import get_inference_config
        cfg = get_inference_config()
        # Exponential moving average
        if cfg.embed_latency_ms > 0:
            cfg.embed_latency_ms = cfg.embed_latency_ms * 0.7 + latency_ms * 0.3
        else:
            cfg.embed_latency_ms = latency_ms
    except Exception as exc:  # noqa: BLE001
        logger.debug("Latency tracking failed: %s", exc)

    embeddings = data["embeddings"]

    # Dimension validation: ChromaDB requires consistent 768-dim vectors
    if embeddings:
        from config.settings import EMBEDDING_DIMENSIONS
        expected = EMBEDDING_DIMENSIONS if EMBEDDING_DIMENSIONS > 0 else 768
        actual = len(embeddings[0])
        if actual != expected:
            raise ValueError(
                f"Sidecar embedding dimension mismatch: expected {expected}, got {actual}. "
                "Check sidecar model configuration."
            )

    logger.debug("Sidecar embed: %d texts in %.1fms", len(texts), latency_ms)
    return embeddings


async def sidecar_rerank(
    query: str,
    documents: list[str],
) -> list[float]:
    """Rerank documents via the sidecar server.

    Returns list of relevance scores. Raises on failure.
    """
    breaker = get_breaker("sidecar")
    url = _get_sidecar_url()
    client = await _get_client()

    t0 = time.perf_counter()

    async def _call():
        resp = await client.post(
            f"{url}/rerank",
            json={"query": query, "documents": documents},
        )
        resp.raise_for_status()
        return resp.json()

    data = await breaker.call(_call)
    latency_ms = (time.perf_counter() - t0) * 1000

    # Update inference config with measured latency
    try:
        from utils.inference_config import get_inference_config
        cfg = get_inference_config()
        if cfg.rerank_latency_ms > 0:
            cfg.rerank_latency_ms = cfg.rerank_latency_ms * 0.7 + latency_ms * 0.3
        else:
            cfg.rerank_latency_ms = latency_ms
    except Exception as exc:  # noqa: BLE001
        logger.debug("Latency tracking failed: %s", exc)

    logger.debug("Sidecar rerank: %d docs in %.1fms", len(documents), latency_ms)
    return data["scores"]


async def sidecar_health() -> dict | None:
    """Check sidecar health. Returns health dict or None if unreachable."""
    try:
        client = await _get_client()
        url = _get_sidecar_url()
        resp = await client.get(f"{url}/health", timeout=2)
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Sidecar health check failed: %s", exc)
    return None


async def close():
    """Shutdown the client."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None
