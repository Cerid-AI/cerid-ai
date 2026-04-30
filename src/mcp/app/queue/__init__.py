# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""RQ-based ingestion queue (Workstream E Phase 5a — sync-default scaffold).

Reuses the Redis instance the rest of the stack already runs against
(``config.REDIS_URL``). Enabled per-deployment by setting
``INGEST_QUEUE_MODE=async``; default is ``sync`` so existing
deployments and the desktop ``/ingest/progress`` contract are
untouched until an operator opts in.

Public surface:

* :func:`get_ingest_queue` — returns the singleton RQ ``Queue`` bound
  to the redis at ``config.REDIS_URL``. Raises :class:`RuntimeError`
  when the rq dep is missing (community installs without queue
  workers don't pay the cost).

* :func:`is_async_mode` — returns True when ``INGEST_QUEUE_MODE=async``
  AND rq is importable. Routers branch on this.

The actual task body lives in :mod:`app.queue.tasks` so workers can
import it without pulling the FastAPI router stack.
"""
from __future__ import annotations

import logging
from typing import Any

import config

logger = logging.getLogger("ai-companion.queue")

_INGEST_QUEUE_NAME = "cerid-ingest"

# Lazy import — the queue is opt-in so community installs don't need rq.
_rq_available = True
_rq_import_error: Exception | None = None
try:
    from redis import Redis  # type: ignore[import-not-found]
    from rq import Queue  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover — environment-dependent
    _rq_available = False
    _rq_import_error = exc
    Queue = None  # type: ignore[misc,assignment]
    Redis = None  # type: ignore[misc,assignment]


_queue_singleton: Any | None = None


def is_async_mode() -> bool:
    """True iff the operator opted into the queue AND rq is importable."""
    if getattr(config, "INGEST_QUEUE_MODE", "sync").lower() != "async":
        return False
    if not _rq_available:
        logger.warning(
            "INGEST_QUEUE_MODE=async but rq is not installed (%s); "
            "falling back to sync ingestion. Install rq>=2.0 to enable.",
            _rq_import_error,
        )
        return False
    return True


def get_ingest_queue() -> Any:
    """Return the singleton RQ Queue bound to ``config.REDIS_URL``."""
    global _queue_singleton
    if _queue_singleton is not None:
        return _queue_singleton
    if not _rq_available:
        raise RuntimeError(
            "rq not installed — cannot create ingest queue. "
            "Install rq>=2.0 or keep INGEST_QUEUE_MODE=sync.",
        )
    redis_conn = Redis.from_url(config.REDIS_URL)
    _queue_singleton = Queue(_INGEST_QUEUE_NAME, connection=redis_conn)
    logger.info(
        "ingest queue initialised name=%s redis=%s",
        _INGEST_QUEUE_NAME, config.REDIS_URL.split("@")[-1],
    )
    return _queue_singleton
