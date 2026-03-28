# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Centralized error handling decorator for Cerid MCP tools.

This is THE ONE pattern for error handling across all routers and tools.
Use @handle_errors() to get consistent logging, circuit breaker integration,
and CeridError propagation without boilerplate try/except blocks.
"""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Callable

from errors import CeridError, RoutingError

__all__ = ["handle_errors"]

logger = logging.getLogger(__name__)


def handle_errors(
    *,
    fallback: Any = None,
    log_level: str = "error",
    breaker_name: str | None = None,
) -> Callable:
    """Decorator factory for unified error handling.

    Args:
        fallback: Value to return on unhandled errors. If None, raises RoutingError.
        log_level: Logging level for unhandled exceptions (default: "error").
        breaker_name: Optional circuit breaker name to record failures against.
    """

    def decorator(fn: Callable) -> Callable:
        def _handle_exception(exc: Exception, func_name: str) -> Any:
            if isinstance(exc, CeridError):
                raise
            log_fn = getattr(logger, log_level, logger.error)
            log_fn(
                "%s raised %s: %s",
                func_name,
                type(exc).__name__,
                exc,
            )
            if breaker_name:
                try:
                    from utils.circuit_breaker import get_breaker

                    get_breaker(breaker_name).record_failure()
                except Exception:
                    pass
            if fallback is not None:
                return fallback
            raise RoutingError(
                str(exc),
                error_code="UNHANDLED_ERROR",
            ) from exc

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    return _handle_exception(exc, fn.__name__)

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                return _handle_exception(exc, fn.__name__)

        return sync_wrapper

    return decorator
