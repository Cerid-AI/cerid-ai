// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Stratigraph user configuration. Persisted in localStorage under
// "cerid-timeline-config". Mirrors the map-config pattern.

const STORAGE_KEY = "cerid-timeline-config"

export type TrackBudget = 8 | 16 | 24

export interface TimelineConfig {
  /** Max DOI tracks to display per expanded stratum */
  trackBudget: TrackBudget
  /** Show event-horizon marker hairlines */
  markersVisible: boolean
  /** Hatch/reduce-alpha identified ingest-burst buckets */
  ingestHatch: boolean
  /** Time window period */
  period: "7d" | "30d" | "90d" | "365d"
}

export const TIMELINE_CONFIG_DEFAULTS: TimelineConfig = {
  trackBudget: 8,
  markersVisible: true,
  ingestHatch: true,
  // 30d: the era view should open filled with data rather than leading with
  // empty months; widen via the period tabs as corpus history grows.
  period: "30d",
}

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
