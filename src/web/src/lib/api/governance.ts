// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { MCP_BASE, mcpHeaders, extractError } from "./common"

export interface McpServerInfo {
  name: string
  transport: string
  enabled?: boolean
  status: string
  error?: string | null
  tool_count?: number
  tools?: string[]
}

export interface McpServerListResponse {
  servers: McpServerInfo[]
  total: number
  total_tools: number
}

/** GET /mcp-servers — list configured external MCP servers + their connection status. */
export async function fetchMcpServers(): Promise<McpServerListResponse> {
  const res = await fetch(`${MCP_BASE}/mcp-servers`, { headers: mcpHeaders() })
  if (!res.ok) {
    throw new Error(await extractError(res, `Failed to load MCP servers (${res.status})`))
  }
  return res.json() as Promise<McpServerListResponse>
}

/**
 * Add-server request body. ``transport`` is "stdio" (local subprocess via
 * ``command`` + ``args`` + ``env``) or "sse" (remote HTTP via ``url`` +
 * ``headers``). Backend validates that name matches ``[a-z][a-z0-9_-]{1,30}``.
 */
export interface McpServerAddRequest {
  name: string
  transport: "stdio" | "sse"
  command?: string
  args?: string[]
  env?: Record<string, string>
  url?: string
  headers?: Record<string, string>
}

/** POST /mcp-servers — register + auto-connect a new external MCP server. */
export async function addMcpServer(req: McpServerAddRequest): Promise<McpServerInfo> {
  const res = await fetch(`${MCP_BASE}/mcp-servers`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(req),
  })
  if (!res.ok) {
    throw new Error(await extractError(res, `Failed to add MCP server (${res.status})`))
  }
  return res.json() as Promise<McpServerInfo>
}

/** DELETE /mcp-servers/{name} — disconnect + remove a configured server. */
export async function deleteMcpServer(name: string): Promise<void> {
  const res = await fetch(`${MCP_BASE}/mcp-servers/${encodeURIComponent(name)}`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!res.ok) {
    throw new Error(await extractError(res, `Failed to remove MCP server (${res.status})`))
  }
}

/** POST /mcp-servers/{name}/reconnect — re-establish session + re-discover tools. */
export async function reconnectMcpServer(name: string): Promise<McpServerInfo> {
  const res = await fetch(
    `${MCP_BASE}/mcp-servers/${encodeURIComponent(name)}/reconnect`,
    { method: "POST", headers: mcpHeaders() },
  )
  if (!res.ok) {
    throw new Error(await extractError(res, `Failed to reconnect MCP server (${res.status})`))
  }
  return res.json() as Promise<McpServerInfo>
}
