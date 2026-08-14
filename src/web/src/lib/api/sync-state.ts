// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

import { MCP_BASE, mcpHeaders, extractError } from "./common"

/**
 * Shared per-connector sync/ingest state (sf-1 status truth). One backend
 * source of truth — the desktop connector cards, the Sources hero/ticker,
 * and the tray all read these same numbers. Backed by
 * GET /ingestion/sync-state; the same list is inlined into
 * GET /ingestion/progress as `connectors`.
 */
export interface ConnectorSyncState {
  connector: string
  /** Derived server-side: "stalled" is a syncing client gone silent. */
  state: "syncing" | "stalled" | "ingesting" | "error" | "idle"
  phase: string
  total: number
  scanned: number
  posted: number
  failed: number
  /** Lifetime artifacts the server actually ingested for this connector. */
  ingested_total: number
  deduped_total: number
  errored_total: number
  /** Artifacts ingested within the current sync window. */
  window_ingested: number
  rate_per_min: number | null
  eta_seconds: number | null
  window_started_at: string | null
  last_ingest_at: string | null
  updated_at: string | null
  last_error: string | null
}

export async function fetchSyncStates(): Promise<ConnectorSyncState[]> {
  const res = await fetch(`${MCP_BASE}/ingestion/sync-state`, {
    headers: mcpHeaders(),
  })
  if (!res.ok) {
    throw new Error(await extractError(res, "fetchSyncStates"))
  }
  const body = (await res.json()) as { connectors: ConnectorSyncState[] }
  return body.connectors ?? []
}

/** True when any connector is actively syncing or ingesting right now. */
export function anySyncActive(states: ConnectorSyncState[]): boolean {
  return states.some((s) => s.state === "syncing" || s.state === "ingesting")
}

/** Sum of live per-connector window rates (artifacts/min). */
export function liveRatePerMin(states: ConnectorSyncState[]): number {
  return (
    Math.round(
      states
        .filter((s) => s.state === "syncing" || s.state === "ingesting")
        .reduce((acc, s) => acc + (s.rate_per_min ?? 0), 0) * 10,
    ) / 10
  )
}
