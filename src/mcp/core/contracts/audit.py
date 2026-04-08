# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Abstract audit log contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class AuditEvent:
    """An auditable action."""

    action: str
    actor: str  # user_id or client_id
    resource: str  # artifact_id, query text, etc.
    detail: dict[str, Any] | None = None
    timestamp: str | None = None  # ISO 8601, auto-filled if None


class AuditLog(ABC):
    """Abstract audit log — Redis, Postgres, append-only file, etc."""

    @abstractmethod
    async def record(self, event: AuditEvent) -> None: ...

    @abstractmethod
    async def query(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]: ...
