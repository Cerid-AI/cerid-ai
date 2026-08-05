# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Pool + circuit-breaker management for MCP HTTP clients.

Per Phase 3 D2 of the 2026-05-20 Pro Tier Implementation Plan. Sits between
``MCPHTTPClient`` (raw transport) and ``MCPConnectorPlugin`` (high-level
DataSource wrapper). Responsibilities:

  - Singleton client per connector (avoid reconnecting on every query)
  - Circuit-breaker: open after 3 consecutive failures, cool-down 30s,
    half-open trial reconnect
  - Lazy connect on first call_tool
  - Reconnect-on-disconnect with bounded backoff

Same model as the existing ``app.data_sources`` circuit breaker, kept as a
separate primitive here because MCP clients are *core*-layer (no FastAPI
imports) and the existing breaker lives in ``app/``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from core.mcp_clients.http_client import MCPHTTPClient

logger = logging.getLogger("ai-companion.mcp_client_pool")

_FAILURE_THRESHOLD = 3
_COOL_DOWN_SECONDS = 30.0


@dataclass
class _ConnectorState:
    client: MCPHTTPClient
    failures: int = 0
    opened_at: float | None = None  # circuit-breaker open timestamp

    def is_open(self) -> bool:
        """Circuit is open while cool-down period hasn't elapsed."""
        if self.opened_at is None:
            return False
        return (time.monotonic() - self.opened_at) < _COOL_DOWN_SECONDS

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= _FAILURE_THRESHOLD:
            self.opened_at = time.monotonic()
            logger.warning(
                "Circuit breaker OPEN for MCP connector %s after %d failures",
                self.client.connector_name, self.failures,
            )

    def record_success(self) -> None:
        if self.failures > 0 or self.opened_at is not None:
            logger.info(
                "Circuit breaker RECOVERED for MCP connector %s",
                self.client.connector_name,
            )
        self.failures = 0
        self.opened_at = None


class MCPClientPool:
    """Per-connector MCP client pool with circuit-breaker semantics.

    Use ``get_pool()`` to access the singleton (process-wide). Manual
    construction is only useful in tests.
    """

    def __init__(self) -> None:
        self._connectors: dict[str, _ConnectorState] = {}
        self._lock = asyncio.Lock()

    def register(
        self,
        connector_name: str,
        url: str,
        *,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Register a connector. Doesn't connect — lazy on first call_tool.

        ``headers`` adds per-connector auth (e.g. ``{"Authorization":
        "Bearer ..."}`` for the sibling Pro-tier MCP servers).
        """
        if connector_name in self._connectors:
            logger.debug("MCP connector %s already registered, ignoring", connector_name)
            return
        client = MCPHTTPClient(
            url=url,
            timeout=timeout,
            connector_name=connector_name,
            headers=headers,
        )
        self._connectors[connector_name] = _ConnectorState(client=client)
        logger.info("MCP connector registered: %s @ %s", connector_name, url)

    def is_registered(self, connector_name: str) -> bool:
        return connector_name in self._connectors

    def list_connectors(self) -> list[dict[str, Any]]:
        """Operator-facing health snapshot — surface via /health.connectors."""
        return [
            {
                "name": name,
                "url": state.client.url,
                "failures": state.failures,
                "circuit_open": state.is_open(),
            }
            for name, state in self._connectors.items()
        ]

    async def call_tool(
        self,
        connector_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Dispatch a tool call to the named connector.

        - If the circuit is open, returns ``None`` immediately (caller
          handles graceful degradation; raising would mask the breaker).
        - If the connector isn't registered, raises ``KeyError``.
        - On transport error, records failure and re-raises.
        - On success, resets failure counter.
        """
        state = self._connectors.get(connector_name)
        if state is None:
            raise KeyError(f"MCP connector not registered: {connector_name}")
        if state.is_open():
            logger.debug(
                "MCP call %s.%s short-circuited (breaker open)",
                connector_name, tool_name,
            )
            return None

        try:
            result = await state.client.call_tool(tool_name, arguments)
            state.record_success()
            return result
        except (ValueError, OSError, RuntimeError, ConnectionError) as exc:
            state.record_failure()
            logger.warning(
                "MCP call %s.%s failed: %s (failures=%d)",
                connector_name, tool_name, exc, state.failures,
            )
            raise

    async def disconnect_all(self) -> None:
        """Cleanly close every connector. Called from FastAPI lifespan
        shutdown hook."""
        for name, state in self._connectors.items():
            try:
                await state.client.disconnect()
            except (ValueError, OSError, RuntimeError) as exc:
                logger.warning("Failed to disconnect MCP connector %s: %s", name, exc)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------
_pool: MCPClientPool | None = None


def get_pool() -> MCPClientPool:
    """Process-wide MCPClientPool singleton."""
    global _pool
    if _pool is None:
        _pool = MCPClientPool()
    return _pool


def reset_pool_for_tests() -> None:
    """Test-only: drop the singleton so each test starts fresh."""
    global _pool
    _pool = None
