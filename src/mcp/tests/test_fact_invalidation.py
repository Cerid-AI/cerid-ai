# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Phase D2 — a superseded memory is routed to the contradiction ledger only
when NLI finds a genuine disagreement (core/agents/fact_invalidation.py).

Closing the old version is the lineage writer's job and never waits on this;
these tests pin only the classification and the sink call.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.agents.fact_invalidation import route_supersession_to_ledger


def _nli(contradiction: float):
    return [{"contradiction": contradiction, "entailment": 0.1, "neutral": 0.1, "label": "x"}]


async def _route(old: str = "OLD text", new: str = "NEW text") -> None:
    await route_supersession_to_ledger(old_content=old, new_content=new, old_artifact_id="old")


@pytest.mark.asyncio
async def test_contradiction_above_threshold_invokes_sink() -> None:
    sink = AsyncMock()
    with (
        patch("core.agents.fact_invalidation.batch_nli_score", return_value=_nli(0.9)),
        patch("core.agents.hallucination.contradiction_sink.get_contradiction_sink", return_value=sink),
    ):
        await _route()
    sink.assert_awaited_once_with(
        claim_text="NEW text", source_text="OLD text", source_artifact_id="old", severity="medium",
    )


@pytest.mark.asyncio
async def test_orderly_update_reaches_no_sink() -> None:
    sink = AsyncMock()
    with (
        patch("core.agents.fact_invalidation.batch_nli_score", return_value=_nli(0.1)),
        patch("core.agents.hallucination.contradiction_sink.get_contradiction_sink", return_value=sink),
    ):
        await _route()
    sink.assert_not_awaited()


@pytest.mark.asyncio
async def test_nli_failure_is_swallowed() -> None:
    sink = AsyncMock()
    with (
        patch("core.agents.fact_invalidation.batch_nli_score", side_effect=RuntimeError("nli down")),
        patch("core.agents.hallucination.contradiction_sink.get_contradiction_sink", return_value=sink),
    ):
        await _route()
    sink.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_text_skips_classification() -> None:
    with patch("core.agents.fact_invalidation.batch_nli_score") as nli:
        await _route(old="")
    nli.assert_not_called()


@pytest.mark.asyncio
async def test_ledger_disabled_skips_nli(monkeypatch) -> None:
    monkeypatch.setattr("config.ENABLE_CONTRADICTION_LEDGER", False)
    with patch("core.agents.fact_invalidation.batch_nli_score", return_value=_nli(0.9)) as nli:
        await _route()
    nli.assert_not_called()
