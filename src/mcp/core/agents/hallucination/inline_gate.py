# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Inline NLI gating — suppress evidence-contradicted sentences mid-stream.

The verification layer's default mode is *post-hoc*: generate the whole answer,
then extract claims and verify them (``verify_claim`` / ``check_hallucinations``).
That surfaces a warning after the fact but cannot stop a contradicted sentence
from reaching the reader.

This module adds the *inline* mode. It consumes a token stream (from
:func:`core.utils.internal_llm.call_internal_llm_stream`), buffers to sentence
boundaries, runs ``NLI(evidence ⊨ sentence)`` per sentence, and drops any
sentence the retrieved evidence contradicts before it is yielded downstream —
true mid-stream gating. Non-contradicted sentences pass through unchanged, so
the streamed reading experience is preserved.

Gating is opt-in via ``config.ENABLE_INLINE_NLI_GATING`` at the call site; this
module itself is always safe to import (pure ``core``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

import config
from core.agents.hallucination.enums import NLIUse
from core.utils.nli import nli_score_async
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.inline_gate")

# A sentence boundary: terminal punctuation followed by whitespace or end. Kept
# deliberately simple — over-splitting only means an extra (cheap, coalesced)
# NLI call, never a correctness problem.
_SENTENCE_END = re.compile(r"[.!?](\s+|$)")

# NLI premise cap — matches the KB-gate cap in verify_claim; DeBERTa truncates
# at 512 tokens anyway, and the evidence lead carries the salient facts.
_PREMISE_CAP = 512


async def inline_nli_gate(
    token_stream: AsyncIterator[str],
    *,
    context: str,
    use: NLIUse = NLIUse.SYNTHESIS_GATE,
    contradiction_ceiling: float | None = None,
    on_suppress: Callable[[str, dict[str, Any]], None] | None = None,
) -> AsyncIterator[str]:
    """Gate a streamed completion sentence-by-sentence against evidence.

    Buffers ``token_stream`` fragments until a sentence boundary, runs
    ``NLI(context ⊨ sentence)``, and SUPPRESSES any sentence the evidence
    contradicts (``contradiction >= ceiling``) before it reaches the consumer.
    Everything else is yielded verbatim, so passthrough is loss-free.

    Fails OPEN: if NLI errors, or if there is no evidence context, the sentence
    passes through — the gate never manufactures suppression on its own failure.

    Args:
        token_stream: async iterator of content fragments (any chunking).
        context: retrieved evidence the answer must not contradict.
        use: which NLI call site this is (observability / future threshold bands).
        contradiction_ceiling: suppress at/above this contradiction probability;
            defaults to ``config.NLI_CONTRADICTION_THRESHOLD``.
        on_suppress: optional callback ``(sentence, nli_scores)`` for each dropped
            sentence (e.g. to record a metric or ledger entry).
    """
    ceiling = (
        contradiction_ceiling
        if contradiction_ceiling is not None
        else getattr(config, "NLI_CONTRADICTION_THRESHOLD", 0.6)
    )
    premise = (context or "")[:_PREMISE_CAP]

    async def _gate(sentence: str) -> str:
        text = sentence.strip()
        if not text or not premise:
            return sentence
        try:
            scores = await nli_score_async(premise, text)
        except Exception as exc:  # noqa: BLE001 — NLI must never block streaming
            log_swallowed_error("core.agents.hallucination.inline_gate", exc)
            return sentence  # fail open
        if scores.get("contradiction", 0.0) >= ceiling:
            logger.debug(
                "inline_nli_gate[%s] suppressed sentence (contradiction=%.2f): %s",
                use.value, scores.get("contradiction", 0.0), text[:80],
            )
            if on_suppress is not None:
                on_suppress(text, scores)
            return ""  # suppressed
        return sentence

    buffer = ""
    async for token in token_stream:
        buffer += token
        while True:
            match = _SENTENCE_END.search(buffer)
            if not match:
                break
            end = match.end()
            sentence, buffer = buffer[:end], buffer[end:]
            gated = await _gate(sentence)
            if gated:
                yield gated
    # Flush the trailing partial sentence through the same gate.
    if buffer:
        gated = await _gate(buffer)
        if gated:
            yield gated


async def gated_synthesis(
    messages: list[dict[str, str]],
    *,
    context: str,
    stage: str,
    temperature: float = 0.1,
    max_tokens: int = 500,
    on_suppress: Callable[[str, dict[str, Any]], None] | None = None,
) -> str:
    """Stream a synthesis completion through the inline gate and collect it.

    Convenience for non-streaming callers (e.g. ``pkb_answer_with_citations``)
    that want the inline-gated answer as one string: contradicted sentences are
    dropped mid-stream, the survivors concatenated. ``stage`` threads the
    observability breadcrumb into :func:`call_internal_llm_stream`.
    """
    # Lazy import keeps this module's import graph shallow (and avoids any
    # future cycle through core.utils.internal_llm).
    import httpx

    from core.utils.internal_llm import call_internal_llm, call_internal_llm_stream

    parts: list[str] = []
    try:
        async for piece in inline_nli_gate(
            call_internal_llm_stream(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stage=stage,
            ),
            context=context,
            on_suppress=on_suppress,
        ):
            parts.append(piece)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        # E1 CR-093: the local stream dropped mid-answer. Returning the partial
        # would feed claims/citations computed over truncated text and present it
        # as verified, so fall back to a COMPLETE non-streaming synthesis. Ungated
        # is acceptable — a mid-stream failure means gating could not finish anyway,
        # and the answer still gets post-hoc verification downstream.
        log_swallowed_error("core.agents.hallucination.inline_gate.stream_fallback", exc)
        full = await call_internal_llm(
            messages, temperature=temperature, max_tokens=max_tokens, stage=stage,
        )
        return full.strip()
    return "".join(parts).strip()
