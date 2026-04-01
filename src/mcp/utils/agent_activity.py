# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Agent activity pub/sub -- emit events for the agent communication console."""

from __future__ import annotations

import json
import logging
import time

from errors import CeridError

logger = logging.getLogger("ai-companion.agent_activity")

_CHANNEL = "cerid:agent:activity"


def emit_activity(agent_name: str, message: str, level: str = "info") -> None:
    """Publish an agent activity event to Redis pub/sub."""
    try:
        from deps import get_redis

        redis = get_redis()
        event = json.dumps({
            "agent": agent_name,
            "message": message,
            "level": level,
            "timestamp": time.time(),
        })
        redis.publish(_CHANNEL, event)
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.debug("Failed to emit agent activity: %s", e)
