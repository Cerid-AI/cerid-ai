"""
Redis audit logging for ingest and recategorization events.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger("ai-companion.cache")


def log_event(
    redis_client,
    event_type: str,
    artifact_id: str,
    domain: str,
    filename: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Append an event to the Redis audit log.

    Args:
        event_type: "ingest", "recategorize", "delete"
        artifact_id: UUID of the artifact
        domain: Current domain after the event
        filename: Original filename
        extra: Additional context (e.g. old_domain for recategorize)
    """
    payload = json.dumps({
        "event": event_type,
        "artifact_id": artifact_id,
        "domain": domain,
        "filename": filename,
        "timestamp": datetime.utcnow().isoformat(),
        **(extra or {}),
    })
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
