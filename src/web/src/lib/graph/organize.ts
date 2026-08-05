// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2

/**
 * Pure domain-grouping utilities for DOM list surfaces (search palette + wiki).
 * No DOM, no React, no side effects — safe in unit tests and server contexts.
 */

import type { EntitySummary } from "@/lib/types/wiki"
import type { DomainCount } from "@/lib/api/domains"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface DomainSection {
  /** Domain name, or null for the "Other" catch-all section. */
  domain: string | null
  /** Title-cased display label (e.g. "Research", "Other"). */
  label: string
  /** Lucide kebab-name for the section icon, or null for fallback. */
  icon: string | null
  /** Total entity count for this domain (from /graph/domains, or derived from entities). */
  count: number
  /** Entities visible in this section (may be capped). */
  entities: EntitySummary[]
  /**
   * How many entities in this domain are hidden by the per-section cap.
   * 0 means all entities are shown.
   */
  overflow: number
}

/**
 * A flat item in the keyboard-nav array. Headers are included so callers can
 * render them, but are marked `isHeader: true` so arrow-key nav skips them.
 */
export type FlatItem =
  | { isHeader: true; sectionIndex: number; domain: string | null; label: string }
  | { isHeader: false; sectionIndex: number; itemIndex: number; entity: EntitySummary }

export interface OrganizeResult {
  sections: DomainSection[]
  /**
   * Pre-flattened array for keyboard navigation.
   * Section headers are included (`isHeader: true`) but arrow keys skip them.
   * Only populated when `sections.length > 1` (headerless collapse returns empty).
   */
  flatItems: FlatItem[]
  /**
   * True when there is only one section and it should render without a header
   * (byte-identical to pre-backbone flat list per A7).
   */
  headerless: boolean
}

export interface OrganizeOptions {
  /**
   * Per-section cap: max entities shown per domain section.
   * Default: no cap (undefined = show all).
   */
  cap?: number
  /**
   * Optional set of entity slugs to exclude from domain sections
   * (used to de-dup Best Matches in the palette — see organizeWithPinned).
   */
  excludeSlugs?: Set<string>
}

// ---------------------------------------------------------------------------
// titleCase — produce display label from domain name
// ---------------------------------------------------------------------------

