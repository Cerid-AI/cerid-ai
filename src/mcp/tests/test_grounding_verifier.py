# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the pluggable grounding-verifier tier (Phase 3.1)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import config
from core.agents.hallucination.grounding_verifier import (
    NLI_PREMISE_CHAR_LIMIT,
    NliDebertaVerifier,
    get_grounding_verifier,
    register_grounding_verifier,
)


def test_default_verifier_is_nli_deberta():
    assert get_grounding_verifier().name == "nli_deberta"


def test_premise_char_limit_matches_token_budget():
    # ~2000 chars ≈ the deberta tokenizer's 512-token ceiling; the old [:512]
    # char slice was ~4× tighter, starving the model of most of the evidence.
    assert NLI_PREMISE_CHAR_LIMIT == 2000


@pytest.mark.asyncio
async def test_default_verifier_delegates_to_nli_score_async():
    """The default verifier must call core.utils.nli.nli_score_async at call
    time so the batching coalescer runs and existing patches keep hitting."""
    fake = {"entailment": 0.8, "contradiction": 0.1, "neutral": 0.1, "label": "entailment"}
    with patch("core.utils.nli.nli_score_async", new_callable=AsyncMock, return_value=fake) as m:
        out = await NliDebertaVerifier().score("premise text", "hypothesis")
    assert out == fake
    m.assert_awaited_once_with("premise text", "hypothesis")


@pytest.mark.asyncio
async def test_config_selects_registered_verifier(monkeypatch):
    """A GROUNDING_VERIFIER setting selects a registered alternate without
    touching the verification call sites."""
    sentinel = {"entailment": 1.0, "contradiction": 0.0, "neutral": 0.0, "label": "entailment"}

    class _FakeVerifier:
        name = "fake_minicheck"

        async def score(self, premise: str, hypothesis: str) -> dict[str, Any]:
            return sentinel

    register_grounding_verifier(_FakeVerifier())
    monkeypatch.setattr(config, "GROUNDING_VERIFIER", "fake_minicheck", raising=False)
    verifier = get_grounding_verifier()
    assert verifier.name == "fake_minicheck"
    assert await verifier.score("p", "h") is sentinel


def test_unknown_config_name_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(config, "GROUNDING_VERIFIER", "does_not_exist", raising=False)
    assert get_grounding_verifier().name == "nli_deberta"
