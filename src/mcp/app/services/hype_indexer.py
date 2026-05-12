# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""HyPE indexer — orchestration layer that ties hype_index.py to storage.

Phase R.3.  This module owns the decision of *how* HyPE data is stored and
provides the single entry-point called by :class:`HyPEIndexingJob`.

Storage choice: parallel ChromaDB collection
--------------------------------------------
Each base collection ``cerid_{domain}`` gets a companion ``cerid_{domain}_hype``
collection.  Each row in the HyPE collection stores one generated question as
the ``document`` and carries ``source_chunk_id`` + ``source_artifact_id`` in
its metadata so retrieval-time dedup can map it back.

Why not inline metadata?  ChromaDB metadata values are scalars; a list of
384-dimensional vectors can't be stored there without JSON-serialisation, which
bypasses the HNSW index entirely.  A separate collection preserves ANN search.
See :mod:`core.retrieval.hype_index` for the full rationale.

Flag gate
---------
``RETRIEVAL_HYPE_ENABLED`` (env var, default ``"false"``).  When false,
``index_chunk_with_hype`` returns early with ``{"enabled": False}`` — no LLM
call, no embed call, no storage write.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.hype_indexer")

# ---------------------------------------------------------------------------
# Flag gate
# ---------------------------------------------------------------------------

def _hype_enabled() -> bool:
    """Return True when ``RETRIEVAL_HYPE_ENABLED`` is explicitly set to a
    truthy value.  Default is ``false`` until eval gate is cleared."""
    val = os.environ.get("RETRIEVAL_HYPE_ENABLED", "false").strip().lower()
    return val in ("true", "1", "yes", "on")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def index_chunk_with_hype(
    chunk_id: str,
    content: str,
    *,
    collection_name: str,
    artifact_id: str,
    chroma: Any | None = None,
    embed_fn: Any | None = None,
    n: int = 5,
) -> dict[str, Any]:
    """Generate HyPE questions for ``chunk_id`` and store them in Chroma.

    Parameters
    ----------
    chunk_id:
        The primary chunk's ChromaDB ID (used as the parent reference in
        HyPE metadata).
    content:
        The chunk's text content.
    collection_name:
        The primary collection name (e.g. ``"cerid_general"``).  The HyPE
        collection name is derived automatically.
    artifact_id:
        The artifact ID of the parent chunk (for metadata provenance).
    chroma:
        ChromaDB client.  When ``None`` and the flag is enabled, the function
        falls back to ``app.deps.get_chroma()``.
    embed_fn:
        Async callable ``(text) → embedding_vector``.  When ``None`` and the
        flag is enabled, the function falls back to the default embedding
        singleton.
    n:
        Number of hypothetical questions to generate (default 5).

    Returns
    -------
    dict
        ``{"enabled": False}`` when the flag is off.
        ``{"enabled": True, "n_prompts": int, "total_tokens": int}`` on
        success (tokens is an estimate — Ollama doesn't return actuals).
    """
    if not _hype_enabled():
        return {"enabled": False}

    # Lazy imports to avoid import-time penalty when flag is off.
    from core.retrieval.hype_index import (
        build_hype_doc_id,
        build_hype_metadata,
        default_hype_llm_caller,
        embed_hype_prompts,
        generate_hype_prompts,
        hype_collection_name,
    )

    # Resolve chroma + embed_fn from app layer when not injected.
    if chroma is None:
        from app.deps import get_chroma
        chroma = get_chroma()

    if embed_fn is None:
        embed_fn = _default_embed_fn()

    try:
        # 1. Generate questions
        questions = await generate_hype_prompts(
            content,
            n=n,
            llm_caller=default_hype_llm_caller,
        )
        if not questions:
            # Empty content — nothing to index.
            logger.debug("hype_indexer.empty_content chunk_id=%s", chunk_id)
            return {"enabled": True, "n_prompts": 0, "total_tokens": 0}

        # 2. Embed questions
        hype_prompts = await embed_hype_prompts(questions, embed_fn=embed_fn)

        # 3. Store in parallel HyPE collection
        hype_coll_name = hype_collection_name(collection_name)
        hype_coll = await asyncio.to_thread(
            chroma.get_or_create_collection, name=hype_coll_name
        )

        doc_ids = [
            build_hype_doc_id(chunk_id, i) for i in range(len(hype_prompts))
        ]
        documents = [p.question for p in hype_prompts]
        embeddings = [p.embedding for p in hype_prompts]
        metadatas = [
            build_hype_metadata(
                source_chunk_id=chunk_id,
                source_artifact_id=artifact_id,
                prompt=p,
                question_index=i,
            )
            for i, p in enumerate(hype_prompts)
        ]

        await asyncio.to_thread(
            hype_coll.upsert,
            ids=doc_ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        # ~3000 in / 800 out per chunk (Ollama estimate)
        est_tokens = 3_000 + 800
        logger.info(
            "hype_indexer.done chunk_id=%s n_prompts=%d",
            chunk_id, len(hype_prompts),
        )
        return {"enabled": True, "n_prompts": len(hype_prompts), "total_tokens": est_tokens}

    except Exception as exc:
        log_swallowed_error(
            "app.services.hype_indexer.index_chunk",
            exc,
            context={"chunk_id": chunk_id, "artifact_id": artifact_id},
        )
        raise


# ---------------------------------------------------------------------------
# Default embed function (production wiring)
# ---------------------------------------------------------------------------

def _default_embed_fn():  # type: ignore[return]
    """Return the default async embed function backed by the embedding singleton.

    The embedding singleton (``get_embedding_function()``) accepts a list of
    texts and returns a list of vectors.  We wrap the single-text case so the
    HyPE indexer can call ``embed_fn(text)`` with a plain string.
    """
    async def _embed(text: str) -> list[float]:
        from core.utils.embeddings import get_embedding_function
        ef = get_embedding_function()
        if ef is None:
            # ChromaDB server-default path — no client-side embedder.
            # Return a zero vector; the caller will store it and the
            # Chroma server will re-embed at query time.
            from core.utils.embeddings import get_embedding_dim
            return [0.0] * get_embedding_dim()
        result: list[list[float]] = await asyncio.to_thread(ef, [text])
        return list(result[0])

    return _embed
