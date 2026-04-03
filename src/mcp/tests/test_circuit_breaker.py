# Copyright 2026 Cerid AI. Apache-2.0 license.
"""Tests for circuit breaker: open/close/half-open states."""

import asyncio
import time

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
