# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-claim user-feedback service (Phase R.1).

Provides the public surface for recording and querying user sentiment
ratings on individual verified claims.

Layering
--------
* Lives in ``app/services/`` — may import from ``core.*`` and from
  ``app.db.neo4j.feedback``.
* MUST NOT be imported by anything in ``core/`` (enforced by
  import-linter contract).
* The Neo4j driver is obtained lazily at call time via
  :func:`app.deps.get_neo4j` so the module is importable without a live
  database (unit tests mock the adapter instead).

Design notes (from the v0.92 plan)
-----------------------------------
- Feedback is **per-claim**, never bundled.
- Sentiment uses integer codes: ``1`` = positive/correct,
  ``0`` = neutral, ``-1`` = negative/incorrect.
- The rolling agreement metric is **operator-facing only** via
  ``/observability/claim-accuracy/{domain}``.  It is not exposed to
  users as a visible badge anywhere.

Usage::

    from app.services.feedback import ClaimFeedback, submit_feedback

    feedback = ClaimFeedback(
        claim_id="claim-abc123",
        sentiment=1,
        session_id="sess-xyz",
    )
    rating_id = await submit_feedback(feedback)
"""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.db.neo4j import feedback as _neo4j_adapter
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.feedback")


class ClaimFeedback(BaseModel):
    """Per-claim user rating.

    Attributes
    ----------
    claim_id:
        Stable identifier for the claim being rated.  Required.
    sentiment:
        ``1`` = positive/correct, ``0`` = neutral, ``-1`` =
        negative/incorrect.
    user_id:
        Optional authenticated user identifier.  When present, ratings
        are de-duplicated per (claim_id, user_id) — re-rating the same
        claim updates the existing edge instead of appending a new one.
    session_id:
        Optional anonymous session identifier.  Used when ``user_id`` is
        absent.  De-duplication key is (claim_id, session_id).
    comment:
        Optional short free-text note (max 500 characters).
    """

    claim_id: str
    sentiment: Literal[-1, 0, 1]
    user_id: str | None = None
    session_id: str | None = None
    comment: str | None = Field(default=None, max_length=500)


class ClaimAccuracyStats(BaseModel):
    """Rolling user-agreement stats for a claim domain.

    Returned by ``/observability/claim-accuracy/{domain}`` and read by
    the TrustScore user_agreement component.

    Attributes
    ----------
    total_rated:
        Number of claim ratings in the window.
    positive:
        Ratings with sentiment=1.
    negative:
        Ratings with sentiment=-1.
    neutral:
        Ratings with sentiment=0.
    agreement_rate:
        Fraction of positive ratings among all rated claims (0.0 if
        total_rated is 0).
    domain:
        Domain filter applied, or ``None`` for global stats.
    window_hours:
        Look-back window in hours.
    as_of_iso:
        ISO-8601 timestamp when the stats were computed.
    """

    total_rated: int
    positive: int
    negative: int
    neutral: int
    agreement_rate: float
    domain: str | None = None
    window_hours: int
    as_of_iso: str


def _stats_from_adapter(raw: "_neo4j_adapter.ClaimAccuracyStats") -> ClaimAccuracyStats:
    """Convert the adapter's plain-object stats to the Pydantic response model."""
    return ClaimAccuracyStats(
        total_rated=raw.total_rated,
        positive=raw.positive,
        negative=raw.negative,
        neutral=raw.neutral,
        agreement_rate=raw.agreement_rate,
        domain=raw.domain,
        window_hours=raw.window_hours,
        as_of_iso=raw.as_of_iso,
    )


async def submit_feedback(feedback: ClaimFeedback) -> str:
    """Persist a user rating for a single claim.

    Idempotent per the adapter's MERGE logic: if the same principal
    (user_id or session_id) has already rated this claim, the existing
    edge is updated.

    Parameters
    ----------
    feedback:
        Validated :class:`ClaimFeedback` record.

    Returns
    -------
    str
        The ``rating_id`` that was persisted.

    Raises
    ------
    Exception
        Propagates Neo4j driver errors after logging them.
    """
    from app.deps import get_neo4j  # lazy — keeps module importable without db

    driver = get_neo4j()
    try:
        return _neo4j_adapter.record_rating(
            driver,
            claim_id=feedback.claim_id,
            sentiment=feedback.sentiment,
            user_id=feedback.user_id,
            session_id=feedback.session_id,
            comment=feedback.comment,
        )
    except Exception as exc:
        log_swallowed_error(
            "feedback.submit_feedback",
            exc,
            context={"claim_id": feedback.claim_id},
        )
        raise


async def get_claim_accuracy(
    *,
    domain: str | None = None,
    window_hours: int = 168,
) -> ClaimAccuracyStats:
    """Compute rolling user-agreement stats.

    Parameters
    ----------
    domain:
        Optional domain filter.  ``None`` or ``"all"`` returns global stats.
    window_hours:
        Look-back window in hours (default 7 days = 168 h).

    Returns
    -------
    ClaimAccuracyStats
        Aggregated stats.  ``total_rated=0`` and ``agreement_rate=0.0``
        when no ratings exist in the window.

    Raises
    ------
    Exception
        Propagates Neo4j driver errors after logging them.
    """
    from app.deps import get_neo4j  # lazy

    driver = get_neo4j()
    try:
        raw = _neo4j_adapter.claim_accuracy_rolling(
            driver,
            domain=domain,
            window_hours=window_hours,
        )
        return _stats_from_adapter(raw)
    except Exception as exc:
        log_swallowed_error(
            "feedback.get_claim_accuracy",
            exc,
            context={"domain": domain, "window_hours": window_hours},
        )
        raise
