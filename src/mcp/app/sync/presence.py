# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Presence tracking for real-time collaborative sync.

Stores per-user presence data in a Redis hash with per-key TTL emulation
(each user is a separate Redis key under a common prefix).
"""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger("ai-companion.presence")

# Redis key prefix for presence data
_PRESENCE_PREFIX = "cerid:presence:user:"
_PRESENCE_INDEX = "cerid:presence:users"


class PresenceManager:
    """Track online user presence via Redis.

    Each user's presence is stored as a separate Redis key with a TTL,
    and indexed in a Redis set for efficient enumeration.
    """

    def __init__(self, timeout_s: int | None = None) -> None:
        # Deferred import to avoid circular deps at module level
        from config.settings import WS_PRESENCE_TIMEOUT_S

        self._timeout_s = timeout_s if timeout_s is not None else WS_PRESENCE_TIMEOUT_S

    def _get_redis(self):  # noqa: ANN202
        from app.deps import get_redis

        return get_redis()

    def update(self, user_id: str, data: dict) -> None:
        """Store or update user presence data with TTL."""
        r = self._get_redis()
        key = f"{_PRESENCE_PREFIX}{user_id}"
        payload = {
            "user_id": user_id,
            "last_seen": time.time(),
            **data,
        }
        r.set(key, json.dumps(payload), ex=self._timeout_s)
        r.sadd(_PRESENCE_INDEX, user_id)

    def remove(self, user_id: str) -> None:
        """Remove a user from presence tracking."""
        r = self._get_redis()
        key = f"{_PRESENCE_PREFIX}{user_id}"
        r.delete(key)
        r.srem(_PRESENCE_INDEX, user_id)

    def get_all(self) -> list[dict]:
        """Return all active users' presence data."""
        r = self._get_redis()
        members = r.smembers(_PRESENCE_INDEX)
        result: list[dict] = []
        expired: list[str] = []

        for uid in members:
            uid_str = uid.decode() if isinstance(uid, bytes) else uid
            key = f"{_PRESENCE_PREFIX}{uid_str}"
            raw = r.get(key)
            if raw is None:
                # Key expired — clean up the index
                expired.append(uid_str)
                continue
            try:
                data = json.loads(raw)
                result.append(data)
            except (json.JSONDecodeError, TypeError):
                expired.append(uid_str)

        # Lazy cleanup of expired entries
        if expired:
            for uid_str in expired:
                r.srem(_PRESENCE_INDEX, uid_str)

        return result

    def heartbeat(self, user_id: str) -> None:
        """Refresh TTL for an existing user's presence key."""
        r = self._get_redis()
        key = f"{_PRESENCE_PREFIX}{user_id}"
        # Only refresh if the key still exists
        if r.exists(key):
            r.expire(key, self._timeout_s)
        else:
            # Re-create minimal presence on heartbeat if expired
            self.update(user_id, {})
