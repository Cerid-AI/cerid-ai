# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""DI sink for persisting contradiction findings without a core→app import.

The NLI verification guard (``core``) detects when a claim contradicts KB
evidence, but it cannot import ``app.services.contradiction_log`` — the
import-linter forbids ``core → app``. App startup registers a sink callable
here; the guard invokes it with primitives, and the app side builds the
``ContradictionFinding`` and persists it (MERGE ``(:Entity)-[:HAS_CONTRADICTION]
->(:ContradictionFinding)`` + emit the wiki-refresh event).

Mirrors :func:`core.agents.hallucination.authoritative_verify.set_data_source_registry`.
App startup wires the sink (``set_contradiction_sink`` in ``app/main.py``), so it
is registered in production. Only in ``core``-standalone / test contexts (no app
registration) does :func:`get_contradiction_sink` return ``None`` — then the
guard simply skips logging, so ``core`` runs standalone unchanged.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

# (claim_text, source_text, *, source_artifact_id, severity, entity_slug,
#  query_ctx_id) -> Awaitable[Any]. Kept as ``...`` (untyped kwargs) so ``core``
# never references the app-side ContradictionFinding model.
SinkFn = Callable[..., Awaitable[Any]]

_sink: SinkFn | None = None


def set_contradiction_sink(fn: SinkFn) -> None:
    """Wire the contradiction persister in from app-land at startup.

    ``app/main.py`` calls this once with a coroutine that builds a
    ``ContradictionFinding`` and calls ``log_contradiction``.
    """
    global _sink
    _sink = fn


def get_contradiction_sink() -> SinkFn | None:
    """Return the app-registered contradiction sink, or None if unwired."""
    return _sink


def stable_id(*parts: Any) -> str:
    """Deterministic short id from content parts, so re-detecting the same
    contradiction is idempotent (the finding MERGEs on a stable finding_id
    instead of creating a duplicate each time)."""
    import hashlib

    joined = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(  # noqa: S324 — non-crypto id
        joined.encode("utf-8", "replace"), usedforsecurity=False
    ).hexdigest()[:16]
