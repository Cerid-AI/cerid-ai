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
    assert ans == "5"  # 6 listed, one dup collapsed → bare scalar (no item list)


@pytest.mark.asyncio
async def test_count_answer_none_when_empty() -> None:
    mock = AsyncMock(return_value='{"answerable": true, "items": []}')
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        assert await compute_count_answer("q", "mem") is None


# --- Stated-total precedence (2026-07-09 arm A regression: knowledge-update
# 'how many X have I <verbed>' questions whose memories STATE a cumulative
# total; enumerating distinct mentions yields the mention count, not the
# answer, and T0.1's bare-count finalization made that misfire fatal) ---


@pytest.mark.asyncio
async def test_count_prefers_stated_total_over_enumeration() -> None:
    """'completed 50 episodes' in memory answers the question; the 2 distinct
    mentions the extractor found must not become the answer."""
    mock = AsyncMock(return_value=(
        '{"answerable": true, "stated_total": 50,'
        ' "items": ["episode 10 of the Science series",'
        ' "completed 50 episodes milestone"]}'
    ))
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        ans = await compute_count_answer(
            "How many episodes of the Science series have I completed?", "mem",
        )
    assert ans == "50"


@pytest.mark.asyncio
async def test_count_stated_total_word_number_parsed() -> None:
    """Stated totals arrive as words too ('five tops from H&M')."""
    mock = AsyncMock(return_value=(
        '{"answerable": true, "stated_total": "five",'
        ' "items": ["three tops from H&M", "five tops from H&M"]}'
    ))
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        ans = await compute_count_answer("How many tops have I bought from H&M?", "mem")
    assert ans == "5"


@pytest.mark.asyncio
async def test_count_enumerates_when_no_stated_total() -> None:
    """stated_total null/absent → enumeration behavior unchanged."""
    mock = AsyncMock(return_value=(
        '{"answerable": true, "stated_total": null, "items": ["a", "b", "c"]}'
    ))
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        assert await compute_count_answer("How many distinct things?", "mem") == "3"


@pytest.mark.asyncio
async def test_count_ignores_garbage_stated_total() -> None:
    """A non-numeric stated_total must not crash or win — fall back to items."""
    mock = AsyncMock(return_value=(
        '{"answerable": true, "stated_total": "several", "items": ["a", "b"]}'
    ))
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        assert await compute_count_answer("How many?", "mem") == "2"


@pytest.mark.asyncio
async def test_count_abstains_on_how_much_amount_question() -> None:
    r"""'how much did X cost' asks for an AMOUNT — len(items) can never answer
    it (arm A 2026-07-09: gold '\$2,000' answered '7' = mention count). The
    operator must abstain BEFORE any LLM call so synthesis extracts the
    stated value with its unit."""
    mock = AsyncMock(return_value='{"answerable": true, "items": ["a", "b"]}')
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        assert await compute_count_answer(
            "How much did the drone cost?", "mem",
        ) is None
        assert await compute_count_answer(
            "How much weight have I lost since March?", "mem",
        ) is None
    mock.assert_not_called()


# --- Tier 1 routing self-guards (deterministic; abstain → synthesis) ---


@pytest.mark.asyncio
async def test_count_abstains_on_frequency_question() -> None:
    """'how often' is a frequency question — the count operator must abstain
    (return None) BEFORE any LLM call so synthesis returns the current rate."""
    mock = AsyncMock(return_value='{"answerable": true, "items": ["a", "b"]}')
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        assert await compute_count_answer(
            "How often do I attend yoga classes?", "mem",
        ) is None
        assert await compute_count_answer(
            "How many times a week do I run?", "mem",
        ) is None
    mock.assert_not_called()  # guarded before the extractor


@pytest.mark.asyncio
async def test_count_still_fires_on_genuine_count() -> None:
    """'how many times have I <verb>' (no a/per) stays a count, not frequency."""
    mock = AsyncMock(return_value='{"answerable": true, "items": ["x", "y", "z"]}')
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        assert await compute_count_answer(
            "How many times have I visited Paris?", "mem",
        ) == "3"


@pytest.mark.asyncio
async def test_temporal_abstains_on_ordering_question() -> None:
    """An ordering question must not hit the date-delta operator."""
    mock = AsyncMock(return_value=_DELTA_7)
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        assert await compute_temporal_answer(
            "Which three events happened in the order from first to last?", "mem",
        ) is None
    mock.assert_not_called()


