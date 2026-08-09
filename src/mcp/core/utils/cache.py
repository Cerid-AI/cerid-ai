# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""
Redis audit logging and conversation metrics storage.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import config
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso
from core.utils.tracing import get_request_id

logger = logging.getLogger("ai-companion.cache")


def log_event(
    redis_client,
    event_type: str,
    artifact_id: str,
    domain: str,
    filename: str,
    extra: dict[str, Any] | None = None,
    conversation_id: str | None = None,
) -> None:
    """
    Append an event to the Redis audit log.

    Args:
        event_type: "ingest", "recategorize", "delete", "feedback"
        artifact_id: UUID of the artifact
        domain: Current domain after the event
        filename: Original filename
        extra: Additional context (e.g. old_domain for recategorize)
        conversation_id: Optional conversation ID for feedback loop events
    """
    entry: dict[str, Any] = {
        "event": event_type,
        "artifact_id": artifact_id,
        "domain": domain,
        "filename": filename,
        "timestamp": utcnow_iso(),
        **(extra or {}),
    }
    rid = get_request_id()
    if rid:
        entry["request_id"] = rid
    if conversation_id:
        entry["conversation_id"] = conversation_id
    payload = json.dumps(entry)
    try:
        pipe = redis_client.pipeline()
        pipe.lpush(config.REDIS_INGEST_LOG, payload)
        pipe.ltrim(config.REDIS_INGEST_LOG, 0, config.REDIS_LOG_MAX - 1)
        pipe.expire(config.REDIS_INGEST_LOG, 86400 * 30)  # 30-day TTL
        pipe.execute()
    except Exception as e:
        log_swallowed_error('core.utils.cache', e)
        logger.error(f"Failed to log event to Redis: {e}")


def get_log(redis_client, limit: int = 50) -> list[dict[str, Any]]:
    """Read recent audit log entries."""
    try:
        entries = redis_client.lrange(config.REDIS_INGEST_LOG, 0, limit - 1)
        return [json.loads(e) for e in entries]
    except Exception as e:
        log_swallowed_error('core.utils.cache', e)
        logger.error(f"Failed to read ingest log: {e}")
        return []


# ---------------------------------------------------------------------------
# Conversation metrics storage
# ---------------------------------------------------------------------------

REDIS_CONV_METRICS_PREFIX = "conv:"
REDIS_CONV_METRICS_TTL = 86400 * 30  # 30 days


def log_conversation_metrics(
    redis_client,
    conversation_id: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int = 0,
) -> None:
    """Store per-turn metrics for a conversation in Redis."""
    key = f"{REDIS_CONV_METRICS_PREFIX}{conversation_id}:metrics"
    entry = json.dumps({
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": latency_ms,
        "timestamp": utcnow_iso(),
    })
    try:
        redis_client.rpush(key, entry)
        redis_client.expire(key, REDIS_CONV_METRICS_TTL)
    except Exception as e:
        log_swallowed_error('core.utils.cache', e)
        logger.warning(f"Failed to log conversation metrics: {e}")


def log_conversation_sentiment(
    redis_client,
    conversation_id: str,
    message_id: str,
    sentiment: str,
) -> None:
    """Record a per-message thumbs sentiment in the chat feedback-loop store.

    E1 CR-043: message thumbs up/down feedback previously POSTed to
    ``/artifacts/{message_id}/feedback`` — an id-space mismatch (a chat-message
    uuid is not an artifact id) with the wrong schema, so it was structurally
    dark. Stored as a hash keyed by ``message_id`` so re-rating (thumbs toggling)
    overwrites rather than floods, alongside the same conversation the feedback
    loop already tracks.
    """
    key = f"{REDIS_CONV_METRICS_PREFIX}{conversation_id}:sentiment"
    try:
        redis_client.hset(key, message_id, sentiment)
        redis_client.expire(key, REDIS_CONV_METRICS_TTL)
    except Exception as e:
        log_swallowed_error('core.utils.cache', e)
        logger.warning(f"Failed to log conversation sentiment: {e}")


# ---------------------------------------------------------------------------
# Verification metrics storage
# ---------------------------------------------------------------------------

REDIS_VERIFICATION_METRICS_KEY = "verify:metrics"
REDIS_VERIFICATION_FEEDBACK_KEY = "verify:feedback"
REDIS_VERIFICATION_ERRORS_KEY = "verify:errors"
REDIS_VERIFICATION_METRICS_TTL = 86400 * 30  # 30 days
REDIS_VERIFICATION_ERRORS_MAX = 200  # Keep last 200 errors


