# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""MCP client infrastructure for Pro connectors.

Phase 3 of the 2026-05-20 Pro Tier Implementation Plan. Cerid's
Pro tier consumes external MCP servers (Gmail/Calendar via
taylorwilsdon/google_workspace_mcp, Outlook via softeria/ms-365-mcp-server)
over **streamable HTTP transport** rather than stdio — stdio doesn't cross
Docker container boundaries.

Two layers:
  - ``http_client``: low-level wrapper around the official ``mcp`` PyPI
    client speaking streamable HTTP. Per-connection state, retry policy,
    timeout configuration.
  - ``client_pool``: per-connector singleton + circuit-breaker management.
"""
from core.mcp_clients.client_pool import MCPClientPool, get_pool
from core.mcp_clients.http_client import MCPHTTPClient

__all__ = ["MCPHTTPClient", "MCPClientPool", "get_pool"]
