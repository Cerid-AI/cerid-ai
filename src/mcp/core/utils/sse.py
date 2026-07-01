# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""One Server-Sent Events (SSE) frame formatter.

Every streaming endpoint (``/chat/stream``, ``/agent/hallucination/stream``,
agent-console, observability, ingestion progress …) hand-rolled its own
``f"data: {json.dumps(x)}\\n\\n"`` line. The wire format is trivial but easy to
get subtly wrong (missing blank-line terminator, ``event:`` ordering,
double-encoding an already-serialized string), and each copy drifts.

``sse_event`` is the single formatter. Pure string work (no FastAPI import), so
it lives in ``core`` and both ``app`` routers and ``core`` streamers use it.
"""

from __future__ import annotations

import json
from typing import Any

# The SSE end-of-stream sentinel most cerid streams emit before closing.
DONE = "data: [DONE]\n\n"


def sse_event(data: Any, *, event: str | None = None) -> str:
    """Format one SSE frame.

    ``data`` is JSON-encoded unless it is already a ``str`` (passed through
    verbatim, so callers holding a pre-serialized payload are not double-encoded).
    ``event`` adds a named-event line before the data line. The frame always ends
    with the blank line that terminates an SSE message.
    """
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {payload}\n\n"


def sse_done() -> str:
    """The ``data: [DONE]`` terminator frame."""
    return DONE
