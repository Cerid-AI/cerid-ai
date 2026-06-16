# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the deterministic analytical operators (extract → compute)."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from core.agents.analytical_ops import (
    compute_count_answer,
    compute_delta,
    compute_temporal_answer,
    dedup_items,
    parse_iso_date,
)

# --- pure compute (no LLM) ---

def test_parse_iso_full_and_partial() -> None:
    assert parse_iso_date("2023-01-15") == date(2023, 1, 15)
    assert parse_iso_date("2023/01/15") == date(2023, 1, 15)
    assert parse_iso_date("2023-03") == date(2023, 3, 1)      # fill month
    assert parse_iso_date("2021") == date(2021, 1, 1)          # fill year
    assert parse_iso_date("[recorded 2023-05-20] foo") == date(2023, 5, 20)
    assert parse_iso_date("not a date") is None
    assert parse_iso_date("") is None


def test_compute_delta_days_exclusive_and_inclusive() -> None:
    # The MoMA case: Jan 8 -> Jan 15. Exclusive = 7, inclusive = 8.
    s, e = date(2023, 1, 8), date(2023, 1, 15)
    assert compute_delta(s, e, "days", inclusive=False) == 7
    assert compute_delta(s, e, "days", inclusive=True) == 8  # the off-by-one


def test_compute_delta_weeks_months_years() -> None:
    assert compute_delta(date(2023, 1, 1), date(2023, 1, 29), "weeks", False) == 4
    # whole months, calendar-correct (floors partial)
    assert compute_delta(date(2023, 1, 15), date(2023, 3, 20), "months", False) == 2
    assert compute_delta(date(2023, 1, 15), date(2023, 3, 10), "months", False) == 1  # day<start → floor
    assert compute_delta(date(2020, 6, 1), date(2023, 6, 1), "years", False) == 3


def test_dedup_items_exact_key_dominates() -> None:
    out = dedup_items(["Kyoto trip", "kyoto  trip!", "Osaka trip", "OSAKA TRIP"])
    assert len(out) == 2  # kyoto + osaka, case/punct collapsed
    assert "Kyoto trip" in out and "Osaka trip" in out


# --- LLM-driven operators (mocked extraction) ---

@pytest.mark.asyncio
async def test_temporal_answer_computes_from_extracted_dates() -> None:
    mock = AsyncMock(return_value=(
        '{"answerable": true,'
        ' "start_event": {"label": "MoMA", "iso_date": "2023-01-08"},'
        ' "end_event": {"label": "Met exhibit", "iso_date": "2023-01-15"},'
        ' "unit": "days", "endpoints_inclusive": false}'
    ))
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        ans = await compute_temporal_answer("How many days between MoMA and the Met?", "mem")
    assert ans == "7 days"


@pytest.mark.asyncio
async def test_temporal_reference_date_injected_and_computes_ago() -> None:
    # "how many weeks ago" — end_event is the reference "now", not a memory.
    mock = AsyncMock(return_value=(
        '{"answerable": true,'
        ' "start_event": {"label": "met aunt", "iso_date": "2023-01-01"},'
        ' "end_event": {"label": "now", "iso_date": "2023-01-29"},'
        ' "unit": "weeks", "endpoints_inclusive": false}'
    ))
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        ans = await compute_temporal_answer(
            "How many weeks ago did I meet my aunt?", "mem",
            reference_date="2023-01-29",
        )
    assert ans == "4 weeks"
    # the reference "now" is threaded into the extraction prompt
    prompt = mock.call_args.args[0][0]["content"]
    assert "2023-01-29" in prompt


@pytest.mark.asyncio
async def test_temporal_answer_none_when_not_answerable() -> None:
    mock = AsyncMock(return_value='{"answerable": false}')
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        ans = await compute_temporal_answer("q", "mem")
    assert ans is None  # caller falls back to synthesis


@pytest.mark.asyncio
async def test_temporal_answer_none_on_unparseable_date() -> None:
    mock = AsyncMock(return_value=(
        '{"answerable": true, "start_event": {"iso_date": "sometime"},'
        ' "end_event": {"iso_date": "2023-01-15"}, "unit": "days"}'
    ))
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        assert await compute_temporal_answer("q", "mem") is None


@pytest.mark.asyncio
async def test_count_answer_dedups_and_counts() -> None:
    mock = AsyncMock(return_value=(
        '{"answerable": true, "items": ["Revell F-15", "Tamiya Spitfire",'
        ' "revell f-15!", "1/24 Mustang", "Airfix Lancaster", "Hasegawa Zero"]}'
    ))
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        ans = await compute_count_answer("How many model kits?", "mem")
    assert ans.startswith("5:")  # 6 listed, one dup collapsed → 5


@pytest.mark.asyncio
async def test_count_answer_none_when_empty() -> None:
    mock = AsyncMock(return_value='{"answerable": true, "items": []}')
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        assert await compute_count_answer("q", "mem") is None


@pytest.mark.asyncio
async def test_operators_fall_back_to_none_on_llm_error() -> None:
    boom = AsyncMock(side_effect=RuntimeError("provider down"))
    with patch("core.agents.analytical_ops.call_internal_llm", new=boom):
        assert await compute_temporal_answer("q", "mem") is None
        assert await compute_count_answer("q", "mem") is None
