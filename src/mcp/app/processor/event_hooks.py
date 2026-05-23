# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Processor event hooks — Phase K1.2.

Lightweight in-process pub/sub for events emitted by background jobs.
The wiki-refresh subscriber (Phase K1.3) consumes ``entities_added``
events to close the wiki orphan loop without coupling the entity
extraction job to the wiki refresh job.

Why in-process?
---------------
The processor worker already runs in the same Python process as the
FastAPI app (single deployment unit). A cross-process bus (Redis
pub/sub, etc.) would buy nothing today and add an extra failure mode.
Subscribers register at import time; emission is a direct function
call dispatched through a synchronous fan-out.

Event types
-----------
``"entities_added"`` — emitted by ``EntityExtractionJob`` after a
successful Neo4j upsert. Payload:
``{"artifact_id": str, "entity_slugs": [str], "tenant_id": str}``.

``"contradiction_detected"`` — emitted when the contradiction logger
records a finding tied to an entity slug. Payload:
``{"finding_id": str, "entity_slug": str, "severity": str}``.

``"summary_drift"`` — emitted by the weekly drift cron (Phase K2.4)
when an entity's summary embedding has drifted past τ. Payload:
``{"entity_slug": str, "drift": float}``.

Subscribers are best-effort. Exceptions are caught and logged via
``log_swallowed_error`` so a broken subscriber can't take down the
emitter.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.event_hooks")

EventName = str
SubscriberFn = Callable[[dict[str, Any]], None]

# Module-level registry. Keyed by event name -> list of subscribers in
# registration order. Order matters when multiple subscribers consume
# the same event; first-registered runs first.
_subscribers: dict[EventName, list[SubscriberFn]] = {}


def subscribe(event: EventName, fn: SubscriberFn) -> None:
    """Register a subscriber for ``event``.

    Idempotent: re-subscribing the same callable is a no-op so module
    re-imports during tests don't double-fire.
    """
    bucket = _subscribers.setdefault(event, [])
    if fn not in bucket:
        bucket.append(fn)
        logger.debug("event_hooks.subscribed event=%s fn=%s", event, fn.__qualname__)


def unsubscribe(event: EventName, fn: SubscriberFn) -> None:
    """Remove a subscriber. No-op if not registered."""
    bucket = _subscribers.get(event)
    if bucket and fn in bucket:
        bucket.remove(fn)


def emit(event: EventName, payload: dict[str, Any]) -> None:
    """Dispatch ``payload`` to all subscribers of ``event``.

    Synchronous fan-out. Each subscriber failure is isolated so one bad
    handler can't break the chain. Subscribers that need async work
    should enqueue jobs rather than awaiting in-line.
    """
    bucket = _subscribers.get(event, [])
    if not bucket:
        logger.debug("event_hooks.no_subscribers event=%s", event)
        return

    for fn in bucket:
        try:
            fn(payload)
        except Exception as exc:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "processor.event_hooks.dispatch",
                exc,
                context={"event": event, "subscriber": fn.__qualname__},
            )


def clear_for_tests() -> None:
    """Reset the subscriber registry. Test-only utility."""
    _subscribers.clear()


# ---------------------------------------------------------------------------
# Auto-register the wiki refresh subscriber on import (Phase K1.3).
# Lazy-imported to avoid a circular dependency: event_hooks ← wiki_subscriber
# ← processor_queue ← (other processor modules that may import event_hooks).
# The import happens at module load; subscriber registration is idempotent.
# ---------------------------------------------------------------------------
import importlib.util as _importlib_util  # noqa: E402 — late import is intentional (lazy registration after module body is defined)

if _importlib_util.find_spec("app.processor.subscribers.wiki_refresh") is not None:
    from app.processor.subscribers import wiki_refresh as _wiki_refresh_subscriber

    _wiki_refresh_subscriber.register()
else:  # pragma: no cover — subscriber package may be absent in stripped builds
    logger.info("event_hooks.wiki_refresh_subscriber_missing — graceful degradation")
