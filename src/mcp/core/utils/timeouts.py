# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Awaitable timeout helper for store / external-service calls.

Wraps any awaitable in ``asyncio.wait_for`` with a stable ``label`` so
that on trip we emit a structured warning + Sentry breadcrumb and raise
a distinguishable ``StoreTimeoutError``. Callers can catch the typed
error to degrade gracefully (return a partial result, fall back to a
slower index, etc.) instead of letting the request hang for the full
client-side budget.

Two enforcement notes:

* ``asyncio.wait_for`` only fires once the wrapped coroutine yields
  control back to the loop. A pure-sync blocking call inside an
  ``async def`` will pin the loop and the timeout will never trigger.
  Store wrappers therefore offload sync drivers via ``asyncio.to_thread``
  *before* handing the awaitable to ``with_timeout``.
* Cancelling a thread-offloaded blocking call does NOT actually stop
  the underlying thread (CPython has no thread-cancellation primitive).
  The awaiter sees the timeout immediately and the orphan thread
  finishes whenever the driver call returns; its result is discarded.
  This is the standard, accepted compromise — the goal is releasing
  the request, not killing the underlying I/O.

Lives in ``core/utils`` so retrieval-layer code can use it without
crossing the ``core must not import app`` import-linter contract.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any, TypeVar

logger = logging.getLogger("ai-companion.timeouts")

T = TypeVar("T")


class StoreTimeoutError(asyncio.TimeoutError):
    """Raised when a labelled store / external call exceeds its budget.

    Subclasses ``asyncio.TimeoutError`` so existing
    ``except asyncio.TimeoutError`` handlers continue to work, but the
    typed subclass lets new code distinguish a store-timeout (degrade)
    from a generic asyncio timeout (re-raise).
    """

    def __init__(self, label: str, seconds: float) -> None:
        self.label = label
        self.seconds = seconds
        super().__init__(f"{label} exceeded {seconds:.1f}s timeout")


async def with_timeout(
    awaitable: Awaitable[T],
    *,
    seconds: float,
    label: str,
    context: dict[str, Any] | None = None,
) -> T:
    """Await ``awaitable`` with a timeout, emitting a breadcrumb on trip.

    Parameters
    ----------
    awaitable
        The coroutine / future to await.
    seconds
        Timeout budget. Must be positive.
    label
        Stable identifier used in logs, breadcrumbs, and the raised
        ``StoreTimeoutError``. Use a dotted path that's stable across
        refactors (e.g. ``"chroma.query"``, ``"neo4j.get_artifact"``).
    context
        Optional structured metadata attached to the warning + Sentry
        breadcrumb (e.g. ``{"domain": "coding", "top_k": 10}``).

    Raises
    ------
    StoreTimeoutError
        When ``awaitable`` does not complete within ``seconds``.
    """
    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except asyncio.TimeoutError as exc:
        logger.warning(
            "timeout: %s exceeded %.1fs",
            label, seconds,
            extra={"timeout_label": label, "timeout_s": seconds, **(context or {})},
        )
        try:
            import sentry_sdk  # type: ignore[import-not-found]
            sentry_sdk.add_breadcrumb(
                category="timeout",
                message=f"{label} exceeded {seconds:.1f}s",
                level="warning",
                data={"label": label, "seconds": seconds, **(context or {})},
            )
        except ImportError:
            pass
        except Exception:  # noqa: BLE001 — observability must never itself raise
            pass
        raise StoreTimeoutError(label, seconds) from exc
