# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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
``core.utils.llm_client.call_llm`` (OpenRouter direct); we bridge sync→async
by running the coroutine on a short-lived event loop inside a worker thread
so we never touch the main thread's loop policy — this keeps pytest fixtures
that rely on ``asyncio.get_event_loop()`` safe from pollution.
"""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import config

logger = logging.getLogger("ai-companion.contextual")

_TIMEOUT = 30.0


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

    Batches chunks into groups to minimise LLM calls.  Each call produces
    a short context prefix per chunk.

    Returns a new list of chunks with context prepended.  On failure,
    returns the original chunks unchanged.
    """
    if not chunks or not config.ENABLE_CONTEXTUAL_CHUNKS:
        return chunks

    filename = (metadata or {}).get("filename", "unknown document")
    domain = (metadata or {}).get("domain", "")

    # Truncate full_text to avoid exceeding LLM context (keep first ~3000 chars)
    doc_preview = full_text[:3000]
    if len(full_text) > 3000:
        doc_preview += "\n[... document continues ...]"

    # Process in batches of 5 to reduce API calls
    batch_size = 5
    enriched: list[str] = []

    for batch_start in range(0, len(chunks), batch_size):
        batch = chunks[batch_start : batch_start + batch_size]
        contexts = _generate_contexts(batch, doc_preview, filename, domain)

        for chunk, ctx in zip(batch, contexts):
            if ctx:
                enriched.append(f"[{ctx}]\n{chunk}")
            else:
                enriched.append(chunk)

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
        # Show first 300 chars of each chunk to keep prompt compact
        preview = chunk[:300].replace("\n", " ").strip()
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
        from core.utils.llm_client import call_llm

        content = _run_coro_isolated(
            call_llm(
                [{"role": "user", "content": prompt}],
                model=config.CONTEXTUAL_CHUNKS_MODEL,
                temperature=0.0,
                max_tokens=300,
                timeout=_TIMEOUT,
                breaker_name="bifrost-synopsis",
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
        logger.warning("Contextual chunk enrichment failed: %s", e)
        return [""] * len(chunks)
    except Exception as e:  # pragma: no cover - defensive catch (httpx / circuit-breaker)
        logger.warning("Contextual chunk enrichment failed (unexpected): %s", e)
        return [""] * len(chunks)