def log_verification_metrics(
    redis_client,
    conversation_id: str,
    model: str | None = None,
    verified: int = 0,
    unverified: int = 0,
    uncertain: int = 0,
    total: int = 0,
    verification_models: list[str] | None = None,
) -> None:
    """Store verification metrics for analytics aggregation.

    ``verification_models`` is an optional list of the distinct LLM model IDs
    that were actually used to verify claims in this run (e.g., GPT-4o-mini,
    Gemini 2.5 Flash, Grok 4.1 Fast).  This enables per-verification-model
    accuracy tracking in the audit dashboard.
    """
    accuracy = round(verified / total, 4) if total > 0 else 0.0
    data: dict = {
        "conversation_id": conversation_id,
        "model": model or "unknown",
        "verified": verified,
        "unverified": unverified,
        "uncertain": uncertain,
        "total": total,
        "accuracy": accuracy,
        "timestamp": utcnow_iso(),
    }
    if verification_models:
        data["verification_models"] = verification_models
    entry = json.dumps(data)
    try:
        redis_client.rpush(REDIS_VERIFICATION_METRICS_KEY, entry)
        redis_client.expire(REDIS_VERIFICATION_METRICS_KEY, REDIS_VERIFICATION_METRICS_TTL)
    except Exception as e:
        log_swallowed_error('core.utils.cache', e)
        logger.warning(f"Failed to log verification metrics: {e}")

    # Phase 4.3 — also feed the time-series MetricsCollector that
    # /observability/quality aggregates (`verification_accuracy` was declared
    # in METRIC_NAMES but never recorded). Best-effort; this is the single
    # chokepoint both verify paths (streaming + non-streaming) flow through.
    if total > 0:
        try:
            from utils.metrics import MetricsCollector
            MetricsCollector(redis_client).record_metric(
                "verification_accuracy", accuracy,
                tags={"model": model or "unknown"},
            )
        except Exception as exc:  # noqa: BLE001 — metrics recording must never block verification
            log_swallowed_error("core.utils.cache.log_verification_metrics.collector", exc)


def log_claim_feedback(
    redis_client,
    conversation_id: str,
    claim_index: int,
    correct: bool,
    model: str | None = None,
) -> None:
    """Store user feedback on a verification claim."""
    entry = json.dumps({
        "conversation_id": conversation_id,
        "claim_index": claim_index,
        "correct": correct,
        "model": model or "unknown",
        "timestamp": utcnow_iso(),
    })
    try:
        redis_client.rpush(REDIS_VERIFICATION_FEEDBACK_KEY, entry)
        redis_client.expire(REDIS_VERIFICATION_FEEDBACK_KEY, REDIS_VERIFICATION_METRICS_TTL)
    except Exception as e:
        log_swallowed_error('core.utils.cache', e)
        logger.warning(f"Failed to log claim feedback: {e}")


def log_verification_error(
    redis_client,
    conversation_id: str,
    error_type: str,
    error_message: str,
    model: str | None = None,
    claim_index: int | None = None,
    phase: str | None = None,
) -> None:
    """Cache verification errors for troubleshooting and analytics.

    Args:
        error_type: Category of error (e.g., ``"stream_interrupted"``,
            ``"claim_verification_failed"``, ``"extraction_failed"``,
            ``"timeout"``, ``"circuit_breaker"``).
        error_message: Human-readable error description.
        model: Model ID that was being used when the error occurred.
        claim_index: Index of the claim being verified (if applicable).
        phase: Pipeline phase (``"extraction"``, ``"verification"``,
            ``"consistency"``, ``"summary"``).
    """
    entry = json.dumps({
        "conversation_id": conversation_id,
        "error_type": error_type,
        "error_message": error_message,
        "model": model or "unknown",
        "claim_index": claim_index,
        "phase": phase,
        "timestamp": utcnow_iso(),
    })
    try:
        redis_client.rpush(REDIS_VERIFICATION_ERRORS_KEY, entry)
        # Trim to keep only the most recent errors
        redis_client.ltrim(REDIS_VERIFICATION_ERRORS_KEY, -REDIS_VERIFICATION_ERRORS_MAX, -1)
        redis_client.expire(REDIS_VERIFICATION_ERRORS_KEY, REDIS_VERIFICATION_METRICS_TTL)
    except Exception as e:
        log_swallowed_error('core.utils.cache', e)
        logger.warning(f"Failed to log verification error: {e}")


# Per-intent RAGAS faithfulness summary (K-program §9, metric 3). The eval path
# writes one JSON summary per intent; scripts/k_program_metrics.py::metric_faithfulness
# reads `cerid:ragas:by_intent:<intent>` during the soak.
_FAITHFULNESS_BY_INTENT_TTL = 86400 * 40

# Producer -> key prefix. The fixtures prefix is the historical key, kept so the
# nightly's own trend history is not orphaned; "live" is a distinct namespace so
# the GA gate can name the population it means.
_FAITHFULNESS_SOURCES = {
    "fixtures": "cerid:ragas:by_intent:",
    "live": "cerid:ragas:live_by_intent:",
}


