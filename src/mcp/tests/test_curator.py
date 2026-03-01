# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for agents/curator.py — artifact quality scoring (Phase 14)."""

import json
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from agents.curator import (
    _score_distribution,
    _store_quality_scores,
    compute_quality_score,
    curate,
    score_completeness,
    score_freshness,
    score_keywords,
    score_summary,
)


# ---------------------------------------------------------------------------
# Tests: score_summary
# ---------------------------------------------------------------------------

class TestScoreSummary:
    def test_empty_string(self):
        assert score_summary("") == 0.0

    def test_none(self):
        assert score_summary(None) == 0.0

    def test_whitespace_only(self):
        assert score_summary("   \t\n  ") == 0.0

    def test_short_below_min(self):
        """20 chars, below MIN_CHARS (50) -> linear ramp: 20/50 = 0.4."""
        text = "a" * 20
        result = score_summary(text)
        assert result == pytest.approx(0.4)

    def test_exactly_min_chars(self):
        """Exactly 50 chars -> at the boundary, should score 1.0."""
        text = "a" * 50
        assert score_summary(text) == 1.0

    def test_optimal_length(self):
        """100 chars, between min (50) and max (500) -> 1.0."""
        text = "a" * 100
        assert score_summary(text) == 1.0

    def test_exactly_max_chars(self):
        """500 chars -> at max boundary, still 1.0."""
        text = "a" * 500
        assert score_summary(text) == 1.0

    def test_long_above_max(self):
        """800 chars, 300 above max (500) -> max(0.3, 1.0 - 300/1000) = 0.7."""
        text = "a" * 800
        result = score_summary(text)
        assert result == pytest.approx(0.7)

    def test_very_long_hits_floor(self):
        """1500 chars, 1000 above max -> max(0.3, 1.0 - 1000/1000) = 0.3."""
        text = "a" * 1500
        result = score_summary(text)
        assert result == pytest.approx(0.3)

    def test_extremely_long_clamps_at_floor(self):
        """2000 chars, 1500 above max -> max(0.3, 1.0 - 1.5) = max(0.3, -0.5) = 0.3."""
        text = "a" * 2000
        result = score_summary(text)
        assert result == pytest.approx(0.3)

    def test_whitespace_stripped_before_length(self):
        """Leading/trailing whitespace should be stripped before measuring."""
        text = "   " + "a" * 20 + "   "
        result = score_summary(text)
        assert result == pytest.approx(0.4)

    def test_linear_ramp_midpoint(self):
        """25 chars -> 25/50 = 0.5."""
        text = "a" * 25
        assert score_summary(text) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Tests: score_keywords
# ---------------------------------------------------------------------------

class TestScoreKeywords:
    def test_empty_json(self):
        assert score_keywords("[]") == 0.0

    def test_malformed_json(self):
        assert score_keywords("not valid json") == 0.0

    def test_none_input(self):
        assert score_keywords(None) == 0.0

    def test_empty_string(self):
        assert score_keywords("") == 0.0

    def test_partial_keywords(self):
        """3 keywords, optimal is 5 -> 3/5 = 0.6."""
        kw = json.dumps(["python", "api", "rest"])
        result = score_keywords(kw)
        assert result == pytest.approx(0.6)

    def test_optimal_keywords(self):
        """5 keywords -> 1.0."""
        kw = json.dumps(["a", "b", "c", "d", "e"])
        assert score_keywords(kw) == 1.0

    def test_more_than_optimal(self):
        """7 keywords, >= 5 -> 1.0."""
        kw = json.dumps(["a", "b", "c", "d", "e", "f", "g"])
        assert score_keywords(kw) == 1.0

    def test_single_keyword(self):
        """1 keyword -> 1/5 = 0.2."""
        kw = json.dumps(["solo"])
        assert score_keywords(kw) == pytest.approx(0.2)

    def test_two_keywords(self):
        """2 keywords -> 2/5 = 0.4."""
        kw = json.dumps(["one", "two"])
        assert score_keywords(kw) == pytest.approx(0.4)

    def test_non_list_json(self):
        """JSON string that decodes to a non-list (e.g. dict) -> treated as truthy but len()."""
        kw = json.dumps({"key": "value"})
        # json.loads returns a dict with 1 key -> len 1 -> 1/5 = 0.2
        assert score_keywords(kw) == pytest.approx(0.2)

    def test_type_error_on_integer(self):
        """Passing an integer -> TypeError caught -> 0.0."""
        assert score_keywords(123) == 0.0