function titleCase(s: string): string {
  return s
    .replace(/_/g, " ")
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

// ---------------------------------------------------------------------------
// organizeByDomain
//
// Groups entities by primary_domain, ordered by the entity_count in the
// /graph/domains response (passed in as domainIndex). Null-domain entities
// land in the "Other" section, always last.
//
// domainIndex is an ordered array from /graph/domains (entity_count desc).
// If domainIndex is empty or null (pre-job state), sections are ordered by
// their own entity count — same degraded behavior, no new state.
// ---------------------------------------------------------------------------

export function organizeByDomain(
  entities: EntitySummary[],
  domainIndex: DomainCount[],
  opts: OrganizeOptions = {},
): OrganizeResult {
  const { cap, excludeSlugs } = opts

  // Build a slug → excluded map for O(1) lookup
  const excluded = excludeSlugs ?? new Set<string>()

  // Partition entities into per-domain buckets
  const buckets = new Map<string | null, EntitySummary[]>()

  for (const entity of entities) {
    if (excluded.has(entity.slug)) continue
    const key = entity.primary_domain ?? null
    const existing = buckets.get(key)
    if (existing) {
      existing.push(entity)
    } else {
      buckets.set(key, [entity])
    }
  }

  // Build an ordered domain list from domainIndex, keeping only domains
  // that have entities in this result set. null (Other) is appended last.
  const orderedKeys: (string | null)[] = []

  // Walk the count-ordered index first
  for (const dc of domainIndex) {
    if (buckets.has(dc.name)) {
      orderedKeys.push(dc.name)
    }
  }

  // Any domain in buckets that wasn't in domainIndex (runtime-minted, pre-job)
  // — ordered by descending entity count, then name for stability.
  const unindexed = [...buckets.keys()]
    .filter((k) => k !== null && !orderedKeys.includes(k))
    .sort((a, b) => {
      const ca = buckets.get(a!)?.length ?? 0
      const cb = buckets.get(b!)?.length ?? 0
      if (cb !== ca) return cb - ca
      return String(a).localeCompare(String(b))
    })
  orderedKeys.push(...unindexed)

  // null (Other) always last
  if (buckets.has(null)) {
    orderedKeys.push(null)
  }

  // Build DomainSection array
  const sections: DomainSection[] = orderedKeys.map((key) => {
    const all = buckets.get(key) ?? []
    const domainMeta = key !== null ? domainIndex.find((d) => d.name === key) : undefined
    const label = key === null ? "Other" : titleCase(key)
    const icon = domainMeta?.icon ?? null

    // The count is the total for this domain across the full corpus (from the
    // index), not just the entities in this result set. This keeps section
    // headers honest even when the palette caps results.
    // If the domain isn't in the index (runtime-minted / pre-job), use the
    // entity count in this result set.
    const count = domainMeta?.entity_count ?? all.length

    const visible = cap !== undefined && all.length > cap ? all.slice(0, cap) : all
    const overflow = all.length - visible.length

    return { domain: key, label, icon, count, entities: visible, overflow }
  })

  const headerless = sections.length <= 1

  // Build flatItems only when there are multiple sections
  const flatItems: FlatItem[] = []
  if (!headerless) {
    sections.forEach((section, si) => {
      flatItems.push({ isHeader: true, sectionIndex: si, domain: section.domain, label: section.label })
      section.entities.forEach((entity, ii) => {
        flatItems.push({ isHeader: false, sectionIndex: si, itemIndex: ii, entity })
      })
    })
  }

  return { sections, flatItems, headerless }
}

// ---------------------------------------------------------------------------
// organizeWithPinned
//
// Palette variant: prepends a "Best Matches" pinned section (first `pinCount`
// entities in server relevance order), then organizes remaining entities
// into domain sections. The de-dup rule: when total results ≤ dedupeThreshold,
// entities appearing in Best Matches are not duplicated in their domain section.
// ---------------------------------------------------------------------------

export interface PinnedSection {
  entities: EntitySummary[]
}

export interface OrganizeWithPinnedResult {
  pinned: PinnedSection
  rest: OrganizeResult
}

export function organizeWithPinned(
  entities: EntitySummary[],
  domainIndex: DomainCount[],
  opts: OrganizeOptions & {
    /** Number of entities to pin in "Best Matches". Default: 5. */
    pinCount?: number
    /** Max results below which de-dup applies. Default: 10. */
    dedupeThreshold?: number
  } = {},
): OrganizeWithPinnedResult {
  const { pinCount = 5, dedupeThreshold = 10, cap, excludeSlugs } = opts

  const pinned = entities.slice(0, pinCount)
  const deDup = entities.length <= dedupeThreshold
  const excludeSlugsForRest = new Set<string>([
    ...(excludeSlugs ?? []),
    ...(deDup ? pinned.map((e) => e.slug) : []),
  ])

  const rest = organizeByDomain(entities, domainIndex, { cap, excludeSlugs: excludeSlugsForRest })

  return { pinned: { entities: pinned }, rest }
}

// ---------------------------------------------------------------------------
// flatItems — extract the navigable items array (alias for external callers)
//
// Returns the FlatItem array from an OrganizeResult. Callers can iterate
// and skip `isHeader` entries for arrow-key navigation.
// ---------------------------------------------------------------------------

export function flatItems(result: OrganizeResult): FlatItem[] {
  return result.flatItems
}

// ---------------------------------------------------------------------------
// Tag filter (Slice 6.3) — pure helpers for the entity-list tag filter bar.
// ---------------------------------------------------------------------------

/** Collect the union of entity top_tags, ordered by frequency desc then name
 *  asc — the chips shown in the filter bar. */
export function collectTopTags(
  entities: ReadonlyArray<{ top_tags?: string[] | null }>,
): string[] {
  const counts = new Map<string, number>()
  for (const e of entities) {
    for (const tag of e.top_tags ?? []) {
      counts.set(tag, (counts.get(tag) ?? 0) + 1)
    }
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([tag]) => tag)
}

/** Filter entities to those carrying ANY of the selected tags. Empty selection
 *  is a no-op (returns all). Never reorders or sections — that stays taxonomy. */
export function filterEntitiesByTags<T extends { top_tags?: string[] | null }>(
  entities: ReadonlyArray<T>,
  selected: ReadonlySet<string>,
): T[] {
  if (selected.size === 0) return [...entities]
  return entities.filter((e) => (e.top_tags ?? []).some((tag) => selected.has(tag)))
}
