// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Enterprise audit-log API client — reads the tamper-evident, hash-chained
// log of administrative and security actions. See docs/ENTERPRISE_AUDIT_LOG.md
// and src/mcp/app/routers/audit_log.py.

import { MCP_BASE, mcpHeaders, extractError } from "./common"

export interface AuditRecord {
  seq: number
  ts: string
  actor: string
  action: string
  target: string
  outcome: "success" | "failure" | "denied"
  detail: Record<string, unknown>
  prev: string
  hash: string
}

export interface AuditRecordsResponse {
  records: AuditRecord[]
  total: number
  limit: number
  offset: number
}

export interface AuditVerifyResponse {
  ok: boolean
  checked: number
  records: number
  broken_at: number | null
  reason: string | null
}

export async function fetchAuditRecords(params: {
  limit?: number
  offset?: number
  action_prefix?: string
  outcome?: "success" | "failure" | "denied"
} = {}): Promise<AuditRecordsResponse> {
  const qs = new URLSearchParams()
  qs.set("limit", String(params.limit ?? 50))
  qs.set("offset", String(params.offset ?? 0))
  if (params.action_prefix) qs.set("action_prefix", params.action_prefix)
  if (params.outcome) qs.set("outcome", params.outcome)
  const res = await fetch(`${MCP_BASE}/audit-log?${qs}`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch audit log"))
  return res.json()
}

export async function verifyAuditChain(): Promise<AuditVerifyResponse> {
  const res = await fetch(`${MCP_BASE}/audit-log/verify`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to verify audit log chain"))
  return res.json()
}
