# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Thin Langfuse client for production tracing (Workstream E Phase 5b).

Mirrors the design of :mod:`core.observability.span_helpers` (Sentry):

  * No-op cleanly when ``LANGFUSE_PUBLIC_KEY`` is unset (zero overhead,
    no import-time failures, no runtime errors)
  * Single canonical wrapper around the Langfuse SDK so call-sites use a
    consistent shape across the codebase
  * ``stage="<name>"`` tag aligns with the existing structlog logging
    contract — Langfuse spans nest under stage names so traces are
    grep-comparable to the structured logs

Privacy posture: by default the client is configured to talk to a
loopback Langfuse container (``http://langfuse:3000`` on the docker
network) and the compose stack disables the upstream telemetry pingback.
**Nothing leaves the box** unless the operator explicitly sets
``LANGFUSE_HOST`` to a remote URL. See ``docs/OBSERVABILITY.md`` for
the full privacy contract.
"""
from __future__ import annotations

import contextlib
import logging
import os
import random
from collections.abc import Iterator
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.observability.langfuse")

# Lazy import: don't crash when langfuse isn't installed (community tier).
_langfuse_available = True
try:
    from langfuse import Langfuse  # type: ignore[import-not-found]
except ImportError:
    _langfuse_available = False
    Langfuse = None  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://langfuse:3000")
LANGFUSE_LLM_JUDGE_SAMPLE_RATE = float(
    os.getenv("LANGFUSE_LLM_JUDGE_SAMPLE_RATE", "0.02"),
)


def is_enabled() -> bool:
    """Return True when Langfuse is fully configured and importable.

    Used by call-sites to decide whether to perform optional work
    (e.g. sample an LLM-judge call) before any tracing call.
    """
    return _langfuse_available and bool(LANGFUSE_PUBLIC_KEY) and bool(LANGFUSE_SECRET_KEY)


# ---------------------------------------------------------------------------
# Client singleton
# ---------------------------------------------------------------------------

_client: Any | None = None
_client_init_failed = False


def get_client() -> Any | None:
    """Return a memoised Langfuse client, or None when disabled.

    Initialisation errors are logged once and the client is permanently
    disabled — we never let observability take down the request path.
    """
    global _client, _client_init_failed

    if _client is not None or _client_init_failed:
        return _client

    if not is_enabled():
        return None

    try:
        _client = Langfuse(
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
            host=LANGFUSE_HOST,
        )
        logger.info(
            "langfuse client initialised host=%s sample_rate=%.3f",
            LANGFUSE_HOST, LANGFUSE_LLM_JUDGE_SAMPLE_RATE,
        )
    except Exception as exc:  # noqa: BLE001 — observability must never crash
        _client_init_failed = True
        logger.warning(
            "langfuse client init failed (host=%s): %s — tracing disabled",
            LANGFUSE_HOST, exc,
        )
        return None

    return _client


# ---------------------------------------------------------------------------
# Span / trace helpers — no-op when client unavailable
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def trace(
    name: str,
    *,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any | None]:
    """Open a top-level trace; no-op when Langfuse is disabled.

    Use one ``trace`` per logical request (e.g. a single ``/agent/query``
    call) and nest finer-grained spans inside via :func:`span`.
    """
    client = get_client()
    if client is None:
        yield None
        return
    try:
        tr = client.trace(name=name, user_id=user_id, metadata=metadata or {})
    except Exception as exc:  # noqa: BLE001
        logger.debug("langfuse trace open failed: %s", exc)
        yield None
        return
    try:
        yield tr
    finally:
        # Langfuse v2 clients flush on a background thread; the explicit
        # flush keeps tests deterministic and ensures short-lived
        # processes (e.g. CLI scripts) don't lose the last few events.
        try:
            client.flush()
        except Exception as flush_exc:  # noqa: BLE001 — observability cleanup
            log_swallowed_error(
                "core.observability.langfuse_client.flush", flush_exc,
            )


@contextlib.contextmanager
def span(
    parent: Any | None,
    name: str,
    *,
    stage: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any | None]:
    """Open a child span under ``parent``; no-op when parent is None.

    ``stage`` is the canonical CLAUDE.md log-stage breadcrumb — pass it
    explicitly so traces are searchable by the same key as structured
    logs and Sentry spans.
    """
    if parent is None:
        yield None
        return
    md = dict(metadata or {})
    if stage is not None:
        md.setdefault("stage", stage)
    try:
        sp = parent.span(name=name, metadata=md)
    except Exception as exc:  # noqa: BLE001
        logger.debug("langfuse span open failed (name=%s): %s", name, exc)
        yield None
        return
    try:
        yield sp
    finally:
        try:
            sp.end()
        except Exception as end_exc:  # noqa: BLE001 — observability cleanup
            log_swallowed_error(
                "core.observability.langfuse_client.span_end", end_exc,
            )


def should_sample_llm_judge() -> bool:
    """Return True when this request was selected for LLM-judge scoring.

    Sampling is independent of Langfuse availability — call-sites can
    use this to decide whether to even prepare the judge prompt before
    the (cheap) tracing call. The sample rate is governed by
    ``LANGFUSE_LLM_JUDGE_SAMPLE_RATE`` (default 0.02 = 2%).
    """
    if LANGFUSE_LLM_JUDGE_SAMPLE_RATE <= 0:
        return False
    return random.random() < LANGFUSE_LLM_JUDGE_SAMPLE_RATE


def score_trace(
    trace: Any | None,
    *,
    name: str,
    value: float,
    comment: str | None = None,
) -> None:
    """Attach a score to an open trace; no-op when trace is None."""
    if trace is None:
        return
    try:
        trace.score(name=name, value=value, comment=comment)
    except Exception as score_exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "core.observability.langfuse_client.score", score_exc,
        )
