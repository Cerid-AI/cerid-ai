# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Hallucination detection — Redis persistence for reports.

Provides storage and retrieval of hallucination verification reports.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("ai-companion.hallucination")

# Redis key prefix and TTL for hallucination reports
REDIS_HALLUCINATION_PREFIX = "hall:"
REDIS_HALLUCINATION_TTL = 86400 * 7  # 7 days


def get_hallucination_report(
    redis_client,
    conversation_id: str,
) -> dict[str, Any] | None:
    """Retrieve a previously stored hallucination report."""
    try:
        key = f"{REDIS_HALLUCINATION_PREFIX}{conversation_id}"
        data = redis_client.get(key)
        if data:
            return json.loads(data)
    except Exception as e:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('core.agents.hallucination.persistence', e)
        logger.warning("Failed to retrieve hallucination report: %s", e)
    return None


def delete_hallucination_report(
    redis_client,
    conversation_id: str,
) -> bool:
    """Delete the durable ``hall:{cid}`` verification report.

    Called by conversation-delete and the L4 session-wipe orchestrator so a
    removed conversation does not leave its report — verbatim claims + source
    snippets — cached in Redis for the 7-day TTL (E1 CR-012). Best-effort:
    returns True if a key was removed, False on miss or error.
    """
    try:
        key = f"{REDIS_HALLUCINATION_PREFIX}{conversation_id}"
        return bool(redis_client.delete(key))
    except Exception as e:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('core.agents.hallucination.persistence', e)
        logger.warning("Failed to delete hallucination report: %s", e)
        return False
