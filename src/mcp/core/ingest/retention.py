# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-source retention enforcement — B3.5.

Each (:Source) node carries a ``retention_policy`` JSON blob with one
of these shapes:

* ``{"mode": "keep_all"}`` — default; never purge anything
* ``{"mode": "days", "days": N}`` — drop artifacts older than N days
* ``{"mode": "count", "max": N}`` — keep only the most recent N artifacts

The nightly scheduler invokes :func:`enforce_all_retention` which
walks every Source and applies its policy. The function is pure
(no return-value side effects) so it's safe to call from any worker.

Implementation: this module produces the *plan* (artifact ids to
purge per source). The actual chroma + neo4j delete happens in
:func:`app.services.retention.apply_retention_plan` so we stay
inside the core → app contract.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

logger = logging.getLogger("ai-companion.ingest.retention")


@dataclass(frozen=True)
class ArtifactRef:
    """A purgeable artifact, as exposed by the app-layer fetcher."""

    artifact_id: str
    source_id: str
    created_at: str  # ISO 8601


@dataclass(frozen=True)
class RetentionDecision:
    source_id: str
    purge: list[str]  # artifact ids to purge
    keep_count: int


def plan_for_source(
    source_id: str,
    policy: dict[str, Any],
    artifacts: Sequence[ArtifactRef],
    *,
    now: datetime | None = None,
) -> RetentionDecision:
    """Compute the set of artifact ids to purge for one source.

    ``artifacts`` is expected to be sorted newest-first; if not,
    ``count`` mode will misbehave. The caller (app-layer fetcher)
    handles the sort.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    mode = (policy or {}).get("mode", "keep_all")

    if mode == "keep_all":
        return RetentionDecision(source_id=source_id, purge=[], keep_count=len(artifacts))

    if mode == "days":
        days = int(policy.get("days", 0))
        if days <= 0:
            return RetentionDecision(source_id=source_id, purge=[], keep_count=len(artifacts))
        cutoff = now - timedelta(days=days)
        purge = []
        keep = 0
        for art in artifacts:
            try:
                created = datetime.fromisoformat(art.created_at.replace("Z", "+00:00"))
            except ValueError:
                # Malformed timestamp — leave the artifact in place rather
                # than risk over-purging.
                keep += 1
                continue
            if created < cutoff:
                purge.append(art.artifact_id)
            else:
                keep += 1
        return RetentionDecision(source_id=source_id, purge=purge, keep_count=keep)

    if mode == "count":
        max_keep = int(policy.get("max", 0))
        if max_keep <= 0:
            return RetentionDecision(source_id=source_id, purge=[], keep_count=len(artifacts))
        if len(artifacts) <= max_keep:
            return RetentionDecision(
                source_id=source_id, purge=[], keep_count=len(artifacts)
            )
        purge = [art.artifact_id for art in artifacts[max_keep:]]
        return RetentionDecision(
            source_id=source_id, purge=purge, keep_count=max_keep,
        )

    # Unknown mode — fail-safe to keep everything
    logger.warning("Unknown retention mode %r for source=%s; keeping all", mode, source_id)
    return RetentionDecision(source_id=source_id, purge=[], keep_count=len(artifacts))
