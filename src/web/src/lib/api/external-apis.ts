// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * API client for the External APIs management surface (Phase API.1 + API.2).
 *
 * Backend routes:
 *   GET  /external-apis                → list all adapters
 *   GET  /external-apis/{slug}/health  → single adapter health check
 *   POST /external-apis/{slug}/enabled → toggle adapter on/off
 */

import { MCP_BASE, mcpHeaders, extractError } from "./common"
import type { ExternalAPISummary, ExternalAPIHealth } from "@/lib/types/external-apis"

// ---------------------------------------------------------------------------
// GET /external-apis
// ---------------------------------------------------------------------------

/**
 * Fetch the full catalogue of registered external API adapters.
 *
 * The backend always returns all 8 adapters (wikipedia, wikidata,
 * openlibrary, stackexchange, arxiv, github, packages, osm).
 */
export async function fetchExternalAPIs(): Promise<ExternalAPISummary[]> {
  const res = await fetch(`${MCP_BASE}/external-apis`, {
    headers: mcpHeaders(),
  })
  if (!res.ok) {
    const msg = await extractError(res, "Failed to fetch external APIs")
    throw new Error(msg)
  }
  const data = (await res.json()) as { adapters: ExternalAPISummary[]; total: number }
  return data.adapters
}

// ---------------------------------------------------------------------------
// GET /external-apis/{slug}/health
// ---------------------------------------------------------------------------

/**
 * Run the named adapter's health check.
 *
 * Returns `{ status: "ok" }` on success, `{ status: "error", detail }` on
 * failure — the call itself never throws; check `status`.
 */
export async function fetchExternalAPIHealth(slug: string): Promise<ExternalAPIHealth> {
  const res = await fetch(
    `${MCP_BASE}/external-apis/${encodeURIComponent(slug)}/health`,
    { headers: mcpHeaders() },
  )
  if (!res.ok) {
    const msg = await extractError(res, `Health check failed for ${slug}`)
    return { status: "error", detail: msg }
  }
  const data = (await res.json()) as { slug: string; status: string; detail?: string | null }
  return {
    status: (data.status === "ok" ? "ok" : "error") as "ok" | "error",
    detail: data.detail ?? null,
  }
}

// ---------------------------------------------------------------------------
// POST /external-apis/{slug}/enabled
// ---------------------------------------------------------------------------

/**
 * Toggle an adapter's enabled state.
 *
 * Returns `{ ok: true, enabled: boolean }` on success.
 * Throws on 404 (unknown slug) or 503 (Redis unavailable).
 */
export async function toggleExternalAPI(
  slug: string,
  enabled: boolean,
): Promise<{ ok: true; enabled: boolean }> {
  const res = await fetch(
    `${MCP_BASE}/external-apis/${encodeURIComponent(slug)}/enabled`,
    {
      method: "POST",
      headers: mcpHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ enabled }),
    },
  )
  if (!res.ok) {
    const msg = await extractError(res, `Failed to ${enabled ? "enable" : "disable"} ${slug}`)
    throw new Error(msg)
  }
  const data = (await res.json()) as { slug: string; enabled: boolean }
  return { ok: true, enabled: data.enabled }
}
