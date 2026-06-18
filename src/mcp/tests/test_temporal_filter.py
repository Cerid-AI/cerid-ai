# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for time-anchored retrieval (temporal proximity boost)."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from core.retrieval.temporal_filter import (
    apply_proximity_boost,
    extract_query_window,
    temporal_proximity,
)

# --- pure proximity (no LLM) ---


def test_proximity_inside_window_is_one() -> None:
    s, e = date(2023, 1, 1), date(2023, 12, 31)
    assert temporal_proximity(date(2023, 6, 15), s, e) == 1.0
    assert temporal_proximity(s, s, e) == 1.0  # on the boundary
    assert temporal_proximity(e, s, e) == 1.0


def test_proximity_decays_outside_window() -> None:
    s, e = date(2023, 6, 1), date(2023, 6, 30)
    # 30 days before start, half_life 30 → 0.5
    assert temporal_proximity(date(2023, 5, 2), s, e, half_life_days=30) == pytest.approx(0.5, abs=0.02)
    # ~a year after end → ~0
    assert temporal_proximity(date(2024, 6, 30), s, e, half_life_days=30) < 0.01


def test_proximity_open_ended_and_none() -> None:
    s = date(2023, 1, 1)
    assert temporal_proximity(date(2023, 5, 1), s, None) == 1.0  # >= start, open end
    assert temporal_proximity(date(2022, 12, 2), s, None, half_life_days=30) == pytest.approx(0.5, abs=0.02)
    assert temporal_proximity(None, s, s) == 0.0          # undated chunk
    assert temporal_proximity(date(2023, 1, 1), None, None) == 0.0  # no window


# --- LLM window extraction (mocked) ---


@pytest.mark.asyncio
async def test_extract_window_scoped() -> None:
    mock = AsyncMock(return_value='{"scoped": true, "start": "2023-03-01", "end": "2023-03-31"}')
    with patch("core.retrieval.temporal_filter.call_internal_llm", new=mock):
        start, end = await extract_query_window("What did I do in March 2023?")
    assert (start, end) == ("2023-03-01", "2023-03-31")


@pytest.mark.asyncio
async def test_extract_window_reference_date_threaded() -> None:
    mock = AsyncMock(return_value='{"scoped": true, "start": "2022-01-01", "end": "2022-12-31"}')
    with patch("core.retrieval.temporal_filter.call_internal_llm", new=mock):
        await extract_query_window("What happened last year?", reference_date="2023-05-10")
    assert "2023-05-10" in mock.call_args.args[0][0]["content"]


@pytest.mark.asyncio
async def test_extract_window_not_scoped_or_error() -> None:
    with patch("core.retrieval.temporal_filter.call_internal_llm",
               new=AsyncMock(return_value='{"scoped": false}')):
        assert await extract_query_window("What is my cat's name?") == (None, None)
    with patch("core.retrieval.temporal_filter.call_internal_llm",
               new=AsyncMock(side_effect=RuntimeError("down"))):
        assert await extract_query_window("q") == (None, None)


# --- boost application (additive, safe) ---


def test_apply_boost_lifts_in_window_only() -> None:
    cands = [
        {"content": "a", "date": "2023-03-15", "score": 0.5},   # in window → +1.0
        {"content": "b", "date": "2020-01-01", "score": 0.9},   # far → ~0 lift
        {"content": "c", "date": "", "score": 0.4},             # undated → no lift
    ]
    lifted = apply_proximity_boost(cands, "2023-03-01", "2023-03-31", weight=1.0, half_life_days=30)
    assert cands[0]["score"] == pytest.approx(1.5)   # 0.5 + 1.0
    assert cands[1]["score"] == pytest.approx(0.9, abs=0.01)  # negligible
    assert cands[2]["score"] == 0.4                  # untouched
    assert lifted >= 1


def test_apply_boost_noop_on_empty_window() -> None:
    cands = [{"content": "a", "date": "2023-03-15", "score": 0.5}]
    assert apply_proximity_boost(cands, None, None, weight=1.0, half_life_days=30) == 0
    assert cands[0]["score"] == 0.5  # never removed or changed
