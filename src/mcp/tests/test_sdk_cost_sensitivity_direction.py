# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The published ``cost_sensitivity`` description must match how it routes.

``app/models/sdk.py`` is the source of the ``/sdk/v1/llm/complete`` request
schema, and that schema is copied verbatim into ``docs/openapi-sdk-v1.json``
and served at ``/sdk/v1/openapi.json``. Every SDK consumer reads the field's
description and nothing else — so a description that names the wrong end of
the scale is a silent 20x overspend, with the response's own
``estimated_cost_per_1k`` as the only clue.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.models.sdk import SDKLLMCompleteRequest
from utils.smart_router import TaskType, route

# A query with a MODERATE complexity signal — the band where cost_sensitivity
# actually decides the tier (SIMPLE always routes FREE, RESEARCH always paid).
MODERATE_QUERY = (
    "explain how photosynthesis works in eukaryotic plant cells "
    "in a way that a high schooler can follow"
)

SPEC_PATH = Path(__file__).resolve().parents[3] / "docs" / "openapi-sdk-v1.json"


def _described_cheapest() -> str:
    """The value the published description advertises as the cheapest."""
    description = SDKLLMCompleteRequest.model_fields["cost_sensitivity"].description or ""
    match = re.search(r"(\w+) \(cheapest\)", description)
    assert match, f"description no longer names a cheapest value: {description!r}"
    return match.group(1)


async def _cost_of(value: str) -> float:
    with patch("utils.smart_router._check_ollama", new_callable=AsyncMock, return_value=False):
        decision = await route(MODERATE_QUERY, task_type=TaskType.CHAT, cost_sensitivity=value)
    return decision.estimated_cost_per_1k


@pytest.mark.asyncio
async def test_described_cheapest_value_is_the_cheapest_route() -> None:
    costs = {value: await _cost_of(value) for value in ("low", "medium", "high")}
    cheapest = _described_cheapest()

    assert costs[cheapest] == min(costs.values()), (
        f"the schema advertises cost_sensitivity={cheapest!r} as cheapest, but "
        f"the router charges {costs} per 1K tokens — a consumer following the "
        "published contract pays the opposite of what it asked for"
    )


def test_published_spec_carries_the_same_description() -> None:
    spec = json.loads(SPEC_PATH.read_text())
    published = spec["components"]["schemas"]["SDKLLMCompleteRequest"]["properties"]["cost_sensitivity"]
    assert published["description"] == SDKLLMCompleteRequest.model_fields["cost_sensitivity"].description