@pytest.mark.asyncio
async def test_temporal_still_fires_on_delta_with_order_idiom() -> None:
    """'in order to' is an idiom, not an ordering question — operator still runs."""
    mock = AsyncMock(return_value=_DELTA_7)
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        ans = await compute_temporal_answer(
            "How many days did I wait in order to get my visa?", "mem",
        )
    assert ans == "7 days"


@pytest.mark.asyncio
async def test_operators_fall_back_to_none_on_llm_error() -> None:
    boom = AsyncMock(side_effect=RuntimeError("provider down"))
    with patch("core.agents.analytical_ops.call_internal_llm", new=boom):
        assert await compute_temporal_answer("q", "mem") is None
        assert await compute_count_answer("q", "mem") is None


# --- self-consistency (sample N, mode-vote the COMPUTED answer) ---

import contextlib  # noqa: E402

import config  # noqa: E402


@contextlib.contextmanager
def _self_consistency(samples: int):
    """Enable self-consistency with a fixed sample count for the block."""
    with patch.object(config, "ENABLE_SELF_CONSISTENCY", True), patch.object(
        config, "SELF_CONSISTENCY_SAMPLES", samples,
    ), patch.object(config, "SELF_CONSISTENCY_TEMPERATURE", 0.7):
        yield


_DELTA_7 = (
    '{"answerable": true, "start_event": {"iso_date": "2023-01-08"},'
    ' "end_event": {"iso_date": "2023-01-15"}, "unit": "days",'
    ' "endpoints_inclusive": false}'
)
_DELTA_8 = (
    '{"answerable": true, "start_event": {"iso_date": "2023-01-08"},'
    ' "end_event": {"iso_date": "2023-01-16"}, "unit": "days",'
    ' "endpoints_inclusive": false}'
)


@pytest.mark.asyncio
async def test_temporal_self_consistency_votes_majority_delta() -> None:
    # Two samples compute "7 days", one computes "8 days" → majority wins.
    mock = AsyncMock(side_effect=[_DELTA_7, _DELTA_8, _DELTA_7])
    with _self_consistency(3), patch(
        "core.agents.analytical_ops.call_internal_llm", new=mock,
    ):
        ans = await compute_temporal_answer("How many days between X and Y?", "mem")
    assert ans == "7 days"
    assert mock.call_count == 3  # N samples, not one


@pytest.mark.asyncio
async def test_temporal_self_consistency_samples_at_nonzero_temperature() -> None:
    mock = AsyncMock(side_effect=[_DELTA_7, _DELTA_7, _DELTA_7])
    with _self_consistency(3), patch(
        "core.agents.analytical_ops.call_internal_llm", new=mock,
    ):
        await compute_temporal_answer("q", "mem")
    # Sampling must use a non-zero temperature (else the votes are identical).
    assert all(c.kwargs["temperature"] == 0.7 for c in mock.call_args_list)


@pytest.mark.asyncio
async def test_count_self_consistency_votes_over_count_not_surface_form() -> None:
    five = '{"answerable": true, "items": ["a", "b", "c", "d", "e"]}'
    five_alt = '{"answerable": true, "items": ["A!", "b", "c", "d", "e"]}'  # same 5
    four = '{"answerable": true, "items": ["a", "b", "c", "d"]}'
    # Two samples count 5 (despite surface drift), one counts 4 → 5 wins.
    mock = AsyncMock(side_effect=[five, four, five_alt])
    with _self_consistency(3), patch(
        "core.agents.analytical_ops.call_internal_llm", new=mock,
    ):
        ans = await compute_count_answer("How many?", "mem")
    assert ans is not None and ans.startswith("5")


@pytest.mark.asyncio
async def test_disabled_self_consistency_makes_one_temperature_zero_call() -> None:
    mock = AsyncMock(return_value=_DELTA_7)
    # No _self_consistency() wrapper → flag is at its default (off).
    with patch("core.agents.analytical_ops.call_internal_llm", new=mock):
        ans = await compute_temporal_answer("q", "mem")
    assert ans == "7 days"
    assert mock.call_count == 1
    assert mock.call_args.kwargs["temperature"] == 0.0
