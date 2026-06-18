// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { MCP_BASE, mcpHeaders, extractError } from "./common"

export type FeatureTier = "community" | "pro" | "enterprise"

export interface FeatureDetail {
  enabled: boolean
  tier_required: FeatureTier
}

/**
 * Response of `GET /billing/capabilities`. The flat `features` map is the
 * authoritative source for the settings Pro pane — it covers every flag,
 * including non-bucketed Community/Enterprise ones that `buckets` omits.
 */
export interface CapabilitiesResponse {
  tier: FeatureTier
  features: Record<string, FeatureDetail>
  buckets: Record<string, unknown>
}

export async function fetchCapabilities(): Promise<CapabilitiesResponse> {
  const res = await fetch(`${MCP_BASE}/billing/capabilities`, { headers: mcpHeaders() })
  if (!res.ok) throw new Error(await extractError(res, "Failed to fetch capabilities"))
  return res.json()
}

/**
 * Open a Stripe Customer Portal session. Returns the hosted portal URL the
 * caller should navigate to (manage payment method, invoices, cancellation).
 * Throws on failure (404 when no subscription is on record, 503 when Stripe
 * is unconfigured).
 */
export async function openBillingPortal(
  opts: { returnUrl?: string } = {},
): Promise<string> {
  const returnUrl = opts.returnUrl ?? (typeof window !== "undefined" ? window.location.href : "")
  const res = await fetch(`${MCP_BASE}/billing/portal`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ return_url: returnUrl }),
  })
  if (!res.ok) throw new Error(await extractError(res, "Failed to open billing portal"))
  const data = await res.json()
  return data.portal_url as string
}
