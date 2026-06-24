// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Client for GET /graph/domains — per-domain taxonomy aggregate.
 * Returns entity counts, icons, and the derived_at timestamp that
 * drives degraded-state detection across all domain-aware surfaces.
 */

import { mcpUrl, mcpHeaders } from "./common"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface DomainSubCategory {
  name: string
  artifact_count: number
  entity_count: number
}

export interface DomainCount {
  name: string
  /** Lucide kebab-name, or null for runtime-minted domains (e.g. "research"). */
  icon: string | null
  description: string | null
  /** Whether this domain is declared in the backend TAXONOMY config. */
  in_taxonomy: boolean
  artifact_count: number
  entity_count: number
  /**
   * Corpus-level salience mass (Slice 6.2). The response is ordered by this
   * (salience desc), so domain cards reflect the salience model — ambient
   * domains sink, distinctive ones rise — rather than raw entity counts.
   * 0 until DeriveDomainsJob runs.
   */
  salience: number
  sub_categories: DomainSubCategory[]
}

export interface DomainCountsResponse {
  domains: DomainCount[]
  /** Entities with no primary_domain (orphans — no MENTIONS path). */
  uncategorized_entities: number
  /** ISO-8601 timestamp of the last derivation job run; null = job has never run. */
  derived_at: string | null
}

// ---------------------------------------------------------------------------
// Fetch
// ---------------------------------------------------------------------------

export async function fetchDomainCounts(
  opts: { includeInternal?: boolean } = {},
): Promise<DomainCountsResponse> {
  const url = mcpUrl("/graph/domains")
  if (opts.includeInternal) url.searchParams.set("include_internal", "true")
  const res = await fetch(url.toString(), { headers: mcpHeaders() })
  if (!res.ok) {
    throw new Error(`Domain counts fetch failed (${res.status})`)
  }
  const raw = (await res.json()) as Record<string, unknown>
  return normalizeDomainCountsResponse(raw)
}

// ---------------------------------------------------------------------------
// Normalizer
// ---------------------------------------------------------------------------

function normalizeDomainSubCategory(raw: Record<string, unknown>): DomainSubCategory {
  return {
    name: String(raw.name ?? ""),
    artifact_count: Number(raw.artifact_count ?? 0),
    entity_count: Number(raw.entity_count ?? 0),
  }
}

function normalizeDomainCount(raw: Record<string, unknown>): DomainCount {
  const subCats = Array.isArray(raw.sub_categories)
    ? (raw.sub_categories as Record<string, unknown>[]).map(normalizeDomainSubCategory)
    : []
  return {
    name: String(raw.name ?? ""),
    icon: raw.icon != null ? String(raw.icon) : null,
    description: raw.description != null ? String(raw.description) : null,
    in_taxonomy: Boolean(raw.in_taxonomy),
    artifact_count: Number(raw.artifact_count ?? 0),
    entity_count: Number(raw.entity_count ?? 0),
    salience: Number(raw.salience ?? 0),
    sub_categories: subCats,
  }
}

function normalizeDomainCountsResponse(raw: Record<string, unknown>): DomainCountsResponse {
  const domains = Array.isArray(raw.domains)
    ? (raw.domains as Record<string, unknown>[]).map(normalizeDomainCount)
    : []
  return {
    domains,
    uncategorized_entities: Number(raw.uncategorized_entities ?? 0),
    derived_at: raw.derived_at != null ? String(raw.derived_at) : null,
  }
}
