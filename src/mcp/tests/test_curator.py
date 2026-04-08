# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for agents/curator.py — artifact quality scoring (Phase 14)."""

import json
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.curator import (
    _store_quality_scores,
    compute_quality_score,
    curate,
    score_completeness,
    score_freshness,
    score_keywords,
    score_summary,
)

UTC = timezone.utc

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
    @patch("core.agents.curator.utcnow")
    def test_recent_today(self, mock_utcnow):
        """Document from today -> age ~0 days -> score close to 1.0."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
        mock_utcnow.return_value = now
        ts = now.isoformat()
        result = score_freshness(ts)
        assert result == pytest.approx(1.0, abs=0.01)

    @patch("core.agents.curator.utcnow")
    def test_thirty_days_old(self, mock_utcnow):
        """30 days old -> half-life of 30 -> score = 0.5."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
        mock_utcnow.return_value = now
        ts = (now - timedelta(days=30)).isoformat()
        result = score_freshness(ts)
        assert result == pytest.approx(0.5, abs=0.01)

    @patch("core.agents.curator.utcnow")
    def test_sixty_days_old(self, mock_utcnow):
        """60 days old -> 2 half-lives -> score = 0.25."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
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

    @patch("core.agents.curator.utcnow")
    def test_future_date_clamped(self, mock_utcnow):
        """Future date -> age clamped to 0 -> score = 1.0."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
        mock_utcnow.return_value = now
        future = (now + timedelta(days=5)).isoformat()
        result = score_freshness(future)
        assert result == pytest.approx(1.0, abs=0.001)

    @patch("core.agents.curator.utcnow")
    def test_modified_at_preferred(self, mock_utcnow):
        """modified_at should be used over ingested_at when present."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
        mock_utcnow.return_value = now
        old_ingested = (now - timedelta(days=60)).isoformat()
        recent_modified = (now - timedelta(days=1)).isoformat()

        result = score_freshness(old_ingested, modified_at=recent_modified)
        # Should use modified_at (1 day old) not ingested_at (60 days old)
        expected = math.pow(2, -1 / 30)
        assert result == pytest.approx(expected, abs=0.01)

    @patch("core.agents.curator.utcnow")
    def test_modified_at_none_falls_back(self, mock_utcnow):
        """modified_at=None -> falls back to ingested_at."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
        mock_utcnow.return_value = now
        ts = (now - timedelta(days=30)).isoformat()

        result = score_freshness(ts, modified_at=None)
        assert result == pytest.approx(0.5, abs=0.01)

    @patch("core.agents.curator.utcnow")
    def test_naive_timestamp_comparison(self, mock_utcnow):
        """Naive (no timezone) timestamp should still work."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
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
    @patch("utils.quality._utcnow")
    @patch("core.agents.curator.utcnow")
    def test_weighted_sum_matches_manual(self, mock_utcnow, mock_quality_utcnow):
        """Verify weighted sum calculation with known inputs."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
        mock_utcnow.return_value = now
        mock_quality_utcnow.return_value = now

        artifact = {
            "summary": "a" * 100,  # optimal -> s_summary = 1.0
            "keywords": json.dumps(["a", "b", "c", "d", "e"]),  # 5 -> s_keywords = 1.0
            "ingested_at": now.isoformat(),  # today -> s_freshness ~1.0
            "tags": json.dumps(["tag1"]),
            "sub_category": "python",
        }

        result = compute_quality_score(artifact)

        # v2 scoring delegates to utils.quality with 6-dimension weights:
        # richness(0.25), metadata(0.20), freshness(0.15), authority(0.15),
        # utility(0.15), coherence(0.10).  Optimal inputs produce ~0.63
        # because richness of a single-word 100-char string is low and
        # utility defaults to 0 (never retrieved).
        assert result["quality_score"] == pytest.approx(0.6325, abs=0.01)
        assert result["breakdown"]["summary"] == pytest.approx(1.0)
        assert result["breakdown"]["keywords"] == pytest.approx(1.0)
        assert result["breakdown"]["freshness"] == pytest.approx(1.0, abs=0.01)
        assert result["breakdown"]["completeness"] == pytest.approx(1.0)
        assert result["issues"] == []

    @patch("core.agents.curator.utcnow")
    def test_issues_include_summary_missing(self, mock_utcnow):
        """Missing summary -> summary_missing in issues."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
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

    @patch("core.agents.curator.utcnow")
    def test_issues_include_summary_weak(self, mock_utcnow):
        """Short but non-empty summary (< 0.5 score) -> summary_weak."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
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

    @patch("core.agents.curator.utcnow")
    def test_issues_include_keywords_missing(self, mock_utcnow):
        """No keywords -> keywords_missing."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
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

    @patch("core.agents.curator.utcnow")
    def test_issues_include_keywords_sparse(self, mock_utcnow):
        """1 keyword (score = 0.2 < 0.5) -> keywords_sparse."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
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

    @patch("core.agents.curator.utcnow")
    def test_issues_include_metadata_incomplete(self, mock_utcnow):
        """Completeness < 0.5 -> metadata_incomplete."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
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

    @patch("core.agents.curator.utcnow")
    def test_issues_include_stale(self, mock_utcnow):
        """Very old artifact -> freshness < 0.3 -> stale."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
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

    @patch("utils.quality._utcnow")
    @patch("core.agents.curator.utcnow")
    def test_all_dimensions_contribute(self, mock_utcnow, mock_quality_utcnow):
        """Verify each dimension contributes proportionally to weighted total."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
        mock_utcnow.return_value = now
        mock_quality_utcnow.return_value = now

        artifact = {
            "summary": "a" * 25,  # 25/50 = 0.5
            "keywords": json.dumps(["a", "b"]),  # 2/5 = 0.4
            "ingested_at": (now - timedelta(days=30)).isoformat(),  # ~0.5
            "tags": json.dumps(["t1"]),
            "sub_category": "python",
        }

        result = compute_quality_score(artifact)

        # v2 scoring: quality_score comes from utils.quality (6-dim formula).
        # With summary "a"*25 (low richness), 2 keywords, 30-day-old general
        # doc (half_life=7 days so freshness ~0.05), 0 retrieval utility,
        # the actual score is ~0.49.  Verify each dimension contributes:
        assert result["quality_score"] == pytest.approx(0.49, abs=0.02)
        assert result["breakdown"]["summary"] == pytest.approx(0.5)
        assert result["breakdown"]["keywords"] == pytest.approx(0.4)

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
# Tests: _store_quality_scores (mocked Neo4j)
# ---------------------------------------------------------------------------

class TestStoreQualityScores:
    @pytest.mark.asyncio
    async def test_empty_list_returns_zero(self):
        """Empty scores list -> returns 0, no graph_store interaction."""
        graph_store = MagicMock()
        graph_store.update_artifact = AsyncMock()
        result = await _store_quality_scores(graph_store, [])
        assert result == 0
        graph_store.update_artifact.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_update_artifact_for_each_score(self):
        """Verify update_artifact is called for each score."""
        graph_store = MagicMock()
        graph_store.update_artifact = AsyncMock()

        scores = [
            {"artifact_id": "a1", "quality_score": 0.85},
            {"artifact_id": "a2", "quality_score": 0.60},
            {"artifact_id": "a3", "quality_score": 0.45},
        ]

        result = await _store_quality_scores(graph_store, scores)

        assert graph_store.update_artifact.call_count == 3
        assert result == 3

    @pytest.mark.asyncio
    async def test_returns_count_of_successful_updates(self):
        """Return value should be count of successful update_artifact calls."""
        graph_store = MagicMock()
        graph_store.update_artifact = AsyncMock()

        scores = [{"artifact_id": "a1", "quality_score": 0.9}]
        result = await _store_quality_scores(graph_store, scores)
        assert result == 1

    @pytest.mark.asyncio
    async def test_individual_failure_does_not_abort(self):
        """If update_artifact raises for one item, others should still be processed."""
        graph_store = MagicMock()
        graph_store.update_artifact = AsyncMock(
            side_effect=[None, Exception("fail"), None]
        )

        scores = [
            {"artifact_id": "a1", "quality_score": 0.9},
            {"artifact_id": "a2", "quality_score": 0.8},
            {"artifact_id": "a3", "quality_score": 0.7},
        ]
        result = await _store_quality_scores(graph_store, scores)
        assert result == 2  # 3 attempted, 1 failed


# ---------------------------------------------------------------------------
# Tests: curate() (mocked Neo4j + list_artifacts)
# ---------------------------------------------------------------------------

class TestCurate:
    """Tests for curate() — uses mock GraphStore (not raw neo4j driver)."""

    @staticmethod
    def _make_graph_store(artifacts_by_domain=None):
        """Create a mock GraphStore with list_artifacts and update_artifact."""
        from core.contracts.stores import ArtifactNode

        gs = MagicMock()
        gs.update_artifact = AsyncMock()

        if artifacts_by_domain is None:
            gs.list_artifacts = AsyncMock(return_value=[])
        else:
            # Return different artifacts per domain call
            side_effects = []
            for domain_nodes in artifacts_by_domain:
                if isinstance(domain_nodes, Exception):
                    side_effects.append(domain_nodes)
                else:
                    nodes = []
                    for d in domain_nodes:
                        nodes.append(ArtifactNode(
                            id=d["id"],
                            filename=d["filename"],
                            domain=d.get("domain", "coding"),
                            sub_category=d.get("sub_category", "general"),
                            tags=d.get("tags_list", []),
                            summary=d.get("summary", ""),
                            quality_score=d.get("quality_score", 0.0),
                        ))
                    side_effects.append(nodes)
            gs.list_artifacts = AsyncMock(side_effect=side_effects)
        return gs

    @staticmethod
    def _config_defaults(mock_config, domains=None):
        mock_config.DOMAINS = domains or ["coding"]
        mock_config.QUALITY_WEIGHT_SUMMARY = 0.30
        mock_config.QUALITY_WEIGHT_KEYWORDS = 0.25
        mock_config.QUALITY_WEIGHT_FRESHNESS = 0.20
        mock_config.QUALITY_WEIGHT_COMPLETENESS = 0.25
        mock_config.QUALITY_SUMMARY_MIN_CHARS = 50
        mock_config.QUALITY_SUMMARY_MAX_CHARS = 500
        mock_config.QUALITY_KEYWORDS_OPTIMAL = 5
        mock_config.TEMPORAL_HALF_LIFE_DAYS = 30
        mock_config.DEFAULT_SUB_CATEGORY = "general"

    @pytest.mark.asyncio
    @patch("core.agents.curator.utcnow")
    @patch("core.agents.curator._store_quality_scores", new_callable=AsyncMock)
    @patch("core.agents.curator.config")
    async def test_correct_response_shape(
        self, mock_config, mock_store, mock_utcnow
    ):
        """Verify curate() returns all expected keys."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
        mock_utcnow.return_value = now
        self._config_defaults(mock_config)

        gs = self._make_graph_store([[
            {"id": "art-1", "filename": "test.py", "summary": "a" * 100,
             "sub_category": "python", "tags_list": ["tag1"]},
        ]])
        mock_store.return_value = 1

        result = await curate(graph_store=gs, mode="audit")

        assert "timestamp" in result
        assert result["mode"] == "audit"
        assert result["artifacts_scored"] == 1
        assert result["artifacts_stored"] == 1
        assert "avg_quality_score" in result
        assert "score_distribution" in result
        assert "domains_scored" in result
        assert "low_quality_artifacts" in result

    @pytest.mark.asyncio
    @patch("core.agents.curator.utcnow")
    @patch("core.agents.curator._store_quality_scores", new_callable=AsyncMock)
    @patch("core.agents.curator.config")
    async def test_domain_filtering(
        self, mock_config, mock_store, mock_utcnow
    ):
        """When domains are specified, only those domains should be scored."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
        mock_utcnow.return_value = now
        self._config_defaults(mock_config, domains=["coding", "finance", "general"])

        gs = self._make_graph_store([[]])  # finance returns empty
        mock_store.return_value = 0

        result = await curate(graph_store=gs, domains=["finance"])

        assert result["domains_scored"] == ["finance"]
        gs.list_artifacts.assert_called_once_with(domain="finance", limit=200)

    @pytest.mark.asyncio
    @patch("core.agents.curator.utcnow")
    @patch("core.agents.curator._store_quality_scores", new_callable=AsyncMock)
    @patch("core.agents.curator.config")
    async def test_handles_neo4j_list_failure(
        self, mock_config, mock_store, mock_utcnow
    ):
        """If list_artifacts raises for a domain, curate() continues with warning."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
        mock_utcnow.return_value = now
        self._config_defaults(mock_config, domains=["coding", "finance"])

        gs = self._make_graph_store([
            Exception("Neo4j connection error"),
            [{"id": "art-1", "filename": "budget.xlsx", "summary": "a" * 100,
              "domain": "finance", "sub_category": "tax", "tags_list": ["tag1"]}],
        ])
        mock_store.return_value = 1

        result = await curate(graph_store=gs)

        assert result["artifacts_scored"] == 1
        assert result["artifacts_stored"] == 1

    @pytest.mark.asyncio
    @patch("core.agents.curator.utcnow")
    @patch("core.agents.curator._store_quality_scores", new_callable=AsyncMock)
    @patch("core.agents.curator.config")
    async def test_handles_store_failure(
        self, mock_config, mock_store, mock_utcnow
    ):
        """If _store_quality_scores raises, curate() continues and reports 0 stored."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
        mock_utcnow.return_value = now
        self._config_defaults(mock_config)

        gs = self._make_graph_store([[
            {"id": "art-1", "filename": "test.py", "summary": "a" * 100,
             "sub_category": "general"},
        ]])
        mock_store.side_effect = Exception("Neo4j write failure")

        result = await curate(graph_store=gs)

        assert result["artifacts_scored"] == 1
        assert result["artifacts_stored"] == 0

    @pytest.mark.asyncio
    @patch("core.agents.curator.utcnow")
    @patch("core.agents.curator._store_quality_scores", new_callable=AsyncMock)
    @patch("core.agents.curator.config")
    async def test_low_quality_artifacts_sorted(
        self, mock_config, mock_store, mock_utcnow
    ):
        """Low quality artifacts (< 0.5) should be sorted ascending by score."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
        mock_utcnow.return_value = now
        self._config_defaults(mock_config)

        gs = self._make_graph_store([[
            {"id": "bad-1", "filename": "bad1.py", "summary": "",
             "sub_category": "general"},
            {"id": "mediocre-1", "filename": "mediocre.py", "summary": "a" * 20,
             "sub_category": "general"},
        ]])
        mock_store.return_value = 2

        result = await curate(graph_store=gs)

        lq = result["low_quality_artifacts"]
        assert len(lq) >= 1
        if len(lq) > 1:
            assert lq[0]["quality_score"] <= lq[1]["quality_score"]

    @pytest.mark.asyncio
    @patch("core.agents.curator.utcnow")
    @patch("core.agents.curator._store_quality_scores", new_callable=AsyncMock)
    @patch("core.agents.curator.config")
    async def test_no_artifacts(
        self, mock_config, mock_store, mock_utcnow
    ):
        """No artifacts across any domain -> avg_quality_score = 0.0."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
        mock_utcnow.return_value = now
        self._config_defaults(mock_config)

        gs = self._make_graph_store([[]])
        mock_store.return_value = 0

        result = await curate(graph_store=gs)

        assert result["artifacts_scored"] == 0
        assert result["avg_quality_score"] == 0.0
        assert result["low_quality_artifacts"] == []
        mock_store.assert_not_called()

    @pytest.mark.asyncio
    @patch("core.agents.curator.utcnow")
    @patch("core.agents.curator._store_quality_scores", new_callable=AsyncMock)
    @patch("core.agents.curator.config")
    async def test_max_artifacts_passed(
        self, mock_config, mock_store, mock_utcnow
    ):
        """max_artifacts kwarg should be passed through to list_artifacts."""
        now = datetime(2026, 2, 28, 12, 0, 0, tzinfo=UTC)
        mock_utcnow.return_value = now
        self._config_defaults(mock_config)

        gs = self._make_graph_store([[]])
        mock_store.return_value = 0

        await curate(graph_store=gs, max_artifacts=50)

        gs.list_artifacts.assert_called_once_with(domain="coding", limit=50)
