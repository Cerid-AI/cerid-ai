# Copyright 2026 Cerid AI. Apache-2.0 license.
"""Tests for circuit breaker: open/close/half-open states."""

import asyncio

import pytest

from utils.circuit_breaker import (
    AsyncCircuitBreaker,
    CircuitOpenError,
    CircuitState,
    _is_client_error,
)

# ---------------------------------------------------------------------------
# Tests: CircuitState enum
# ---------------------------------------------------------------------------

class TestCircuitState:
    def test_states_exist(self):
        assert CircuitState.CLOSED.value == "closed"
        assert CircuitState.OPEN.value == "open"
        assert CircuitState.HALF_OPEN.value == "half_open"


# ---------------------------------------------------------------------------
# Tests: _is_client_error helper
# ---------------------------------------------------------------------------

class TestIsClientError:
    def test_generic_exception_not_client_error(self):
        assert _is_client_error(ValueError("oops")) is False

    def test_400_string_detected(self):
        assert _is_client_error(RuntimeError("400 bad request")) is True

    def test_404_string_detected(self):
        assert _is_client_error(RuntimeError("404 not found")) is True


# ---------------------------------------------------------------------------
# Tests: AsyncCircuitBreaker state transitions
# ---------------------------------------------------------------------------

class TestAsyncCircuitBreaker:
    def test_initial_state_is_closed(self):
        cb = AsyncCircuitBreaker("test", failure_threshold=3, recovery_timeout=60)
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_success_stays_closed(self):
        cb = AsyncCircuitBreaker("test", failure_threshold=3)

        async def ok():
            return "ok"

        result = await cb.call(ok)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_failures_open_circuit(self):
        cb = AsyncCircuitBreaker("test", failure_threshold=2, recovery_timeout=60)

        async def fail():
            raise RuntimeError("server down")

        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(fail)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_open_circuit_rejects_calls(self):
        cb = AsyncCircuitBreaker("test", failure_threshold=1, recovery_timeout=60)

        async def fail():
            raise RuntimeError("down")

        with pytest.raises(RuntimeError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN

        with pytest.raises(CircuitOpenError) as exc_info:
            await cb.call(fail)
        assert exc_info.value.retry_after > 0

    @pytest.mark.asyncio
    async def test_half_open_after_timeout(self):
        cb = AsyncCircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)

        async def fail():
            raise RuntimeError("down")

        with pytest.raises(RuntimeError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN

        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_half_open_success_closes_circuit(self):
        cb = AsyncCircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)

        async def fail():
            raise RuntimeError("down")

        async def ok():
            return "recovered"

        with pytest.raises(RuntimeError):
            await cb.call(fail)

        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        result = await cb.call(ok)
        assert result == "recovered"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens(self):
        cb = AsyncCircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)

        async def fail():
            raise RuntimeError("still down")

        with pytest.raises(RuntimeError):
            await cb.call(fail)

        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        with pytest.raises(RuntimeError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_client_errors_not_counted(self):
        """4xx errors should not count toward circuit breaker failures."""
        cb = AsyncCircuitBreaker("test", failure_threshold=2, recovery_timeout=60)

        async def client_err():
            raise RuntimeError("400 bad request from upstream")

        for _ in range(5):
            with pytest.raises(RuntimeError):
                await cb.call(client_err)

        # Should still be closed since 4xx errors are not counted
        assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# Tests: External datasource breaker tuning
# ---------------------------------------------------------------------------


def test_external_datasource_breakers_are_tolerant():
    """Regression: audit found failure_threshold=1 + 120s recovery was too
    aggressive — one transient failure locked sources out for 2 minutes.

    New tuning: threshold=3, recovery=30s. The per-request 5s timeout
    already bounds hang cost, so tolerating a couple of flakes avoids
    punishing the user for transient network blips.
    """
    from utils.circuit_breaker import get_breaker
    for name in (
        "datasource-wikipedia",
        "datasource-duckduckgo",
        "datasource-wolfram_alpha",
        "datasource-exchange_rates",
        "datasource-openlibrary",
        "datasource-pubchem",
        "datasource-bookmarks",
        "datasource-email-imap",
        "datasource-rss_feeds",
    ):
        cb = get_breaker(name)
        assert cb.failure_threshold == 3, f"{name} threshold={cb.failure_threshold}"
        assert cb.recovery_timeout == 30, f"{name} recovery={cb.recovery_timeout}"
