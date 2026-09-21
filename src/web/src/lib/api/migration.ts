// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Migration client — zip-upload import from Notion exports (RA-41). Obsidian
 * users have a working on-ramp via the vault-semantics folder scan
 * (Settings → Knowledge); Notion users had no client at all.
 *
 * `/api/migrate/*` is served by a Pro/Enterprise router that is not part of
 * the source-available distribution and is mounted only by the commercial
 * entrypoint, so whether the endpoints exist depends on the edition this
 * client is talking to. Call `isNotionMigrationAvailable()` before offering
 * the import.
 */

import { mcpUrl, mcpHeaders } from "./common"

export interface MigrationStartResponse {
  job_id: string
  pages_found: number
}

export interface MigrationStatusResponse {
  job_id: string
  status: "queued" | "processing" | "completed" | "unknown" | string
  total: number
  processed: number
  errors: number
}

/**
 * Does this server mount the migration router?
 *
 * Probed rather than assumed, the same way fetchCapabilities probes the
 * /billing vs /license edition split: a mounted POST-only route answers a
 * bare GET with 405 Method Not Allowed, an unmounted one with 404. Throws on
 * a transport failure so the caller can tell "no such endpoint" apart from
 * "could not ask".
 */
export async function isNotionMigrationAvailable(): Promise<boolean> {
  const r = await fetch(mcpUrl("/api/migrate/notion").toString(), {
    headers: mcpHeaders(),
  })
  return r.status !== 404
}

export async function migrateNotionExport(file: File): Promise<MigrationStartResponse> {
  const fd = new FormData()
  fd.append("file", file, file.name)
  const r = await fetch(mcpUrl("/api/migrate/notion").toString(), {
    method: "POST",
    headers: mcpHeaders(),
    body: fd,
  })
  if (!r.ok) {
    const text = await r.text()
    throw new Error(`Notion import failed: HTTP ${r.status}: ${text}`)
  }
  return r.json()
}

export async function fetchMigrationStatus(jobId: string): Promise<MigrationStatusResponse> {
  const r = await fetch(mcpUrl(`/api/migrate/status/${encodeURIComponent(jobId)}`).toString(), {
    headers: mcpHeaders(),
  })
  if (!r.ok) {
    const text = await r.text()
    throw new Error(`Migration status fetch failed: HTTP ${r.status}: ${text}`)
  }
  return r.json()
}
