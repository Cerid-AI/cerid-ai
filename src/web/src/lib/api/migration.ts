// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Migration client — zip-upload import from Notion exports, consuming the
 * already-implemented `/api/migrate/*` backend (RA-41). Obsidian users have
 * a working on-ramp via the vault-semantics folder scan (Settings →
 * Knowledge); Notion users had no client for this endpoint at all.
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
