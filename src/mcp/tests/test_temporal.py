# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for temporal awareness utilities."""

from datetime import datetime, timedelta, timezone

import pytest

import config
from core.utils.temporal import (
    TemporalIntent,
    apply_temporal_intent,
    is_within_window,
    parse_temporal_intent,
    recency_score,
)


def _aware(days_ago: float) -> str:
    """The shape ingestion writes (utcnow_iso): carries +00:00."""
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _naive(days_ago: float) -> str:
    """The legacy shape some stored rows still carry: UTC with no offset."""
    return (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)).isoformat()


class TestParseTemporalIntent:
    @pytest.mark.parametrize(("query", "days"), [
        ("what did I add today", 1),
        ("notes from yesterday", 2),
        ("what changed in the last few days", 3),
        ("show me this week's uploads", 7),
        ("files from last week", 14),
        ("this month's reports", 30),
        ("last month analysis", 60),
        ("this year's goals", 365),
    ])
    def test_calendar_phrases_are_hard_windows(self, query, days):
        assert parse_temporal_intent(query) == TemporalIntent(days=days, hard=True)

    @pytest.mark.parametrize(("query", "days"), [
        ("show recent documents", 7),
        ("recently added", 7),
        ("what happened over the last few months", 90),
    ])
    def test_vague_phrases_are_soft(self, query, days):
        assert parse_temporal_intent(query) == TemporalIntent(days=days, hard=False)

    def test_a_calendar_phrase_outranks_a_vague_one(self):
        assert parse_temporal_intent("recent notes from last month") == TemporalIntent(60, True)

    def test_no_temporal(self):
        assert parse_temporal_intent("python data structures") is None
        assert parse_temporal_intent("budget planning template") is None


class TestRecencyScore:
    def test_just_now(self):
        score = recency_score(_naive(0), half_life_days=30)
        assert score is not None and 0.99 <= score <= 1.0

    def test_half_life(self):
        score = recency_score(_naive(30), half_life_days=30)
        assert score is not None and 0.45 <= score <= 0.55

    def test_old_document(self):
        score = recency_score(_naive(120), half_life_days=30)
        assert score is not None and score < 0.1  # 4 half-lives -> 0.0625

    @pytest.mark.parametrize("stamp", [_aware(0), _naive(0)])
    def test_aware_and_naive_stamps_score_the_same_age(self, stamp):
        score = recency_score(stamp, half_life_days=30)
        assert score is not None and score > 0.99

    def test_aware_stamp_decays(self):
        score = recency_score(_aware(60), half_life_days=30)
        assert score is not None and 0.2 <= score <= 0.3

    def test_offset_stamp_is_compared_in_utc(self):
        # "Now" written at -12:00: dropping the offset instead of converting
        # would read it as twelve hours old (0.71 at a one-day half-life).
        local = datetime.now(timezone(timedelta(hours=-12))).isoformat()
        score = recency_score(local, half_life_days=1)
        assert score is not None and score > 0.99

    @pytest.mark.parametrize("stamp", ["not-a-date", ""])
    def test_undated_earns_no_score(self, stamp):
        assert recency_score(stamp) is None


class TestIsWithinWindow:
    @pytest.mark.parametrize("stamp", [_aware(2), _naive(2)])
    def test_inside_the_window(self, stamp):
        assert is_within_window(stamp, days=7) is True

    @pytest.mark.parametrize("stamp", [_aware(10), _naive(10)])
    def test_outside_the_window(self, stamp):
        assert is_within_window(stamp, days=7) is False

    def test_invalid_date_included(self):
        assert is_within_window("invalid", days=7) is True


def _rows(**dates: str) -> list[dict]:
    return [{"artifact_id": name, "relevance": 0.5, "ingested_at": stamp}
            for name, stamp in dates.items()]


class TestApplyTemporalIntent:
    def test_this_week_drops_a_ten_day_old_row_and_keeps_a_two_day_old_one(self):
        rows = _rows(fresh=_aware(2), stale=_aware(10))
        kept = apply_temporal_intent(rows, "what did we decide this week")
        assert [r["artifact_id"] for r in kept] == ["fresh"]

    def test_an_undated_row_survives_a_hard_window_without_a_boost(self):
        rows = [{"artifact_id": "undated", "relevance": 0.5}]
        kept = apply_temporal_intent(rows, "what did we decide this week")
        assert kept == [{"artifact_id": "undated", "relevance": 0.5}]

    def test_recent_drops_nothing_and_favours_newer_rows(self):
        rows = _rows(old=_aware(200), new=_aware(1))
        kept = apply_temporal_intent(rows, "what have I been reading recently")
        by_id = {r["artifact_id"]: r["relevance"] for r in kept}
        assert set(by_id) == {"old", "new"}
        assert by_id["new"] > by_id["old"]
        assert by_id["new"] - 0.5 > config.TEMPORAL_RECENCY_WEIGHT * 0.9

    def test_memory_rows_are_dated_by_created_at(self):
        rows = [{"artifact_id": "m", "relevance": 0.5, "created_at": _aware(1)}]
        kept = apply_temporal_intent(rows, "anything recent about the garden")
        assert kept[0]["relevance"] > 0.5

    def test_no_temporal_intent_means_no_boost(self):
        rows = _rows(new=_aware(0), old=_aware(400))
        kept = apply_temporal_intent(rows, "how does photosynthesis work")
        assert [(r["artifact_id"], r["relevance"]) for r in kept] == [("new", 0.5), ("old", 0.5)]
