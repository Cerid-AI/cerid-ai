// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Cartographer map user configuration — edge budget, label density, hull
// visibility. Persisted in localStorage under "cerid-map-config".

const STORAGE_KEY = "cerid-map-config"

export type EdgeBudget = "off" | "2k" | "8k" | "all"
export type LabelDensity = "sparse" | "normal" | "rich"

export interface MapConfig {
  /** Edge budget — limits edges rendered at rest by weight percentile */
  edgeBudget: EdgeBudget
  /** Label density — controls sigma labelDensity */
  labelDensity: LabelDensity
  /** Show community hull fills + labels */
  hullsVisible: boolean
  /** Run the live ForceAtlas2 simulation (warm + breathing). Reduced-motion overrides. */
  liveLayout: boolean
  /** Hide degree-0 (orphan) nodes from the graph. */
  hideOrphans: boolean
  /** Collapse member nodes into super-node discs when zoomed out past the
   *  overview threshold (camera ratio >= 1.4; see COLLAPSE_THRESHOLD_DEFAULT). */
  collapseCommunities: boolean
}

export const MAP_CONFIG_DEFAULTS: MapConfig = {
  edgeBudget: "8k",
  labelDensity: "normal",
  hullsVisible: true,
  liveLayout: true,
  hideOrphans: false,
  collapseCommunities: true,
}

export const EDGE_BUDGET_LABELS: Record<EdgeBudget, string> = {
  off: "Off",
  "2k": "2k",
  "8k": "8k",
  all: "All",
}

export const LABEL_DENSITY_LABELS: Record<LabelDensity, string> = {
  sparse: "Sparse",
  normal: "Normal",
  rich: "Rich",
}

/** sigma labelDensity values per setting */
export const LABEL_DENSITY_VALUES: Record<LabelDensity, number> = {
  sparse: 0.05,
  normal: 0.10,
  rich: 0.18,
}

export function loadMapConfig(): MapConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<MapConfig>
      return { ...MAP_CONFIG_DEFAULTS, ...parsed }
    }
  } catch {
    // localStorage unavailable (private mode) — fall through to defaults
  }
  return { ...MAP_CONFIG_DEFAULTS }
}

export function saveMapConfig(config: MapConfig): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config))
  } catch {
    // storage unavailable — selection lives for the session only
  }
}