def record_faithfulness_by_intent(
    redis_client,
    *,
    intent: str,
    faithfulness: float,
    n: int,
    source: str,
    abstention_rate: float | None = None,
    now: datetime | None = None,
) -> None:
    """Write a per-intent RAGAS faithfulness summary, namespaced by producer.

    ``source`` is REQUIRED and it is the whole point of this function's shape.
    Two producers write per-intent faithfulness and they measure different
    populations:

    * ``"fixtures"`` — the nightly ``ragas-eval`` job over
      ``golden_dataset.json``, hand-authored triples whose ground truths were
      edited to match their own contexts. It scores ~0.9 by construction.
    * ``"live"`` — the compiled-summary soak over answers the product actually
      generated from what retrieval actually returned.

    They shared one key. Both slice by the same router intents and both land
    ``compiled_summary`` at **n=30** — 30 of the golden 50 classify that way,
    and the soak defaults to 30 entities — so the collision was invisible in
    the stored payload, and ``metric_faithfulness`` reported whichever job ran
    last. That is where the retracted 0.917 came from: not an unreproducible
    measurement, but the nightly's fixture number sitting in the key the soak
    also writes. The GA gate reads the ``live`` namespace only, and does not
    fall back to fixtures when it is empty — the fallback IS the defect.

    ``abstention_rate`` is stored beside the mean because faithfulness alone can
    be satisfied by degrading the product. The score is
    ``entailed_claims / total_claims``, so a one-sentence answer makes one
    easily-entailed claim and scores 1.000 where a five-sentence overview makes
    five and scores 0.600 with zero contradictions — and an abstention scores
    best of all by leaving the mean entirely. Measured A/B (2026-08-03): a
    richer answer mode raised mean answer length 427 → 640 chars and converted
    two refusals into substantive answers, and faithfulness *fell* 0.835 →
    0.763. A gate on the mean alone therefore rewards terseness and refusal, so
    the collector requires this counter-metric to move the other way.

    Best-effort: a no-op when redis is unavailable and never raises into the
    caller — a metric write must not fail an eval run.
    """
    if redis_client is None:
        return
    if source not in _FAITHFULNESS_SOURCES:
        raise ValueError(
            f"source must be one of {sorted(_FAITHFULNESS_SOURCES)}, got {source!r}",
        )
    key = f"{_FAITHFULNESS_SOURCES[source]}{intent}"
    body: dict[str, Any] = {
        "faithfulness": round(float(faithfulness), 4),
        "n": int(n),
        "source": source,
        "updated_at": (now or datetime.now(tz=timezone.utc)).isoformat(),
    }
    if abstention_rate is not None:
        body["abstention_rate"] = round(float(abstention_rate), 4)
    payload = json.dumps(body)
    try:
        redis_client.set(key, payload)
        redis_client.expire(key, _FAITHFULNESS_BY_INTENT_TTL)
    except Exception as exc:  # noqa: BLE001 — metric emit is best-effort
        log_swallowed_error("core.utils.cache", exc)


# Chunks-per-answer soak metric (K-program §9, metric 4). The answer path
# emits one sample per answer into a daily Redis list; scripts/k_program_metrics.py
# reads it at days 1/7/14 of the soak. 40-day TTL covers the window (the
# collector compares week-over-week) with slack.
_CHUNKS_PER_ANSWER_PREFIX = "cerid:metrics:chunks_per_answer:samples"
_CHUNKS_PER_ANSWER_TTL = 86400 * 40


def chunks_per_answer_stream(intent: str | None) -> str:
    """Map a surface-router intent to its chunks-per-answer metric stream.

    Compiled-summary answers are the optimization target (a verified wiki
    summary displaces raw chunks); every other intent forms the baseline
    arm the reduction % is measured against.
    """
    return "compiled_summary" if intent == "compiled_summary" else "baseline"


def record_chunks_per_answer(
    redis_client,
    *,
    intent: str | None,
    chunk_count: int,
    now: datetime | None = None,
) -> None:
    """Append one chunks-per-answer sample to today's Redis list.

    Best-effort: a no-op when redis is unavailable and never raises into
    the answer path — a metric write must not fail a user query. ``now``
    is injectable for deterministic tests; the answer path passes nothing
    and uses the wall clock.
    """
    if redis_client is None:
        return
    stream = chunks_per_answer_stream(intent)
    bucket = (now or datetime.now(tz=timezone.utc)).strftime("%Y-%m-%d")
    key = f"{_CHUNKS_PER_ANSWER_PREFIX}:{stream}:{bucket}"
    try:
        redis_client.rpush(key, int(chunk_count))
        redis_client.expire(key, _CHUNKS_PER_ANSWER_TTL)
    except Exception as exc:  # noqa: BLE001 — metric emit is best-effort
        log_swallowed_error("core.utils.cache", exc)
