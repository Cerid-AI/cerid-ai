# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Clipboard SourceConnector.

Promotes the existing host-side clipboard daemon
(``scripts/clipboard_daemon.py``) into the SourceConnector protocol.

The daemon itself runs on the host (it needs ``pbpaste`` access on
macOS); this connector is the registry stub that records the daemon
is active for the user and provides health-check by reading the
daemon's Redis heartbeat key ``cerid:clipboard:alive``.

Cursor shape: ``{"last_heartbeat_at": iso8601 | None}``
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncIterator

from core.ingest.sources.base import (
    ConnectResult,
    HealthStatus,
    SourceArtifactEvent,
    SourceConnector,
)

logger = logging.getLogger("ai-companion.connectors.clipboard")

_HEARTBEAT_KEY = "cerid:clipboard:alive"
# Daemon writes the key with a 60s TTL on every poll. Anything more
# than 90s stale is considered unhealthy (one or two missed polls is
# expected during brief network blips).
_STALE_AFTER_S = 90


class ClipboardConnector(SourceConnector):
    """Records that a host-side clipboard daemon is active.

    The daemon writes artifacts via ``POST /ingest/webhook``, so this
    connector is a registry stub plus a health-check decorator that
    surfaces the daemon's heartbeat status to the source-detail pane.
    """

    kind = "clipboard"
    tier = "core"
    # Ingestion depends on the host-side clipboard daemon. The actual
    # presence probe (Redis heartbeat) lives in the router layer — core
    # cannot touch app-owned store clients; this flag only marks the kind
    # as helper-backed for /sources/kinds.
    requires_desktop = True

    async def connect(self, config: dict[str, Any]) -> ConnectResult:
        min_length = int(config.get("min_length", 50))
        poll_seconds = float(config.get("poll_seconds", 2.0))
        return ConnectResult(
            source_id=str(uuid.uuid4()),
            config={
                "name": config.get("name") or "Clipboard",
                "min_length": min_length,
                "poll_seconds": poll_seconds,
            },
            connection_time_ms=0,
            initial_cursor={"last_heartbeat_at": None},
        )

    async def fetch_since(
        self, source_id: str, cursor: dict[str, Any], config: dict[str, Any]
    ) -> AsyncIterator[SourceArtifactEvent]:
        """No-op — the daemon ingests via POST /ingest/webhook directly."""
        if False:  # pragma: no cover
            yield SourceArtifactEvent(  # type: ignore[unreachable]
                source_id=source_id,
                artifact_id="",
                elapsed_ms=0,
                cursor_after={},
            )
        return

    async def health_check(self, source_id: str, config: dict[str, Any]) -> HealthStatus:
        """Returns a basic configured status. The router layer
        decorates this with a Redis heartbeat check
        (``cerid:clipboard:alive``) to determine whether the host
        daemon is actually running — that Redis touch can't happen
        inside core/ without violating the core → app import rule.
        """
        return HealthStatus(
            ok=True,
            detail="connector configured (router checks daemon heartbeat)",
        )

    async def disconnect(self, source_id: str, config: dict[str, Any]) -> None:
        return
