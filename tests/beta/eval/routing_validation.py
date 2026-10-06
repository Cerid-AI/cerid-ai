# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tier 3: Smart routing validation — model selection via /chat/completions."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import load_jsonl

CASES = load_jsonl("routing_cases.jsonl")

# Model tier classification keywords
FREE_OR_CHEAP = {"llama", "qwen", "gpt-4o-mini", "gemini-flash", "gemini-2", "ollama"}
CAPABLE = {"claude", "sonnet", "gpt-4o", "gpt-4", "gpt-5", "opus"}
RESEARCH = {"grok", "online", "perplexity"}


def classify_model_tier(model_id: str) -> str:
    """Classify a model ID into a tier based on known patterns."""
    model_lower = model_id.lower()
    if ":online" in model_lower or "online" in model_lower:
        return "research_online"
    # Grok models used for research/online tasks
    for keyword in RESEARCH:
        if keyword in model_lower:
            return "research_online"
    for keyword in CAPABLE:
        if keyword in model_lower and "mini" not in model_lower:
            return "capable"
    for keyword in FREE_OR_CHEAP:
        if keyword in model_lower:
            return "free_or_cheap"
    return "unknown"


async def get_routed_model(client: httpx.AsyncClient, query: str) -> str | None:
    """Send a chat query and extract the model from cerid_meta SSE event or response."""
    body = {
        "model": "auto",
        "messages": [{"role": "user", "content": query}],
        "stream": True,
    }
    model = None
    async with client.stream("POST", "/chat/stream", json=body, timeout=30.0) as resp:
        if resp.status_code != 200:
            return None
        async for line in resp.aiter_lines():
            line = line.strip()
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                # cerid_meta event contains resolved model
                if "cerid_meta" in chunk:
                    model = chunk["cerid_meta"].get("model", model)
                # Standard OpenAI chunk may have model field
                if not model and "model" in chunk:
                    model = chunk["model"]
            except json.JSONDecodeError:
                continue
    return model


# Tier ordering for downgrade detection (higher index = more capable)
_TIER_RANK = {"free_or_cheap": 0, "capable": 1, "research_online": 2}


def routing_failure(expected: str, actual: str, model: str) -> str | None:
    """Why a routed tier fails the case, or ``None``.

    A capable or research query routed to any cheaper tier is a failure: a
    complex analysis on a mini model degrades the answer, and a current-events
    question on a model without web access cannot answer at all. Until
    2026-10-05 this was a ``warnings.warn`` and the gate could not fail on it.
    Routing a cheap query to a better model is a cost question, not a quality
    one, and passes.
    """
    expected_rank = _TIER_RANK.get(expected, 0)
    actual_rank = _TIER_RANK.get(actual, 0)
    if expected_rank >= 1 and actual_rank < expected_rank:
        return f"DOWNGRADE: expected '{expected}', got '{actual}' ({model})"
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[c["description"] for c in CASES])
async def test_routing_case(case: dict, aclient: httpx.AsyncClient) -> None:
    """Verify model selection reflects expected complexity routing."""
    model = await get_routed_model(aclient, case["query"])
    assert model is not None, f"No model returned for: {case['query']}"

    tier = classify_model_tier(model)
    expected = case["expected_tier"]

    # Hard invariant: model must be recognized
    assert tier != "unknown", (
        f"[{case['description']}] Unrecognized model tier for: {model}"
    )

    failure = routing_failure(expected, tier, model)
    assert failure is None, f"[{case['description']}] {failure}"

    # Informational: log tier match/mismatch
    match_marker = "MATCH" if tier == expected else "MISMATCH"
    print(f"  [{case['description']}] expected={expected} actual={tier} "
          f"model={model} [{match_marker}]")
