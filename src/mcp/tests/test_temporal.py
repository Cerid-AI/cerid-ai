# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for temporal awareness utilities."""

from datetime import datetime, timedelta, timezone

from core.utils.temporal import is_within_window, parse_temporal_intent, recency_score


class TestParseTemporalIntent:
    def test_today(self):
        assert parse_temporal_intent("what did I add today") == 1

    def test_yesterday(self):
        assert parse_temporal_intent("notes from yesterday") == 2

    def test_this_week(self):
        assert parse_temporal_intent("show me this week's uploads") == 7

    def test_last_week(self):
        assert parse_temporal_intent("files from last week") == 14

    def test_recent(self):
        assert parse_temporal_intent("show recent documents") == 7
        assert parse_temporal_intent("recently added") == 7

    def test_this_month(self):
        assert parse_temporal_intent("this month's reports") == 30

    def test_last_month(self):
        assert parse_temporal_intent("last month analysis") == 60

    def test_no_temporal(self):
        assert parse_temporal_intent("python data structures") is None
        assert parse_temporal_intent("budget planning template") is None


class TestRecencyScore:
    def test_just_now(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        score = recency_score(now, half_life_days=30)
        assert 0.99 <= score <= 1.0

    def test_half_life(self):
        thirty_days_ago = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)).isoformat()
        score = recency_score(thirty_days_ago, half_life_days=30)
        assert 0.45 <= score <= 0.55  # should be ~0.5

    def test_old_document(self):
        old = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=120)).isoformat()
        score = recency_score(old, half_life_days=30)
        assert score < 0.1  # 4 half-lives -> 0.0625

    def test_invalid_date(self):
        score = recency_score("not-a-date")
        assert score == 0.5  # default


class TestIsWithinWindow:
    def test_recent_within(self):
        recent = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=6)).isoformat()
        assert is_within_window(recent, days=7) is True

    def test_old_outside(self):
        old = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)).isoformat()
        assert is_within_window(old, days=7) is False

    def test_invalid_date_included(self):
        assert is_within_window("invalid", days=7) is True
