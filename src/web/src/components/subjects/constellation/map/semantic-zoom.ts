// Copyright (c) 2026 Cerid AI. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Pure camera/LOD helpers for semantic zoom. bboxOf computes a camera target
// (centroid + a zoom ratio proportional to the cluster's graph-space extent)
// for a set of points; lodTier buckets the camera ratio into reveal tiers.

export function bboxOf(points: [number, number][]): { x: number; y: number; ratio: number } | null {
  if (points.length === 0) return null
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  let sx = 0, sy = 0
  for (const [x, y] of points) {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x)
    minY = Math.min(minY, y); maxY = Math.max(maxY, y)
    sx += x; sy += y
  }
  const extent = Math.max(maxX - minX, maxY - minY)
  // Map graph-space extent to a sigma camera ratio. Sigma's default full view
  // is ratio 1; a tighter cluster gets a proportionally smaller ratio (zoom in)
  // with a sane floor. The 0.06 factor is tuned in-browser in Step 8.
  const ratio = Math.max(0.05, Math.min(1, extent * 0.06))
  return { x: sx / points.length, y: sy / points.length, ratio }
}

/**
 * Camera target for a set of points already in Sigma's framed-graph coordinate
 * space (the space `camera.x/y/ratio` and `getNodeDisplayData` operate in).
 * Returns the bbox CENTER (better framing than a centroid) and a ratio sized to
 * fit the points' extent with padding. Unlike `bboxOf`, this is fed framed
 * coords so its x/y can be applied directly to the camera (true re-centering).
 */
export function cameraTargetForPoints(
  pts: [number, number][],
  opts: { padding?: number; minRatio?: number; maxRatio?: number } = {},
): { x: number; y: number; ratio: number } | null {
  if (pts.length === 0) return null
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const [x, y] of pts) {
    if (x < minX) minX = x
    if (x > maxX) maxX = x
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  }
  const padding = opts.padding ?? 1.4
  const minRatio = opts.minRatio ?? 0.15
  const maxRatio = opts.maxRatio ?? 1
  const extent = Math.max(maxX - minX, maxY - minY)
  const ratio = Math.min(maxRatio, Math.max(minRatio, extent * padding))
  return { x: (minX + maxX) / 2, y: (minY + maxY) / 2, ratio }
}

export function lodTier(cameraRatio: number): "overview" | "mid" | "detail" {
  if (cameraRatio >= 2.0) return "overview"
  if (cameraRatio >= 0.4) return "mid"
  return "detail"
}

/**
 * Minimum edge `size` (∝ weight) to render at a given LOD tier. Zoomed in
 * (detail) shows every edge; as the camera pulls back (mid → overview) the
 * floor rises so the thinnest co-mention threads drop out and the structure
 * stays legible. Edge `size` is clamped to 1..2.5 at build time, so a 1.6 floor
 * hides the lower ~40% of the size range.
 */
export function lodEdgeMinSize(tier: "overview" | "mid" | "detail"): number {
  switch (tier) {
    case "detail": return 0
    case "mid": return 1.6
    case "overview": return 2.2
  }
}

/**
 * Continuous edge alpha for a LOD tier: 0 at/below the tier floor, ramping to
 * 1 across a fixed fade band above it. Lets edges FADE out as the camera pulls
 * back instead of popping at the binary `lodEdgeMinSize` floor; the reducer
 * treats 0 as hidden.
 */
const LOD_EDGE_FADE_BAND = 0.4

export function lodEdgeAlpha(tier: "overview" | "mid" | "detail", size: number): number {
  if (tier === "detail") return 1
  const floor = lodEdgeMinSize(tier)
  return Math.max(0, Math.min(1, (size - floor) / LOD_EDGE_FADE_BAND))
}
