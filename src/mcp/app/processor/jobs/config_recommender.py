# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""
ConfigRecommenderJob — periodically evaluate the recommendation registry.

Cerid's adaptive recommendation engine (Cycle 3.2) surfaces feature
flags whose corpus-size threshold has been crossed but which the
operator hasn't yet enabled. The registry lives in
:mod:`core.config.recommendations`; this job is the (single) consumer
that wires it up to live state (Neo4j corpus count + current env-var
flag state) and writes the result to Redis for the ``/health``
endpoint to surface.

Job contract:

* Priority: LOW — never preempts user-triggered work
* Cost: zero USD — Cypher + Redis only, no LLM
* Idempotent: re-running with no state change leaves Redis untouched
* Cron: ``SCHEDULE_CONFIG_RECOMMENDER`` (default ``0 */6 * * *``)

Redis schema:

* ``cerid:recommendations`` — hash keyed by ``RecommendationSpec.id``.
  Each value is a JSON blob ``{id, label, reason, triggered_at,
  enable_payload, corpus_size}``.
* ``cerid:recommendations:dismissed:{tenant}`` — set of ids the
  tenant explicitly dismissed. The ``/health`` reader filters this
  set out before returning to the client.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

from core.config.recommendations import CorpusStats, evaluate
from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.config_recommender")

_REDIS_HASH_KEY = "cerid:recommendations"


# ---------------------------------------------------------------------------
# Helpers — kept private + free-functioned so tests can call them directly.
# ---------------------------------------------------------------------------

def _read_flag_state() -> frozenset[str]:
    """Read the current on/off state of every retrieval flag.

    Returns a ``frozenset`` of env-var names that are currently "on"
    in the canonical sense — see each branch's docstring for what
    counts as on. The registry's ``condition_fn`` consumes this.
    """
    on: set[str] = set()
    for var in ("RETRIEVAL_SPARSE_ENABLED", "RETRIEVAL_HYPE_ENABLED", "PARENT_CHILD_ENABLED"):
        val = os.getenv(var, "false").strip().lower()
        if val in {"1", "true", "yes", "on"}:
            on.add(var)
    # HYBRID_FUSION_MODE is non-boolean — "weighted_sum" counts as off,
    # anything else as on so the rrf_fusion recommendation can be
    # short-circuited once the operator flips to rrf / tri_rrf.
    mode = os.getenv("HYBRID_FUSION_MODE", "weighted_sum").strip().lower()
    if mode and mode != "weighted_sum":
        on.add("HYBRID_FUSION_MODE_ACTIVE")
    return frozenset(on)


def _corpus_size(neo4j_driver) -> int:
    """Count distinct non-eval-corpus Artifacts.

    The eval-corpus exclusion mirrors the recall-saturation note in
    ``docs/EVAL_BASELINES.md`` — synthetic eval docs shouldn't trip
    real-corpus recommendations.

    Returns ``0`` on any Neo4j failure so the job remains side-effect
    safe (the registry then evaluates to "no fires").
    """
    if neo4j_driver is None:
        return 0
    try:
        with neo4j_driver.session() as sess:
            res = sess.run(
                "MATCH (a:Artifact) "
                "WHERE coalesce(a.sub_category, '') <> 'eval-corpus' "
                # Artifact's identifier property is `a.id` (a.artifact_id never
                # existed → this counted 0 over a 10k-artifact corpus, so feature
                # recommendations never fired). count(a) is exact: nodes are unique.
                "RETURN count(a) AS n",
            )
            row = res.single()
            return int(row["n"]) if row and row["n"] is not None else 0
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("app.processor.jobs.config_recommender.corpus_count", exc)
        return 0


def _write_recommendations(redis_client, entries: list[dict]) -> int:
    """Replace the Redis hash with ``entries`` (one per recommendation).

    Idempotent — repeated runs with the same input result in the same
    final state. Returns the number of entries written.
    """
    if redis_client is None:
        return 0
    try:
        # Atomically wipe + write so a partial run doesn't leave stale
        # entries from a previous tick that no longer apply.
        with redis_client.pipeline() as pipe:
            pipe.delete(_REDIS_HASH_KEY)
            if entries:
                for entry in entries:
                    pipe.hset(_REDIS_HASH_KEY, entry["id"], json.dumps(entry))
            pipe.execute()
        return len(entries)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.processor.jobs.config_recommender.write", exc,
        )
        return 0


def run_recommender_sync(neo4j_driver, redis_client) -> dict:
    """Single-pass evaluation. Returns the metadata used in :class:`JobResult`.

    Public free-function so the scheduler fallback can run the body
    without instantiating the full :class:`BaseJob`. Mirrors the
    ``ingest_recovery`` direct-call fallback.
    """
    corpus = _corpus_size(neo4j_driver)
    flags_on = _read_flag_state()
    stats = CorpusStats(artifact_count=corpus, flags_enabled=flags_on)
    hits = evaluate(stats)

    now = datetime.now(timezone.utc).isoformat()
    entries: list[dict] = []
    for spec, reason in hits:
        entries.append({
            "id": spec.id,
            "label": spec.label,
            "reason": reason,
            "triggered_at": now,
            "corpus_size": corpus,
            "enable_payload": spec.enable_payload,
        })

    written = _write_recommendations(redis_client, entries)
    return {
        "corpus_size": corpus,
        "active_flags": sorted(flags_on),
        "recommendations_written": written,
        "recommendation_ids": [e["id"] for e in entries],
    }


# ---------------------------------------------------------------------------
# BaseJob subclass
# ---------------------------------------------------------------------------

class ConfigRecommenderJob(BaseJob):
    """Run :func:`run_recommender_sync` from the processor queue.

    Pulls Neo4j + Redis from the FastAPI app state via the canonical
    deps helpers so the job stays parallel-safe (driver instances are
    thread-safe, Redis client is a connection pool).
    """

    job_type = "config_recommender"

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        """Zero USD — Cypher + Redis only, no LLM."""
        return CostEstimate(
            estimated_tokens_in=0,
            estimated_tokens_out=0,
            model="none",
            estimated_usd=Decimal("0.00"),
            confidence="high",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        await progress_cb(0.0)
        # Lazy import the deps helpers to avoid a load-time cycle
        # between core/app.
        from app.deps import get_neo4j, get_redis

        try:
            driver = get_neo4j()
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "app.processor.jobs.config_recommender.get_neo4j", exc,
            )
            driver = None

        try:
            redis_client = get_redis()
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "app.processor.jobs.config_recommender.get_redis", exc,
            )
            redis_client = None

        await progress_cb(0.5)
        meta = run_recommender_sync(driver, redis_client)
        await progress_cb(1.0)

        logger.info("config_recommender_completed", extra=meta)
        # The job_id is supplied by the processor wrapper before run()
        # is invoked, but JobResult requires it as a constructor arg.
        # Match the IngestRecoveryJob shape — empty job_id is allowed
        # and the wrapper fills it in via dataclasses.replace.
        return JobResult(
            job_id="",
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata=meta,
        )
