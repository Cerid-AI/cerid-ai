# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""DI sink for metamorphic scoring without a core→app import.

The metamorphic scorer is a Pro-tier plugin whose import interface lives in
``app.agents.hallucination.metamorphic``. The streaming verifier that should
call it lives in ``core`` and cannot import ``app`` (import-linter forbids
``core → app``) — which is exactly why the feature shipped with a plugin, a
tier-matrix entry, a frontend type, and **no caller at all**.

App startup registers the stub's ``metamorphic_score`` here; the verifier
invokes it with primitives (answer text, context text). Unwired — in
``core``-standalone or test contexts — :func:`get_metamorphic_sink` returns
``None`` and the verifier simply omits the score.

Mirrors :mod:`core.agents.hallucination.contradiction_sink`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

# (answer: str, context: str) -> Awaitable[dict[str, Any]]
SinkFn = Callable[..., Awaitable[Any]]

_sink: SinkFn | None = None


def set_metamorphic_sink(fn: SinkFn | None) -> None:
    """Wire the metamorphic scorer in from app-land at startup."""
    global _sink
    _sink = fn


def get_metamorphic_sink() -> SinkFn | None:
    """Return the app-registered metamorphic sink, or None if unwired."""
    return _sink
