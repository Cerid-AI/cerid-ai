// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Setup-wizard API helpers beyond the legacy first-run calls in
 * `settings.ts`:
 *
 *  - force-aware `/setup/configure` that surfaces the backend's 409
 *    already-configured guard as a structured result instead of a thrown
 *    string (beta triage 2026-07-12 P0-B4)
 *  - server-side onboarding-complete flag
 *  - async knowledge-pack install kickoff (202 job contract with a legacy
 *    synchronous-200 fallback)
 *
 * Intentionally NOT re-exported from the `lib/api` barrel — the wizard
 * imports this module directly, mirroring how `sample-pack-tab` imports
 * `lib/api/knowledge-packs`.
 */

import { MCP_BASE, mcpHeaders, extractError } from "./common"
import type { SetupConfig } from "../types"

// ---------------------------------------------------------------------------
// Configure (force-aware)
// ---------------------------------------------------------------------------

export interface ApplySetupResult {
  success: boolean
  /** True when the backend refused with 409 "already configured". */
  conflict?: boolean
  error?: string
}

// Mirrors the ConfigureRequest field names on the backend.
const KEY_FIELD_MAP: Record<string, string> = {
  openrouter: "openrouter_api_key",
  openai: "openai_api_key",
  anthropic: "anthropic_api_key",
  xai: "xai_api_key",
  neo4j: "neo4j_password",
}

/**
 * Apply the wizard configuration. Unlike `applySetupConfig` (settings.ts)
 * this variant can send `force: true` and reports the backend's 409
 * already-configured response as `{ success: false, conflict: true }` so the
 * wizard can render it distinctly from a connection failure.
 */
export async function applySetupConfiguration(
  config: SetupConfig,
  opts: { force?: boolean } = {},
): Promise<ApplySetupResult> {
  const payload: Record<string, unknown> = {}

  if (config.keys) {
    for (const [provider, value] of Object.entries(config.keys)) {
      const field = KEY_FIELD_MAP[provider.toLowerCase()]
      if (field) payload[field] = value
    }
  }
  if (config.archive_path !== undefined) payload.archive_path = config.archive_path
  if (config.domains !== undefined) payload.domains = config.domains
  if (config.lightweight_mode !== undefined) payload.lightweight_mode = config.lightweight_mode
  if (config.watch_folder !== undefined) payload.watch_folder = config.watch_folder
  if (config.ollama_enabled !== undefined) payload.ollama_enabled = config.ollama_enabled
  if (config.ollama_model !== undefined) payload.ollama_model = config.ollama_model
  if (opts.force) payload.force = true

  const res = await fetch(`${MCP_BASE}/setup/configure`, {
    method: "POST",
    headers: mcpHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  })
  if (res.status === 409) {
    return {
      success: false,
      conflict: true,
      error: await extractError(res, "This instance is already configured."),
    }
  }
  if (!res.ok) throw new Error(await extractError(res, `Setup configure failed: ${res.status}`))
  return res.json()
}

// ---------------------------------------------------------------------------
// Onboarding flag
// ---------------------------------------------------------------------------

/**
 * Persist the server-side onboarding-complete flag. The backend is the
 * source of truth for whether the first-run wizard should show —
 * localStorage remains only a client cache.
 */
export async function completeOnboarding(): Promise<{ onboarding_complete: boolean }> {
  const res = await fetch(`${MCP_BASE}/setup/onboarding-complete`, {
    method: "POST",
    headers: mcpHeaders(),
  })
  if (!res.ok) throw new Error(await extractError(res, `Onboarding flag save failed: ${res.status}`))
  return res.json()
}

// ---------------------------------------------------------------------------
// Async knowledge-pack install
// ---------------------------------------------------------------------------

export type PackInstallStartStatus = "queued" | "already_installed" | "installed"

export interface PackInstallStart {
  status: PackInstallStartStatus
  jobId: string | null
}

/**
 * Kick off a knowledge-pack install.
 *
 * New backends respond `202 {"job_id", "status": "queued"}` (install runs as
 * an async processor job — poll the registry's `installing`/`installed`
 * flags) or `200 {"status": "already_installed"}`. Older backends respond
 * 200 with the synchronous `InstallKnowledgePackResponse` body (no `status`
 * field) — treated as an immediate `"installed"`.
 */
export async function startPackInstall(packId: string): Promise<PackInstallStart> {
  const res = await fetch(
    `${MCP_BASE}/knowledge_packs/${encodeURIComponent(packId)}/install`,
    { method: "POST", headers: mcpHeaders() },
  )
  if (!res.ok) throw new Error(await extractError(res, `Install failed: ${res.status}`))

  let body: Record<string, unknown> = {}
  try {
    body = await res.json()
  } catch {
    // tolerate empty/non-JSON bodies from older backends
  }
  if (res.status === 202) {
    return { status: "queued", jobId: typeof body.job_id === "string" ? body.job_id : null }
  }
  if (body.status === "already_installed") return { status: "already_installed", jobId: null }
  return { status: "installed", jobId: null }
}
