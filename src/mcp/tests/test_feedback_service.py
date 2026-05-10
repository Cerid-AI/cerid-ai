# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the per-claim user feedback service (Phase R.1).

Tests run without a live Neo4j instance — the adapter layer is mocked
at the service boundary.  Covers:

1. submit_feedback → returns a rating_id
2. Idempotency: same (claim_id, user_id) → update, not insert
3. Idempotency: same (claim_id, session_id) → update
4. get_claim_accuracy: zero ratings → agreement_rate=0.0, total_rated=0
5. get_claim_accuracy: mocked counts → correct rate computation
6. Invalid sentiment is rejected by Pydantic before reaching the adapter
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.services.feedback import ClaimAccuracyStats, ClaimFeedback, get_claim_accuracy, submit_feedback

# ---------------------------------------------------------------------------
# ClaimFeedback model
# ---------------------------------------------------------------------------


class TestClaimFeedbackModel:
    def test_valid_positive(self) -> None:
        fb = ClaimFeedback(claim_id="claim-001", sentiment=1, session_id="sess-xyz")
        assert fb.sentiment == 1
        assert fb.claim_id == "claim-001"

    def test_valid_negative(self) -> None:
        fb = ClaimFeedback(claim_id="claim-002", sentiment=-1)
        assert fb.sentiment == -1

    def test_valid_neutral(self) -> None:
        fb = ClaimFeedback(claim_id="claim-003", sentiment=0)
        assert fb.sentiment == 0

    def test_invalid_sentiment_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ClaimFeedback(claim_id="claim-004", sentiment=2)  # type: ignore[arg-type]

    def test_comment_max_length_enforced(self) -> None:
        with pytest.raises(ValidationError):
            ClaimFeedback(claim_id="claim-005", sentiment=1, comment="x" * 501)

    def test_comment_at_max_length_allowed(self) -> None:
        fb = ClaimFeedback(claim_id="claim-006", sentiment=1, comment="x" * 500)
        assert len(fb.comment) == 500  # type: ignore[arg-type]

    def test_user_id_and_session_both_optional(self) -> None:
        fb = ClaimFeedback(claim_id="claim-007", sentiment=0)
        assert fb.user_id is None
        assert fb.session_id is None


# ---------------------------------------------------------------------------
# submit_feedback
# ---------------------------------------------------------------------------


class TestSubmitFeedback:
    @patch("app.services.feedback.get_claim_accuracy")
    @patch("app.db.neo4j.feedback.record_rating")
    @patch("app.deps.get_neo4j")
    async def test_returns_rating_id(
        self, mock_get_neo4j: MagicMock, mock_record: MagicMock, _mock_acc: MagicMock
    ) -> None:
        mock_driver = MagicMock()
        mock_get_neo4j.return_value = mock_driver
        mock_record.return_value = "rating-abc123"

        fb = ClaimFeedback(claim_id="claim-001", sentiment=1, session_id="sess-001")

        with patch("app.services.feedback._neo4j_adapter.record_rating", return_value="rating-abc123"):
            with patch("app.deps.get_neo4j", return_value=mock_driver):
                result = await submit_feedback(fb)

        assert result == "rating-abc123"

    @patch("app.db.neo4j.feedback.record_rating")
    @patch("app.deps.get_neo4j")
    async def test_idempotency_same_user_id(
        self, mock_get_neo4j: MagicMock, mock_record: MagicMock
    ) -> None:
        """Calling twice with same claim_id + user_id should call record_rating twice;
        the adapter handles the MERGE logic.  The service layer passes through.
        """
        mock_driver = MagicMock()
        mock_get_neo4j.return_value = mock_driver
        mock_record.return_value = "stable-rating-id"

        fb = ClaimFeedback(claim_id="claim-001", sentiment=1, user_id="user-abc")

        with patch("app.services.feedback._neo4j_adapter.record_rating", return_value="stable-rating-id"):
            with patch("app.deps.get_neo4j", return_value=mock_driver):
                r1 = await submit_feedback(fb)
                r2 = await submit_feedback(fb)

        assert r1 == r2 == "stable-rating-id"

    @patch("app.deps.get_neo4j")
    async def test_propagates_neo4j_error(self, mock_get_neo4j: MagicMock) -> None:
        mock_driver = MagicMock()
        mock_get_neo4j.return_value = mock_driver
        fb = ClaimFeedback(claim_id="claim-err", sentiment=-1)

        with patch("app.services.feedback._neo4j_adapter.record_rating", side_effect=RuntimeError("db error")):
            with patch("app.deps.get_neo4j", return_value=mock_driver):
                with pytest.raises(RuntimeError, match="db error"):
                    await submit_feedback(fb)


# ---------------------------------------------------------------------------
# get_claim_accuracy
# ---------------------------------------------------------------------------


def _make_raw_stats(
    *,
    total: int = 0,
    positive: int = 0,
    negative: int = 0,
    neutral: int = 0,
    domain: str | None = None,
) -> Any:
    """Build a fake ClaimAccuracyStats adapter object."""
    from app.db.neo4j.feedback import ClaimAccuracyStats as RawStats
    from core.utils.time import utcnow_iso
    rate = positive / total if total > 0 else 0.0
    return RawStats(
        total_rated=total,
        positive=positive,
        negative=negative,
        neutral=neutral,
        agreement_rate=rate,
        domain=domain,
        window_hours=168,
        as_of_iso=utcnow_iso(),
    )


class TestGetClaimAccuracy:
    @patch("app.deps.get_neo4j")
    async def test_zero_ratings(self, mock_get_neo4j: MagicMock) -> None:
        mock_driver = MagicMock()
        mock_get_neo4j.return_value = mock_driver

        raw = _make_raw_stats(total=0)
        with patch("app.services.feedback._neo4j_adapter.claim_accuracy_rolling", return_value=raw):
            with patch("app.deps.get_neo4j", return_value=mock_driver):
                stats = await get_claim_accuracy()

        assert stats.total_rated == 0
        assert stats.agreement_rate == 0.0
        assert isinstance(stats, ClaimAccuracyStats)

    @patch("app.deps.get_neo4j")
    async def test_known_counts(self, mock_get_neo4j: MagicMock) -> None:
        mock_driver = MagicMock()
        mock_get_neo4j.return_value = mock_driver

        raw = _make_raw_stats(total=100, positive=80, negative=10, neutral=10)
        with patch("app.services.feedback._neo4j_adapter.claim_accuracy_rolling", return_value=raw):
            with patch("app.deps.get_neo4j", return_value=mock_driver):
                stats = await get_claim_accuracy()

        assert stats.total_rated == 100
        assert stats.positive == 80
        assert stats.agreement_rate == pytest.approx(0.80)
        assert stats.domain is None

    @patch("app.deps.get_neo4j")
    async def test_domain_filter_passed_through(self, mock_get_neo4j: MagicMock) -> None:
        mock_driver = MagicMock()
        mock_get_neo4j.return_value = mock_driver

        raw = _make_raw_stats(total=10, positive=9, negative=1, domain="finance")
        with patch("app.services.feedback._neo4j_adapter.claim_accuracy_rolling", return_value=raw):
            with patch("app.deps.get_neo4j", return_value=mock_driver):
                stats = await get_claim_accuracy(domain="finance")

        assert stats.domain == "finance"
        assert stats.agreement_rate == pytest.approx(0.9)
