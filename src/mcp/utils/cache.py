# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Redis audit logging and conversation metrics storage.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import config
from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.cache")


def log_event(
    redis_client,
    event_type: str,
    artifact_id: str,
    domain: str,
    filename: str,
    extra: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
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
    entry: Dict[str, Any] = {
        "event": event_type,
        "artifact_id": artifact_id,
        "domain": domain,
        "filename": filename,
        "timestamp": utcnow_iso(),
        **(extra or {}),
    }
    if conversation_id:
        entry["conversation_id"] = conversation_id
    payload = json.dumps(entry)
    try:
        redis_client.lpush(config.REDIS_INGEST_LOG, payload)
        redis_client.ltrim(config.REDIS_INGEST_LOG, 0, config.REDIS_LOG_MAX - 1)
    except Exception as e:
        logger.error(f"Failed to log event to Redis: {e}")


def get_log(redis_client, limit: int = 50) -> List[Dict[str, Any]]:
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
REDIS_VERIFICATION_METRICS_TTL = 86400 * 30  # 30 days


def log_verification_metrics(
    redis_client,
    conversation_id: str,
    model: Optional[str] = None,
    verified: int = 0,
    unverified: int = 0,
    uncertain: int = 0,
    total: int = 0,
) -> None:
    """Store verification metrics for analytics aggregation."""
    accuracy = round(verified / total, 4) if total > 0 else 0.0
    entry = json.dumps({
        "conversation_id": conversation_id,
        "model": model or "unknown",
        "verified": verified,
        "unverified": unverified,
        "uncertain": uncertain,
        "total": total,
        "accuracy": accuracy,
        "timestamp": utcnow_iso(),
    })
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
    model: Optional[str] = None,
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
