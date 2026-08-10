// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

// Community-edition entitlement API. Purchase happens on cerid.ai; activation
// and the self-serve trial are local and offline — nothing here leaves the
// machine, and no payment provider is contacted.

import { MCP_BASE, mcpHeaders, extractError } from "./common"

export type FeatureTier = "community" | "pro" | "enterprise"

export interface TrialState {
  available: boolean
  active: boolean
  days_remaining: number | null
  expires_at: number | null
}

export interface LicenseStatus {
  tier: FeatureTier
  active: boolean
  /** Where the entitlement came from: license_key | trial | env_override | default. */
  source: string
  key_masked: string | null
  expires_at: number | null
  trial: TrialState
  purchase_url: string
}

async function post(path: string, body?: unknown): Promise<LicenseStatus> {
  const res = await fetch(`${MCP_BASE}${path}`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await extractError(res, "Request failed"))
  return res.json()
}

export async function fetchLicenseStatus(): Promise<LicenseStatus> {
  const res = await fetch(`${MCP_BASE}/license/status`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch license status"))
  return res.json()
}

/** Validate and persist a purchased key. Rejects with the server's reason. */
export function activateLicense(key: string): Promise<LicenseStatus> {
  return post("/license/activate", { key })
}

/** Start the one-time 14-day Pro trial. Rejects with 409 if already used. */
export function startTrial(): Promise<LicenseStatus> {
  return post("/license/trial")
}

export function deactivateLicense(): Promise<{ status: string; tier: FeatureTier }> {
  return post("/license/deactivate") as unknown as Promise<{ status: string; tier: FeatureTier }>
}
