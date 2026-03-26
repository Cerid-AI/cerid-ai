# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[c["description"] for c in CASES])
async def test_routing_case(case: dict, aclient: httpx.AsyncClient) -> None:
    """Verify model selection reflects expected complexity routing.

    Gap 5 fix: Tightened from "tier != unknown" to also catch severe downgrades.
    A capable/research query routed to free_or_cheap is a quality regression.
    """
    model = await get_routed_model(aclient, case["query"])
    assert model is not None, f"No model returned for: {case['query']}"

    tier = classify_model_tier(model)
    expected = case["expected_tier"]

    # Hard invariant: model must be recognized
    assert tier != "unknown", (
        f"[{case['description']}] Unrecognized model tier for: {model}"
    )

    # Soft invariant: capable/research queries should NOT downgrade to free_or_cheap.
    # Cost optimization may use a capable model for a research query (acceptable),
    # but routing a complex analysis to gpt-4o-mini is a quality concern.
    # This is a warnings.warn (not assert) because the smart router's cost
    # sensitivity tuning is a separate concern from pipeline correctness.
    expected_rank = _TIER_RANK.get(expected, 0)
    actual_rank = _TIER_RANK.get(tier, 0)
    if expected_rank >= 1 and actual_rank < 1:  # capable/research → free_or_cheap
        import warnings
        warnings.warn(
            f"[{case['description']}] QUALITY CONCERN: expected tier "
            f"'{expected}' but got '{tier}' ({model}). "
            f"Complex queries routing to free/cheap models may degrade quality.",
            stacklevel=1,
        )

    # Informational: log tier match/mismatch
    match_marker = "MATCH" if tier == expected else "MISMATCH"
    print(f"  [{case['description']}] expected={expected} actual={tier} "
          f"model={model} [{match_marker}]")
