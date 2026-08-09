# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Neo4j persistence for the per-claim user feedback loop (Phase R.1).

Schema (matches the R.1 plan):

    (:User {user_id})
    (:Session {session_id})
    (:Claim {claim_id, ...})

    (:User)-[:RATED {
        rating_id, sentiment, claim_id, ts, comment
    }]->(:Claim)

    (:Session)-[:RATED {
        rating_id, sentiment, claim_id, ts, comment
    }]->(:Claim)  -- when no user_id is provided

Idempotency: if the same (claim_id, user_id) or (claim_id, session_id)
is rated again, the existing RATED edge is updated in place (sentiment +
ts overwritten, rating_id preserved from first write).

Callers: :mod:`app.services.feedback` only.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from core.utils.swallowed import log_swallowed_error

if TYPE_CHECKING:
    pass

logger = logging.getLogger("ai-companion.graph.feedback")


class ClaimAccuracyStats:
    """Rolling window user-agreement stats for a domain (or global).

    Built as a plain dataclass-style class so it can be constructed by
    the adapter without importing Pydantic here.  The service layer
    converts to a Pydantic model before exposing it to the API.
    """

    __slots__ = (
        "total_rated",
        "positive",
        "negative",
        "neutral",
        "agreement_rate",
        "domain",
        "window_hours",
        "as_of_iso",
    )

    def __init__(
        self,
        *,
        total_rated: int,
        positive: int,
        negative: int,
        neutral: int,
        agreement_rate: float,
        domain: str | None,
        window_hours: int,
        as_of_iso: str,
    ) -> None:
        self.total_rated = total_rated
        self.positive = positive
        self.negative = negative
        self.neutral = neutral
        self.agreement_rate = agreement_rate
        self.domain = domain
        self.window_hours = window_hours
        self.as_of_iso = as_of_iso


def record_rating(
    driver: Any,
    *,
    claim_id: str,
    sentiment: int,
    user_id: str | None = None,
    session_id: str | None = None,
    comment: str | None = None,
) -> str:
    """Persist (or update) a user rating of a claim.

    Idempotency: if a RATED edge already exists from the same principal
    (user_id or session_id) to the same claim, the existing edge's
    sentiment and ts are updated.  The rating_id is preserved from the
    first write so the caller can surface a stable reference.

    Parameters
    ----------
    driver:
        Neo4j driver instance.
    claim_id:
        Stable identifier for the claim being rated.
    sentiment:
        ``1`` (positive / correct), ``0`` (neutral), or ``-1``
        (negative / incorrect).
    user_id:
        Optional authenticated user identifier.  When present, the RATED
        edge is attached to a ``(:User)`` node keyed by ``user_id``.
    session_id:
        Optional anonymous session identifier.  Used when ``user_id`` is
        absent.  Must provide at least one of ``user_id`` or
        ``session_id`` for idempotency to work; if both are absent a new
        edge is created each call.
    comment:
        Optional free-text rider on the rating.

    Returns
    -------
    str
        The ``rating_id`` that was persisted (either new or the existing
        idempotent id).
    """
    if sentiment not in (-1, 0, 1):
        raise ValueError(f"sentiment must be -1, 0, or 1; got {sentiment!r}")

    ts = datetime.now(timezone.utc).isoformat()
    new_rating_id = uuid.uuid4().hex

    # Determine principal type and execute the correct Cypher branch.
    with driver.session() as session:
        try:
            if user_id:
                result = session.run(
                    """
                    MERGE (u:User {user_id: $user_id})
                    MERGE (c:Claim {claim_id: $claim_id})
                    MERGE (u)-[r:RATED {claim_id: $claim_id}]->(c)
                      ON CREATE SET
                        r.rating_id  = $new_rating_id,
                        r.sentiment  = $sentiment,
                        r.ts         = $ts,
                        r.comment    = $comment
                      ON MATCH SET
                        r.sentiment  = $sentiment,
                        r.ts         = $ts,
                        r.comment    = $comment
                    RETURN r.rating_id AS rating_id
                    """,
                    user_id=user_id,
                    claim_id=claim_id,
                    new_rating_id=new_rating_id,
                    sentiment=sentiment,
                    ts=ts,
                    comment=comment or "",
                )
            elif session_id:
                result = session.run(
                    """
                    MERGE (s:Session {session_id: $session_id})
                    MERGE (c:Claim {claim_id: $claim_id})
                    MERGE (s)-[r:RATED {claim_id: $claim_id}]->(c)
                      ON CREATE SET
                        r.rating_id  = $new_rating_id,
                        r.sentiment  = $sentiment,
                        r.ts         = $ts,
                        r.comment    = $comment
                      ON MATCH SET
                        r.sentiment  = $sentiment,
                        r.ts         = $ts,
                        r.comment    = $comment
                    RETURN r.rating_id AS rating_id
                    """,
                    session_id=session_id,
                    claim_id=claim_id,
                    new_rating_id=new_rating_id,
                    sentiment=sentiment,
                    ts=ts,
                    comment=comment or "",
                )
            else:
                # No principal — no idempotency; always create a new edge.
                result = session.run(
                    """
                    MERGE (c:Claim {claim_id: $claim_id})
                    CREATE (c)<-[r:RATED]-(p:AnonymousRating {rating_id: $new_rating_id})
                    SET r.rating_id = $new_rating_id,
                        r.sentiment = $sentiment,
                        r.ts        = $ts,
                        r.comment   = $comment,
                        r.claim_id  = $claim_id
                    RETURN r.rating_id AS rating_id
                    """,
                    claim_id=claim_id,
                    new_rating_id=new_rating_id,
                    sentiment=sentiment,
                    ts=ts,
                    comment=comment or "",
                )

            record = result.single()
            return record["rating_id"] if record else new_rating_id
        except Exception as exc:
            log_swallowed_error(
                "feedback.record_rating",
                exc,
                context={"claim_id": claim_id, "user_id": user_id},
            )
            raise


