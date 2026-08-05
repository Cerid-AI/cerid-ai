# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""DI ingest sink for connector polling, without a core→app import.

``SourceConnector.fetch_since`` (in ``core``) fetches new items from a feed and
must persist each into the KB — but it cannot import ``app.services.ingestion``
(import-linter forbids ``core → app``). App startup registers an ingest
callable here; connectors invoke it with primitives and the app side runs the
real ``ingest_content`` and returns the artifact id.

Mirrors :func:`core.agents.hallucination.contradiction_sink.set_contradiction_sink`.
Unwired → :func:`get_source_ingest_fn` returns ``None`` and a connector's
``fetch_since`` yields nothing (safe no-op), so ``core`` runs standalone.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

# (content: str, *, domain: str, metadata: dict) -> Awaitable[str | None]
# Returns the persisted artifact_id (None if the ingest was a dedup no-op).
IngestFn = Callable[..., Awaitable[Any]]

_ingest_fn: IngestFn | None = None


def set_source_ingest_fn(fn: IngestFn) -> None:
    """Wire the ingest callable in from app-land at startup."""
    global _ingest_fn
    _ingest_fn = fn


def get_source_ingest_fn() -> IngestFn | None:
    """Return the app-registered ingest callable, or None if unwired."""
    return _ingest_fn
