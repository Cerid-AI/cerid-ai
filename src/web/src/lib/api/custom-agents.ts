// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { MCP_BASE, mcpHeaders, extractError } from "./common"

export interface CustomAgentDefinition {
  agent_id?: string
  name: string
  description?: string
  system_prompt: string
  tools?: string[]
  domains?: string[]
  rag_mode?: "smart" | "manual" | "off"
  model_override?: string | null
  temperature?: number
  metadata?: Record<string, unknown>
  template_id?: string | null
  created_at?: string
  updated_at?: string
}

export interface AgentTemplate {
  template_id: string
  name: string
  description: string
  system_prompt: string
  tools?: string[]
  domains?: string[]
  rag_mode?: string
  temperature?: number
}

export interface CustomAgentListResponse {
  agents: CustomAgentDefinition[]
  total: number
}

export interface CustomAgentTemplatesResponse {
  templates: AgentTemplate[]
}

/** Custom-agents endpoints — all return 403 when STRICT_AGENTS_ONLY=true. */

export async function listCustomAgents(): Promise<CustomAgentListResponse> {
  const res = await fetch(`${MCP_BASE}/custom-agents`, { headers: mcpHeaders() })
  if (!res.ok) {
    throw new Error(await extractError(res, `Failed to list custom agents (${res.status})`))
  }
  return res.json() as Promise<CustomAgentListResponse>
}

export async function listAgentTemplates(): Promise<CustomAgentTemplatesResponse> {
  const res = await fetch(`${MCP_BASE}/custom-agents/templates`, { headers: mcpHeaders() })
  if (!res.ok) {
    throw new Error(await extractError(res, `Failed to load agent templates (${res.status})`))
  }
  return res.json() as Promise<CustomAgentTemplatesResponse>
}

export async function createAgentFromTemplate(
  templateId: string,
  overrides?: Partial<CustomAgentDefinition>,
): Promise<CustomAgentDefinition> {
  const res = await fetch(
    `${MCP_BASE}/custom-agents/from-template/${encodeURIComponent(templateId)}`,
    {
      method: "POST",
      headers: mcpHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(overrides ?? {}),
    },
  )
  if (!res.ok) {
    throw new Error(
      await extractError(res, `Failed to create agent from template (${res.status})`),
    )
  }
  return res.json() as Promise<CustomAgentDefinition>
}

export async function deleteCustomAgent(agentId: string): Promise<void> {
  const res = await fetch(
    `${MCP_BASE}/custom-agents/${encodeURIComponent(agentId)}`,
    { method: "DELETE", headers: mcpHeaders() },
  )
  if (!res.ok) {
    throw new Error(await extractError(res, `Failed to delete agent (${res.status})`))
  }
}
