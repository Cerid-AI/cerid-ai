# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Async circuit breaker for external service calls (Bifrost, etc.).

States:
  CLOSED  — requests flow through normally. Failures are counted.
  OPEN    — requests are immediately rejected. After `recovery_timeout`,
            transitions to HALF_OPEN.
  HALF_OPEN — a single probe request is allowed. On success → CLOSED,
              on failure → OPEN again.

Usage:
    breaker = AsyncCircuitBreaker("bifrost-rerank", failure_threshold=3, recovery_timeout=60)

    try:
        result = await breaker.call(my_async_fn, arg1, arg2)
    except CircuitOpenError:
        # Fallback logic
        ...
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any, TypeVar

from errors import CeridError

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when the circuit is open and calls are being rejected."""

    def __init__(self, name: str, retry_after: float):
        self.name = name
        self.retry_after = retry_after
        super().__init__(f"Circuit '{name}' is open, retry after {retry_after:.0f}s")


class NonTransientError(Exception):
    """Base class for errors that should NOT count as circuit breaker failures.

    Subclass this for errors like 402 (credits exhausted) that are permanent
    until the user takes external action -- retrying won't help and the circuit
    breaker shouldn't open because of them.
    """


def _is_client_error(exc: Exception) -> bool:
    """Check if an exception represents an HTTP 4xx client error.

    Client errors (400, 401, 403, 404, 422) indicate OUR request is malformed
    or unauthorized — not that the service is down. These should NOT count as
    circuit breaker failures because retrying won't help and the service is
    actually healthy.
    """
    # httpx.HTTPStatusError has a .response.status_code
    if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        return 400 <= exc.response.status_code < 500
    # String-based detection as fallback
    msg = str(exc).lower()
    return "400 bad request" in msg or "401 " in msg or "403 " in msg or "404 " in msg or "422 " in msg


class AsyncCircuitBreaker:
    """Async circuit breaker with failure counting and automatic recovery."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
        excluded_exceptions: tuple[type[Exception], ...] = (),
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.excluded_exceptions = excluded_exceptions

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                return CircuitState.HALF_OPEN
        return self._state

    async def call(
        self,
        fn: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute `fn` through the circuit breaker."""
        current_state = self.state

        if current_state == CircuitState.OPEN:
            remaining = self.recovery_timeout - (time.monotonic() - self._last_failure_time)
            raise CircuitOpenError(self.name, max(0, remaining))

        try:
            result = await fn(*args, **kwargs)
        except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            if isinstance(exc, self.excluded_exceptions):
                raise
            # Don't count HTTP 4xx client errors as failures — they indicate
            # our request is malformed, not that the service is down.
            if _is_client_error(exc):
                raise
            await self._on_failure(exc)
            raise
        else:
            await self._on_success()
            return result

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state in (CircuitState.HALF_OPEN, CircuitState.CLOSED):
                self._failure_count = 0
                if self._state == CircuitState.HALF_OPEN:
                    logger.info("Circuit '%s' recovered → CLOSED", self.name)
                self._state = CircuitState.CLOSED

    async def _on_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning(
                    "Circuit '%s' probe failed → OPEN (recovery in %.0fs): %s",
                    self.name, self.recovery_timeout, exc,
                )
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    "Circuit '%s' threshold reached (%d/%d) → OPEN (recovery in %.0fs): %s",
                    self.name, self._failure_count, self.failure_threshold,
                    self.recovery_timeout, exc,
                )
            else:
                logger.info(
                    "Circuit '%s' failure %d/%d: %s",
                    self.name, self._failure_count, self.failure_threshold, exc,
                )

    def call_sync(
        self,
        fn: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute a synchronous `fn` through the circuit breaker."""
        current_state = self.state

        if current_state == CircuitState.OPEN:
            remaining = self.recovery_timeout - (time.monotonic() - self._last_failure_time)
            raise CircuitOpenError(self.name, max(0, remaining))

        try:
            result = fn(*args, **kwargs)
        except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            if isinstance(exc, self.excluded_exceptions):
                raise
            self._on_failure_sync(exc)
            raise
        else:
            self._on_success_sync()
            return result

    def _on_success_sync(self) -> None:
        if self._state in (CircuitState.HALF_OPEN, CircuitState.CLOSED):
            self._failure_count = 0
            if self._state == CircuitState.HALF_OPEN:
                logger.info("Circuit '%s' recovered → CLOSED", self.name)
            self._state = CircuitState.CLOSED

    def _on_failure_sync(self, exc: Exception) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.warning(
                "Circuit '%s' probe failed → OPEN (recovery in %.0fs): %s",
                self.name, self.recovery_timeout, exc,
            )
        elif self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "Circuit '%s' threshold reached (%d/%d) → OPEN (recovery in %.0fs): %s",
                self.name, self._failure_count, self.failure_threshold,
                self.recovery_timeout, exc,
            )
        else:
            logger.info(
                "Circuit '%s' failure %d/%d: %s",
                self.name, self._failure_count, self.failure_threshold, exc,
            )

    def reset(self) -> None:
        """Manually reset the circuit to CLOSED."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0


def exponential_backoff_with_jitter(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
) -> float:
    """Calculate delay with exponential backoff and full jitter.

    Formula: random(0, min(max_delay, base_delay * 2^attempt))
    """
    delay = min(max_delay, base_delay * (2 ** attempt))
    return random.uniform(0, delay)


# --- Shared circuit breaker instances ---

_bifrost_rerank = AsyncCircuitBreaker("bifrost-rerank", failure_threshold=3, recovery_timeout=60)
_bifrost_claims = AsyncCircuitBreaker("bifrost-claims", failure_threshold=3, recovery_timeout=60)
_bifrost_verify = AsyncCircuitBreaker(
    "bifrost-verify", failure_threshold=5, recovery_timeout=90,
    excluded_exceptions=(NonTransientError,),
)
_bifrost_synopsis = AsyncCircuitBreaker("bifrost-synopsis", failure_threshold=3, recovery_timeout=60)
_bifrost_memory = AsyncCircuitBreaker("bifrost-memory", failure_threshold=3, recovery_timeout=60)
_neo4j = AsyncCircuitBreaker("neo4j", failure_threshold=5, recovery_timeout=30)
_ollama = AsyncCircuitBreaker("ollama", failure_threshold=3, recovery_timeout=30)
_bifrost_compress = AsyncCircuitBreaker("bifrost-compress", failure_threshold=3, recovery_timeout=60)
_bifrost_decompose = AsyncCircuitBreaker("bifrost-decompose", failure_threshold=3, recovery_timeout=60)
_web_search = AsyncCircuitBreaker("web-search", failure_threshold=3, recovery_timeout=30)
_openrouter = AsyncCircuitBreaker("openrouter", failure_threshold=5, recovery_timeout=60)
_tavily = AsyncCircuitBreaker("tavily", failure_threshold=3, recovery_timeout=30)
_searxng = AsyncCircuitBreaker("searxng", failure_threshold=3, recovery_timeout=30)
_ragas_eval = AsyncCircuitBreaker("ragas_eval", failure_threshold=3, recovery_timeout=60)
_trading_agent = AsyncCircuitBreaker("trading-agent", failure_threshold=3, recovery_timeout=120)
_email_imap = AsyncCircuitBreaker("email-imap", failure_threshold=3, recovery_timeout=60)
_rss_feed = AsyncCircuitBreaker("rss-feed", failure_threshold=3, recovery_timeout=60)
_gmail = AsyncCircuitBreaker("gmail", failure_threshold=3, recovery_timeout=60)
_outlook = AsyncCircuitBreaker("outlook", failure_threshold=3, recovery_timeout=60)

# Dynamic per-stage Ollama breakers — lazily created for "ollama-{stage}" names.
_dynamic_ollama_breakers: dict[str, AsyncCircuitBreaker] = {}


def get_breaker(name: str) -> AsyncCircuitBreaker:
    """Get a named circuit breaker instance."""
    _breakers = {
        "bifrost-rerank": _bifrost_rerank,
        "bifrost-claims": _bifrost_claims,
        "bifrost-verify": _bifrost_verify,
        "bifrost-synopsis": _bifrost_synopsis,
        "bifrost-memory": _bifrost_memory,
        "bifrost-compress": _bifrost_compress,
        "bifrost-decompose": _bifrost_decompose,
        "web-search": _web_search,
        "openrouter": _openrouter,
        "tavily": _tavily,
        "searxng": _searxng,
        "ragas_eval": _ragas_eval,
        "neo4j": _neo4j,
        "ollama": _ollama,
        "trading-agent": _trading_agent,
        "email-imap": _email_imap,
        "rss-feed": _rss_feed,
        "gmail": _gmail,
        "outlook": _outlook,
    }
    breaker = _breakers.get(name)
    if breaker is not None:
        return breaker
    # Auto-create per-stage Ollama breakers (e.g. "ollama-claim_extraction")
    if name.startswith("ollama-"):
        if name not in _dynamic_ollama_breakers:
            _dynamic_ollama_breakers[name] = AsyncCircuitBreaker(
                name, failure_threshold=3, recovery_timeout=30,
            )
        return _dynamic_ollama_breakers[name]
    raise ValueError(f"Unknown circuit breaker: {name}")
