// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { MCP_BASE, mcpHeaders } from "./common"
import type { TrustScore } from "@/lib/types/trust-score"

/**
 * GET /observability/trust-score
 *
 * Returns the system-level TrustScore with component breakdown.
 * Pure presentation — does not affect retrieval, generation, or any
 * model decision. Score updates nightly.
 *
 * Phase E.5 / E.6 of the v0.92 plan. Preservation gate I14.
 */
export async function fetchTrustScore(): Promise<TrustScore> {
  const res = await fetch(`${MCP_BASE}/observability/trust-score`, {
    headers: mcpHeaders(),
  })
  if (!res.ok) {
    throw new Error(`Trust score fetch failed (${res.status})`)
  }
  return res.json() as Promise<TrustScore>
}
