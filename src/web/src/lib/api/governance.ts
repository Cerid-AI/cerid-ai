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
