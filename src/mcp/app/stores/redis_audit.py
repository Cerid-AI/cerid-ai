# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Redis implementation of AuditLog contract."""

from __future__ import annotations

import json
from typing import Any

from core.contracts.audit import AuditEvent, AuditLog
from core.utils.time import utcnow_iso


class RedisAuditLog(AuditLog):
    """AuditLog backed by Redis lists."""

    AUDIT_KEY = "cerid:audit_log"
    MAX_ENTRIES = 10000

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def record(self, event: AuditEvent) -> None:
        if event.timestamp is None:
            event.timestamp = utcnow_iso()
        entry = json.dumps({
            "action": event.action,
            "actor": event.actor,
            "resource": event.resource,
            "detail": event.detail,
            "timestamp": event.timestamp,
        })
        self._redis.lpush(self.AUDIT_KEY, entry)
        self._redis.ltrim(self.AUDIT_KEY, 0, self.MAX_ENTRIES - 1)

    async def query(
        self, *, actor: str | None = None, action: str | None = None,
        since: str | None = None, limit: int = 100,
    ) -> list[AuditEvent]:
        raw = self._redis.lrange(self.AUDIT_KEY, 0, limit * 3)
        events: list[AuditEvent] = []
        for entry in raw or []:
            data = json.loads(entry.decode() if isinstance(entry, bytes) else entry)
            if actor and data.get("actor") != actor:
                continue
            if action and data.get("action") != action:
                continue
            if since and data.get("timestamp", "") < since:
                continue
            events.append(AuditEvent(
                action=data["action"], actor=data["actor"], resource=data["resource"],
                detail=data.get("detail"), timestamp=data.get("timestamp"),
            ))
            if len(events) >= limit:
                break
        return events
