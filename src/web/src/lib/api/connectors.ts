// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Connectors REST client — typed surface over /connectors registry endpoints.
 * Mirrors the Pydantic schemas in src/mcp/app/routers/connectors.py.
 */

import { mcpUrl, mcpHeaders } from "./common"

// Mirrors ConnectorStatus (connectors.py lines 137-149)
export interface ConnectorStatus {
  slug: string
  display_name: string
  feature_flag: string
  feature_enabled: boolean
  env_complete: boolean
  missing_env: string[]
  data_source_registered: boolean
  data_source_configured: boolean
  sibling_reachable: boolean | null
  sibling_circuit_open: boolean | null
  auth_kind: string
  instruction_doc: string
}

// Mirrors OAuthStartResponse (connectors.py lines 156-167)
export interface OAuthStartResponse {
  auth_kind: string
  auth_url?: string | null
  device_code?: string | null
  verification_uri?: string | null
  expires_in?: number | null
  settings_url?: string | null
  instructions: string
}

// Mirrors OAuthStatusResponse (connectors.py lines 169-172)
export interface OAuthStatusResponse {
  slug: string
  completed: boolean
  detail: string
}

// Mirrors DisconnectResponse (connectors.py lines 175-178)
export interface DisconnectResponse {
  slug: string
  cleared: boolean
  detail: string
}

export async function listConnectors(): Promise<ConnectorStatus[]> {
  const r = await fetch(mcpUrl("/connectors").toString(), { headers: mcpHeaders() })
  if (!r.ok) throw new Error(`listConnectors failed: ${r.status}`)
  return (await r.json()).connectors
}

export async function getConnector(slug: string): Promise<ConnectorStatus> {
  const r = await fetch(mcpUrl(`/connectors/${slug}`).toString(), { headers: mcpHeaders() })
  if (!r.ok) throw new Error(`getConnector failed: ${r.status}`)
  return r.json()
}

export async function startConnectorAuth(slug: string): Promise<OAuthStartResponse> {
  const r = await fetch(mcpUrl(`/connectors/${slug}/auth/start`).toString(), {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!r.ok) throw new Error(`startConnectorAuth failed: ${r.status}`)
  return r.json()
}

export async function getConnectorAuthStatus(slug: string): Promise<OAuthStatusResponse> {
  const r = await fetch(mcpUrl(`/connectors/${slug}/auth/status`).toString(), { headers: mcpHeaders() })
  if (!r.ok) throw new Error(`getConnectorAuthStatus failed: ${r.status}`)
  return r.json()
}

export async function disconnectConnector(slug: string): Promise<DisconnectResponse> {
  const r = await fetch(mcpUrl(`/connectors/${slug}/disconnect`).toString(), {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!r.ok) throw new Error(`disconnectConnector failed: ${r.status}`)
  return r.json()
}
