# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Core ingestion sources — the canonical SourceConnector protocol +
kind registry shared between the protocol layer (this package) and
the Neo4j data-access layer (``app.db.neo4j.sources``).

See ``tasks/2026-05-24-ingestion-experience-plan.md`` §2 for the
full architecture. Each connector kind ships its own module here
(e.g., ``rss.py``, ``webhook.py``, ``voice_note.py``) implementing
:class:`SourceConnector`.
"""
from __future__ import annotations

from core.ingest.sources.base import (
    ConnectResult,
    HealthStatus,
    SourceArtifactEvent,
    SourceConnector,
)
from core.ingest.sources.kinds import (
    CORE_KINDS,
    KIND_FAMILY,
    KIND_TIER,
    PRO_KINDS,
    SOURCE_KINDS,
    SourceFamily,
    SourceKind,
)
from core.ingest.sources.registry import (
    get_connector,
    iter_connectors,
    register_connector,
)

__all__ = [
    "ConnectResult",
    "HealthStatus",
    "SourceArtifactEvent",
    "SourceConnector",
    "SourceFamily",
    "SourceKind",
    "SOURCE_KINDS",
    "CORE_KINDS",
    "PRO_KINDS",
    "KIND_FAMILY",
    "KIND_TIER",
    "register_connector",
    "get_connector",
    "iter_connectors",
]
