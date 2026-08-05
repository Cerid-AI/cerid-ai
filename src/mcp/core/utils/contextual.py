# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Contextual chunking — LLM-generated situational summaries.

Inspired by Anthropic's "Contextual Retrieval" technique.  During ingestion,
each chunk receives a 1-2 sentence situational summary explaining how it fits
within the broader document.  This summary is prepended to the chunk text
before embedding and BM25 indexing, dramatically improving retrieval precision.

Example::

    Before: "The quarterly revenue increased by 15% compared to Q2."
    After:  "[From Q3 2025 financial report — revenue growth section]
             The quarterly revenue increased by 15% compared to Q2."

Runs synchronously (ingestion is sync).  LLM calls go through
``core.utils.internal_llm.call_internal_llm`` with ``stage="contextual_chunks"``
so provider + model selection flow through the per-stage registry
(``PROVIDER_STAGE_CONTEXTUAL_CHUNKS`` env / ``config.stage_profiles``) rather
than any hardcoded vendor model.  We bridge sync→async by running the coroutine
on a short-lived event loop inside a worker thread so we never touch the main
thread's loop policy — this keeps pytest fixtures that rely on
``asyncio.get_event_loop()`` safe from pollution.

Cost/latency is bounded three ways (Phase 2.6): batching
(``CONTEXTUAL_CHUNK_BATCH_SIZE`` chunks per call), a per-call wall-clock budget
(``CONTEXTUAL_CHUNK_LLM_TIMEOUT``), and a per-artifact cap
(``CONTEXTUAL_CHUNKS_MAX_PER_ARTIFACT``).  Every failure path degrades
gracefully — the chunk is ingested WITHOUT a context prefix (never a failed
ingest) — and is recorded via ``log_swallowed_error``.
"""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import config
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.contextual")

# Named fallback defaults — used only if the config knob is absent. The live
# values come from config (env-overridable); see config/features.py.
_DEFAULT_BATCH_SIZE = 5
_DEFAULT_LLM_TIMEOUT = 30.0
_DEFAULT_MAX_CHUNKS = 200
# Prompt-shaping widths: how much of the document / each chunk to show the LLM.
_DOC_PREVIEW_CHARS = 3000
_CHUNK_PREVIEW_CHARS = 300


def _run_coro_isolated(coro):
    """Run an async coroutine from a sync context without disturbing the
    calling thread's event-loop state.

    Uses a thread-pool worker with a dedicated new loop so
    ``asyncio.get_event_loop()`` on the caller's thread is unaffected.
    """
    def _runner():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_runner).result()


def contextualize_chunks(
    chunks: list[str],
    full_text: str,
    metadata: dict[str, Any] | None = None,
) -> list[str]:
    """Add LLM-generated situational context to each chunk.

    Batches chunks into groups of ``CONTEXTUAL_CHUNK_BATCH_SIZE`` to minimise
    LLM calls.  Each call produces a short context prefix per chunk.

    Cost guard: at most ``CONTEXTUAL_CHUNKS_MAX_PER_ARTIFACT`` chunks pay an
    LLM call; any beyond the cap pass through un-prefixed so a pack of hundreds
    of chunks cannot hang ingestion.  Worst-case call count is therefore
    ceil(cap / batch_size).

    Returns a new list of chunks with context prepended.  On any failure the
    affected chunk is returned unchanged (never a failed ingest).
    """
    if not chunks or not config.ENABLE_CONTEXTUAL_CHUNKS:
        return chunks

    filename = (metadata or {}).get("filename", "unknown document")
    domain = (metadata or {}).get("domain", "")

    # Truncate full_text to avoid exceeding LLM context.
    doc_preview = full_text[:_DOC_PREVIEW_CHARS]
    if len(full_text) > _DOC_PREVIEW_CHARS:
        doc_preview += "\n[... document continues ...]"

    batch_size = int(getattr(config, "CONTEXTUAL_CHUNK_BATCH_SIZE", _DEFAULT_BATCH_SIZE))
    if batch_size < 1:
        batch_size = _DEFAULT_BATCH_SIZE
    max_per_artifact = int(
        getattr(config, "CONTEXTUAL_CHUNKS_MAX_PER_ARTIFACT", _DEFAULT_MAX_CHUNKS)
    )

    # Per-artifact cap: only the first ``max_per_artifact`` chunks are enriched;
    # the tail passes through un-prefixed. ``max_per_artifact <= 0`` disables the
    # cap (unbounded — operator escape hatch).
    if max_per_artifact > 0:
        to_enrich = chunks[:max_per_artifact]
        passthrough = chunks[max_per_artifact:]
    else:
        to_enrich = chunks
        passthrough = []

    enriched: list[str] = []
    for batch_start in range(0, len(to_enrich), batch_size):
        batch = to_enrich[batch_start : batch_start + batch_size]
        contexts = _generate_contexts(batch, doc_preview, filename, domain)

        for chunk, ctx in zip(batch, contexts):
            if ctx:
                enriched.append(f"[{ctx}]\n{chunk}")
            else:
                enriched.append(chunk)

    enriched.extend(passthrough)
    return enriched


def _generate_contexts(
    chunks: list[str],
    doc_preview: str,
    filename: str,
    domain: str,
) -> list[str]:
    """Call the LLM to generate situational contexts for a batch of chunks.

    Returns a list of context strings (one per chunk).  On failure, returns
    empty strings so chunks pass through unchanged.
    """
    chunk_texts = ""
    for i, chunk in enumerate(chunks):
        # Show only the head of each chunk to keep the prompt compact.
        preview = chunk[:_CHUNK_PREVIEW_CHARS].replace("\n", " ").strip()
        chunk_texts += f"\n[CHUNK {i}] {preview}"

    prompt = (
        f"You are helping improve search retrieval for a knowledge base.\n"
        f"Document: {filename}" + (f" (domain: {domain})" if domain else "") + "\n\n"
        f"Document preview:\n{doc_preview}\n\n"
        f"For each chunk below, write a SHORT context phrase (under 20 words) that "
        f"describes what this section covers within the broader document. "
        f"Focus on WHO/WHAT/WHEN — not opinions.\n"
        f"{chunk_texts}\n\n"
        f"Respond with a JSON array of strings, one per chunk. "
        f"Example: [\"Q3 revenue discussion in annual report\", "
        f"\"API authentication setup in developer guide\"]"
    )

    try:
        # Route via call_internal_llm so provider + model selection flow
        # through the per-stage registry (stage="contextual_chunks"): the
        # call honors INTERNAL_LLM_PROVIDER (e.g. quenchforge for on-device
        # GPU) or a PROVIDER_STAGE_CONTEXTUAL_CHUNKS override, and picks the
        # model from config.stage_profiles — never a hardcoded vendor id.
        # This call fires once per BATCH (CONTEXTUAL_CHUNK_BATCH_SIZE chunks),
        # not once per chunk. A per-call wall-clock budget bounds a slow/hung
        # local slot so ingest degrades to un-prefixed chunks instead of
        # stalling for the full httpx timeout × retries.
        from core.utils.internal_llm import call_internal_llm

        timeout = float(
            getattr(config, "CONTEXTUAL_CHUNK_LLM_TIMEOUT", _DEFAULT_LLM_TIMEOUT)
        )
        content = _run_coro_isolated(
            asyncio.wait_for(
                call_internal_llm(
                    [{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=300,
                    stage="contextual_chunks",
                ),
                timeout=timeout,
            )
        )
        content = (content or "").strip()

        # Parse JSON array from response
        # Handle markdown code blocks
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        contexts = json.loads(content)
        if isinstance(contexts, list) and len(contexts) == len(chunks):
            return [str(c) for c in contexts]

        logger.warning(
            "Contextual enrichment returned %d contexts for %d chunks",
            len(contexts) if isinstance(contexts, list) else 0,
            len(chunks),
        )
        return [""] * len(chunks)

    except (json.JSONDecodeError, KeyError, IndexError) as e:
        # Malformed LLM output — swallow so ingest continues un-prefixed, but
        # record it so /health.swallowed_errors_last_hour stays truthful.
        log_swallowed_error("core.utils.contextual.generate_contexts", e)
        return [""] * len(chunks)
    except Exception as e:  # noqa: BLE001 — defensive catch for httpx/timeout/circuit-breaker errors
        # Transport failure, per-call timeout (asyncio.TimeoutError), or
        # circuit-open — degrade to un-prefixed chunks; never fail the ingest.
        log_swallowed_error("core.utils.contextual.generate_contexts", e)
        return [""] * len(chunks)
