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
        except Exception as exc:
            if isinstance(exc, self.excluded_exceptions):
                raise
            if _is_client_error(exc):
                raise
            await self._on_failure(exc)
            raise
        else:
            await self._on_success()
            return result

    async def _on_success(self) -> None:
        async with self._lock:
            # Use the computed state (which derives HALF_OPEN from OPEN + elapsed time)
            effective = self.state
            if effective in (CircuitState.HALF_OPEN, CircuitState.CLOSED):
                self._failure_count = 0
                if effective == CircuitState.HALF_OPEN:
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
        except Exception as exc:
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
    "bifrost-verify", failure_threshold=5, recovery_timeout=30,
    excluded_exceptions=(NonTransientError,),
)
_bifrost_synopsis = AsyncCircuitBreaker("bifrost-synopsis", failure_threshold=3, recovery_timeout=60)
_bifrost_memory = AsyncCircuitBreaker("bifrost-memory", failure_threshold=3, recovery_timeout=60)
_neo4j = AsyncCircuitBreaker("neo4j", failure_threshold=5, recovery_timeout=30)
_ollama = AsyncCircuitBreaker("ollama", failure_threshold=3, recovery_timeout=30)
_bifrost_compress = AsyncCircuitBreaker("bifrost-compress", failure_threshold=3, recovery_timeout=60)
_bifrost_decompose = AsyncCircuitBreaker("bifrost-decompose", failure_threshold=3, recovery_timeout=60)
_web_search = AsyncCircuitBreaker("web-search", failure_threshold=3, recovery_timeout=30)
_openrouter = AsyncCircuitBreaker("openrouter", failure_threshold=5, recovery_timeout=30)
_tavily = AsyncCircuitBreaker("tavily", failure_threshold=3, recovery_timeout=30)
_searxng = AsyncCircuitBreaker("searxng", failure_threshold=3, recovery_timeout=30)
_ragas_eval = AsyncCircuitBreaker("ragas_eval", failure_threshold=3, recovery_timeout=60)


def _is_client_error(exc: Exception) -> bool:
    """Return True when the exception looks like an HTTP 4xx client error.

    Checks the string representation for common 4xx status codes so the
    circuit breaker can distinguish client errors (which shouldn't count
    toward the failure threshold) from server errors.
    """
    import re

    text = str(exc)
    return bool(re.search(r"\b4\d{2}\b", text))


_chromadb = AsyncCircuitBreaker("chromadb", failure_threshold=5, recovery_timeout=30)

# External data-source breakers. Tuned for transient-tolerance:
# - failure_threshold=3: tolerate 2 flaky requests before opening. A single
#   timeout or DNS blip no longer locks the source out for minutes.
# - recovery_timeout=30: re-probe every 30s rather than punishing for 2 min.
# The per-request 5s timeout already bounds hang cost; audit RC-B showed the
# old (1, 120) tuning turned one transient flake into 2 full minutes of
# ext_count=0 / strategy=degraded_budget_exhausted responses.
_datasource_wikipedia = AsyncCircuitBreaker("datasource-wikipedia", failure_threshold=3, recovery_timeout=30)
_datasource_duckduckgo = AsyncCircuitBreaker("datasource-duckduckgo", failure_threshold=3, recovery_timeout=30)
_datasource_wolfram_alpha = AsyncCircuitBreaker("datasource-wolfram_alpha", failure_threshold=3, recovery_timeout=30)
_datasource_exchange_rates = AsyncCircuitBreaker("datasource-exchange_rates", failure_threshold=3, recovery_timeout=30)
_datasource_openlibrary = AsyncCircuitBreaker("datasource-openlibrary", failure_threshold=3, recovery_timeout=30)
_datasource_pubchem = AsyncCircuitBreaker("datasource-pubchem", failure_threshold=3, recovery_timeout=30)
_datasource_bookmarks = AsyncCircuitBreaker("datasource-bookmarks", failure_threshold=3, recovery_timeout=30)
_datasource_email_imap = AsyncCircuitBreaker("datasource-email-imap", failure_threshold=3, recovery_timeout=30)
_datasource_rss_feeds = AsyncCircuitBreaker("datasource-rss_feeds", failure_threshold=3, recovery_timeout=30)

_BREAKER_REGISTRY: dict[str, AsyncCircuitBreaker] = {
    "chromadb": _chromadb,
    "datasource-wikipedia": _datasource_wikipedia,
    "datasource-duckduckgo": _datasource_duckduckgo,
    "datasource-wolfram_alpha": _datasource_wolfram_alpha,
    "datasource-exchange_rates": _datasource_exchange_rates,
    "datasource-openlibrary": _datasource_openlibrary,
    "datasource-pubchem": _datasource_pubchem,
    "datasource-bookmarks": _datasource_bookmarks,
    "datasource-email-imap": _datasource_email_imap,
    "datasource-rss_feeds": _datasource_rss_feeds,
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
}


def get_breaker(name: str) -> AsyncCircuitBreaker:
    """Get a named circuit breaker instance, auto-creating for unknown names."""
    breaker = _BREAKER_REGISTRY.get(name)
    if breaker is None:
        breaker = AsyncCircuitBreaker(name, failure_threshold=3, recovery_timeout=60)
        _BREAKER_REGISTRY[name] = breaker
        logger.debug("Auto-created circuit breaker '%s' with default thresholds", name)
    return breaker