# ---------------------------------------------------------------------------
# Tests: score_freshness
# ---------------------------------------------------------------------------

class TestScoreFreshness:
    @patch("agents.curator.utcnow")
    def test_recent_today(self, mock_utcnow):
        """Document from today -> age ~0 days -> score close to 1.0."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now
        ts = now.isoformat()
        result = score_freshness(ts)
        assert result == pytest.approx(1.0, abs=0.01)

    @patch("agents.curator.utcnow")
    def test_thirty_days_old(self, mock_utcnow):
        """30 days old -> half-life of 30 -> score = 0.5."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now
        ts = (now - timedelta(days=30)).isoformat()
        result = score_freshness(ts)
        assert result == pytest.approx(0.5, abs=0.01)

    @patch("agents.curator.utcnow")
    def test_sixty_days_old(self, mock_utcnow):
        """60 days old -> 2 half-lives -> score = 0.25."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now
        ts = (now - timedelta(days=60)).isoformat()
        result = score_freshness(ts)
        assert result == pytest.approx(0.25, abs=0.01)

    def test_bad_timestamp(self):
        """Bad timestamp string -> fallback 0.5."""
        assert score_freshness("not-a-date") == 0.5

    def test_empty_timestamp(self):
        """Empty string -> fallback 0.5."""
        assert score_freshness("") == 0.5

    def test_none_ingested_at(self):
        """None ingested_at, no modified_at -> fallback 0.5."""
        assert score_freshness(None) == 0.5

    @patch("agents.curator.utcnow")
    def test_future_date_clamped(self, mock_utcnow):
        """Future date -> age clamped to 0 -> score = 1.0."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now
        future = (now + timedelta(days=5)).isoformat()
        result = score_freshness(future)
        assert result == pytest.approx(1.0, abs=0.001)

    @patch("agents.curator.utcnow")
    def test_modified_at_preferred(self, mock_utcnow):
        """modified_at should be used over ingested_at when present."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now
        old_ingested = (now - timedelta(days=60)).isoformat()
        recent_modified = (now - timedelta(days=1)).isoformat()

        result = score_freshness(old_ingested, modified_at=recent_modified)
        # Should use modified_at (1 day old) not ingested_at (60 days old)
        expected = math.pow(2, -1 / 30)
        assert result == pytest.approx(expected, abs=0.01)

    @patch("agents.curator.utcnow")
    def test_modified_at_none_falls_back(self, mock_utcnow):
        """modified_at=None -> falls back to ingested_at."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now
        ts = (now - timedelta(days=30)).isoformat()

        result = score_freshness(ts, modified_at=None)
        assert result == pytest.approx(0.5, abs=0.01)

    @patch("agents.curator.utcnow")
    def test_naive_timestamp_comparison(self, mock_utcnow):
        """Naive (no timezone) timestamp should still work."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now
        # Naive timestamp (no +00:00 suffix)
        naive_ts = "2026-02-28T12:00:00"
        result = score_freshness(naive_ts)
        assert result == pytest.approx(1.0, abs=0.01)


# ---------------------------------------------------------------------------
# Tests: score_completeness
# ---------------------------------------------------------------------------

class TestScoreCompleteness:
    def test_full_metadata(self):
        """All 4 checks pass -> 1.0."""
        artifact = {
            "summary": "This is a sufficiently long summary text here.",  # >=20 chars
            "keywords": json.dumps(["a", "b"]),  # >=2 keywords
            "tags": json.dumps(["tag1"]),  # has tags
            "sub_category": "python",  # non-default
        }
        assert score_completeness(artifact) == 1.0

    def test_empty_artifact(self):
        """No fields -> 0.0."""
        assert score_completeness({}) == 0.0

    def test_partial_two_of_four(self):
        """2 of 4 checks pass -> 0.5."""
        artifact = {
            "summary": "This is a valid summary text",  # >=20 chars: pass
            "keywords": json.dumps(["a", "b"]),  # >=2: pass
            "tags": "[]",  # empty: fail
            "sub_category": "general",  # default: fail
        }
        assert score_completeness(artifact) == pytest.approx(0.5)

    def test_short_summary_fails(self):
        """Summary <20 chars fails the check."""
        artifact = {
            "summary": "short",  # <20: fail
            "keywords": json.dumps(["a", "b", "c"]),  # pass
            "tags": json.dumps(["tag1"]),  # pass
            "sub_category": "python",  # pass
        }
        assert score_completeness(artifact) == pytest.approx(0.75)

    def test_one_keyword_fails(self):
        """Only 1 keyword fails the >=2 check."""
        artifact = {
            "summary": "A long enough summary for test.",  # pass
            "keywords": json.dumps(["only_one"]),  # <2: fail
            "tags": json.dumps(["tag1"]),  # pass
            "sub_category": "devops",  # pass
        }
        assert score_completeness(artifact) == pytest.approx(0.75)

    def test_default_sub_category_fails(self):
        """sub_category == 'general' (default) fails."""
        artifact = {
            "summary": "A long enough summary text.",
            "keywords": json.dumps(["a", "b"]),
            "tags": json.dumps(["t1"]),
            "sub_category": "general",
        }
        assert score_completeness(artifact) == pytest.approx(0.75)

    def test_missing_tags_fails(self):
        """No tags field -> empty list -> fail."""
        artifact = {
            "summary": "A long enough summary text.",
            "keywords": json.dumps(["a", "b"]),
            "sub_category": "python",
        }
        # tags defaults to "[]" via .get -> empty -> fail
        assert score_completeness(artifact) == pytest.approx(0.75)

    def test_malformed_keywords_json(self):
        """Malformed keywords JSON -> empty list -> fail."""
        artifact = {
            "summary": "A long enough summary text.",
            "keywords": "not json",
            "tags": json.dumps(["tag1"]),
            "sub_category": "python",
        }
        assert score_completeness(artifact) == pytest.approx(0.75)

    def test_malformed_tags_json(self):
        """Malformed tags JSON -> empty list -> fail."""
        artifact = {
            "summary": "A long enough summary text.",
            "keywords": json.dumps(["a", "b"]),
            "tags": "not json",
            "sub_category": "python",
        }
        assert score_completeness(artifact) == pytest.approx(0.75)

    def test_none_keywords(self):
        """keywords=None -> empty list -> fail."""
        artifact = {
            "summary": "A long enough summary text.",
            "keywords": None,
            "tags": json.dumps(["tag1"]),
            "sub_category": "python",
        }
        assert score_completeness(artifact) == pytest.approx(0.75)

    def test_empty_sub_category(self):
        """Empty sub_category -> falsy -> fail."""
        artifact = {
            "summary": "A long enough summary text.",
            "keywords": json.dumps(["a", "b"]),
            "tags": json.dumps(["tag1"]),
            "sub_category": "",
        }
        assert score_completeness(artifact) == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# Tests: compute_quality_score
# ---------------------------------------------------------------------------

class TestComputeQualityScore:
    @patch("agents.curator.utcnow")
    def test_weighted_sum_matches_manual(self, mock_utcnow):
        """Verify weighted sum calculation with known inputs."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now

        artifact = {
            "summary": "a" * 100,  # optimal -> s_summary = 1.0
            "keywords": json.dumps(["a", "b", "c", "d", "e"]),  # 5 -> s_keywords = 1.0
            "ingested_at": now.isoformat(),  # today -> s_freshness ~1.0
            "tags": json.dumps(["tag1"]),
            "sub_category": "python",
        }

        result = compute_quality_score(artifact)

        # s_summary = 1.0, s_keywords = 1.0, s_freshness ~1.0, s_completeness = 1.0
        # total = 0.30*1.0 + 0.25*1.0 + 0.20*1.0 + 0.25*1.0 = 1.0
        assert result["quality_score"] == pytest.approx(1.0, abs=0.01)
        assert result["breakdown"]["summary"] == pytest.approx(1.0)
        assert result["breakdown"]["keywords"] == pytest.approx(1.0)
        assert result["breakdown"]["freshness"] == pytest.approx(1.0, abs=0.01)
        assert result["breakdown"]["completeness"] == pytest.approx(1.0)
        assert result["issues"] == []

    @patch("agents.curator.utcnow")
    def test_issues_include_summary_missing(self, mock_utcnow):
        """Missing summary -> summary_missing in issues."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now

        artifact = {
            "summary": "",
            "keywords": json.dumps(["a", "b", "c", "d", "e"]),
            "ingested_at": now.isoformat(),
            "tags": json.dumps(["t1"]),
            "sub_category": "python",
        }

        result = compute_quality_score(artifact)
        assert "summary_missing" in result["issues"]

    @patch("agents.curator.utcnow")
    def test_issues_include_summary_weak(self, mock_utcnow):
        """Short but non-empty summary (< 0.5 score) -> summary_weak."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now

        # 20 chars -> score = 20/50 = 0.4 < 0.5
        artifact = {
            "summary": "a" * 20,
            "keywords": json.dumps(["a", "b", "c", "d", "e"]),
            "ingested_at": now.isoformat(),
            "tags": json.dumps(["t1"]),
            "sub_category": "python",
        }

        result = compute_quality_score(artifact)
        assert "summary_weak" in result["issues"]

    @patch("agents.curator.utcnow")
    def test_issues_include_keywords_missing(self, mock_utcnow):
        """No keywords -> keywords_missing."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now

        artifact = {
            "summary": "a" * 100,
            "keywords": "[]",
            "ingested_at": now.isoformat(),
            "tags": json.dumps(["t1"]),
            "sub_category": "python",
        }

        result = compute_quality_score(artifact)
        assert "keywords_missing" in result["issues"]

    @patch("agents.curator.utcnow")
    def test_issues_include_keywords_sparse(self, mock_utcnow):
        """1 keyword (score = 0.2 < 0.5) -> keywords_sparse."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now

        artifact = {
            "summary": "a" * 100,
            "keywords": json.dumps(["one"]),
            "ingested_at": now.isoformat(),
            "tags": json.dumps(["t1"]),
            "sub_category": "python",
        }

        result = compute_quality_score(artifact)
        assert "keywords_sparse" in result["issues"]

    @patch("agents.curator.utcnow")
    def test_issues_include_metadata_incomplete(self, mock_utcnow):
        """Completeness < 0.5 -> metadata_incomplete."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now

        artifact = {
            "summary": "short",  # <20 chars -> fail
            "keywords": "[]",  # empty -> fail
            "ingested_at": now.isoformat(),
            "tags": "[]",  # empty -> fail
            "sub_category": "general",  # default -> fail
        }

        result = compute_quality_score(artifact)
        assert "metadata_incomplete" in result["issues"]

    @patch("agents.curator.utcnow")
    def test_issues_include_stale(self, mock_utcnow):
        """Very old artifact -> freshness < 0.3 -> stale."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now

        # 90 days old -> 2^(-90/30) = 2^-3 = 0.125 < 0.3
        artifact = {
            "summary": "a" * 100,
            "keywords": json.dumps(["a", "b", "c", "d", "e"]),
            "ingested_at": (now - timedelta(days=90)).isoformat(),
            "tags": json.dumps(["t1"]),
            "sub_category": "python",
        }

        result = compute_quality_score(artifact)
        assert "stale" in result["issues"]

    @patch("agents.curator.utcnow")
    def test_all_dimensions_contribute(self, mock_utcnow):
        """Verify each dimension contributes proportionally to weighted total."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now

        artifact = {
            "summary": "a" * 25,  # 25/50 = 0.5
            "keywords": json.dumps(["a", "b"]),  # 2/5 = 0.4
            "ingested_at": (now - timedelta(days=30)).isoformat(),  # ~0.5
            "tags": json.dumps(["t1"]),
            "sub_category": "python",
        }

        result = compute_quality_score(artifact)

        # Manually compute expected:
        # completeness: summary >=20 (pass), kw >=2 (pass), tags (pass), sub_cat non-default (pass) = 1.0
        # total = 0.30*0.5 + 0.25*0.4 + 0.20*0.5 + 0.25*1.0
        #       = 0.15 + 0.10 + 0.10 + 0.25 = 0.60
        expected = 0.30 * 0.5 + 0.25 * 0.4 + 0.20 * result["breakdown"]["freshness"] + 0.25 * 1.0
        assert result["quality_score"] == pytest.approx(expected, abs=0.01)

    def test_result_shape(self):
        """Ensure result has the expected keys."""
        artifact = {
            "summary": "test",
            "keywords": "[]",
            "ingested_at": "",
        }
        result = compute_quality_score(artifact)
        assert "quality_score" in result
        assert "breakdown" in result
        assert "issues" in result
        assert set(result["breakdown"].keys()) == {"summary", "keywords", "freshness", "completeness"}


# ---------------------------------------------------------------------------
# Tests: _score_distribution
# ---------------------------------------------------------------------------

class TestScoreDistribution:
    def test_excellent_bucket(self):
        """Score >= 0.8 -> excellent."""
        dist = _score_distribution([0.85])
        assert dist == {"excellent": 1, "good": 0, "fair": 0, "poor": 0}

    def test_good_bucket(self):
        """0.6 <= score < 0.8 -> good."""
        dist = _score_distribution([0.65])
        assert dist == {"excellent": 0, "good": 1, "fair": 0, "poor": 0}

    def test_fair_bucket(self):
        """0.4 <= score < 0.6 -> fair."""
        dist = _score_distribution([0.45])
        assert dist == {"excellent": 0, "good": 0, "fair": 1, "poor": 0}

    def test_poor_bucket(self):
        """score < 0.4 -> poor."""
        dist = _score_distribution([0.2])
        assert dist == {"excellent": 0, "good": 0, "fair": 0, "poor": 1}

    def test_boundary_0_8(self):
        """Exactly 0.8 -> excellent."""
        dist = _score_distribution([0.8])
        assert dist["excellent"] == 1

    def test_boundary_0_6(self):
        """Exactly 0.6 -> good."""
        dist = _score_distribution([0.6])
        assert dist["good"] == 1

    def test_boundary_0_4(self):
        """Exactly 0.4 -> fair."""
        dist = _score_distribution([0.4])
        assert dist["fair"] == 1

    def test_boundary_0_0(self):
        """0.0 -> poor."""
        dist = _score_distribution([0.0])
        assert dist["poor"] == 1

    def test_empty_list(self):
        """Empty input -> all zeros."""
        dist = _score_distribution([])
        assert dist == {"excellent": 0, "good": 0, "fair": 0, "poor": 0}

    def test_mixed_scores(self):
        """Multiple scores across buckets."""
        scores = [0.9, 0.85, 0.7, 0.65, 0.5, 0.3, 0.1]
        dist = _score_distribution(scores)
        assert dist == {"excellent": 2, "good": 2, "fair": 1, "poor": 2}


# ---------------------------------------------------------------------------
# Tests: _store_quality_scores (mocked Neo4j)
# ---------------------------------------------------------------------------

class TestStoreQualityScores:
    def test_empty_list_returns_zero(self):
        """Empty scores list -> returns 0, no session interaction."""
        driver = MagicMock()
        result = _store_quality_scores(driver, [])
        assert result == 0
        driver.session.assert_not_called()

    def test_calls_session_run_with_unwind(self, mock_neo4j):
        """Verify session.run is called with UNWIND query and correct params."""
        driver, session = mock_neo4j
        record = MagicMock()
        record.__getitem__ = lambda self, key: 3  # 3 updated
        session.run.return_value.single.return_value = record

        scores = [
            {"artifact_id": "a1", "quality_score": 0.85},
            {"artifact_id": "a2", "quality_score": 0.60},
            {"artifact_id": "a3", "quality_score": 0.45},
        ]

        result = _store_quality_scores(driver, scores)

        # Check session.run was called
        session.run.assert_called_once()
        call_args = session.run.call_args

        # Verify the query contains UNWIND
        query = call_args[0][0]
        assert "UNWIND" in query
        assert "quality_score" in query

        # Verify items parameter
        items = call_args[1]["items"]
        assert len(items) == 3
        assert items[0] == {"id": "a1", "score": 0.85}
        assert items[1] == {"id": "a2", "score": 0.60}

        assert result == 3

    def test_returns_count_from_result(self, mock_neo4j):
        """Return value should come from the 'updated' field in result."""
        driver, session = mock_neo4j
        record = MagicMock()
        record.__getitem__ = lambda self, key: 5
        session.run.return_value.single.return_value = record

        scores = [{"artifact_id": "a1", "quality_score": 0.9}]
        result = _store_quality_scores(driver, scores)
        assert result == 5

    def test_none_record_returns_zero(self, mock_neo4j):
        """If result.single() returns None, return 0."""
        driver, session = mock_neo4j
        session.run.return_value.single.return_value = None

        scores = [{"artifact_id": "a1", "quality_score": 0.9}]
        result = _store_quality_scores(driver, scores)
        assert result == 0


# ---------------------------------------------------------------------------
# Tests: curate() (mocked Neo4j + list_artifacts)
# ---------------------------------------------------------------------------

class TestCurate:
    @pytest.mark.asyncio
    @patch("agents.curator.utcnow")
    @patch("agents.curator._store_quality_scores")
    @patch("agents.curator.list_artifacts")
    @patch("agents.curator.config")
    async def test_correct_response_shape(
        self, mock_config, mock_list, mock_store, mock_utcnow
    ):
        """Verify curate() returns all expected keys."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now
        mock_config.DOMAINS = ["coding"]
        mock_config.QUALITY_WEIGHT_SUMMARY = 0.30
        mock_config.QUALITY_WEIGHT_KEYWORDS = 0.25
        mock_config.QUALITY_WEIGHT_FRESHNESS = 0.20
        mock_config.QUALITY_WEIGHT_COMPLETENESS = 0.25
        mock_config.QUALITY_SUMMARY_MIN_CHARS = 50
        mock_config.QUALITY_SUMMARY_MAX_CHARS = 500
        mock_config.QUALITY_KEYWORDS_OPTIMAL = 5
        mock_config.TEMPORAL_HALF_LIFE_DAYS = 30
        mock_config.DEFAULT_SUB_CATEGORY = "general"

        mock_list.return_value = [
            {
                "id": "art-1",
                "filename": "test.py",
                "summary": "a" * 100,
                "keywords": json.dumps(["a", "b", "c"]),
                "ingested_at": now.isoformat(),
                "tags": json.dumps(["tag1"]),
                "sub_category": "python",
            },
        ]
        mock_store.return_value = 1

        driver = MagicMock()
        result = await curate(driver, mode="audit")

        assert "timestamp" in result
        assert result["mode"] == "audit"
        assert result["artifacts_scored"] == 1
        assert result["artifacts_stored"] == 1
        assert "avg_quality_score" in result
        assert "score_distribution" in result
        assert "domains_scored" in result
        assert "low_quality_artifacts" in result

    @pytest.mark.asyncio
    @patch("agents.curator.utcnow")
    @patch("agents.curator._store_quality_scores")
    @patch("agents.curator.list_artifacts")
    @patch("agents.curator.config")
    async def test_domain_filtering(
        self, mock_config, mock_list, mock_store, mock_utcnow
    ):
        """When domains are specified, only those domains should be scored."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now
        mock_config.DOMAINS = ["coding", "finance", "general"]
        mock_config.QUALITY_WEIGHT_SUMMARY = 0.30
        mock_config.QUALITY_WEIGHT_KEYWORDS = 0.25
        mock_config.QUALITY_WEIGHT_FRESHNESS = 0.20
        mock_config.QUALITY_WEIGHT_COMPLETENESS = 0.25
        mock_config.QUALITY_SUMMARY_MIN_CHARS = 50
        mock_config.QUALITY_SUMMARY_MAX_CHARS = 500
        mock_config.QUALITY_KEYWORDS_OPTIMAL = 5
        mock_config.TEMPORAL_HALF_LIFE_DAYS = 30
        mock_config.DEFAULT_SUB_CATEGORY = "general"

        mock_list.return_value = []
        mock_store.return_value = 0

        driver = MagicMock()
        result = await curate(driver, domains=["finance"])

        assert result["domains_scored"] == ["finance"]
        # list_artifacts should only be called once (for "finance")
        mock_list.assert_called_once_with(driver, domain="finance", limit=200)

    @pytest.mark.asyncio
    @patch("agents.curator.utcnow")
    @patch("agents.curator._store_quality_scores")
    @patch("agents.curator.list_artifacts")
    @patch("agents.curator.config")
    async def test_handles_neo4j_list_failure(
        self, mock_config, mock_list, mock_store, mock_utcnow
    ):
        """If list_artifacts raises for a domain, curate() continues with warning."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now
        mock_config.DOMAINS = ["coding", "finance"]
        mock_config.QUALITY_WEIGHT_SUMMARY = 0.30
        mock_config.QUALITY_WEIGHT_KEYWORDS = 0.25
        mock_config.QUALITY_WEIGHT_FRESHNESS = 0.20
        mock_config.QUALITY_WEIGHT_COMPLETENESS = 0.25
        mock_config.QUALITY_SUMMARY_MIN_CHARS = 50
        mock_config.QUALITY_SUMMARY_MAX_CHARS = 500
        mock_config.QUALITY_KEYWORDS_OPTIMAL = 5
        mock_config.TEMPORAL_HALF_LIFE_DAYS = 30
        mock_config.DEFAULT_SUB_CATEGORY = "general"

        # First domain fails, second succeeds
        mock_list.side_effect = [
            Exception("Neo4j connection error"),
            [
                {
                    "id": "art-1",
                    "filename": "budget.xlsx",
                    "summary": "a" * 100,
                    "keywords": json.dumps(["a", "b", "c", "d", "e"]),
                    "ingested_at": now.isoformat(),
                    "tags": json.dumps(["tag1"]),
                    "sub_category": "tax",
                },
            ],
        ]
        mock_store.return_value = 1

        driver = MagicMock()
        result = await curate(driver)

        # Should still succeed with the second domain's artifact
        assert result["artifacts_scored"] == 1
        assert result["artifacts_stored"] == 1

    @pytest.mark.asyncio
    @patch("agents.curator.utcnow")
    @patch("agents.curator._store_quality_scores")
    @patch("agents.curator.list_artifacts")
    @patch("agents.curator.config")
    async def test_handles_store_failure(
        self, mock_config, mock_list, mock_store, mock_utcnow
    ):
        """If _store_quality_scores raises, curate() continues and reports 0 stored."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now
        mock_config.DOMAINS = ["coding"]
        mock_config.QUALITY_WEIGHT_SUMMARY = 0.30
        mock_config.QUALITY_WEIGHT_KEYWORDS = 0.25
        mock_config.QUALITY_WEIGHT_FRESHNESS = 0.20
        mock_config.QUALITY_WEIGHT_COMPLETENESS = 0.25
        mock_config.QUALITY_SUMMARY_MIN_CHARS = 50
        mock_config.QUALITY_SUMMARY_MAX_CHARS = 500
        mock_config.QUALITY_KEYWORDS_OPTIMAL = 5
        mock_config.TEMPORAL_HALF_LIFE_DAYS = 30
        mock_config.DEFAULT_SUB_CATEGORY = "general"

        mock_list.return_value = [
            {
                "id": "art-1",
                "filename": "test.py",
                "summary": "a" * 100,
                "keywords": json.dumps(["a", "b", "c"]),
                "ingested_at": now.isoformat(),
                "tags": "[]",
                "sub_category": "general",
            },
        ]
        mock_store.side_effect = Exception("Neo4j write failure")

        driver = MagicMock()
        result = await curate(driver)

        # Scored but not stored
        assert result["artifacts_scored"] == 1
        assert result["artifacts_stored"] == 0

    @pytest.mark.asyncio
    @patch("agents.curator.utcnow")
    @patch("agents.curator._store_quality_scores")
    @patch("agents.curator.list_artifacts")
    @patch("agents.curator.config")
    async def test_low_quality_artifacts_sorted(
        self, mock_config, mock_list, mock_store, mock_utcnow
    ):
        """Low quality artifacts (< 0.5) should be sorted ascending by score."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now
        mock_config.DOMAINS = ["coding"]
        mock_config.QUALITY_WEIGHT_SUMMARY = 0.30
        mock_config.QUALITY_WEIGHT_KEYWORDS = 0.25
        mock_config.QUALITY_WEIGHT_FRESHNESS = 0.20
        mock_config.QUALITY_WEIGHT_COMPLETENESS = 0.25
        mock_config.QUALITY_SUMMARY_MIN_CHARS = 50
        mock_config.QUALITY_SUMMARY_MAX_CHARS = 500
        mock_config.QUALITY_KEYWORDS_OPTIMAL = 5
        mock_config.TEMPORAL_HALF_LIFE_DAYS = 30
        mock_config.DEFAULT_SUB_CATEGORY = "general"

        # Create two low-quality artifacts
        mock_list.return_value = [
            {
                "id": "bad-1",
                "filename": "bad1.py",
                "summary": "",  # 0.0
                "keywords": "[]",  # 0.0
                "ingested_at": (now - timedelta(days=120)).isoformat(),
                "tags": "[]",
                "sub_category": "general",
            },
            {
                "id": "mediocre-1",
                "filename": "mediocre.py",
                "summary": "a" * 20,  # 0.4
                "keywords": json.dumps(["a"]),  # 0.2
                "ingested_at": now.isoformat(),
                "tags": "[]",
                "sub_category": "general",
            },
        ]
        mock_store.return_value = 2

        driver = MagicMock()
        result = await curate(driver)

        lq = result["low_quality_artifacts"]
        assert len(lq) >= 1
        # Should be sorted ascending
        if len(lq) > 1:
            assert lq[0]["quality_score"] <= lq[1]["quality_score"]

    @pytest.mark.asyncio
    @patch("agents.curator.utcnow")
    @patch("agents.curator._store_quality_scores")
    @patch("agents.curator.list_artifacts")
    @patch("agents.curator.config")
    async def test_no_artifacts(
        self, mock_config, mock_list, mock_store, mock_utcnow
    ):
        """No artifacts across any domain -> avg_quality_score = 0.0."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now
        mock_config.DOMAINS = ["coding"]
        mock_config.QUALITY_WEIGHT_SUMMARY = 0.30
        mock_config.QUALITY_WEIGHT_KEYWORDS = 0.25
        mock_config.QUALITY_WEIGHT_FRESHNESS = 0.20
        mock_config.QUALITY_WEIGHT_COMPLETENESS = 0.25
        mock_config.QUALITY_SUMMARY_MIN_CHARS = 50
        mock_config.QUALITY_SUMMARY_MAX_CHARS = 500
        mock_config.QUALITY_KEYWORDS_OPTIMAL = 5
        mock_config.TEMPORAL_HALF_LIFE_DAYS = 30
        mock_config.DEFAULT_SUB_CATEGORY = "general"

        mock_list.return_value = []
        mock_store.return_value = 0

        driver = MagicMock()
        result = await curate(driver)

        assert result["artifacts_scored"] == 0
        assert result["avg_quality_score"] == 0.0
        assert result["low_quality_artifacts"] == []
        # _store_quality_scores should not be called with empty list
        mock_store.assert_not_called()

    @pytest.mark.asyncio
    @patch("agents.curator.utcnow")
    @patch("agents.curator._store_quality_scores")
    @patch("agents.curator.list_artifacts")
    @patch("agents.curator.config")
    async def test_max_artifacts_passed(
        self, mock_config, mock_list, mock_store, mock_utcnow
    ):
        """max_artifacts kwarg should be passed through to list_artifacts."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        mock_utcnow.return_value = now
        mock_config.DOMAINS = ["coding"]
        mock_config.QUALITY_WEIGHT_SUMMARY = 0.30
        mock_config.QUALITY_WEIGHT_KEYWORDS = 0.25
        mock_config.QUALITY_WEIGHT_FRESHNESS = 0.20
        mock_config.QUALITY_WEIGHT_COMPLETENESS = 0.25
        mock_config.QUALITY_SUMMARY_MIN_CHARS = 50
        mock_config.QUALITY_SUMMARY_MAX_CHARS = 500
        mock_config.QUALITY_KEYWORDS_OPTIMAL = 5
        mock_config.TEMPORAL_HALF_LIFE_DAYS = 30
        mock_config.DEFAULT_SUB_CATEGORY = "general"

        mock_list.return_value = []
        mock_store.return_value = 0

        driver = MagicMock()
        await curate(driver, max_artifacts=50)

        mock_list.assert_called_once_with(driver, domain="coding", limit=50)
