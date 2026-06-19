// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Sources REST client — backs the gallery, FAB, wizard, and detail
 * pane. Mirrors the Pydantic schemas in src/mcp/app/routers/sources.py.
 */

import { mcpUrl, mcpHeaders } from "./common"

export type SourceAvailability = "available" | "oauth" | "coming_soon"

export interface SourceKindMeta {
  kind: string
  family: string
  tier: "core" | "pro"
  // Whether this kind is connectable via the add-source wizard. "available" →
  // has a SourceConnector (or webhook); "oauth" → connect via Settings →
  // Connectors; "coming_soon" → declared but not yet implemented. Backend
  // default for older payloads is "coming_soon".
  availability?: SourceAvailability
  // Recipe providers for webhook-backed kinds (chat_capture → slack/discord/
  // teams/matrix; dev_events → github/linear/sentry/stripe). The wizard renders
  // these as a required picker. Empty/absent for kinds with no provider choice.
  providers?: string[]
}

export interface SourceRecord {
  id: string
  kind: string
  family: string
  display_name: string
  tier: string
  status: string
  config: Record<string, unknown>
  sync_cursor: Record<string, unknown>
  total_artifacts: number
  total_chunks: number
  total_edges: number
  total_artifacts_24h: number
  connection_time_ms: number | null
  last_sync_at: string | null
  created_at: string | null
  last_error: string | null
  quality_floor?: number
}

export interface CreateSourceRequest {
  kind: string
  display_name: string
  config: Record<string, unknown>
}

export interface HealthProbeResult {
  ok: boolean
  detail: string
  last_error: string | null
}

export async function listSourceKinds(): Promise<SourceKindMeta[]> {
  const r = await fetch(mcpUrl("/sources/kinds").toString(), { headers: mcpHeaders() })
  if (!r.ok) throw new Error(`listSourceKinds failed: ${r.status}`)
  return r.json()
}

export async function listSources(kind?: string): Promise<SourceRecord[]> {
  const url = mcpUrl("/sources", { kind })
  const r = await fetch(url.toString(), { headers: mcpHeaders() })
  if (!r.ok) throw new Error(`listSources failed: ${r.status}`)
  return r.json()
}

export async function createSource(body: CreateSourceRequest): Promise<SourceRecord> {
  const r = await fetch(mcpUrl("/sources").toString(), {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  })
  if (!r.ok) {
    const text = await r.text()
    throw new Error(`createSource failed: HTTP ${r.status}: ${text}`)
  }
  return r.json()
}

export async function testSource(sourceId: string): Promise<HealthProbeResult> {
  const r = await fetch(mcpUrl(`/sources/${sourceId}/test`).toString(), { method: "POST", headers: mcpHeaders() })
  if (!r.ok) throw new Error(`testSource failed: ${r.status}`)
  return r.json()
}

export interface PolicyPatch {
  retention_policy?: { mode: "keep_all" } | { mode: "days"; days: number } | { mode: "count"; max: number }
  quality_floor?: number
}

export async function patchSourcePolicy(
  sourceId: string,
  patch: PolicyPatch,
): Promise<SourceRecord> {
  const r = await fetch(mcpUrl(`/sources/${sourceId}/policy`).toString(), {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(patch),
  })
  if (!r.ok) throw new Error(`patchSourcePolicy failed: ${r.status}`)
  return r.json()
}

export async function deleteSource(sourceId: string, cascade = false): Promise<void> {
  const url = mcpUrl(`/sources/${sourceId}`, { cascade: cascade ? "true" : undefined })
  const r = await fetch(url.toString(), { method: "DELETE", headers: mcpHeaders() })
  if (!r.ok) throw new Error(`deleteSource failed: ${r.status}`)
}
