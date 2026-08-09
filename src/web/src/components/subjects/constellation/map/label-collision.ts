// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: FSL-1.1-ALv2
//
// Pure screen-space label collision pass (A3). Community labels are placed
// greedily by priority (community size) — the largest community claims its
// space first, and any lower-priority label whose box overlaps an
// already-placed one is suppressed. Recomputed per camera move over the few
// hundred visible labels (cheap; runs on the main thread). This is the
// DataMapPlot / Nomic-Atlas legibility trick: reveal only the labels that fit.

export interface LabelRect {
  id: string
  /** Center in viewport px. */
  cx: number
  cy: number
  /** Box extent in px. */
  w: number
  h: number
  /** Higher wins its space; ties broken by input order. */
  priority: number
}

/** AABB overlap of two center-anchored boxes; edge-touching is NOT overlap. */
export function rectsOverlap(a: LabelRect, b: LabelRect, pad = 0): boolean {
  return (
    Math.abs(a.cx - b.cx) * 2 < a.w + b.w + pad * 2 &&
    Math.abs(a.cy - b.cy) * 2 < a.h + b.h + pad * 2
  )
}

/**
 * Returns the set of label ids to render: highest priority first, skipping any
 * box that collides with one already accepted. `pad` inflates every box so
 * near-misses are also suppressed (breathing room between labels).
 */
export function selectVisibleLabels(rects: readonly LabelRect[], pad = 0): Set<string> {
  const ordered = [...rects].sort((a, b) => b.priority - a.priority)
  const placed: LabelRect[] = []
  const visible = new Set<string>()
  for (const rect of ordered) {
    if (placed.some((p) => rectsOverlap(rect, p, pad))) continue
    placed.push(rect)
    visible.add(rect.id)
  }
  return visible
}
