# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Redis audit logging and conversation metrics storage.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import config
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
        logger.error(f"Failed to log event to Redis: {e}")


def get_log(redis_client, limit: int = 50) -> list[dict[str, Any]]:
    """Read recent audit log entries."""
    try:
        entries = redis_client.lrange(config.REDIS_INGEST_LOG, 0, limit - 1)
        return [json.loads(e) for e in entries]
    except Exception as e:
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
        logger.warning(f"Failed to log conversation metrics: {e}")


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
        logger.warning(f"Failed to log verification metrics: {e}")


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
        logger.warning(f"Failed to log verification error: {e}")
