// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Stratigraph user configuration. Persisted in localStorage under
// "cerid-timeline-config". Mirrors the map-config pattern.

const STORAGE_KEY = "cerid-timeline-config"

export type TrackBudget = 8 | 16 | 24

// ---------------------------------------------------------------------------
// Pinned items — generalized to lane | event | entity (amendment #7 / L3)
// ---------------------------------------------------------------------------

/** A pinned lane (stratum) — survives window changes */
export interface PinnedLaneItem {
  type: "lane"
  id: string
  /** ISO-8601 timestamp when pinned */
  ts: string
}

/** A pinned event glyph — jump-to control re-centers the brush */
export interface PinnedEventItem {
  type: "event"
  id: string
  /** ISO-8601 timestamp of the event (used for brush re-center) */
  ts: string
  /** Human label for the pin shelf */
  label: string
}

export type PinnedItem = PinnedLaneItem | PinnedEventItem

// ---------------------------------------------------------------------------
// Main config shape
// ---------------------------------------------------------------------------

export interface TimelineConfig {
  /** Max DOI tracks to display per expanded stratum */
  trackBudget: TrackBudget
  /** Show event-horizon marker hairlines */
  markersVisible: boolean
  /** Hatch/reduce-alpha identified ingest-burst buckets */
  ingestHatch: boolean
  /** Time window period */
  period: "7d" | "30d" | "90d" | "180d" | "365d"
  /** Pinned items (lanes + events) persisted across window changes (amendment #7) */
  pinnedItems: PinnedItem[]
  /** ISO-8601 timestamp of last pane visit — drives since-you-last-looked band.
   *  Written on unmount, not on mount, so the band survives the session. */
  lastViewedAt: string | null
  /** Override the default 180d data-extent window (amendment #7).
   *  null = use the payload's extent_hint clamped to 180d. */
  dataExtentOverride: "7d" | "30d" | "90d" | "180d" | "365d" | null
}

export const TIMELINE_CONFIG_DEFAULTS: TimelineConfig = {
  trackBudget: 8,
  markersVisible: true,
  ingestHatch: true,
  // Amendment #7: default to 180d (data-extent clamped) rather than 30d.
  // Guarantees the May 9–10 deposition events (73% of mentions) and the full
  // VerificationReport series stay in the default view regardless of corpus age.
  period: "180d",
  pinnedItems: [],
  lastViewedAt: null,
  dataExtentOverride: null,
}

// ---------------------------------------------------------------------------
// Load / save
// ---------------------------------------------------------------------------

export function loadTimelineConfig(): TimelineConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<TimelineConfig>
      return { ...TIMELINE_CONFIG_DEFAULTS, ...parsed }
    }
  } catch {
    // localStorage unavailable — fall through to defaults
  }
  return { ...TIMELINE_CONFIG_DEFAULTS }
}

export function saveTimelineConfig(config: TimelineConfig): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config))
  } catch {
    // storage unavailable — config lives for the session only
  }
}

// ---------------------------------------------------------------------------
// lastViewedAt helpers (amendment #3 — since-you-last-looked)
// ---------------------------------------------------------------------------

/** Write the current ISO timestamp as lastViewedAt. Call on pane unmount. */
export function stampLastViewed(config: TimelineConfig): TimelineConfig {
  return { ...config, lastViewedAt: new Date().toISOString() }
}

// ---------------------------------------------------------------------------
// Pinned item helpers
// ---------------------------------------------------------------------------

export function addPinnedItem(config: TimelineConfig, item: PinnedItem): TimelineConfig {
  const filtered = config.pinnedItems.filter(
    (p) => !(p.type === item.type && p.id === item.id),
  )
  return { ...config, pinnedItems: [...filtered, item] }
}

export function removePinnedItem(config: TimelineConfig, type: PinnedItem["type"], id: string): TimelineConfig {
  return { ...config, pinnedItems: config.pinnedItems.filter((p) => !(p.type === type && p.id === id)) }
}
