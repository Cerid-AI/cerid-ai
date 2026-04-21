# src/mcp/core/reliability/read_after_write.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read-after-write verification for state-critical Neo4j writes.

Writes that "succeed" from the driver's perspective can silently fail to
land — MERGE patterns with unmatched MATCH clauses become no-ops, for
instance. This helper runs a verification query immediately after each
write and logs + Sentry-captures when the expected state is not present.

Use at every MERGE/CREATE site where a missing result would be a bug,
not a routine degraded condition. Safe to call in hot paths — the
verification query is expected to be constant-time (indexed MATCH) and
runs after the state-changing write has already completed.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

import sentry_sdk

logger = logging.getLogger("ai-companion.read_after_write")


class _Neo4jSession(Protocol):
    """Minimal Neo4j session protocol — just .run(). Avoids importing neo4j for typing."""

    def run(self, cypher: str, **params: Any) -> Any: ...


def assert_created(
    session: _Neo4jSession,
    *,
    check_cypher: str,
    params: dict[str, Any],
    event_name: str,
    boolean_key: str = "exists",
) -> bool:
    """Verify that a Cypher write landed by querying the graph afterward.

    Returns True when the write is confirmed, False when it is not.
    Callers may choose to retry, raise, or degrade based on the return.

    The check_cypher must return a single scalar boolean (or truthy value)
    under the ``boolean_key`` (default "exists"). Recommended shape::

        MATCH (r:VerificationReport {conversation_id: $cid})-[:VERIFIED]->(a:Artifact)
        RETURN count(a) > 0 AS exists

    On check failure (False result OR driver exception during the check)
    this emits:
      - logger.warning("<event_name>.write_did_not_land") with structured
        ``extra`` carrying check_cypher + params
      - sentry_sdk.capture_message(..., level="warning") with the same
        extras — visible in Sentry under the event_name tag

    Parameters
    ----------
    session:
        An active Neo4j session. Must expose ``.run(cypher, **params)``.
    check_cypher:
        Cypher that returns exactly one row with one boolean-typed column.
    params:
        Parameters bound into the check_cypher.
    event_name:
        Short identifier for observability grouping (e.g.
        ``"verification_report.verified_edge"``).
    boolean_key:
        Column name on the returned record that holds the existence flag.
    """
    try:
        result = session.run(check_cypher, **params)
        record = result.single()
    except Exception:
        logger.exception(
            "%s.check_raised",
            event_name,
            extra={"check_cypher": check_cypher, "params": params},
        )
        sentry_sdk.capture_exception()
        return False

    if record is None or not record.get(boolean_key, False):
        logger.warning(
            "%s.write_did_not_land",
            event_name,
            extra={"check_cypher": check_cypher, "params": params, "record_keys": list(record.keys()) if record else None},
        )
        sentry_sdk.capture_message(
            f"{event_name}.write_did_not_land",
            level="warning",
        )
        return False

    return True
