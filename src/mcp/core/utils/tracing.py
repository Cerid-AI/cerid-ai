# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Request tracing context — pure contextvars accessors with zero HTTP dependency.

These functions read values set by the middleware but have no FastAPI/Starlette
imports themselves, making them safe for use in core agents and utilities.
"""

from __future__ import annotations

import contextvars

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
client_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "client_id", default="unknown"
)


def get_request_id() -> str:
    """Get the current request ID from context."""
    return request_id_var.get()


def get_client_id() -> str:
    """Get the current client ID from context."""
    return client_id_var.get()


def tracing_headers() -> dict[str, str]:
    """Build tracing headers from current context for outgoing HTTP calls."""
    return {
        "X-Request-ID": request_id_var.get(),
        "X-Client-ID": client_id_var.get(),
    }
