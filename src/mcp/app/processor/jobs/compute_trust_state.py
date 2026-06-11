# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Derive and write Entity.trust_state from VerificationReport evidence.

Derivation rule
---------------
For each Entity ``e``, collect every VerificationReport that references an
Artifact mentioning ``e`` (via the EXTRACTED_FROM → Artifact → MENTIONS
path).  Aggregate across those reports:

    verified_total   = sum of r.verified
    unverified_total = sum of r.unverified
    total            = verified_total + unverified_total

Then:
    if total == 0:          no evidence → skip (don't write; entity keeps
                            whatever state it had, or null which is coalesced
                            to 'unknown' by every reader)
    if verified_total / total >= 0.70:  'verified'
    if verified_total / total >= 0.20:  'partial'
    else:                               'unverified'

Rationale: 70/20 thresholds match the two visible bands in the Trust lens
(verified = green, partial = amber, unverified = red per Constellation.tsx:57).
The 0.20 lower bound for 'partial' means an entity must have at least one
significant verified report to escape 'unverified', preventing noise from a
single low-quality report flipping an entity to amber.

Runs nightly alongside compute_umap_3d (same scheduler slot + one minute
offset so it consumes fresh coordinates).  Zero LLM cost — pure graph
aggregation.
"""
from __future__ import annotations

import asyncio
import logging
import os
from decimal import Decimal
from typing import Any

from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.compute_trust_state")

# Threshold for 'verified': share of verified claims >= this value.
_VERIFIED_THRESHOLD = float(os.getenv("TRUST_STATE_VERIFIED_THRESHOLD", "0.70"))
# Threshold for 'partial': share of verified claims >= this value (< _VERIFIED_THRESHOLD).
_PARTIAL_THRESHOLD = float(os.getenv("TRUST_STATE_PARTIAL_THRESHOLD", "0.20"))
# Maximum entities to process per run — guard against runaway scans.
_MAX_ENTITIES = int(os.getenv("TRUST_STATE_MAX_ENTITIES", "50000"))
# Batch size for the SET writes.
_WRITE_BATCH = int(os.getenv("TRUST_STATE_WRITE_BATCH", "500"))


class ComputeTrustStateJob(BaseJob):
    """Nightly job: derive Entity.trust_state from VerificationReport evidence.

    Idempotent: each run overwrites stored trust_state on entities that have
    evidence.  Entities with no covering VerificationReport are left unchanged.
    """

    job_type = "compute_trust_state"

    def __init__(self, tenant_id: str = "default") -> None:
        self._tenant_id = tenant_id

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        return CostEstimate(
            estimated_tokens_in=0,
            estimated_tokens_out=0,
            model="cpu/cypher",
            estimated_usd=Decimal("0.00"),
            confidence="high",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        await progress_cb(0.0)
        from app.deps import get_neo4j  # noqa: PLC0415

        driver = get_neo4j()
        if driver is None:
            logger.warning("compute_trust_state: neo4j unavailable, skipping")
            return JobResult(
                job_id=f"compute_trust_state:{self._tenant_id}",
                actual_tokens_in=0,
                actual_tokens_out=0,
                metadata={"status": "skipped", "reason": "neo4j unavailable"},
            )

        await progress_cb(0.1)
        scores = await asyncio.to_thread(self._fetch_trust_scores, driver)
        if not scores:
            await progress_cb(1.0)
            return JobResult(
                job_id=f"compute_trust_state:{self._tenant_id}",
                actual_tokens_in=0,
                actual_tokens_out=0,
                metadata={"status": "no_op", "count": 0},
            )

        await progress_cb(0.6)
        written = await asyncio.to_thread(self._write_trust_states, driver, scores)
        await progress_cb(1.0)

        distribution = _count_distribution(scores)
        logger.info(
            "compute_trust_state.wrote count=%d distribution=%s",
            written,
            distribution,
        )
        return JobResult(
            job_id=f"compute_trust_state:{self._tenant_id}",
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata={
                "written": written,
                "distribution": distribution,
            },
        )

    def _fetch_trust_scores(self, driver: Any) -> list[dict[str, Any]]:
        """Aggregate VerificationReport evidence per Entity.

        Returns a list of {id, verified_total, total} dicts for every entity
        that has at least one covering report.  Entities with no report are
        excluded — the caller must not write to them.
        """
        cypher = f"""
            MATCH (r:VerificationReport)-[:EXTRACTED_FROM]->(a:Artifact)-[:MENTIONS]->(e:Entity)
            WHERE e.canonical_id IS NOT NULL
              AND coalesce(r.total, r.verified + r.unverified, 0) > 0
            WITH
                e.canonical_id AS entity_id,
                sum(coalesce(r.verified, 0))   AS verified_total,
                sum(coalesce(r.total,
                    r.verified + coalesce(r.unverified, 0), 0)) AS evidence_total
            WHERE evidence_total > 0
            RETURN entity_id, verified_total, evidence_total
            LIMIT {_MAX_ENTITIES}
        """
        try:
            with driver.session() as session:
                rows = list(session.run(cypher).data())
        except (OSError, RuntimeError, ValueError) as exc:
            log_swallowed_error("compute_trust_state._fetch_trust_scores", exc)
            return []

        results = []
        for row in rows:
            entity_id = row.get("entity_id")
            if not entity_id:
                continue
            verified = float(row.get("verified_total") or 0)
            total = float(row.get("evidence_total") or 0)
            if total <= 0:
                continue
            share = verified / total
            if share >= _VERIFIED_THRESHOLD:
                state = "verified"
            elif share >= _PARTIAL_THRESHOLD:
                state = "partial"
            else:
                state = "unverified"
            results.append({"id": entity_id, "trust_state": state})

        return results

    def _write_trust_states(
        self,
        driver: Any,
        scores: list[dict[str, Any]],
    ) -> int:
        """Batch-write trust_state to Entity nodes. Returns count written."""
        if not scores:
            return 0

        cypher = """
            UNWIND $rows AS row
            MATCH (e:Entity {canonical_id: row.id})
            SET e.trust_state = row.trust_state
        """
        written = 0
        for i in range(0, len(scores), _WRITE_BATCH):
            batch = scores[i : i + _WRITE_BATCH]
            try:
                with driver.session() as session:
                    session.run(cypher, rows=batch)
                written += len(batch)
            except (OSError, RuntimeError, ValueError) as exc:
                log_swallowed_error(
                    "compute_trust_state._write_trust_states",
                    exc,
                    context={"batch_start": i, "batch_size": len(batch)},
                )
        return written


def _count_distribution(scores: list[dict[str, Any]]) -> dict[str, int]:
    dist: dict[str, int] = {"verified": 0, "partial": 0, "unverified": 0}
    for s in scores:
        state = s.get("trust_state", "unverified")
        dist[state] = dist.get(state, 0) + 1
    return dist
