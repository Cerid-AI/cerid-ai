// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Workflow builder metadata — node-type / agent catalog for tooltips and the
// node detail panel. CRUD + run helpers live in ./settings (§ Workflows).
import { MCP_BASE, mcpHeaders, extractError } from "./common"

export interface WorkflowNodeTypeInfo {
  type: string
  label: string
  description: string
  inputs: string
  outputs: string
  config_schema_summary: string | null
}

export interface WorkflowAgentInfo {
  name: string
  description: string
  inputs: string
  outputs: string
}

export interface WorkflowNodeCatalog {
  node_types: WorkflowNodeTypeInfo[]
  agents: WorkflowAgentInfo[]
}

export async function fetchWorkflowNodeTypes(): Promise<WorkflowNodeCatalog> {
  const res = await fetch(`${MCP_BASE}/workflows/node-types`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, `Fetch workflow node types failed: ${res.status}`))
  return res.json()
}
