// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

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

/**
 * Per-feature entitlement map, from whichever edition this build talks to.
 *
 * The commercial server serves it under /billing; the community server has no
 * billing router and serves the same shape under /license. Probing rather than
 * hardcoding keeps one client working against both — and the fallback is not
 * cosmetic: without it every Pro surface in the community build renders locked
 * forever, including for a customer who activated a real key.
 */
// Which edition this server is, once discovered. A server does not change
// edition under a running tab, so probing once spares the community build a
// wasted 404 on every refetch.
let _capabilitiesPath: string | null = null

export async function fetchCapabilities(): Promise<CapabilitiesResponse> {
  if (_capabilitiesPath) {
    const cached = await fetch(`${MCP_BASE}${_capabilitiesPath}`, { headers: mcpHeaders() })
    if (cached.ok) return cached.json()
    // Re-probe next call rather than pinning a path that has stopped working.
    _capabilitiesPath = null
    throw new Error(await extractError(cached, "Failed to fetch capabilities"))
  }

  const res = await fetch(`${MCP_BASE}/billing/capabilities`, { headers: mcpHeaders() })
  if (res.ok) {
    _capabilitiesPath = "/billing/capabilities"
    return res.json()
  }
  // Only a missing route means "wrong edition" — a 500 or a 403 is a real
  // failure on the endpoint that does exist, and must not be retried elsewhere.
  if (res.status !== 404) {
    throw new Error(await extractError(res, "Failed to fetch capabilities"))
  }

  const community = await fetch(`${MCP_BASE}/license/capabilities`, { headers: mcpHeaders() })
  if (!community.ok) {
    throw new Error(await extractError(community, "Failed to fetch capabilities"))
  }
  _capabilitiesPath = "/license/capabilities"
  return community.json()
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
