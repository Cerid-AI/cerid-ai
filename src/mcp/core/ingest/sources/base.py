# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The SourceConnector protocol — every connector kind implements it.

A connector is a thin object that knows how to (a) accept a config
payload, (b) connect to its external system, (c) fetch artifacts
since a cursor, (d) report its health. The protocol stays in
``core/`` so it doesn't import FastAPI / Pydantic-v2 router glue —
keeps the connector implementations testable in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from core.ingest.sources.kinds import SourceKind


@dataclass(frozen=True)
class ConnectResult:
    """Returned by :meth:`SourceConnector.connect` when a new source
    is being instantiated. The caller persists ``source_id`` into the
    Neo4j (:Source) node and surfaces ``connection_time_ms`` to the
    FE's speed counter.
    """

    source_id: str
    config: dict[str, Any]  # echoes back the normalized config (post-validation)
    connection_time_ms: int
    initial_cursor: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HealthStatus:
    """Per-connector health probe result. Surfaces on the source-
    detail pane's "Health" section and feeds the operator dashboard's
    connector-health view.
    """

    ok: bool
    detail: str = ""
    last_error: str | None = None


@dataclass(frozen=True)
class SourceArtifactEvent:
    """Emitted on the SSE stream as each artifact lands. The FE's
    activity feed (F6) and the Constellation's particle stream (F5)
    subscribe to these.

    ``cursor_after`` is the cursor value that should be persisted
    *after* this event — the next fetch picks up from here. The
    consumer is responsible for the persist (not the emitter).
    """

    source_id: str
    artifact_id: str
    elapsed_ms: int
    cursor_after: dict[str, Any]
    title: str = ""
    domain: str = ""


# Redis pubsub channel the poller (app/scheduler.py::_run_source_poll)
# publishes each SourceArtifactEvent to; the SSE endpoint
# (app/routers/observability.py::source_activity_stream) subscribes and
# forwards matching events to connected clients.
SOURCE_ACTIVITY_CHANNEL = "cerid:source-activity"


@runtime_checkable
class SourceConnector(Protocol):
    """Every ingestion connector implements this protocol.

    The four lifecycle methods are:

    1. :meth:`connect` — called once when the user clicks "Connect"
       in the wizard. Validates config, performs any one-time setup
       (OAuth callback, scoped permissions, watch handle), returns
       a :class:`ConnectResult` with the initial sync cursor.
    2. :meth:`fetch_since` — async-iterates artifacts since the last
       known cursor. Each yielded :class:`SourceArtifactEvent`
       represents one ingested artifact; the cursor advance is
       embedded in the event so resume is exact.
    3. :meth:`health_check` — cheap probe (no network round-trip for
       local sources; one HEAD/auth-validate call for remote sources).
       Called by the source-detail pane and the operator dashboard.
    4. :meth:`disconnect` — clean up resources (OAuth revocation, file
       watch deregistration, daemon stop). Idempotent.
    """

    kind: SourceKind
    tier: str  # "core" | "pro"

    async def connect(self, config: dict[str, Any]) -> ConnectResult:
        ...

    def fetch_since(
        self, source_id: str, cursor: dict[str, Any], config: dict[str, Any]
    ) -> AsyncIterator[SourceArtifactEvent]:
        # ``config`` is passed by the polling worker (which already holds the
        # source row) so the connector gets its URL/credentials without a
        # core→app Neo4j round-trip — same contract as health_check/disconnect.
        ...

    async def health_check(self, source_id: str, config: dict[str, Any]) -> HealthStatus:
        ...

    async def disconnect(self, source_id: str, config: dict[str, Any]) -> None:
        ...
