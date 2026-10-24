# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Hallucination detection — Redis persistence for reports.

Provides storage and retrieval of hallucination verification reports.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger("ai-companion.hallucination")

# Redis key prefix and TTL for hallucination reports
REDIS_HALLUCINATION_PREFIX = "hall:"
REDIS_HALLUCINATION_TTL = 86400 * 7  # 7 days


def message_reports_prefix(conversation_id: str) -> str:
    """The prefix of a conversation's per-message report keys. The id is hashed:
    a conversation id is the caller's string and may hold ":", so
    ``hall:{cid}:{mid}`` could be another conversation's ``hall:{cid}`` key."""
    return f"{REDIS_HALLUCINATION_PREFIX}msg:{hashlib.sha256(conversation_id.encode()).hexdigest()[:32]}:"


def report_key(conversation_id: str, message_id: str = "") -> str:
    """The cache key of one report. A report belongs to the assistant message it
    verified (forget spec §6.2); a caller that has no messages (an API check of a
    bare response) keeps the conversation-level key."""
    if message_id:
        return f"{message_reports_prefix(conversation_id)}{message_id}"
    if conversation_id.startswith("msg:"):
        # The per-message namespace: such an id would name another
        # conversation's report and bypass its forget check.
        raise ValueError("conversation id is in a reserved namespace")
    return f"{REDIS_HALLUCINATION_PREFIX}{conversation_id}"


def get_hallucination_report(
    redis_client,
    conversation_id: str,
    message_id: str = "",
) -> dict[str, Any] | None:
    """Retrieve a previously stored hallucination report; none for a forgotten
    conversation, whose report stays in Redis while it is in the trash."""
    from core.forget import registry as forget_registry
    if forget_registry.is_forgotten("conversation", conversation_id):
        return None
    try:
        key = report_key(conversation_id, message_id)
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