def list_ratings_for_claim(
    driver: Any,
    claim_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return raw rating edge properties for a single claim.

    Parameters
    ----------
    driver:
        Neo4j driver instance.
    claim_id:
        Stable claim identifier.
    limit:
        Maximum rows returned (hard cap at 1000).
    """
    effective_limit = min(limit, 1000)
    rows: list[dict[str, Any]] = []
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH ()-[r:RATED]->(c:Claim {claim_id: $claim_id})
                RETURN r.rating_id AS rating_id,
                       r.sentiment AS sentiment,
                       r.ts        AS ts,
                       r.comment   AS comment
                ORDER BY r.ts DESC
                LIMIT $limit
                """,
                claim_id=claim_id,
                limit=effective_limit,
            )
            for record in result:
                rows.append(dict(record))
    except Exception as exc:
        log_swallowed_error(
            "feedback.list_ratings_for_claim",
            exc,
            context={"claim_id": claim_id},
        )
        raise
    return rows


def claim_accuracy_rolling(
    driver: Any,
    *,
    domain: str | None = None,
    window_hours: int = 168,  # 24 * 7
) -> ClaimAccuracyStats:
    """Compute rolling user-agreement stats.

    Parameters
    ----------
    driver:
        Neo4j driver.
    domain:
        Optional domain filter.  ``None`` or ``"all"`` returns global stats.
        Domain matching is done against the ``Claim.domain`` property if set.
    window_hours:
        Look-back window in hours (default 7 days).

    Returns
    -------
    ClaimAccuracyStats
        Aggregated counts and agreement rate.
        ``agreement_rate`` is the fraction of positive ratings (sentiment=1)
        among all rated claims.  Returns 0.0 when total_rated is 0.
    """
    since_iso = (
        datetime.now(timezone.utc) - timedelta(hours=window_hours)
    ).isoformat()
    as_of_iso = datetime.now(timezone.utc).isoformat()

    params: dict[str, Any] = {"since": since_iso}
    domain_filter = ""
    if domain and domain != "all":
        domain_filter = "AND c.domain = $domain"
        params["domain"] = domain

    cypher = f"""
        MATCH ()-[r:RATED]->(c:Claim)
        WHERE r.ts >= $since {domain_filter}
        RETURN
            count(r)                                        AS total_rated,
            sum(CASE WHEN r.sentiment =  1 THEN 1 ELSE 0 END) AS positive,
            sum(CASE WHEN r.sentiment = -1 THEN 1 ELSE 0 END) AS negative,
            sum(CASE WHEN r.sentiment =  0 THEN 1 ELSE 0 END) AS neutral
    """

    try:
        with driver.session() as session:
            result = session.run(cypher, **params)
            row = result.single()
            if row is None:
                total, pos, neg, neu = 0, 0, 0, 0
            else:
                total = int(row["total_rated"] or 0)
                pos   = int(row["positive"]    or 0)
                neg   = int(row["negative"]    or 0)
                neu   = int(row["neutral"]     or 0)
    except Exception as exc:
        log_swallowed_error(
            "feedback.claim_accuracy_rolling",
            exc,
            context={"domain": domain, "window_hours": window_hours},
        )
        raise

    agreement_rate = pos / total if total > 0 else 0.0

    return ClaimAccuracyStats(
        total_rated=total,
        positive=pos,
        negative=neg,
        neutral=neu,
        agreement_rate=agreement_rate,
        domain=domain if domain and domain != "all" else None,
        window_hours=window_hours,
        as_of_iso=as_of_iso,
    )
