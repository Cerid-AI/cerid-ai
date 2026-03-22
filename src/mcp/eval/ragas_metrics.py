# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
RAGAS-inspired generation quality metrics (LLM-as-judge).

Four metrics that evaluate answer quality relative to retrieved context:
- faithfulness: Are answer claims supported by context?
- answer_relevancy: Does the answer address the question?
- context_precision: Are retrieved contexts relevant to the question?
- context_recall: Is needed context retrieved for the answer?

Each function returns a 0.0–1.0 score. Designed for offline evaluation,
not inline query pipeline use.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from utils.llm_client import call_llm

logger = logging.getLogger("ai-companion")

_BREAKER = "ragas_eval"


@dataclass
class MetricResult:
    """Score with reasoning from the LLM judge."""

    score: float
    reasoning: str


def _parse_score(raw: str) -> MetricResult:
    """Extract score and reasoning from LLM JSON response."""
    try:
        data = json.loads(raw)
        score = float(data.get("score", 0.0))
        score = max(0.0, min(1.0, score))
        return MetricResult(score=score, reasoning=data.get("reasoning", ""))
    except (json.JSONDecodeError, ValueError, TypeError):
        # Fallback: try to extract a number from the text
        import re

        match = re.search(r"(\d+\.?\d*)", raw)
        if match:
            score = max(0.0, min(1.0, float(match.group(1))))
            return MetricResult(score=score, reasoning=raw[:200])
        return MetricResult(score=0.0, reasoning=f"Failed to parse: {raw[:200]}")


async def faithfulness(
    answer: str,
    contexts: list[str],
    *,
    model: str | None = None,
) -> MetricResult:
    """Evaluate whether claims in the answer are supported by the contexts.

    High score = answer is grounded in provided context.
    Low score = answer contains unsupported claims.
    """
    ctx_block = "\n---\n".join(contexts[:10])
    messages = [
        {
            "role": "system",
            "content": (
                "You are an evaluation judge. Assess whether the claims in the "
                "ANSWER are supported by the provided CONTEXTS. Return JSON with "
                '"score" (0.0-1.0) and "reasoning" (brief explanation). '
                "Score 1.0 = all claims fully supported. Score 0.0 = no claims supported."
            ),
        },
        {
            "role": "user",
            "content": f"CONTEXTS:\n{ctx_block}\n\nANSWER:\n{answer}",
        },
    ]

    content = await call_llm(
        messages, breaker_name=_BREAKER, model=model or "", temperature=0.0, max_tokens=500,
    )
    return _parse_score(content)


async def answer_relevancy(
    question: str,
    answer: str,
    *,
    model: str | None = None,
) -> MetricResult:
    """Evaluate whether the answer addresses the question.

    High score = answer directly addresses the question.
    Low score = answer is off-topic or incomplete.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are an evaluation judge. Assess whether the ANSWER directly "
                "and completely addresses the QUESTION. Return JSON with "
                '"score" (0.0-1.0) and "reasoning" (brief explanation). '
                "Score 1.0 = perfectly relevant and complete. "
                "Score 0.0 = completely irrelevant."
            ),
        },
        {
            "role": "user",
            "content": f"QUESTION:\n{question}\n\nANSWER:\n{answer}",
        },
    ]

    content = await call_llm(
        messages, breaker_name=_BREAKER, model=model or "", temperature=0.0, max_tokens=500,
    )
    return _parse_score(content)


async def context_precision(
    question: str,
    contexts: list[str],
    *,
    model: str | None = None,
) -> MetricResult:
    """Evaluate whether the retrieved contexts are relevant to the question.

    High score = contexts are highly relevant.
    Low score = contexts are mostly irrelevant noise.
    """
    ctx_block = "\n---\n".join(f"[{i+1}] {c}" for i, c in enumerate(contexts[:10]))
    messages = [
        {
            "role": "system",
            "content": (
                "You are an evaluation judge. Assess what proportion of the "
                "provided CONTEXTS are relevant to answering the QUESTION. "
                'Return JSON with "score" (0.0-1.0) and "reasoning". '
                "Score 1.0 = all contexts highly relevant. "
                "Score 0.0 = no contexts are relevant."
            ),
        },
        {
            "role": "user",
            "content": f"QUESTION:\n{question}\n\nCONTEXTS:\n{ctx_block}",
        },
    ]

    content = await call_llm(
        messages, breaker_name=_BREAKER, model=model or "", temperature=0.0, max_tokens=500,
    )
    return _parse_score(content)


async def context_recall(
    question: str,
    answer: str,
    contexts: list[str],
    *,
    model: str | None = None,
) -> MetricResult:
    """Evaluate whether the retrieved contexts contain info needed for the answer.

    High score = contexts cover all information in the answer.
    Low score = answer relies on information not present in contexts.
    """
    ctx_block = "\n---\n".join(contexts[:10])
    messages = [
        {
            "role": "system",
            "content": (
                "You are an evaluation judge. Given a QUESTION, ANSWER, and "
                "CONTEXTS, assess whether the contexts contain all the "
                "information needed to produce the answer. "
                'Return JSON with "score" (0.0-1.0) and "reasoning". '
                "Score 1.0 = contexts fully cover the answer. "
                "Score 0.0 = contexts contain none of the needed information."
            ),
        },
        {
            "role": "user",
            "content": (
                f"QUESTION:\n{question}\n\n"
                f"CONTEXTS:\n{ctx_block}\n\n"
                f"ANSWER:\n{answer}"
            ),
        },
    ]

    content = await call_llm(
        messages, breaker_name=_BREAKER, model=model or "", temperature=0.0, max_tokens=500,
    )
    return _parse_score(content)


async def evaluate_all(
    question: str,
    answer: str,
    contexts: list[str],
    *,
    model: str | None = None,
) -> dict[str, MetricResult]:
    """Run all four RAGAS metrics and return results keyed by metric name."""
    import asyncio

    results = await asyncio.gather(
        faithfulness(answer, contexts, model=model),
        answer_relevancy(question, answer, model=model),
        context_precision(question, contexts, model=model),
        context_recall(question, answer, contexts, model=model),
    )
    return {
        "faithfulness": results[0],
        "answer_relevancy": results[1],
        "context_precision": results[2],
        "context_recall": results[3],
    }
