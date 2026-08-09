// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Types for the External APIs management surface (Phase API.1 + API.2).
 *
 * Mirrors the Pydantic models in:
 *   src/mcp/app/routers/external_apis.py
 */

/** A single external API adapter as returned by GET /external-apis. */
export interface ExternalAPISummary {
  /** Unique adapter identifier, e.g. "wikipedia". */
  slug: string
  /** Human-readable name, e.g. "Wikipedia". */
  display_name: string
  /** Whether the adapter is currently enabled (Redis-persisted). */
  enabled: boolean
  /** True when the adapter requires an operator-supplied API key. */
  requires_key: boolean
  /**
   * True when the adapter's required key env var is non-empty.
   * Always true for keyless adapters.
   */
  key_configured: boolean
}

/** Response for GET /external-apis/{slug}/health. */
export interface ExternalAPIHealth {
  status: "ok" | "error"
  detail?: string | null
}
