// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Adaptive recommendation API — Cycle 3.2 (v0.93.3).
 *
 * Two endpoints back the dismissable banner in the Settings pane:
 *
 *  - POST /settings/recommendations/{id}/dismiss
 *      Permanently dismiss the rec for this tenant (server-side state).
 *  - DELETE /settings/recommendations/{id}
 *      Clear the rec from the active hash + drop the per-tenant
 *      dismissal record. Called after "Enable now" succeeds so the
 *      banner closes immediately rather than waiting for the next
 *      6-hour recommender tick.
 *
 * The /health endpoint is the read side — it surfaces the live
 * recommendations as `recommended_features` and the banner polls it
 * every 60 s via TanStack Query.
 */

import { MCP_BASE, mcpHeaders, extractError } from "./common"

export async function dismissRecommendation(id: string): Promise<void> {
  const res = await fetch(`${MCP_BASE}/settings/recommendations/${encodeURIComponent(id)}/dismiss`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok && res.status !== 204) {
    throw new Error(await extractError(res, `Dismiss failed (${res.status})`))
  }
}

export async function clearRecommendation(id: string): Promise<void> {
  const res = await fetch(`${MCP_BASE}/settings/recommendations/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: mcpHeaders(),
  })
  if (!res.ok && res.status !== 204) {
    throw new Error(await extractError(res, `Clear failed (${res.status})`))
  }
}
