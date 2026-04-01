# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Hallucination detection — Redis persistence for reports.

Provides storage and retrieval of hallucination verification reports.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from errors import VerificationError

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
    except (VerificationError, ValueError, OSError, RuntimeError) as e:
        logger.warning("Failed to retrieve hallucination report: %s", e)
    return None
