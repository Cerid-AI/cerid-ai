# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""REST endpoints for managing external MCP server connections.

Allows users to add, remove, and monitor external MCP servers whose
tools are merged into the agent pipeline alongside ``pkb_*`` tools.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/mcp-servers", tags=["MCP Client"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class MCPServerAddRequest(BaseModel):
    """Request to add a new external MCP server."""

    name: str = Field(..., pattern=r"^[a-z][a-z0-9_-]{1,30}$", description="Unique server name (lowercase, no spaces)")
    transport: str = Field(..., pattern=r"^(stdio|sse)$", description="Transport: stdio or sse")
    command: str = Field("", description="For stdio: executable (e.g. npx, python)")
    args: list[str] = Field(default_factory=list, description="For stdio: command arguments")
    env: dict[str, str] = Field(default_factory=dict, description="For stdio: environment variables")
    url: str = Field("", description="For sse: server URL")
    headers: dict[str, str] = Field(default_factory=dict, description="For sse: HTTP headers")


class MCPServerInfo(BaseModel):
    """Status info for a configured MCP server."""

    name: str
    transport: str
    enabled: bool = True
    status: str  # connected, disconnected, error
    error: str | None = None
    tool_count: int = 0
    tools: list[str] = Field(default_factory=list)


class MCPServerListResponse(BaseModel):
    """Response listing all configured MCP servers."""

    servers: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    total_tools: int = 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=MCPServerListResponse, summary="List MCP Servers")
def list_mcp_servers():
    """List all configured external MCP servers with their connection status."""
    from utils.mcp_client import mcp_client_manager

    servers = mcp_client_manager.list_servers()
    total_tools = sum(s.get("tool_count", 0) for s in servers)
    return MCPServerListResponse(
        servers=servers, total=len(servers), total_tools=total_tools,
    )


@router.post("", response_model=MCPServerInfo, summary="Add MCP Server", status_code=201)
async def add_mcp_server(req: MCPServerAddRequest):
    """Add and connect to a new external MCP server."""
    from utils.mcp_client import MCPServerConfig, mcp_client_manager

    cfg = MCPServerConfig(
        name=req.name,
        transport=req.transport,
        command=req.command,
        args=req.args,
        env=req.env,
        url=req.url,
        headers=req.headers,
    )
    mcp_client_manager.add_server(cfg)

    # Attempt to connect immediately
    await mcp_client_manager.connect_all()
    servers = mcp_client_manager.list_servers()
    info = next((s for s in servers if s["name"] == req.name), {})

    return MCPServerInfo(
        name=req.name,
        transport=req.transport,
        status=info.get("status", "disconnected"),
        error=info.get("error"),
        tool_count=info.get("tool_count", 0),
        tools=info.get("tools", []),
    )


@router.delete("/{name}", summary="Remove MCP Server")
def remove_mcp_server(name: str):
    """Remove an external MCP server and disconnect."""
    from utils.mcp_client import mcp_client_manager

    removed = mcp_client_manager.remove_server(name)
    if not removed:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    return {"status": "removed", "name": name}


@router.post("/{name}/reconnect", response_model=MCPServerInfo, summary="Reconnect MCP Server")
async def reconnect_mcp_server(name: str):
    """Reconnect to a specific MCP server."""
    from utils.mcp_client import mcp_client_manager

    await mcp_client_manager.reconnect(name)
    servers = mcp_client_manager.list_servers()
    info = next((s for s in servers if s["name"] == name), None)
    if not info:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")

    return MCPServerInfo(
        name=name,
        transport=info.get("transport", ""),
        status=info.get("status", "error"),
        error=info.get("error"),
        tool_count=info.get("tool_count", 0),
        tools=info.get("tools", []),
    )


@router.get("/{name}/tools", summary="List Server Tools")
def list_server_tools(name: str):
    """List all tools from a specific external MCP server."""
    from utils.mcp_client import mcp_client_manager

    servers = mcp_client_manager.list_servers()
    info = next((s for s in servers if s["name"] == name), None)
    if not info:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")

    # Return full tool definitions, not just names
    all_tools = mcp_client_manager.list_external_tools()
    prefix = f"ext_{name}_"
    server_tools = [t for t in all_tools if t["name"].startswith(prefix)]

    return {"server": name, "tools": server_tools, "total": len(server_tools)}
